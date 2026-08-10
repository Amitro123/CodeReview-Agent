let lastHoveredElement = null;

document.addEventListener('mouseover', (e) => {
    lastHoveredElement = e.target;
}, true);

function getSelectedElementInfo() {
    if (!lastHoveredElement) return null;
    return {
        tagName: lastHoveredElement.tagName,
        className: lastHoveredElement.className,
        id: lastHoveredElement.id,
        innerText: lastHoveredElement.innerText.substring(0, 100),
        selector: getSimplifiedSelector(lastHoveredElement)
    };
}

function getSimplifiedSelector(el) {
    if (el.id) return `#${el.id}`;
    if (el.className) return `.${el.className.split(' ').join('.')}`;
    return el.tagName.toLowerCase();
}

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    if (request.action === "get_ci_data" || request.action === "get_universal_context") {
        // Scrape logs/text
        const ci_logs = document.querySelector('.log-viewer-container')?.innerText ||
            document.querySelector('.highlight.actions-log')?.innerText ||
            document.body.innerText.substring(0, 10000);

        const dom_context = {
            selectedElement: getSelectedElementInfo(),
            pageTitle: document.title,
            url: window.location.href
        };

        // Screenshots are captured natively by the background service worker
        // (chrome.tabs.captureVisibleTab), not here - see background.js.
        sendResponse({
            ci_logs: ci_logs,
            dom: dom_context,
            repo: window.location.pathname.split('/').slice(1, 3).join('/')
        });
    }
});
