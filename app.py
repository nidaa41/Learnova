import os
import json
import time
from datetime import datetime, timezone, timedelta
from functools import wraps

import bcrypt
from bson import ObjectId
from dotenv import load_dotenv
from flask import (Flask, render_template, request, redirect, url_for, session,
                   jsonify, flash, send_from_directory)
from google import genai
from google.genai import types
from pydantic import BaseModel
from pymongo import MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError
from pypdf import PdfReader

# ----- Setup -----
# Load .env from the same folder as app.py, wherever you run it from
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

if not os.getenv("SECRET_KEY"):
    raise RuntimeError("SECRET_KEY missing: check that .env exists next to app.py")

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB max upload (PDFs); photos are checked separately

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
AVATAR_FOLDER = os.path.join(BASE_DIR, "uploads", "avatars")
os.makedirs(AVATAR_FOLDER, exist_ok=True)
AVATAR_MAX_BYTES = 2 * 1024 * 1024  # 2 MB for profile photos


@app.context_processor
def inject_avatar():
    """Make the avatar version available in every template."""
    return {"avatar": session.get("avatar")}


# ----- Database -----
client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017/"))
db = client["learnova"]

users = db["users"]
users.create_index("email", unique=True)  # no two accounts with the same email

chats = db["chats"]
chats.create_index([("user_id", 1), ("updated_at", -1)])  # fast "my recent chats" lookups

quizzes = db["quizzes"]
quizzes.create_index([("user_id", 1), ("completed_at", -1)])

summaries = db["summaries"]

study_logs = db["study_logs"]
study_logs.create_index([("user_id", 1), ("date", 1)], unique=True)  # one record per user per day


# ===== Gemini setup (one client for the whole app) =====
gemini = genai.Client(
    api_key=os.getenv("GEMINI_API_KEY"),
    http_options=types.HttpOptions(api_version="v1beta"),  # needed for system instructions
)

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# Backup models, tried in order when the main one is busy
_fallbacks = os.getenv("GEMINI_FALLBACK_MODELS") or ""
GEMINI_FALLBACK_MODELS = [m.strip() for m in _fallbacks.split(",") if m.strip()]

print("Main model:", GEMINI_MODEL)
print("Fallbacks:", GEMINI_FALLBACK_MODELS)


def gemini_generate(contents, config):
    """
    Call Gemini. If a model is busy (503) or rate-limited (429),
    move straight on to the next model. Two quick rounds through all models.
    """
    models = [GEMINI_MODEL] + [m for m in GEMINI_FALLBACK_MODELS if m != GEMINI_MODEL]
    last_error = None

    for round_no in range(2):
        for model in models:
            try:
                return gemini.models.generate_content(model=model, contents=contents, config=config)
            except Exception as e:
                code = getattr(e, "code", None)
                if code in (429, 500, 503):
                    last_error = e
                    app.logger.warning("Gemini busy (%s) on %s, round %d", code, model, round_no + 1)
                    time.sleep(0.5)
                    continue
                raise
        time.sleep(2)

    raise last_error


NOVA_INSTRUCTIONS = """
You are Nova, the friendly AI study assistant inside Learnova, a study app for college students.
- Explain things clearly and step by step, like a helpful senior student.
- Keep answers short (under about 200 words) unless the student asks for more detail.
- Use plain text only: no Markdown symbols like ** or #. For lists, use simple dashes.
- If a question is not about studying, answer briefly and gently steer back to learning.
- If you are not sure about something, say so honestly.
"""


def ask_gemini(messages, instructions=None):
    """Send a conversation to Gemini and return the reply text."""
    contents = [
        types.Content(
            role="user" if m["role"] == "user" else "model",
            parts=[types.Part(text=m["text"])],
        )
        for m in messages
    ]
    response = gemini_generate(
        contents,
        types.GenerateContentConfig(system_instruction=instructions or NOVA_INSTRUCTIONS),
    )
    return response.text
# ----- Quiz generation -----
class QuizQuestion(BaseModel):
    question: str
    options: list[str]
    answer_index: int
    explanation: str


QUIZ_INSTRUCTIONS = """
You create multiple-choice quizzes for college students.
Rules:
- Every question has exactly 4 options and exactly ONE correct answer.
- answer_index is the position of the correct option: 0, 1, 2 or 3.
- Spread the correct answers across different positions (don't always use the same one).
- Wrong options must be believable, not silly.
- explanation: 1-2 short sentences saying why the correct answer is right.
- Plain text only: no Markdown, no "A)" or "1." labels inside the options.
- Never repeat a question.
"""


