# Learnova – AI-Powered Study Assistant

Learnova is a web app that helps students study smarter. It puts an AI tutor, a notes summarizer and a quiz generator in one place, and tracks your study time, goals and progress on a personal dashboard.

Built with **Flask**, **MongoDB** and **Google Gemini** as a CSE minor project.

---

## Features

### Chat with Nova
- AI study tutor that answers questions clearly, step by step
- Remembers the conversation, so follow-up questions work naturally
- Chat history saved per user: reopen or delete any past chat
- Floating Nova assistant on the home page for quick questions

### Notes Summarizer
- Paste text **or upload a PDF** (up to 10 MB)
- Get a short summary and key points
- History of all summaries with a viewer, copy and delete

### Quiz Generator
- Create multiple-choice quizzes from **any topic** or **your own notes**
- 5, 10 or 15 questions · Easy, Medium or Hard
- Instant feedback with explanations for every answer
- Scores calculated on the server and saved; review answers after each quiz

### Dashboard
- Personal greeting, live stats (questions asked, notes summarized, quizzes taken, average score)
- Recent activity timeline: click any item to open it
- Smart notifications built from your own activity

### Study Goals & Streaks
- Choose a daily goal (15 / 30 / 45 / 60 minutes)
- Study time tracked automatically while you actively use the tools
- Streak counter and a 7-day progress chart

### And also
- Secure sign up / login with hashed passwords
- Profile page with photo upload
- Light and dark mode
- Responsive design for desktop, tablet and mobile

---

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | HTML5, CSS3, JavaScript (no frameworks) |
| Backend | Python, Flask, Jinja2 |
| Database | MongoDB (PyMongo) |
| AI | Google Gemini API (`google-genai` SDK) |
| Security | bcrypt, Flask sessions, python-dotenv |
| Other | pypdf (PDF text extraction), Pydantic (structured AI output) |

---

## Getting Started

### Prerequisites
- **Python 3.10+**
- **MongoDB Community Server** running locally (or a MongoDB Atlas connection string)
- A free **Gemini API key** from [Google AI Studio](https://aistudio.google.com/)

### 1. Clone the repository
```bash
git clone https://github.com/nidaa41/Learnova.git
cd Learnova
```

### 2. Create and activate a virtual environment
```bash
python -m venv venv

# Windows
venv\Scripts\activate

# macOS / Linux
source venv/bin/activate
```

### 3. Install dependencies
```bash
pip install -r requirements.txt
```

### 4. Create a `.env` file
Create a file named `.env` in the project root (next to `app.py`):

```env
SECRET_KEY=your-random-secret-key
MONGO_URI=mongodb://localhost:27017/
GEMINI_API_KEY=your-gemini-api-key
GEMINI_MODEL=gemini-2.5-flash
GEMINI_FALLBACK_MODELS=gemini-3.5-flash,gemini-3.6-flash
```

Generate a secret key with:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

> **Never commit `.env`.** It is already listed in `.gitignore`.

### 5. Run the app
```bash
python app.py
```

Open **http://127.0.0.1:5000** in your browser and create an account.

---

## Project Structure

```
Learnova/
├── app.py                  # Flask app: routes, auth, database, AI logic
├── requirements.txt
├── .env                    # your secrets (not in the repo)
├── templates/
│   ├── index.html          # landing page
│   ├── login.html, signup.html
│   ├── dashboard.html
│   ├── chat.html           # Chat with Nova
│   ├── summarizer.html     # Notes Summarizer
│   ├── quiz.html           # Quiz Generator
│   ├── profile.html
│   └── partials/nova.html  # floating Nova widget
├── static/
│   ├── css/                # stylesheets
│   └── js/                 # page scripts, theme, study timer
└── uploads/avatars/        # profile photos (not in the repo)
```

---

## Database Collections

| Collection | Stores |
|---|---|
| `users` | Accounts (name, email, hashed password, goal, avatar) |
| `chats` | Nova conversations and messages |
| `summaries` | Saved summaries and key points |
| `quizzes` | Generated quizzes, answers and scores |
| `study_logs` | Minutes studied per user per day |

---

## Security

- Passwords hashed with **bcrypt**; plain-text passwords are never stored
- Every query is filtered by the logged-in user's ID, so users only see their own data
- API key kept on the server; the browser never talks to Gemini directly
- Uploaded images checked by **file contents**, not just extension, with size limits
- Quiz scores calculated on the server so they can't be faked
- AI responses validated before being saved or shown

---

## Reliability

The Gemini free tier can be busy at peak times. All AI features go through one helper that **automatically retries and fails over to backup models** (set in `GEMINI_FALLBACK_MODELS`), so users still get an answer when one model is overloaded. If every model is busy, the app shows a friendly message instead of crashing.

---

## Future Scope

- OCR for scanned PDFs and handwritten notes
- Multi-language support

---
