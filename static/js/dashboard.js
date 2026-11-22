document.addEventListener("DOMContentLoaded", function () {

    // ----- User name (TEMPORARY: comes from the browser until Flask is ready) -----
    const fullName = document.body.dataset.user || "Student";

    const firstName = fullName.split(" ")[0];
    const initials = fullName
        .split(" ")
        .filter(Boolean)
        .slice(0, 2)
        .map(function (part) { return part[0].toUpperCase(); })
        .join("");

    document.getElementById("userName").textContent = firstName;
    document.getElementById("profileName").textContent = fullName;
    const avatarEl = document.getElementById("avatar");
    if (!avatarEl.querySelector("img")) {
        avatarEl.textContent = initials || "S"; // only show initials when there's no photo
    }

    // ----- Greeting based on time of day -----
    const hour = new Date().getHours();
    let greeting = "Good evening";
    if (hour < 12) greeting = "Good morning";
    else if (hour < 17) greeting = "Good afternoon";
    document.getElementById("greeting").textContent = greeting;

    // ----- Today's date -----
    document.getElementById("todayDate").textContent = new Date().toLocaleDateString("en-IN", {
        weekday: "long",
        day: "numeric",
        month: "long",
        year: "numeric"
    });

    // ----- Tip of the day -----
    const tips = [
        "Study in 25-minute blocks with 5-minute breaks (the Pomodoro technique).",
        "Explain a topic out loud as if teaching someone — it shows what you really understand.",
        "Test yourself with a quick quiz instead of just re-reading your notes.",
        "Review new material within 24 hours to remember it much longer.",
        "Put your phone in another room while studying to stay focused.",
        "Summarize each chapter in 3 sentences to lock in the key ideas."
    ];
    const dayIndex = new Date().getDate() % tips.length;
    document.getElementById("tipText").textContent = tips[dayIndex];

    // ----- Daily goal ring -----
    const minutesDone = 0;   // will come from the backend later
    const goalMinutes = 30;
    const percent = Math.min(100, Math.round((minutesDone / goalMinutes) * 100));
    const circumference = 326.7;

    document.getElementById("goalDone").textContent = minutesDone;
    document.getElementById("goalPercent").textContent = percent + "%";
    setTimeout(function () {
        document.getElementById("ringFg").style.strokeDashoffset =
            circumference - (percent / 100) * circumference;
    }, 300);

    // ----- Mobile sidebar -----
    const sidebar = document.getElementById("sidebar");
    const overlay = document.getElementById("sidebarOverlay");

    document.getElementById("menuBtn").addEventListener("click", function () {
        sidebar.classList.add("open");
        overlay.classList.add("show");
    });

    overlay.addEventListener("click", function () {
        sidebar.classList.remove("open");
        overlay.classList.remove("show");
    });

    // ----- Logout -----
        // ----- Logout -----
    document.getElementById("logoutBtn").addEventListener("click", function () {
        try {
            localStorage.removeItem("learnovaUser");
        } catch (e) {}
    });

        // ----- Notifications -----
    const bellBtn = document.getElementById("bellBtn");
    const notifPanel = document.getElementById("notifPanel");
    const bellDot = document.getElementById("bellDot");
    const notifCount = document.getElementById("notifCount");
    const items = document.querySelectorAll(".notif-item");
    const seenKey = "learnovaSeen-" + fullName;

    let seen = [];
    try {
        seen = JSON.parse(localStorage.getItem(seenKey)) || [];
    } catch (e) {}

    // Mark the ones not seen before as unread
    let unread = 0;
    items.forEach(function (item) {
        if (!seen.includes(item.dataset.id)) {
            item.classList.add("unread");
            unread++;
        }
    });

    if (unread > 0) {
        bellDot.classList.remove("hidden");
        notifCount.textContent = unread + " new";
    }

    function openPanel() {
        notifPanel.classList.add("open");
        bellBtn.setAttribute("aria-expanded", "true");

        // Mark everything as seen (the unread highlight stays until next visit)
        bellDot.classList.add("hidden");
        const ids = Array.from(items).map(function (item) { return item.dataset.id; });
        try {
            localStorage.setItem(seenKey, JSON.stringify(ids));
        } catch (e) {}
    }

    function closePanel() {
        notifPanel.classList.remove("open");
        bellBtn.setAttribute("aria-expanded", "false");
    }

    bellBtn.addEventListener("click", function (e) {
        e.stopPropagation();
        notifPanel.classList.contains("open") ? closePanel() : openPanel();
    });

    // Close when clicking outside or pressing Escape
    document.addEventListener("click", function (e) {
        if (!notifPanel.contains(e.target)) closePanel();
    });
    document.addEventListener("keydown", function (e) {
        if (e.key === "Escape") closePanel();
    });
});