def generate_quiz(mode, text, count, difficulty):
    """Ask Gemini for a quiz and return a cleaned list of questions."""
    if mode == "topic":
        prompt = f"Create {count} {difficulty}-level multiple-choice questions about: {text}"
    else:
        prompt = (f"Create {count} {difficulty}-level multiple-choice questions "
                  f"based ONLY on the following notes:\n\n{text}")

    response = gemini_generate(
        prompt,
        types.GenerateContentConfig(
            system_instruction=QUIZ_INSTRUCTIONS,
            response_mime_type="application/json",
            response_schema=list[QuizQuestion],
        ),
    )
    raw = json.loads(response.text)

    # Double-check every question; AI output can't be trusted blindly
    cleaned = []
    for q in raw if isinstance(raw, list) else []:
        if not isinstance(q, dict):
            continue
        question = q.get("question")
        options = q.get("options")
        answer = q.get("answer_index")
        if (isinstance(question, str) and question.strip()
                and isinstance(options, list) and len(options) == 4
                and all(isinstance(o, str) and o.strip() for o in options)
                and isinstance(answer, int) and 0 <= answer <= 3):
            cleaned.append({
                "question": question.strip(),
                "options": [o.strip() for o in options],
                "answer_index": answer,
                "explanation": str(q.get("explanation", "")).strip(),
            })
    return cleaned[:count]


# ----- Helper: protect pages that need login -----
def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapper

def api_login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return jsonify(error="Please log in again."), 401
        return view(*args, **kwargs)
    return wrapper


def get_user_chat(chat_id):
    """Return the chat only if it exists AND belongs to the logged-in user."""
    if not ObjectId.is_valid(chat_id):
        return None
    return chats.find_one({"_id": ObjectId(chat_id), "user_id": session["user_id"]})


