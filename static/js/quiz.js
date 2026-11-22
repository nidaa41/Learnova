document.addEventListener("DOMContentLoaded", function () {

    // ----- State -----
    let mode = "topic";
    let count = 10;
    let difficulty = "medium";
    let quiz = null;      // { id, topic, questions }
    let index = 0;        // current question number
    let answers = [];     // what the user picked for each question
    let score = 0;
    let answered = false; // has the current question been answered?

    const $ = function (id) { return document.getElementById(id); };
    const LETTERS = ["A", "B", "C", "D"];

    function icon(id, cls) {
        return '<svg class="ic ' + (cls || "") + '"><use href="#' + id + '"/></svg>';
    }

    function showScreen(id) {
        document.querySelectorAll(".quiz-screen").forEach(function (s) {
            s.classList.toggle("active", s.id === id);
        });
        window.scrollTo({ top: 0, behavior: "smooth" });
    }

    async function api(url, options) {
        const res = await fetch(url, options);
        const data = await res.json().catch(function () { return {}; });
        if (res.status === 401) {
            window.location.href = "/login";
            throw new Error("Logged out");
        }
        if (!res.ok) throw new Error(data.error || "Something went wrong.");
        return data;
    }

    function badgeClass(percent) {
        if (percent >= 80) return "good";
        if (percent >= 50) return "ok";
        return "low";
    }

    // ===== 1. Setup screen =====

    // Topic / Notes tabs
    document.querySelectorAll(".mode-tab").forEach(function (tab) {
        tab.addEventListener("click", function () {
            mode = tab.dataset.mode;
            document.querySelectorAll(".mode-tab").forEach(function (t) {
                t.classList.toggle("active", t === tab);
            });
            $("topicPanel").classList.toggle("hidden", mode !== "topic");
            $("notesPanel").classList.toggle("hidden", mode !== "notes");
            $("setupError").textContent = "";
        });
    });

    // Pill buttons (count + difficulty)
    function setupPills(groupId, onChange) {
        const group = $(groupId);
        group.querySelectorAll(".pill").forEach(function (pill) {
            pill.addEventListener("click", function () {
                group.querySelectorAll(".pill").forEach(function (p) {
                    p.classList.toggle("active", p === pill);
                });
                onChange(pill.dataset.value);
            });
        });
    }
    setupPills("countGroup", function (v) { count = parseInt(v, 10); });
    setupPills("difficultyGroup", function (v) { difficulty = v; });

    // Character counter for notes
    $("notesInput").addEventListener("input", function () {
        $("charCount").textContent = this.value.length.toLocaleString() + " / 15,000";
    });

    // Enter in the topic box = generate
    $("topicInput").addEventListener("keydown", function (e) {
        if (e.key === "Enter") generateQuiz();
    });

    $("generateBtn").addEventListener("click", generateQuiz);

    async function generateQuiz() {
        const text = (mode === "topic" ? $("topicInput").value : $("notesInput").value).trim();
        const error = $("setupError");

        if (mode === "topic" && text.length < 2) {
            error.textContent = "Please enter a topic.";
            return;
        }
        if (mode === "notes" && text.length < 100) {
            error.textContent = "Please paste at least 100 characters of notes.";
            return;
        }
        error.textContent = "";

        showScreen("loadingScreen");
        const stopLoadingText = startLoadingText();

        try {
            const data = await api("/api/quiz/generate", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ mode: mode, text: text, count: count, difficulty: difficulty })
            });
            stopLoadingText();
            quiz = { id: data.quiz_id, topic: data.topic, questions: data.questions };
            startQuiz();
        } catch (err) {
            stopLoadingText();
            showScreen("setupScreen");
            error.textContent = err.message;
        }
    }

    // Rotating messages while waiting
    function startLoadingText() {
        const messages = [
            "Reading the topic",
            "Thinking of good questions",
            "Writing tricky options",
            "Checking the answers",
            "Almost ready"
        ];
        let i = 0;
        $("loadingText").textContent = messages[0];
        const timer = setInterval(function () {
            i = Math.min(i + 1, messages.length - 1);
            $("loadingText").textContent = messages[i];
        }, 1800);
        return function () { clearInterval(timer); };
    }

    // Recent quizzes list
    async function loadRecent() {
        try {
            const data = await api("/api/quizzes/recent");
            const list = $("recentList");
            if (data.quizzes.length === 0) {
                list.innerHTML = '<p class="muted">No quizzes yet. Your results will show up here.</p>';
                return;
            }
            list.innerHTML = "";
            data.quizzes.forEach(function (q) {
                const item = document.createElement("div");
                item.className = "recent-item";
                item.innerHTML =
                    '<div class="recent-info"><span class="recent-topic"></span>' +
                    '<span class="recent-meta"></span></div>' +
                    '<span class="score-badge ' + badgeClass(q.percent) + '"></span>';
                item.querySelector(".recent-topic").textContent = q.topic;
                item.querySelector(".recent-meta").textContent = q.difficulty + " · " + q.score + " / " + q.total;
                item.querySelector(".score-badge").textContent = q.percent + "%";
                list.appendChild(item);
            });
        } catch (err) {
            // Not critical, leave the default text
        }
    }

    // ===== 2. Quiz screen =====
    function startQuiz() {
        index = 0;
        score = 0;
        answers = new Array(quiz.questions.length).fill(null);
        $("quizTopic").textContent = quiz.topic;
        showScreen("questionScreen");
        renderQuestion();
    }

    function renderQuestion() {
        const q = quiz.questions[index];
        const total = quiz.questions.length;
        answered = false;

        $("quizCounter").textContent = "Question " + (index + 1) + " of " + total;
        $("progressBar").style.width = (index / total) * 100 + "%";
        $("liveScore").textContent = score;
        $("questionText").textContent = q.question;
        $("explanation").classList.add("hidden");
        $("nextBtn").classList.add("hidden");

        const options = $("options");
        options.innerHTML = "";
        q.options.forEach(function (text, i) {
            const btn = document.createElement("button");
            btn.className = "option";
            btn.innerHTML = '<span class="option-letter">' + LETTERS[i] + '</span>' +
                            '<span class="option-text"></span><span class="option-mark"></span>';
            btn.querySelector(".option-text").textContent = text;
            btn.addEventListener("click", function () { choose(i); });
            options.appendChild(btn);
        });
    }

    function choose(picked) {
        if (answered) return;
        answered = true;

        const q = quiz.questions[index];
        const correct = picked === q.answer_index;
        answers[index] = picked;
        if (correct) score++;

        // Colour the options
        document.querySelectorAll(".option").forEach(function (btn, i) {
            btn.disabled = true;
            if (i === q.answer_index) {
                btn.classList.add("correct");
                btn.querySelector(".option-mark").innerHTML = icon("i-check");
            } else if (i === picked) {
                btn.classList.add("wrong");
                btn.querySelector(".option-mark").innerHTML = icon("i-x");
            } else {
                btn.classList.add("faded");
            }
        });

        // Explanation
        const box = $("explanation");
        box.innerHTML = correct
            ? '<strong class="good">Correct!</strong> '
            : '<strong class="bad">Not quite.</strong> The answer is ' + LETTERS[q.answer_index] + ". ";
        box.appendChild(document.createTextNode(q.explanation || ""));
        box.classList.remove("hidden");

        // Next button
        const isLast = index === quiz.questions.length - 1;
        $("nextBtn").textContent = isLast ? "See results" : "Next question";
        $("nextBtn").classList.remove("hidden");
        $("liveScore").textContent = score;
        $("progressBar").style.width = ((index + 1) / quiz.questions.length) * 100 + "%";
    }

    $("nextBtn").addEventListener("click", function () {
        if (index < quiz.questions.length - 1) {
            index++;
            renderQuestion();
        } else {
            finishQuiz();
        }
    });

    // Keyboard: 1-4 or A-D to answer, Enter for next
    document.addEventListener("keydown", function (e) {
        if (!$("questionScreen").classList.contains("active")) return;
        const key = e.key.toUpperCase();
        const num = ["1", "2", "3", "4"].indexOf(key);
        const letter = LETTERS.indexOf(key);
        if (!answered && (num !== -1 || letter !== -1)) {
            choose(num !== -1 ? num : letter);
        } else if (answered && e.key === "Enter") {
            $("nextBtn").click();
        }
    });

    // ===== 3. Results =====
    async function finishQuiz() {
        let result = {
            score: score,
            total: quiz.questions.length,
            percent: Math.round((score * 100) / quiz.questions.length)
        };

        try {
            // The server double-checks the score and saves it
            result = await api("/api/quiz/" + quiz.id + "/submit", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ answers: answers })
            });
        } catch (err) {
            // Still show the result even if saving failed
        }

        showResults(result);
        loadRecent();
    }

    function showResults(result) {
        showScreen("resultScreen");

        $("resultPercent").textContent = result.percent + "%";
        $("resultScore").textContent = result.score + " / " + result.total;

        let title = "Keep practicing!";
        let text = "Review the explanations below and try again. You'll get there.";
        if (result.percent >= 80) {
            title = "Excellent work!";
            text = "You really know this topic. Try a harder difficulty next time.";
        } else if (result.percent >= 50) {
            title = "Good job!";
            text = "You're on the right track. Check the questions you missed below.";
        }
        $("resultTitle").textContent = title;
        $("resultText").textContent = text;

        // Animate the score ring
        const ring = $("resultRing");
        const circumference = 326.7;
        ring.style.transition = "none";
        ring.style.strokeDashoffset = circumference;
        setTimeout(function () {
            ring.style.transition = "";
            ring.style.strokeDashoffset = circumference - (result.percent / 100) * circumference;
        }, 100);

        // Review list
        const list = $("reviewList");
        list.innerHTML = "";
        quiz.questions.forEach(function (q, i) {
            const picked = answers[i];
            const correct = picked === q.answer_index;

            const item = document.createElement("div");
            item.className = "review-item";
            item.innerHTML =
                '<div class="review-q">' + icon(correct ? "i-check" : "i-x", correct ? "good" : "bad") +
                '<span class="q-text"></span></div>' +
                '<div class="review-a"></div>';
            item.querySelector(".q-text").textContent = (i + 1) + ". " + q.question;

            const answer = item.querySelector(".review-a");
            if (!correct) {
                const yours = document.createElement("div");
                yours.className = "your-wrong";
                yours.textContent = "Your answer: " + LETTERS[picked] + ". " + q.options[picked];
                answer.appendChild(yours);
            }
            const right = document.createElement("div");
            right.className = "right";
            right.textContent = "Correct answer: " + LETTERS[q.answer_index] + ". " + q.options[q.answer_index];
            answer.appendChild(right);

            if (q.explanation) {
                const exp = document.createElement("div");
                exp.textContent = q.explanation;
                answer.appendChild(exp);
            }

            list.appendChild(item);
        });
    }

    $("retryBtn").addEventListener("click", startQuiz);

    $("newQuizBtn").addEventListener("click", function () {
        showScreen("setupScreen");
        $("topicInput").focus();
    });

    // ----- Start -----
    loadRecent();
    $("topicInput").focus();
});