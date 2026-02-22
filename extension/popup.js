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
                    screenshot: response.screenshot,
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
                    screenshot: response.screenshot,
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

// Listen for incoming messages from background
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    if (request.action === "analysis_result") {
        removeThinkingBubble();
        addHistoryItem('bot', "<b>[DONE] Analysis Complete!</b><br>Check your IDE for the fix plan.");
        addHistoryItem('bot', request.text);
    }
    if (request.action === "status_update") {
        showThinkingBubble(request.text);
    }
});

// Start
init();