# ----- Pages -----
@app.route("/")
def index():
    return render_template("index.html", name=session.get("name"))


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if "user_id" in session:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")

        # Server-side checks (never trust only the browser)
        error = None
        if not name:
            error = "Please enter your name."
        elif "@" not in email or "." not in email:
            error = "Please enter a valid email address."
        elif len(password) < 6:
            error = "Password must be at least 6 characters."
        elif password != confirm:
            error = "Passwords do not match."
        elif users.find_one({"email": email}):
            error = "An account with this email already exists."

        if error:
            return render_template("signup.html", error=error, name=name, email=email)

        # Hash the password, never store it as plain text
        hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())

        try:
            result = users.insert_one({
                "name": name,
                "email": email,
                "password": hashed,
                "created_at": datetime.now(timezone.utc),
            })
        except DuplicateKeyError:
            return render_template("signup.html",
                                   error="An account with this email already exists.",
                                   name=name, email=email)

        # Log the new user in straight away
        session["user_id"] = str(result.inserted_id)
        session["name"] = name
        return redirect(url_for("dashboard"))

    return render_template("signup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        user = users.find_one({"email": email})

        # Same message for wrong email or wrong password (safer)
        if not user or not bcrypt.checkpw(password.encode("utf-8"), user["password"]):
            return render_template("login.html", error="Incorrect email or password.", email=email)

        session["user_id"] = str(user["_id"])
        session["name"] = user["name"]
        session["avatar"] = user.get("avatar_v")
        return redirect(url_for("dashboard"))

    return render_template("login.html")

@app.route("/dashboard")
@login_required
def dashboard():
    uid = session["user_id"]

    # Questions asked to Nova
    pipeline = [
        {"$match": {"user_id": uid}},
        {"$project": {"n": {"$size": {"$filter": {
            "input": "$messages",
            "cond": {"$eq": ["$$this.role", "user"]},
        }}}}},
        {"$group": {"_id": None, "total": {"$sum": "$n"}}},
    ]
    result = list(chats.aggregate(pipeline))
    questions_count = result[0]["total"] if result else 0

    # Quizzes taken + average score
    quiz_stats = list(quizzes.aggregate([
        {"$match": {"user_id": uid, "completed": True}},
        {"$group": {"_id": None, "count": {"$sum": 1}, "avg": {"$avg": "$percent"}}},
    ]))
    quizzes_taken = quiz_stats[0]["count"] if quiz_stats else 0
    avg_score = round(quiz_stats[0]["avg"]) if quiz_stats else None
    notes_count = summaries.count_documents({"user_id": uid})

    return render_template(
        "dashboard.html",
        name=session["name"],
        questions_count=questions_count,
        quizzes_taken=quizzes_taken,
        avg_score=avg_score,
        notes_count=notes_count,
        recent_activity=build_recent_activity(uid),
        notifications=build_notifications(uid, questions_count),
        study=build_study_stats(uid),
    )

# ----- Notifications -----
STUDY_TIPS = [
    "Study in 25-minute blocks with 5-minute breaks (the Pomodoro technique).",
    "Explain a topic out loud as if teaching someone. It shows what you really understand.",
    "Test yourself with a quick quiz instead of just re-reading your notes.",
    "Review new material within 24 hours to remember it much longer.",
    "Put your phone in another room while studying to stay focused.",
    "Summarize each chapter in 3 sentences to lock in the key ideas.",
]

NEXT_LEVEL = {"easy": "Medium", "medium": "Hard"}


def build_notifications(uid, questions_count):
    """Create notifications from the user's own activity."""
    notes = []

    # New user: no questions asked yet
    if questions_count == 0:
        notes.append({
            "id": "welcome",
            "icon": "chat", "color": "chat",
            "title": "Welcome to Learnova!",
            "text": "Start by asking Nova your first study question.",
            "link": "/chat",
        })

    # Latest quiz result, or a nudge to take one
    last_quiz = quizzes.find_one({"user_id": uid, "completed": True}, sort=[("completed_at", -1)])
    if last_quiz:
        p = last_quiz["percent"]
        topic = last_quiz["topic"]
        topic = topic[:35] + "…" if len(topic) > 35 else topic
        if p >= 80:
            nxt = NEXT_LEVEL.get(last_quiz.get("difficulty"))
            text = f"You scored {p}% on {topic}. " + (f"Ready to try {nxt} mode?" if nxt else "Excellent work!")
        elif p >= 50:
            text = f"You scored {p}% on {topic}. Review the explanations and try again!"
        else:
            text = f"You scored {p}% on {topic}. Ask Nova to explain the tricky parts."
        notes.append({
            "id": f"quiz-{last_quiz['_id']}-{p}",  # changes when the score changes
            "icon": "trophy", "color": "score",
            "title": "Quiz result",
            "text": text,
            "link": "/quiz",
        })
    else:
        notes.append({
            "id": "no-quiz",
            "icon": "quiz", "color": "quiz",
            "title": "Test yourself",
            "text": "You haven't taken a quiz yet. Generate one on any topic!",
            "link": "/quiz",
        })

    # Profile photo reminder
    if not session.get("avatar"):
        notes.append({
            "id": "no-photo",
            "icon": "user", "color": "chat",
            "title": "Add a profile photo",
            "text": "Personalise your account with a photo.",
            "link": "/profile",
        })

    # Daily study tip (new one each day)
    today = datetime.now().date()
    notes.append({
        "id": f"tip-{today.isoformat()}",
        "icon": "idea", "color": "idea",
        "title": "Study tip of the day",
        "text": STUDY_TIPS[today.toordinal() % len(STUDY_TIPS)],
        "link": None,
    })

    return notes

# ----- Recent activity -----
def time_ago(dt):
    """Turn a date into '5 min ago', '2 hours ago', etc."""
    if dt.tzinfo is None:  # MongoDB returns dates without a timezone (they're UTC)
        dt = dt.replace(tzinfo=timezone.utc)
    seconds = int((datetime.now(timezone.utc) - dt).total_seconds())

    if seconds < 60:
        return "Just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hour{'s' if hours > 1 else ''} ago"
    days = hours // 24
    if days < 7:
        return "Yesterday" if days == 1 else f"{days} days ago"
    return dt.strftime("%d %b %Y")


def shorten(text, length=55):
    text = (text or "").strip()
    return text[:length] + "…" if len(text) > length else text


def build_recent_activity(uid, limit=6):
    """Merge the latest chats, quizzes and summaries into one timeline."""
    items = []

    for c in chats.find({"user_id": uid}, {"title": 1, "updated_at": 1}).sort("updated_at", -1).limit(limit):
        items.append({
            "icon": "chat", "color": "chat",
            "title": shorten(c["title"]),
            "subtitle": "Chat with Nova",
            "time": c["updated_at"],
            "link": f"/chat?id={c['_id']}",
        })

    for q in (quizzes.find({"user_id": uid, "completed": True},
                           {"topic": 1, "percent": 1, "completed_at": 1})
                     .sort("completed_at", -1).limit(limit)):
        items.append({
            "icon": "quiz", "color": "quiz",
            "title": shorten(q["topic"]),
            "subtitle": f"Quiz · scored {q['percent']}%",
            "time": q["completed_at"],
            "link": "/quiz",
        })

    for s in summaries.find({"user_id": uid}, {"summary": 1, "created_at": 1}).sort("created_at", -1).limit(limit):
        items.append({
            "icon": "notes", "color": "notes",
            "title": shorten(s.get("summary")),
            "subtitle": "Notes summarized",
            "time": s["created_at"],
            "link": f"/summarizer?id={s['_id']}",
        })

    # Newest first, keep only the top few
    items.sort(key=lambda i: i["time"], reverse=True)
    for item in items:
        item["ago"] = time_ago(item["time"])
    return items[:limit]

# ----- Study goal -----
GOAL_CHOICES = (15, 30, 45, 60)


def build_study_stats(uid):
    """Today's minutes, goal, streak and the last 7 days."""
    user = users.find_one({"_id": ObjectId(uid)}, {"goal_minutes": 1})
    goal = (user or {}).get("goal_minutes", 30)

    today = datetime.now().date()
    start = today - timedelta(days=59)
    logs = {
        log["date"]: log["minutes"]
        for log in study_logs.find({"user_id": uid, "date": {"$gte": start.isoformat()}})
    }
    today_minutes = logs.get(today.isoformat(), 0)

    # Streak: count back through consecutive days where the goal was met.
    # If today's goal isn't met yet, start counting from yesterday.
    streak = 0
    day = today if today_minutes >= goal else today - timedelta(days=1)
    while logs.get(day.isoformat(), 0) >= goal:
        streak += 1
        day -= timedelta(days=1)

    # Last 7 days for the mini bar chart
    week = []
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        minutes = logs.get(d.isoformat(), 0)
        week.append({
            "label": d.strftime("%a")[:2],
            "minutes": minutes,
            "pct": min(100, round(minutes * 100 / goal)),
            "today": i == 0,
        })

    return {"goal": goal, "today": today_minutes, "streak": streak, "week": week, "choices": GOAL_CHOICES}

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))

