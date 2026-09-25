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
        addHistoryItem('user', 'Why did this pipeline run fail?');
        triggerAnalysis('Why did this pipeline run fail?');
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
    addHistoryItem('user', escapeHtml(query));
    triggerAnalysis(query);
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

// Every question goes through the backend router, which decides which agent handles it.
// On a CI run page the content script also reads the failed steps from the CI system's API.
function triggerAnalysis(query) {
    showThinkingBubble('Reading the page...');

    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
        if (!tabs[0]) return;

        chrome.tabs.sendMessage(tabs[0].id, { action: "get_context" }, (context) => {
            if (chrome.runtime.lastError || !context) {
                removeThinkingBubble();
                addHistoryItem('bot', 'Error: Could not connect to page. Try reloading the tab.');
                return;
            }
            if (context.ci && context.ci.api_error) {
                addHistoryItem('bot', escapeHtml(`Couldn't read the run from the CI API (${context.ci.api_error}); using the text on the page.`));
            }
            chrome.runtime.sendMessage({
                action: "analyze",
                query,
                dom: context.dom,
                repo: context.repo,
                ci: context.ci
            }, (response) => {
                if (!response || response.status !== 'sent') {
                    removeThinkingBubble();
                    addHistoryItem('bot', `Error: ${escapeHtml(response?.message || 'backend not connected')}`);
                }
            });
            showThinkingBubble('Classifying the problem...');
        });
    });
}

function reroute(category, overridesRun) {
    chrome.runtime.sendMessage({ action: "reroute", category, overridesRun }, (response) => {
        if (!response || response.status !== 'sent') {
            addHistoryItem('bot', `Error: ${escapeHtml(response?.message || 'backend not connected')}`);
            return;
        }
        showThinkingBubble('Re-running with the agent you picked...');
    });
}

// Buttons for the categories other than `current`, most likely first.
function categoryButtons(route, onPick, current) {
    const row = document.createElement('div');
    row.className = 'route-choices';
    const ranked = Object.entries(route.probabilities).sort((a, b) => b[1] - a[1]);
    for (const [category, probability] of ranked) {
        if (category === current) continue;
        const button = document.createElement('button');
        button.className = 'feedback-btn';
        // Percentages only when they came from the classifier (not from the page type or a pick).
        const estimated = route.method === 'jev' || route.method === 'llm';
        button.textContent = estimated ? `${route.labels[category]} ${Math.round(probability * 100)}%` : route.labels[category];
        button.addEventListener('click', () => {
            row.querySelectorAll('button').forEach(b => { b.disabled = true; });
            onPick(category);
        });
        row.appendChild(button);
    }
    return row;
}

// Labels and probabilities of the last route, for the re-route buttons under the result.
let lastRoute = null;

function showRoute(route) {
    // A re-run the user picked keeps the classifier's original estimate for the buttons.
    lastRoute = route.method === 'user' && lastRoute ? { ...lastRoute, text: route.text } : route;
    const box = document.createElement('div');
    box.className = 'route';
    const text = document.createElement('div');
    text.className = 'route-text';
    text.textContent = route.text;
    box.appendChild(text);
    if (route.notes && route.notes.length) {
        const notes = document.createElement('div');
        notes.className = 'route-note';
        notes.textContent = route.notes.join(' ');
        box.appendChild(notes);
    }
    if (route.ask_user) {
        removeThinkingBubble();
        box.appendChild(categoryButtons(route, (category) => reroute(category, null), null));
    } else if (route.enough_evidence !== null && route.enough_evidence < 0.3) {
        const hint = document.createElement('div');
        hint.className = 'route-note';
        hint.textContent = 'There is little to go on - describing what you expected and what happened helps.';
        box.appendChild(hint);
    }
    const emptyState = chatHistory.querySelector('.empty-state');
    if (emptyState) emptyState.remove();
    chatHistory.appendChild(box);
    chatHistory.scrollTop = chatHistory.scrollHeight;
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
    // A wrong route is feedback too: re-running as another category records the override,
    // which is what the routing calibration report counts as a miss.
    if (lastRoute && runRef.category) {
        const label = document.createElement('span');
        label.className = 'route-note';
        label.textContent = 'Wrong area? Re-run as:';
        box.append(label, categoryButtons(lastRoute, (category) => reroute(category, runRef.run_id), runRef.category));
    }
    chatHistory.appendChild(box);
    chatHistory.scrollTop = chatHistory.scrollHeight;
}

// Listen for incoming messages from background
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    if (request.action === "analysis_result") {
        removeThinkingBubble();
        addHistoryItem('bot', "<b>[DONE] Analysis Complete!</b>");
        // The answer can echo text scraped from the analyzed page; never render it as HTML.
        addHistoryItem('bot', escapeHtml(request.text));
        if (request.runRef && request.runRef.run_id) {
            addFeedbackControls(request.runRef);
        }
    }
    if (request.action === "route") {
        showRoute(request.route);
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
