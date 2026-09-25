const statusDiv = document.getElementById('status');
const chatHistory = document.getElementById('chatHistory');
const settingsPanel = document.getElementById('settingsPanel');

// Inputs
const universalChatInput = document.getElementById('universalChatInput');
const sendBtn = document.getElementById('sendBtn');

// Settings Inputs
const apiKeyInput = document.getElementById('apiKey');
const backendUrlInput = document.getElementById('backendUrlInput');
const watchedReposInput = document.getElementById('watchedReposInput');
const voiceModeCheckbox = document.getElementById('voiceMode');

// Toggles/Pills
const settingsToggleBtn = document.getElementById('settingsToggleBtn');
const closeSettingsBtn = document.getElementById('closeSettingsBtn');
const contextPill = document.getElementById('contextPill');
const ciActionPill = document.getElementById('ciActionPill');

// State
let isSettingsOpen = false;

// --- Initialization ---

function init() {
    loadSettings();
    loadHistory();
    setupEventListeners();

    // Sync status on load (polling/pull)
    chrome.runtime.sendMessage({ action: "get_status" }, (response) => {
        if (response && response.status) {
            showThinkingBubble(response.status);
        }
    });
}

function setupEventListeners() {
    // Settings Slide
    settingsToggleBtn.addEventListener('click', toggleSettings);
    closeSettingsBtn.addEventListener('click', toggleSettings);

    // Save Settings
    document.getElementById('saveBtn').addEventListener('click', saveSettings);

    // Chat
    sendBtn.addEventListener('click', handleSendMessage);
    universalChatInput.addEventListener('keyup', (e) => {
        if (e.key === 'Enter') handleSendMessage();
    });

    // Pills
    contextPill.addEventListener('click', () => setActivePill('context'));
    ciActionPill.addEventListener('click', () => {
        setActivePill('ci');
        triggerCIAnalysis();
    });
}

// --- Logic ---

function toggleSettings() {
    isSettingsOpen = !isSettingsOpen;
    if (isSettingsOpen) {
        settingsPanel.classList.add('open');
    } else {
        settingsPanel.classList.remove('open');
    }
}

function setActivePill(type) {
    if (type === 'context') {
        contextPill.classList.add('active');
        ciActionPill.classList.remove('active');
    } else {
        ciActionPill.classList.add('active');
        contextPill.classList.remove('active');
    }
}

function loadSettings() {
    chrome.storage.sync.get(['perplexityApiKey', 'watchedRepos', 'voiceMode', 'backend_url'], (result) => {
        apiKeyInput.value = result.perplexityApiKey || '';
        backendUrlInput.value = result.backend_url || 'ws://localhost:8000';
        voiceModeCheckbox.checked = result.voiceMode || false;
        watchedReposInput.value = result.watchedRepos || '';
    });
}

function saveSettings() {
    const config = {
        perplexityApiKey: apiKeyInput.value.trim(),
        watchedRepos: watchedReposInput.value.trim(),
        voiceMode: voiceModeCheckbox.checked,
        backend_url: backendUrlInput.value.trim()
    };

    chrome.storage.sync.set(config, () => {
        chrome.runtime.sendMessage({ action: "reconnect", backend_url: config.backend_url });
        statusDiv.textContent = 'Settings Saved';
        setTimeout(() => statusDiv.textContent = '', 2000);
        toggleSettings(); // Close panel on save
    });
}

// --- Chat & Analysis ---

function handleSendMessage() {
    const query = universalChatInput.value.trim();
    if (!query) return;

    universalChatInput.value = '';
    addHistoryItem('user', query);
    triggerUniversalAnalysis(query);
}

function showThinkingBubble(message) {
    const existingBubble = document.getElementById('thinking-bubble');
    if (existingBubble) existingBubble.remove();

    const item = document.createElement('div');
    item.id = 'thinking-bubble';
    item.className = 'chat-message bot thinking';
    item.innerHTML = `<div class="loading-spinner"></div><span>${message}</span>`;

    chatHistory.appendChild(item);
    chatHistory.scrollTop = chatHistory.scrollHeight;
}