# ----- AI API -----
@app.route("/api/nova", methods=["POST"])
def api_nova():
    data = request.get_json(silent=True) or {}
    raw = data.get("messages", [])

    # Keep only valid messages, last 10, max 2000 characters each
    messages = [
        {"role": m["role"], "text": m["text"][:2000]}
        for m in raw[-10:]
        if isinstance(m, dict)
        and m.get("role") in ("user", "bot")
        and isinstance(m.get("text"), str)
        and m["text"].strip()
    ]

    # Gemini expects the conversation to start with the user
    while messages and messages[0]["role"] != "user":
        messages.pop(0)

    if not messages or messages[-1]["role"] != "user":
        return jsonify(error="Please send a question."), 400

    try:
        reply = ask_gemini(messages)
    except Exception as e:
        app.logger.error("Gemini error: %s", e)
        return jsonify(error="Nova is taking a short break. Please try again in a minute."), 503

    return jsonify(reply=reply)

    # ----- Chat with Nova (full page) -----
@app.route("/chat")
@login_required
def chat_page():
    return render_template("chat.html", name=session["name"])


@app.route("/api/chats")
@api_login_required
def list_chats():
    docs = (chats.find({"user_id": session["user_id"]}, {"title": 1})
                 .sort("updated_at", -1)
                 .limit(50))
    return jsonify(chats=[{"id": str(d["_id"]), "title": d["title"]} for d in docs])


@app.route("/api/chats/<chat_id>")
@api_login_required
def get_chat(chat_id):
    chat = get_user_chat(chat_id)
    if not chat:
        return jsonify(error="Chat not found."), 404
    return jsonify(
        id=str(chat["_id"]),
        title=chat["title"],
        messages=[{"role": m["role"], "text": m["text"]} for m in chat["messages"]],
    )


@app.route("/api/chats/<chat_id>", methods=["DELETE"])
@api_login_required
def delete_chat(chat_id):
    chat = get_user_chat(chat_id)
    if not chat:
        return jsonify(error="Chat not found."), 404
    chats.delete_one({"_id": chat["_id"]})
    return jsonify(ok=True)


