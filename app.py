import os
from datetime import datetime, timezone
from functools import wraps
import json
from google import genai
from google.genai import types
from pypdf import PdfReader

import bcrypt
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from google import genai
from google.genai import types
from pymongo import MongoClient
from bson import ObjectId

from pymongo.errors import DuplicateKeyError

# ----- Setup -----
# Load .env from the same folder as app.py, wherever you run it from
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
...
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")
print("Secret key loaded:", bool(app.secret_key))

client = MongoClient(os.getenv("mongodb://localhost:27017/"))
db = client["learnova"]
users = db["users"]
users.create_index("email", unique=True)  # no two accounts with the same email
summaries = db["summaries"]
gemini = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
GEMINI_MODEL = os.getenv("GEMINI_MODEL")
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB upload limit
chats = db["chats"]
chats.create_index([("user_id", 1), ("updated_at", -1)])  # fast "my recent chats" lookups

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
    response = gemini.models.generate_content(
        model=GEMINI_MODEL,
        contents=contents,
        config=types.GenerateContentConfig(system_instruction=instructions),
    )
    return response.text


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
        return redirect(url_for("dashboard"))

    return render_template("login.html")

@app.route("/dashboard")
@login_required
def dashboard():
    # Count all questions this user has asked Nova
    pipeline = [
        {"$match": {"user_id": session["user_id"]}},
        {"$project": {"n": {"$size": {"$filter": {
            "input": "$messages",
            "cond": {"$eq": ["$$this.role", "user"]},
        }}}}},
        {"$group": {"_id": None, "total": {"$sum": "$n"}}},
    ]
    result = list(chats.aggregate(pipeline))
    questions_count = result[0]["total"] if result else 0

    return render_template("dashboard.html", name=session["name"], questions_count=questions_count)


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
if __name__ == "__main__":
    app.run(debug=True)