function removeThinkingBubble() {
    const existingBubble = document.getElementById('thinking-bubble');
    if (existingBubble) existingBubble.remove();
}

function triggerUniversalAnalysis(query) {
    showThinkingBubble('Analyzing page context...');

    // Get active tab context
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
        if (!tabs[0]) return;

        chrome.tabs.sendMessage(tabs[0].id, { action: "get_universal_context" }, (response) => {
            if (chrome.runtime.lastError) {
                removeThinkingBubble();
                addHistoryItem('bot', 'Error: Could not connect to page. Try reloading the tab.');
                return;
            }

            if (response) {
                chrome.runtime.sendMessage({
                    action: "universal-analyze",
                    query: query,
                    dom: response.dom,
                    repo: response.repo
                });
                showThinkingBubble('Agents thinking...');
            }
        });
    });
}

function triggerCIAnalysis() {
    showThinkingBubble('Accessing CI Logs...');

    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
        if (!tabs[0]) return;

        chrome.tabs.sendMessage(tabs[0].id, { action: "get_ci_data" }, (response) => {
            if (chrome.runtime.lastError) {
                removeThinkingBubble();
                addHistoryItem('bot', 'Error: Not a GitHub Actions page? Reload to try again.');
                return;
            }

            if (response) {
                addHistoryItem('user', `Analyzing CI Failure: ${response.repo}`);
                chrome.runtime.sendMessage({
                    action: "ci-analyze",
                    ci_log: response.ci_logs,
                    repo: response.repo
                });
                showThinkingBubble('Multi-Agent Analysis started...');
            } else {
                removeThinkingBubble();
                addHistoryItem('bot', 'Error: No data received from page.');
            }
        });
    });
}

// --- History Management ---

function addHistoryItem(role, text) {
    const item = document.createElement('div');
    item.className = `chat-message ${role}`;
    item.innerHTML = text.replace(/\n/g, '<br>'); // Simple formatting

    const emptyState = chatHistory.querySelector('.empty-state');
    if (emptyState) emptyState.remove();

    chatHistory.appendChild(item);
    chatHistory.scrollTop = chatHistory.scrollHeight;

    // Save to local storage
    const history = JSON.parse(localStorage.getItem('chatHistory') || '[]');
    history.push({ role, text: item.innerHTML });
    localStorage.setItem('chatHistory', JSON.stringify(history.slice(-50))); // Keep last 50
}

