document.addEventListener("DOMContentLoaded", function () {
    const list = document.getElementById("shList");
    const count = document.getElementById("shCount");
    const overlay = document.getElementById("shOverlay");
    const summaryEl = document.getElementById("shSummary");
    const keyPointsEl = document.getElementById("shKeyPoints");
    const kpTitle = document.getElementById("shKpTitle");
    const dateEl = document.getElementById("shModalDate");
    const copyBtn = document.getElementById("shCopy");
    const deleteBtn = document.getElementById("shDelete");

    let openId = null;
    let openText = "";

    // ----- Helpers -----
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

    function formatDate(iso) {
        if (!iso) return "";
        return new Date(iso).toLocaleString("en-IN", {
            day: "numeric", month: "short", year: "numeric",
            hour: "numeric", minute: "2-digit"
        });
    }

    // ----- History list -----
    async function loadHistory() {
        try {
            const data = await api("/api/summaries");
            list.innerHTML = "";
            count.textContent = data.summaries.length ? data.summaries.length + " saved" : "";

            if (data.summaries.length === 0) {
                list.innerHTML = '<p class="sh-empty">No summaries yet. Your summaries will be saved here.</p>';
                return;
            }

            data.summaries.forEach(function (s) {
                const item = document.createElement("button");
                item.className = "sh-item" + (s.id === openId ? " active" : "");
                item.dataset.id = s.id;
                item.innerHTML =
                    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M8 13h8M8 17h5"/></svg>' +
                    '<span class="sh-item-text"><strong></strong><small></small></span>';
                item.querySelector("strong").textContent = s.title;
                item.querySelector("small").textContent = formatDate(s.created_at);
                item.addEventListener("click", function () { openSummary(s.id); });
                list.appendChild(item);
            });
        } catch (err) {
            list.innerHTML = '<p class="sh-empty">Could not load history.</p>';
        }
    }

    // Lets your partner's code refresh the list after a new summary
    window.refreshSummaryHistory = loadHistory;

    // ----- Viewer -----
    async function openSummary(id) {
        try {
            const s = await api("/api/summaries/" + id);
            openId = s.id;
            openText = s.summary;

            summaryEl.textContent = s.summary;
            dateEl.textContent = formatDate(s.created_at);

            keyPointsEl.innerHTML = "";
            s.key_points.forEach(function (point) {
                const li = document.createElement("li");
                li.textContent = point;
                keyPointsEl.appendChild(li);
            });
            kpTitle.style.display = s.key_points.length ? "" : "none";

            if (s.key_points.length) {
                openText += "\n\nKey points:\n- " + s.key_points.join("\n- ");
            }

            copyBtn.textContent = "Copy summary";
            overlay.classList.add("open");
            document.body.style.overflow = "hidden";
            highlight(id);

            // Put the id in the address bar so it can be shared/refreshed
            history.replaceState(null, "", "/summarizer?id=" + id);
        } catch (err) {
            alert(err.message);
        }
    }

    function closeViewer() {
        overlay.classList.remove("open");
        document.body.style.overflow = "";
        openId = null;
        highlight(null);
        history.replaceState(null, "", "/summarizer");
    }

    function highlight(id) {
        list.querySelectorAll(".sh-item").forEach(function (item) {
            item.classList.toggle("active", item.dataset.id === id);
        });
    }

    document.getElementById("shClose").addEventListener("click", closeViewer);
    overlay.addEventListener("click", function (e) {
        if (e.target === overlay) closeViewer(); // click outside the box
    });
    document.addEventListener("keydown", function (e) {
        if (e.key === "Escape" && overlay.classList.contains("open")) closeViewer();
    });

    copyBtn.addEventListener("click", async function () {
        try {
            await navigator.clipboard.writeText(openText);
            copyBtn.textContent = "Copied!";
        } catch (err) {
            copyBtn.textContent = "Copy failed";
        }
        setTimeout(function () { copyBtn.textContent = "Copy summary"; }, 1800);
    });

    deleteBtn.addEventListener("click", async function () {
        if (!openId || !confirm("Delete this summary? This can't be undone.")) return;
        try {
            await api("/api/summaries/" + openId, { method: "DELETE" });
            closeViewer();
            loadHistory();
        } catch (err) {
            alert(err.message);
        }
    });

    // ----- Start -----
    loadHistory().then(function () {
        // Opened from Recent activity? (/summarizer?id=...)
        const id = new URLSearchParams(window.location.search).get("id");
        if (id) openSummary(id);
    });
});

