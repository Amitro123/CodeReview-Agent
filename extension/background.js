// Background Service Worker
let socket = null;
let connectingPromise = null;
let tabErrors = {}; // { tabId: { network: [], console: [] } }

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

        let currentStatus = null; // Store current analysis status

        socket.onmessage = (event) => {
            const data = JSON.parse(event.data);
            if (data.type === 'analysis_result') {
                currentStatus = null; // Clear status on completion
                chrome.runtime.sendMessage({ action: "analysis_result", text: data.answer });
            }
            if (data.type === 'status') {
                currentStatus = data.message; // Update current status
                chrome.runtime.sendMessage({ action: "status_update", text: data.message });
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

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    if (request.action === "reconnect") {
        console.log("Reconnecting to: " + request.backend_url);
        connect(request.backend_url);
        return false;
    }
    if (request.action === "universal-analyze") {
        getSocket().then(s => {
            if (s && s.readyState === WebSocket.OPEN) {
                chrome.tabs.query({ active: true, currentWindow: true }, async (tabs) => {
                    const tab = tabs[0];
                    const tabId = tab?.id;
                    const screenshot = tab ? await captureScreenshot(tab.windowId) : null;

                    if (tabId) {
                        if (!tabErrors[tabId]) tabErrors[tabId] = { network: [], console: [] };
                        // Ensure debugger is attached and logs are flushed
                        await attachDebugger(tabId);
                        // Brief wait for Log history to flush if just attached
                        await new Promise(r => setTimeout(r, 500));

                        currentStatus = "Initializing Agents..."; // Set initial status

                        chrome.storage.sync.get(['perplexityApiKey'], (result) => {
                            s.send(JSON.stringify({
                                type: "universal_analyze",
                                query: request.query,
                                screenshot: screenshot,
                                dom: request.dom,
                                repo: request.repo,
                                api_key: result.perplexityApiKey,
                                network_errors: tabErrors[tabId].network,
                                console_errors: tabErrors[tabId].console
                            }));
                            sendResponse({ status: "sent" });
                        });
                    } else {
                        // Fallback if no tab found (unlikely)
                        chrome.storage.sync.get(['perplexityApiKey'], (result) => {
                            s.send(JSON.stringify({
                                type: "universal_analyze",
                                query: request.query,
                                screenshot: screenshot,
                                dom: request.dom,
                                repo: request.repo,
                                api_key: result.perplexityApiKey,
                                network_errors: [],
                                console_errors: []
                            }));
                            sendResponse({ status: "sent" });
                        });
                    }
                });
            } else {
                console.error("universal-analyze failed: Backend not connected");
                sendResponse({ status: "error", message: "Backend not connected" });
            }
        });
        return true;
    }
    if (request.action === "ci-analyze") {
        getSocket().then(s => {
            if (s && s.readyState === WebSocket.OPEN) {
                console.log("Sending ci_analyze to backend...");
                // No screenshot here: the backend's ci_analyze handler only
                // reads ci_log/repo (text-only CI log analysis), so capturing
                // one would just ship sensitive tab content for nothing.
                chrome.storage.sync.get(['perplexityApiKey'], (result) => {
                    s.send(JSON.stringify({
                        type: "ci_analyze",
                        ci_log: request.ci_log,
                        repo: request.repo,
                        api_key: result.perplexityApiKey
                    }));
                    sendResponse({ status: "sent" });
                });
            } else {
                console.error("ci-analyze failed: Backend not connected");
                sendResponse({ status: "error", message: "Backend not connected" });
            }
        });
        return true;
    }

    if (request.action === "analyze") {
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
