document.addEventListener("DOMContentLoaded", function () {
    const $ = function (id) { return document.getElementById(id); };
    const MAX_BYTES = 10 * 1024 * 1024; // 10 MB (same limit as the server)

    // ----- Profile (same idea as the dashboard) -----
    const fullName = document.body.dataset.user || "Student";
    const initials = fullName
        .split(" ")
        .filter(Boolean)
        .slice(0, 2)
        .map(function (part) { return part[0].toUpperCase(); })
        .join("");
    $("profileName").textContent = fullName;
    $("avatar").textContent = initials || "S";

    // ----- Mobile sidebar -----
    const sidebar = $("sidebar");
    const overlay = $("sidebarOverlay");

    $("menuBtn").addEventListener("click", function () {
        sidebar.classList.add("open");
        overlay.classList.add("show");
    });
    overlay.addEventListener("click", function () {
        sidebar.classList.remove("open");
        overlay.classList.remove("show");
    });

    // ----- Error helpers -----
    const errorBox = $("sumError");
    function showError(msg) {
        errorBox.textContent = msg;
        errorBox.classList.remove("show");
        void errorBox.offsetWidth; // restart the shake animation
        errorBox.classList.add("show");
    }
    function hideError() {
        errorBox.classList.remove("show");
    }

    // ----- Tabs: paste text / upload PDF -----
    let mode = "text";
    const tabs = document.querySelectorAll(".sum-tab");
    tabs.forEach(function (tab) {
        tab.addEventListener("click", function () {
            mode = tab.dataset.mode;
            tabs.forEach(function (t) { t.classList.toggle("active", t === tab); });
            $("paneText").classList.toggle("show", mode === "text");
            $("panePdf").classList.toggle("show", mode === "pdf");
            hideError();
        });
    });

    // ----- Character counter -----
    const notes = $("notes");
    const count = $("sumCount");
    function updateCount() {
        count.textContent = notes.value.length.toLocaleString() + " / 30,000 characters";
    }
    notes.addEventListener("input", updateCount);
    updateCount();

    // ----- PDF picker + drag and drop -----
    const drop = $("sumDrop");
    const pdfInput = $("pdf");
    const fileBox = $("sumFile");

    function clearFile() {
        pdfInput.value = "";
        fileBox.classList.remove("show");
    }

    function setFile(file) {
        if (!file) return;
        if (!file.name.toLowerCase().endsWith(".pdf")) {
            clearFile();
            showError("Please choose a PDF file.");
            return;
        }
        if (file.size > MAX_BYTES) {
            clearFile();
            showError("That file is too big. The limit is 10 MB.");
            return;
        }
        hideError();
        $("sumFileName").textContent = file.name;
        $("sumFileSize").textContent = (file.size / (1024 * 1024)).toFixed(2) + " MB";
        fileBox.classList.add("show");
    }

    drop.addEventListener("click", function () { pdfInput.click(); });
    pdfInput.addEventListener("change", function () { setFile(pdfInput.files[0]); });
    $("sumFileRemove").addEventListener("click", clearFile);

    ["dragenter", "dragover"].forEach(function (evt) {
        drop.addEventListener(evt, function (e) {
            e.preventDefault();
            drop.classList.add("dragover");
        });
    });
    ["dragleave", "drop"].forEach(function (evt) {
        drop.addEventListener(evt, function (e) {
            e.preventDefault();
            drop.classList.remove("dragover");
        });
    });
    drop.addEventListener("drop", function (e) {
        if (e.dataTransfer.files.length) {
            pdfInput.files = e.dataTransfer.files;
            setFile(pdfInput.files[0]);
        }
    });

    // ----- Result states -----
    function showState(name) {
        ["stateEmpty", "stateLoading", "stateResult"].forEach(function (id) {
            $(id).classList.toggle("show", id === name);
        });
        $("copyBtn").style.display = name === "stateResult" ? "inline-block" : "none";
    }

    let lastResult = null;

    function renderResult(data) {
        const points = Array.isArray(data.key_points) ? data.key_points : [];
        lastResult = { summary: data.summary || "", points: points };

        $("sumSummary").textContent = lastResult.summary;

        const list = $("sumPoints");
        list.innerHTML = "";
        points.forEach(function (p, i) {
            const li = document.createElement("li");
            li.textContent = p;
            li.style.setProperty("--d", (i * 0.06) + "s");
            list.appendChild(li);
        });

        showState("stateResult");
        if (window.innerWidth <= 950) {
            $("resultPanel").scrollIntoView({ behavior: "smooth", block: "start" });
        }
    }

    // ----- Summarize -----
    const btn = $("go");
    const btnLabel = $("goLabel");

    btn.addEventListener("click", async function () {
        hideError();

        const formData = new FormData();
        if (mode === "text") {
            const text = notes.value.trim();
            if (text.length < 50) {
                showError("Please paste at least a few sentences of notes.");
                return;
            }
            formData.append("text", text);
        } else {
            const file = pdfInput.files[0];
            if (!file) {
                showError("Please choose a PDF first.");
                return;
            }
            formData.append("pdf", file);
        }

        btn.disabled = true;
        btn.classList.add("loading");
        btnLabel.textContent = "Summarizing...";
        showState("stateLoading");

        try {
            const res = await fetch("/api/summarize", { method: "POST", body: formData });
            let data = {};
            try { data = await res.json(); } catch (e) {}

            if (!res.ok) {
                showState(lastResult ? "stateResult" : "stateEmpty");
                showError(
                    data.error ||
                    (res.status === 413
                        ? "That file is too big. The limit is 10 MB."
                        : "Something went wrong. Please try again.")
                );
            } else {
                renderResult(data);
            }
        } catch (e) {
            showState(lastResult ? "stateResult" : "stateEmpty");
            showError("Could not reach the server. Is it running?");
        }

        btn.disabled = false;
        btn.classList.remove("loading");
        btnLabel.textContent = "Summarize";
    });

    // ----- Copy summary -----
    $("copyBtn").addEventListener("click", async function () {
        if (!lastResult) return;
        const text =
            "Summary\n" + lastResult.summary + "\n\nKey points\n" +
            lastResult.points.map(function (p, i) { return (i + 1) + ". " + p; }).join("\n");

        const copyBtn = $("copyBtn");
        try {
            await navigator.clipboard.writeText(text);
            copyBtn.textContent = "Copied!";
        } catch (e) {
            copyBtn.textContent = "Copy failed";
        }
        setTimeout(function () { copyBtn.textContent = "Copy"; }, 1500);
    });

    showState("stateEmpty");
});