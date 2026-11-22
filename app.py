import os
from datetime import datetime, timezone
from functools import wraps
import json
from google import genai
from google.genai import types
from pypdf import PdfReader

import bcrypt
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash, send_from_directory
from google import genai
from google.genai import types
from pymongo import MongoClient
from bson import ObjectId

from pymongo.errors import DuplicateKeyError

import json
from pydantic import BaseModel

import time

# ----- Setup -----
# Load .env from the same folder as app.py, wherever you run it from
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
...
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")

app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024  # 2 MB upload limit

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
AVATAR_FOLDER = os.path.join(BASE_DIR, "uploads", "avatars")
os.makedirs(AVATAR_FOLDER, exist_ok=True)


@app.context_processor
def inject_avatar():
    """Make the avatar version available in every template."""
    return {"avatar": session.get("avatar")}
print("Secret key loaded:", bool(app.secret_key))

client = MongoClient(os.getenv("mongodb://localhost:27017/"))
db = client["learnova"]
users = db["users"]
users.create_index("email", unique=True)  # no two accounts with the same email
summaries = db["summaries"]
gemini = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
GEMINI_MODEL = os.getenv("GEMINI_MODEL")

GEMINI_FALLBACK_MODEL = os.getenv("GEMINI_FALLBACK_MODEL")


def gemini_generate(contents, config):
    """
    Call Gemini, retrying when Google is busy (503) or rate-limited (429).
    Tries the main model first, then the fallback model if one is set.
    """
    models = [GEMINI_MODEL] + ([GEMINI_FALLBACK_MODEL] if GEMINI_FALLBACK_MODEL else [])
    last_error = None

    for model in models:
        for attempt in range(3):
            try:
                return gemini.models.generate_content(model=model, contents=contents, config=config)
            except Exception as e:
                last_error = e
                code = getattr(e, "code", None)
                if code in (429, 500, 503):
                    app.logger.warning("Gemini busy (%s) on %s, attempt %d", code, model, attempt + 1)
                    time.sleep(1.5 * (attempt + 1))  # wait 1.5s, 3s, 4.5s
                    continue
                raise  # other errors (bad key, bad request): stop right away
        # this model failed 3 times; try the next one

    raise last_error

app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB upload limit
chats = db["chats"]
chats.create_index([("user_id", 1), ("updated_at", -1)])  # fast "my recent chats" lookups
quizzes = db["quizzes"]
quizzes.create_index([("user_id", 1), ("completed_at", -1)])

# ----- Gemini setup -----
gemini = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")

NOVA_INSTRUCTIONS = """
You are Nova, the friendly AI study assistant inside Learnova, a study app for college students.
- Explain things clearly and step by step, like a helpful senior student.
- Keep answers short (under about 200 words) unless the student asks for more detail.
- Use plain text only: no Markdown symbols like ** or #. For lists, use simple dashes.
- If a question is not about studying, answer briefly and gently steer back to learning.
- If you are not sure about something, say so honestly.
"""


def ask_gemini(messages, instructions=NOVA_INSTRUCTIONS):
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
        types.GenerateContentConfig(system_instruction=instructions),
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

    return render_template(
        "dashboard.html",
        name=session["name"],
        questions_count=questions_count,
        quizzes_taken=quizzes_taken,
        avg_score=avg_score,
    )

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
@login_required
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

    try:
        response = gemini.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        data = json.loads(response.text)
    except Exception as e:
        print("Gemini error:", e)
        return jsonify(error="The AI could not summarize this. Please try again."), 500

    summary = data.get("summary", "")
    key_points = data.get("key_points", [])

    summaries.insert_one({
        "user_id": session["user_id"],
        "summary": summary,
        "key_points": key_points,
        "created_at": datetime.now(timezone.utc),
    })

    return jsonify(summary=summary, key_points=key_points)

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
    flash("That image is too big. Please choose one under 2 MB.", "avatar_error")
    return redirect(url_for("profile"))

if __name__ == "__main__":
    app.run(debug=True, use_reloader=False)