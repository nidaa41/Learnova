// Counts study time: pings the server once a minute while the user is active
(function () {
    const IDLE_LIMIT = 3 * 60 * 1000; // 3 minutes without activity = not studying
    let lastActivity = Date.now();

    ["mousemove", "keydown", "scroll", "click", "touchstart"].forEach(function (event) {
        document.addEventListener(event, function () {
            lastActivity = Date.now();
        }, { passive: true });
    });

    setInterval(function () {
        if (document.visibilityState !== "visible") return;     // tab in background
        if (Date.now() - lastActivity > IDLE_LIMIT) return;     // user walked away
        fetch("/api/study/ping", { method: "POST" }).catch(function () {});
    }, 60 * 1000);
})();