@app.route("/api/chat", methods=["POST"])
@api_login_required
def send_chat():
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()[:4000]
    if not message:
        return jsonify(error="Please type a question."), 400

    # Continue an existing chat, or start a new one
    chat = None
    if data.get("chat_id"):
        chat = get_user_chat(data["chat_id"])
        if not chat:
            return jsonify(error="Chat not found."), 404

    # Send the last 10 messages so Nova remembers the conversation
    history = [{"role": m["role"], "text": m["text"]} for m in (chat["messages"][-10:] if chat else [])]
    history.append({"role": "user", "text": message})

    try:
        reply = ask_gemini(history) or "Sorry, I can't help with that one. Try asking another way?"
    except Exception as e:
        app.logger.error("Gemini error: %s", e)
        return jsonify(error="Nova is taking a short break. Please try again in a minute."), 503

    # Save the question and answer together (only after Gemini succeeded)
    now = datetime.now(timezone.utc)
    new_messages = [
        {"role": "user", "text": message, "time": now},
        {"role": "bot", "text": reply, "time": now},
    ]

    if chat:
        chats.update_one(
            {"_id": chat["_id"]},
            {"$push": {"messages": {"$each": new_messages}}, "$set": {"updated_at": now}},
        )
        chat_id, title = str(chat["_id"]), chat["title"]
    else:
        title = message[:40] + ("…" if len(message) > 40 else "")
        result = chats.insert_one({
            "user_id": session["user_id"],
            "title": title,
            "messages": new_messages,
            "created_at": now,
            "updated_at": now,
        })
        chat_id = str(result.inserted_id)

    return jsonify(chat_id=chat_id, title=title, reply=reply)

@app.route("/summarizer")
@login_required
def summarizer():
    return render_template("summarizer.html", name=session["name"])


@app.route("/api/summarize", methods=["POST"])
@api_login_required
def api_summarize():
    text = request.form.get("text", "").strip()
    pdf = request.files.get("pdf")

    # If a PDF was uploaded, read the text out of it
    if pdf and pdf.filename:
        if not pdf.filename.lower().endswith(".pdf"):
            return jsonify(error="Please upload a PDF file."), 400
        try:
            reader = PdfReader(pdf)
            text = "\n".join((page.extract_text() or "") for page in reader.pages).strip()
        except Exception:
            return jsonify(error="Could not read that PDF."), 400

    if len(text) < 50:
        return jsonify(error="Please give at least a few sentences of notes."), 400

    text = text[:30000]  # keep the request a reasonable size

    prompt = (
        "You are a study assistant. Summarize the student's notes below.\n"
        "Reply ONLY with JSON in this format:\n"
        '{"summary": "a clear summary in 4-6 sentences", '
        '"key_points": ["point 1", "point 2", "..."]}\n'
        "Give 5 to 8 short key points.\n\n"
        "NOTES:\n" + text
    )

    # Tell Gemini to answer in pure JSON (no extra text around it)
    config = types.GenerateContentConfig(response_mime_type="application/json")

    try:
        response = gemini_generate(prompt, config)
        data = json.loads(response.text)
    except Exception as e:
        app.logger.error("Summarizer error: %s", e)
        return jsonify(error="Nova is taking a short break. Please try again in a minute."), 503

    summary = str(data.get("summary", "")).strip()
    key_points = [str(p) for p in data.get("key_points", []) if str(p).strip()]

    if not summary:
        return jsonify(error="The AI could not summarize this. Please try again."), 502

    result = summaries.insert_one({
        "user_id": session["user_id"],
        "summary": summary,
        "key_points": key_points,
        "created_at": datetime.now(timezone.utc),
    })

    return jsonify(id=str(result.inserted_id), summary=summary, key_points=key_points)
# ----- Quiz Generator -----
@app.route("/quiz")
@login_required
def quiz_page():
    return render_template("quiz.html", name=session["name"])


def get_user_quiz(quiz_id):
    if not ObjectId.is_valid(quiz_id):
        return None
    return quizzes.find_one({"_id": ObjectId(quiz_id), "user_id": session["user_id"]})