function loadHistory() {
    const history = JSON.parse(localStorage.getItem('chatHistory') || '[]');
    if (history.length > 0) {
        const emptyState = chatHistory.querySelector('.empty-state');
        if (emptyState) emptyState.remove();

        history.forEach(item => {
            const div = document.createElement('div');
            div.className = `chat-message ${item.role}`;
            div.innerHTML = item.text;
            chatHistory.appendChild(div);
        });
        chatHistory.scrollTop = chatHistory.scrollHeight;
    }
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Status element per run id, updated when the backend confirms the feedback was saved.
const feedbackStatus = {};
// Result list per run id, filled when a "Verify fix" re-check comes back.
const verifyResults = {};

// Re-runs the plan's browser checks after the user applied the fix: the tab is reloaded
// and each check passes or fails with no LLM call. Evidence only - the verdict stays 👍/👎.
function addVerifyControls(box, runRef) {
    const button = document.createElement('button');
    button.className = 'feedback-btn verify-btn';
    button.textContent = '🔍 Verify fix';

    const results = document.createElement('ul');
    results.className = 'verify-results';
    verifyResults[runRef.run_id] = { list: results, button };

    button.addEventListener('click', () => {
        button.disabled = true;
        results.replaceChildren();
        const pending = document.createElement('li');
        pending.textContent = 'Reloading the page and re-checking…';
        results.appendChild(pending);
        chrome.runtime.sendMessage({ action: "verify_fix", runRef }, (response) => {
            if (!response || response.status !== 'sent') {
                pending.textContent = `Could not verify: ${response?.message || 'backend not connected'}`;
                button.disabled = false;
            }
        });
    });
    box.append(button, results);
}

function showVerification(result) {
    const target = verifyResults[result.run_id];
    if (!target) return;
    target.button.disabled = false;
    const items = [];
    if (result.message) {
        const li = document.createElement('li');
        li.textContent = result.message;
        items.push(li);
    }
    for (const r of result.results || []) {
        const li = document.createElement('li');
        li.className = r.ok ? 'check-ok' : 'check-fail';
        const actual = typeof r.actual === 'string' ? r.actual : JSON.stringify(r.actual);
        li.textContent = `${r.ok ? '✓' : '✗'} ${r.label} (now: ${actual})`;
        items.push(li);
    }
    if ((result.results || []).length) {
        const summary = document.createElement('li');
        summary.className = 'verify-summary';
        summary.textContent = result.passed
            ? 'All checks passed. If the bug is gone, confirm with 👍.'
            : 'Some checks still fail.';
        items.push(summary);
    }
    target.list.replaceChildren(...items);
}

// 👍/👎 under a result. The verdict is what turns a run into knowledge: it's logged to
// MISTAKES.md and the run is ingested into the project wiki. Not saved in chat history,
// so a reopened panel can't send a second verdict for an old run.
function addFeedbackControls(runRef) {
    const box = document.createElement('div');
    box.className = 'feedback';

    const question = document.createElement('span');
    question.textContent = 'Did this fix work?';

    const note = document.createElement('input');
    note.type = 'text';
    note.className = 'feedback-note';
    note.placeholder = "Optional: what was the actual cause?";

    const status = document.createElement('span');
    status.className = 'feedback-status';
    feedbackStatus[runRef.run_id] = status;

    const buttons = [];
    const send = (worked) => {
        buttons.forEach(b => { b.disabled = true; });
        note.disabled = true;
        status.textContent = 'Saving…';
        chrome.runtime.sendMessage(
            { action: "send_feedback", runRef, worked, note: note.value.trim() },
            (response) => {
                if (!response || response.status !== 'sent') {
                    status.textContent = `Not saved: ${response?.message || 'backend not connected'}`;
                    buttons.forEach(b => { b.disabled = false; });
                    note.disabled = false;
                }
            }
        );
    };
    for (const [label, worked] of [['👍 Worked', true], ["👎 Didn't work", false]]) {
        const button = document.createElement('button');
        button.className = 'feedback-btn';
        button.textContent = label;
        button.addEventListener('click', () => send(worked));
        buttons.push(button);
    }

    box.append(question, ...buttons, note, status);
    addVerifyControls(box, runRef);
    chatHistory.appendChild(box);
    chatHistory.scrollTop = chatHistory.scrollHeight;
}

// Listen for incoming messages from background
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    if (request.action === "analysis_result") {
        removeThinkingBubble();
        addHistoryItem('bot', "<b>[DONE] Analysis Complete!</b><br>Check your IDE for the fix plan.");
        // The answer can echo text scraped from the analyzed page; never render it as HTML.
        addHistoryItem('bot', escapeHtml(request.text));
        if (request.runRef && request.runRef.run_id) {
            addFeedbackControls(request.runRef);
        }
    }
    if (request.action === "status_update") {
        showThinkingBubble(request.text);
    }
    if (request.action === "verification_result") {
        showVerification(request.result);
    }
    if (request.action === "feedback_saved") {
        const status = feedbackStatus[request.runId];
        if (status) {
            status.textContent = request.duplicate
                ? 'Already recorded for this run.'
                : 'Saved. The agent will learn from this.';
        }
    }
    if (request.action === "backend_error") {
        removeThinkingBubble();
        addHistoryItem('bot', `Error: ${escapeHtml(request.text)}`);
    }
});

// Start
init();
