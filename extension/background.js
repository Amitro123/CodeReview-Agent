// Background Service Worker
let socket = null;
let connectingPromise = null;
let tabErrors = {}; // { tabId: { network: [], console: [] } }
let currentStatus = null; // the running analysis' latest status, for a panel opened mid-run

// Open side panel on action click
chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch((error) => console.error(error));

chrome.debugger.onEvent.addListener((source, method, params) => {
    const tabId = source.tabId;
    if (!tabErrors[tabId]) tabErrors[tabId] = { network: [], console: [] };

    if (method === "Log.entryAdded") {
        if (params.entry.level === "error" || params.entry.level === "warning") {
            tabErrors[tabId].console.push({
                source: params.entry.source,
                level: params.entry.level,
                text: params.entry.text,
                timestamp: params.entry.timestamp,
                url: params.entry.url
            });
        }
    } else if (method === "Network.responseReceived") {
        if (params.response.status >= 400) {
            tabErrors[tabId].network.push({
                url: params.response.url,
                status: params.response.status,
                statusText: params.response.statusText,
                type: params.response.type
            });
        }
    }
});

// Captures exactly what the user sees in their logged-in tab (native Chrome
// API) - unlike html2canvas (fails on iframes/CORS) or a headless browser
// hitting the URL fresh (which would just see a login wall).
function captureScreenshot(windowId) {
    return new Promise((resolve) => {
        chrome.tabs.captureVisibleTab(windowId, { format: "png" }, (dataUrl) => {
            if (chrome.runtime.lastError) {
                console.warn("captureVisibleTab failed:", chrome.runtime.lastError.message);
                resolve(null);
                return;
            }
            resolve(dataUrl || null);
        });
    });
}

// Tab the current universal analysis is about; the backend's visual agent can inspect it.
let analysisTabId = null;

// Runs a backend tool_request (e.g. inspect_element) in the analyzed tab's content
// script and sends the answer back as a tool_result with the same id.
async function handleToolRequest(request) {
    let result;
    try {
        if (analysisTabId === null) throw new Error("no page is being analyzed");
        result = await chrome.tabs.sendMessage(analysisTabId, {
            action: "run_tool",
            tool: request.tool,
            args: request.args || {}
        });
    } catch (e) {
        result = `Error: ${e.message}`;
    }
    if (socket && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: "tool_result", id: request.id, result: result ?? "" }));
    }
}

function attachDebugger(tabId) {
    return new Promise((resolve, reject) => {
        chrome.debugger.attach({ tabId: tabId }, "1.3", () => {
            if (chrome.runtime.lastError) {
                console.warn("Debugger attach failed (maybe already attached):", chrome.runtime.lastError.message);
                resolve(); // resolve anyway to try enabling
                return;
            }
            chrome.debugger.sendCommand({ tabId: tabId }, "Network.enable");
            chrome.debugger.sendCommand({ tabId: tabId }, "Log.enable");
            resolve();
        });
    });
}

function connect(forcedUrl = null) {
    if (connectingPromise) return connectingPromise;

    connectingPromise = new Promise((resolve, reject) => {
        if (!forcedUrl) {
            chrome.storage.sync.get(['backend_url'], (result) => {
                const url = result.backend_url || 'ws://localhost:8000';
                initSocket(url + '/ws/chat').then(resolve).catch(reject);
            });
        } else {
            initSocket(forcedUrl + '/ws/chat').then(resolve).catch(reject);
        }
    });

    return connectingPromise;
}

function initSocket(url) {
    if (socket) {
        socket.close();
    }

    connectingPromise = new Promise((resolve, reject) => {
        console.log('Connecting to backend: ' + url);
        socket = new WebSocket(url);

        socket.onopen = () => {
            console.log('Connected to CodeReview Agent Backend');
            resolve(socket);
        };

        socket.onmessage = (event) => {
            const data = JSON.parse(event.data);
            if (data.type === 'analysis_result') {
                currentStatus = null; // Clear status on completion
                chrome.runtime.sendMessage({ action: "analysis_result", text: data.answer, runRef: data.run_ref || null });
            }
            if (data.type === 'route') {
                chrome.runtime.sendMessage({ action: "route", route: data });
            }
            if (data.type === 'verification_result') {
                chrome.runtime.sendMessage({ action: "verification_result", result: data });
            }
            if (data.type === 'feedback_saved') {
                chrome.runtime.sendMessage({ action: "feedback_saved", runId: data.run_id, duplicate: !!data.duplicate });
            }
            if (data.type === 'error') {
                currentStatus = null;
                chrome.runtime.sendMessage({ action: "backend_error", text: data.message });
            }
            if (data.type === 'status') {
                currentStatus = data.message; // Update current status
                chrome.runtime.sendMessage({ action: "status_update", text: data.message });
            }
            if (data.type === 'tool_request') {
                handleToolRequest(data);
            }
        };

        socket.onclose = () => {
            console.log('Disconnected from backend. Socket closed.');
            socket = null;
            connectingPromise = null;
        };

        socket.onerror = (error) => {
            console.error('WebSocket Error:', error);
            socket = null;
            connectingPromise = null;
            reject(error);
        };
    });

    return connectingPromise;
}

async function getSocket() {
    if (socket && socket.readyState === WebSocket.OPEN) {
        return socket;
    }
    try {
        return await connect();
    } catch (e) {
        return null;
    }
}

// Keep-alive heartbeat
setInterval(async () => {
    const s = await getSocket();
    if (s && s.readyState === WebSocket.OPEN) {
        s.send(JSON.stringify({ type: "ping" }));
    }
}, 20000);

connect(); // Initial connection