@app.route("/api/quiz/generate", methods=["POST"])
@api_login_required
def api_generate_quiz():
    data = request.get_json(silent=True) or {}
    mode = data.get("mode")
    text = (data.get("text") or "").strip()
    count = data.get("count")
    difficulty = data.get("difficulty")

    # Validate everything coming from the browser
    if mode not in ("topic", "notes"):
        return jsonify(error="Please choose Topic or My notes."), 400
    if count not in (5, 10, 15):
        return jsonify(error="Please choose 5, 10 or 15 questions."), 400
    if difficulty not in ("easy", "medium", "hard"):
        return jsonify(error="Please choose a difficulty."), 400
    if mode == "topic" and not (2 <= len(text) <= 200):
        return jsonify(error="Please enter a topic (up to 200 characters)."), 400
    if mode == "notes" and not (100 <= len(text) <= 15000):
        return jsonify(error="Please paste between 100 and 15,000 characters of notes."), 400

    try:
        questions = generate_quiz(mode, text, count, difficulty)
    except Exception as e:
        app.logger.error("Quiz generation error: %s", e)
        return jsonify(error="Nova is taking a short break. Please try again in a minute."), 503

    if len(questions) < 3:
        return jsonify(error="Nova couldn't make a good quiz from that. Try a clearer topic or longer notes."), 502

    topic = text if mode == "topic" else "Quiz from your notes"
    result = quizzes.insert_one({
        "user_id": session["user_id"],
        "topic": topic,
        "mode": mode,
        "difficulty": difficulty,
        "questions": questions,
        "completed": False,
        "created_at": datetime.now(timezone.utc),
    })

    return jsonify(quiz_id=str(result.inserted_id), topic=topic, questions=questions)


@app.route("/api/quiz/<quiz_id>/submit", methods=["POST"])
@api_login_required
def api_submit_quiz(quiz_id):
    quiz = get_user_quiz(quiz_id)
    if not quiz:
        return jsonify(error="Quiz not found."), 404

    data = request.get_json(silent=True) or {}
    answers = data.get("answers")
    questions = quiz["questions"]

    if not isinstance(answers, list) or len(answers) != len(questions):
        return jsonify(error="Answers don't match the quiz."), 400

    # The server calculates the score, so the browser can't fake it
    score = sum(1 for a, q in zip(answers, questions) if a == q["answer_index"])
    total = len(questions)
    percent = round(score * 100 / total)

    quizzes.update_one({"_id": quiz["_id"]}, {"$set": {
        "answers": answers,
        "score": score,
        "total": total,
        "percent": percent,
        "completed": True,
        "completed_at": datetime.now(timezone.utc),
    }})

    return jsonify(score=score, total=total, percent=percent)


@app.route("/api/quizzes/recent")
@api_login_required
def api_recent_quizzes():
    docs = (quizzes.find({"user_id": session["user_id"], "completed": True},
                         {"topic": 1, "score": 1, "total": 1, "percent": 1, "difficulty": 1})
                   .sort("completed_at", -1)
                   .limit(5))
    return jsonify(quizzes=[{
        "topic": d["topic"],
        "score": d["score"],
        "total": d["total"],
        "percent": d["percent"],
        "difficulty": d.get("difficulty", ""),
    } for d in docs])

# ----- Settings -----
def current_user():
    return users.find_one({"_id": ObjectId(session["user_id"])})

@app.route("/profile")
@login_required
def profile():
    user = current_user()
    if not user:  # account no longer exists
        session.clear()
        return redirect(url_for("login"))
    return render_template("profile.html", name=session["name"], user=user)

# ----- Avatar -----
def detect_image_type(header):
    """Check the real file type from its first bytes (not the file name)."""
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if header.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "webp"
    return None


def delete_avatar_files(user_id):
    for ext in ("png", "jpg", "webp"):
        path = os.path.join(AVATAR_FOLDER, f"{user_id}.{ext}")
        if os.path.exists(path):
            os.remove(path)


@app.route("/profile/avatar", methods=["POST"])
@login_required
def upload_avatar():
    file = request.files.get("avatar")
    if not file or file.filename == "":
        flash("Please choose an image first.", "avatar_error")
        return redirect(url_for("profile"))
    header = file.read(16)
    file.seek(0)
    file.seek(0, os.SEEK_END)
    size = file.tell()
    file.seek(0)
    if size > AVATAR_MAX_BYTES:
        flash("That image is too big. Please choose one under 2 MB.", "avatar_error")
        return redirect(url_for("profile"))
    ext = detect_image_type(header)
    if not ext:
        flash("Please upload a PNG, JPG or WEBP image.", "avatar_error")
        return redirect(url_for("profile"))

    uid = session["user_id"]
    delete_avatar_files(uid)  # remove the old photo, whatever its type
    file.save(os.path.join(AVATAR_FOLDER, f"{uid}.{ext}"))

    version = int(time.time())  # changes the image URL so browsers don't show the old one
    users.update_one({"_id": ObjectId(uid)}, {"$set": {"avatar_ext": ext, "avatar_v": version}})
    session["avatar"] = version

    flash("Your photo has been updated.", "avatar_success")
    return redirect(url_for("profile"))


