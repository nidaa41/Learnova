document.addEventListener("DOMContentLoaded", function () {
    const chatList = document.getElementById("chatList");
    const chatWindow = document.getElementById("chatWindow");
    const chatEmpty = document.getElementById("chatEmpty");
    const chatMessages = document.getElementById("chatMessages");
    const form = document.getElementById("chatForm");
    const input = document.getElementById("chatInput");
    const sendBtn = document.getElementById("sendBtn");
    const sidebar = document.getElementById("chatSidebar");
    const overlay = document.getElementById("chatOverlay");

    let currentChatId = null;
    let sending = false;

    // ----- Helpers -----
    function icon(id) {
        return '<svg class="ic"><use href="#' + id + '"/></svg>';
    }

    function scrollToBottom() {
        chatWindow.scrollTop = chatWindow.scrollHeight;
    }

    function showEmpty(show) {
        chatEmpty.style.display = show ? "flex" : "none";
    }

    function addMessage(role, text) {
        const msg = document.createElement("div");
        msg.className = "msg " + role;

        if (role !== "user") {
            msg.innerHTML = '<div class="bot-avatar">' + icon("i-bot") + "</div>";
        }

        const bubble = document.createElement("div");
        bubble.className = "bubble";
        bubble.textContent = text; // safe: never inserts HTML
        msg.appendChild(bubble);

        chatMessages.appendChild(msg);
        scrollToBottom();
        return msg;
    }

    function addTyping() {
        const msg = document.createElement("div");
        msg.className = "msg bot";
        msg.innerHTML = '<div class="bot-avatar">' + icon("i-bot") +
            '</div><div class="bubble typing"><span></span><span></span><span></span></div>';
        chatMessages.appendChild(msg);
        scrollToBottom();
        return msg;
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

    // ----- Chat history list -----
    async function loadChats() {
        try {
            const data = await api("/api/chats");
            chatList.innerHTML = "";

            if (data.chats.length === 0) {
                chatList.innerHTML = '<p class="chat-list-empty">No chats yet</p>';
                return;
            }

            data.chats.forEach(function (chat) {
                const item = document.createElement("div");
                item.className = "chat-item" + (chat.id === currentChatId ? " active" : "");
                item.innerHTML = icon("i-chat") +
                    '<span class="chat-item-title"></span>' +
                    '<button class="chat-item-delete" aria-label="Delete chat">' + icon("i-trash") + "</button>";
                item.querySelector(".chat-item-title").textContent = chat.title;

                item.addEventListener("click", function () {
                    openChat(chat.id);
                });

                item.querySelector(".chat-item-delete").addEventListener("click", function (e) {
                    e.stopPropagation(); // don't open the chat when deleting
                    deleteChat(chat.id);
                });

                chatList.appendChild(item);
            });
        } catch (err) {
            chatList.innerHTML = '<p class="chat-list-empty">Could not load chats</p>';
        }
    }

    async function openChat(id) {
        try {
            const data = await api("/api/chats/" + id);
            currentChatId = data.id;
            chatMessages.innerHTML = "";
            showEmpty(false);
            data.messages.forEach(function (m) {
                addMessage(m.role, m.text);
            });
            highlightActive();
            closeSidebar();
        } catch (err) {
            alert(err.message);
        }
    }

    async function deleteChat(id) {
        if (!confirm("Delete this chat? This can't be undone.")) return;
        try {
            await api("/api/chats/" + id, { method: "DELETE" });
            if (id === currentChatId) newChat();
            loadChats();
        } catch (err) {
            alert(err.message);
        }
    }

    function newChat() {
        currentChatId = null;
        chatMessages.innerHTML = "";
        showEmpty(true);
        highlightActive();
        closeSidebar();
        input.focus();
    }

    function highlightActive() {
        loadChats();
    }

    // ----- Sending messages -----
    async function sendMessage(text) {
        if (sending || !text.trim()) return;
        sending = true;
        sendBtn.disabled = true;

        showEmpty(false);
        addMessage("user", text);
        input.value = "";
        autoResize();

        const typing = addTyping();

        try {
            const data = await api("/api/chat", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ chat_id: currentChatId, message: text })
            });

            typing.remove();
            addMessage("bot", data.reply);

            const isNew = currentChatId === null;
            currentChatId = data.chat_id;
            if (isNew) loadChats(); // show the new chat in the list
        } catch (err) {
            typing.remove();
            addMessage("error", err.message);
        } finally {
            sending = false;
            sendBtn.disabled = false;
            input.focus();
        }
    }

    form.addEventListener("submit", function (e) {
        e.preventDefault();
        sendMessage(input.value);
    });

    // Enter sends, Shift+Enter makes a new line
    input.addEventListener("keydown", function (e) {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            sendMessage(input.value);
        }
    });

    // Grow the text box as you type
    function autoResize() {
        input.style.height = "auto";
        input.style.height = input.scrollHeight + "px";
    }
    input.addEventListener("input", autoResize);

    // Starter questions
    document.querySelectorAll(".starter").forEach(function (btn) {
        btn.addEventListener("click", function () {
            sendMessage(btn.textContent);
        });
    });

    document.getElementById("newChatBtn").addEventListener("click", newChat);

    // ----- Mobile sidebar -----
    function closeSidebar() {
        sidebar.classList.remove("open");
        overlay.classList.remove("show");
    }

    document.getElementById("chatMenuBtn").addEventListener("click", function () {
        sidebar.classList.add("open");
        overlay.classList.add("show");
    });

    overlay.addEventListener("click", closeSidebar);

    // ----- Start -----
    loadChats();

    // Opened from Recent activity? (/chat?id=...) then open that chat
    const chatIdFromUrl = new URLSearchParams(window.location.search).get("id");
    if (chatIdFromUrl) {
        openChat(chatIdFromUrl);
    }

    input.focus();
});