// Reloads a tab and resolves once it has finished loading, plus a short settle time for
// late scripts and requests (or after 20s if it never completes).
function reloadAndWait(tabId) {
    return new Promise((resolve) => {
        const onUpdated = (id, info) => {
            if (id === tabId && info.status === 'complete') {
                chrome.tabs.onUpdated.removeListener(onUpdated);
                clearTimeout(timer);
                setTimeout(resolve, 1500);
            }
        };
        const timer = setTimeout(() => {
            chrome.tabs.onUpdated.removeListener(onUpdated);
            resolve();
        }, 20000);
        chrome.tabs.onUpdated.addListener(onUpdated);
        chrome.tabs.reload(tabId);
    });
}

// "Verify fix": reload the page with fresh error capture, then let the backend re-run the
// fix plan's browser checks (it inspects elements through tool_requests to this tab).
async function verifyFix(runRef) {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab) throw new Error("no active tab to verify");
    const s = await getSocket();
    if (!s || s.readyState !== WebSocket.OPEN) throw new Error("backend not connected");
    analysisTabId = tab.id;
    await attachDebugger(tab.id);
    tabErrors[tab.id] = { network: [], console: [] };
    await reloadAndWait(tab.id);
    s.send(JSON.stringify({
        type: "verify",
        run_ref: runRef,
        console_errors: tabErrors[tab.id].console,
        network_errors: tabErrors[tab.id].network
    }));
}

// The last analysis sent, so the panel can re-run it with another agent (the user's pick
// when the router isn't sure, or a correction when it picked the wrong area).
let lastAnalysis = null;

// Sends one reported problem to the backend router. On a regular page it also captures the
// screenshot and the DevTools errors; on a CI run page the failure itself is the evidence.
async function startAnalysis(request) {
    const s = await getSocket();
    if (!s || s.readyState !== WebSocket.OPEN) throw new Error("Backend not connected");
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    analysisTabId = tab ? tab.id : null;
    const isCiRun = !!request.ci;
    let screenshot = null;
    let errors = { network: [], console: [] };
    if (tab && !isCiRun) {
        screenshot = await captureScreenshot(tab.windowId);
        if (!tabErrors[tab.id]) tabErrors[tab.id] = { network: [], console: [] };
        await attachDebugger(tab.id);
        // Brief wait for Log history to flush if just attached
        await new Promise(r => setTimeout(r, 500));
        errors = tabErrors[tab.id];
    }
    lastAnalysis = {
        type: "analyze",
        query: request.query,
        page_url: request.dom ? request.dom.url : null,
        dom: request.dom,
        repo: request.repo,
        ci: request.ci,
        screenshot,
        network_errors: errors.network,
        console_errors: errors.console
    };
    s.send(JSON.stringify(lastAnalysis));
}

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    if (request.action === "verify_fix") {
        verifyFix(request.runRef)
            .then(() => sendResponse({ status: "sent" }))
            .catch((e) => sendResponse({ status: "error", message: e.message }));
        return true;
    }
    if (request.action === "send_feedback") {
        getSocket().then(s => {
            if (s && s.readyState === WebSocket.OPEN) {
                s.send(JSON.stringify({
                    type: "feedback",
                    run_ref: request.runRef,
                    worked: request.worked,
                    note: request.note || ""
                }));
                sendResponse({ status: "sent" });
            } else {
                sendResponse({ status: "error", message: "Backend not connected" });
            }
        });
        return true;
    }
    if (request.action === "reconnect") {
        console.log("Reconnecting to: " + request.backend_url);
        connect(request.backend_url);
        return false;
    }
    if (request.action === "analyze") {
        startAnalysis(request)
            .then(() => sendResponse({ status: "sent" }))
            .catch((e) => sendResponse({ status: "error", message: e.message }));
        return true;
    }
    if (request.action === "reroute") {
        if (!lastAnalysis) {
            sendResponse({ status: "error", message: "nothing to re-run; ask again" });
            return false;
        }
        getSocket().then(s => {
            if (!s || s.readyState !== WebSocket.OPEN) {
                sendResponse({ status: "error", message: "Backend not connected" });
                return;
            }
            s.send(JSON.stringify({
                ...lastAnalysis,
                force_category: request.category,
                overrides_run: request.overridesRun || null
            }));
            sendResponse({ status: "sent" });
        });
        return true;
    }

    if (request.action === "analyze_url") {
        getSocket().then(s => {
            if (s && s.readyState === WebSocket.OPEN) {
                chrome.storage.sync.get(['perplexityApiKey'], (result) => {
                    s.send(JSON.stringify({
                        type: "analyze_url",
                        url: request.url,
                        api_key: result.perplexityApiKey
                    }));
                    sendResponse({ status: "sent" });
                });
            } else {
                sendResponse({ status: "error", message: "Backend not connected (Socket offline)" });
            }
        });
        return true;
    } else if (request.action === "get_status") {
        sendResponse({ status: currentStatus });
        return false;
    } else if (request.action === "scrape_github_actions") {
        chrome.scripting.executeScript({
            target: { tabId: sender.tab.id },
            func: () => {
                const logs = document.querySelector('.log-viewer-container')?.innerText ||
                    document.querySelector('.highlight.actions-log')?.innerText ||
                    "No logs found in typical GitHub Actions containers.";
                return logs;
            }
        }).then((results) => {
            const logs = results[0].result;
            getSocket().then(s => {
                if (s && s.readyState === WebSocket.OPEN) {
                    chrome.storage.sync.get(['perplexityApiKey'], (result) => {
                        s.send(JSON.stringify({
                            type: "analyze_logs",
                            logs: logs,
                            api_key: result.perplexityApiKey
                        }));
                    });
                }
            });
        });
        return true;
    }
});