@app.route("/profile/avatar/remove", methods=["POST"])
@login_required
def remove_avatar():
    uid = session["user_id"]
    delete_avatar_files(uid)
    users.update_one({"_id": ObjectId(uid)}, {"$unset": {"avatar_ext": "", "avatar_v": ""}})
    session.pop("avatar", None)
    flash("Your photo has been removed.", "avatar_success")
    return redirect(url_for("profile"))


@app.route("/my-avatar")
@login_required
def my_avatar():
    user = current_user()
    ext = user.get("avatar_ext") if user else None
    if not ext:
        return "", 404
    return send_from_directory(AVATAR_FOLDER, f"{session['user_id']}.{ext}")


@app.errorhandler(413)
def file_too_large(e):
    if request.path.startswith("/profile"):
        flash("That image is too big. Please choose one under 2 MB.", "avatar_error")
        return redirect(url_for("profile"))
    return jsonify(error="That file is too big. The maximum is 10 MB."), 413

# ----- Summary history -----
def iso(dt):
    """Date to ISO text the browser can read (MongoDB dates are UTC)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def get_user_summary(summary_id):
    if not ObjectId.is_valid(summary_id):
        return None
    return summaries.find_one({"_id": ObjectId(summary_id), "user_id": session["user_id"]})


@app.route("/api/summaries")
@api_login_required
def api_list_summaries():
    docs = (summaries.find({"user_id": session["user_id"]}, {"summary": 1, "created_at": 1})
                     .sort("created_at", -1)
                     .limit(50))
    return jsonify(summaries=[{
        "id": str(d["_id"]),
        "title": shorten(d.get("summary"), 70),
        "created_at": iso(d.get("created_at")),
    } for d in docs])


@app.route("/api/summaries/<summary_id>")
@api_login_required
def api_get_summary(summary_id):
    s = get_user_summary(summary_id)
    if not s:
        return jsonify(error="Summary not found."), 404
    return jsonify(
        id=str(s["_id"]),
        summary=s.get("summary", ""),
        key_points=[str(p) for p in s.get("key_points", [])],
        created_at=iso(s.get("created_at")),
    )


@app.route("/api/summaries/<summary_id>", methods=["DELETE"])
@api_login_required
def api_delete_summary(summary_id):
    s = get_user_summary(summary_id)
    if not s:
        return jsonify(error="Summary not found."), 404
    summaries.delete_one({"_id": s["_id"]})
    return jsonify(ok=True)

@app.route("/api/study/ping", methods=["POST"])
@api_login_required
def study_ping():
    """Called once a minute while the user is actively studying."""
    uid = session["user_id"]
    today = datetime.now().date().isoformat()
    now = datetime.now(timezone.utc)

    # Ignore pings that come too fast (e.g. two tabs open at once)
    log = study_logs.find_one({"user_id": uid, "date": today})
    if log and log.get("last_ping"):
        last = log["last_ping"]
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if (now - last).total_seconds() < 50:
            return jsonify(minutes=log["minutes"], counted=False)

    log = study_logs.find_one_and_update(
        {"user_id": uid, "date": today},
        {"$inc": {"minutes": 1}, "$set": {"last_ping": now}},
        upsert=True,  # create today's record if it doesn't exist yet
        return_document=ReturnDocument.AFTER,
    )
    return jsonify(minutes=log["minutes"], counted=True)


@app.route("/api/study/goal", methods=["POST"])
@api_login_required
def set_study_goal():
    data = request.get_json(silent=True) or {}
    minutes = data.get("minutes")
    if minutes not in GOAL_CHOICES:
        return jsonify(error="Please choose 15, 30, 45 or 60 minutes."), 400
    users.update_one({"_id": ObjectId(session["user_id"])}, {"$set": {"goal_minutes": minutes}})
    return jsonify(goal=minutes)

if __name__ == "__main__":
    app.run(debug=True, use_reloader=False)