let lastHoveredElement = null;

document.addEventListener('mouseover', (e) => {
    lastHoveredElement = e.target;
}, true);

function getSelectedElementInfo() {
    if (!lastHoveredElement) return null;
    return {
        tagName: lastHoveredElement.tagName,
        className: [...lastHoveredElement.classList].join(' '),
        id: lastHoveredElement.id,
        innerText: (lastHoveredElement.innerText || lastHoveredElement.textContent || '').substring(0, 100),
        selector: getSimplifiedSelector(lastHoveredElement)
    };
}

// classList (not className) so SVG elements, whose className isn't a string, work too.
function getSimplifiedSelector(el) {
    if (el.id) return `#${CSS.escape(el.id)}`;
    if (el.classList.length) return el.tagName.toLowerCase() + [...el.classList].map(c => `.${CSS.escape(c)}`).join('');
    return el.tagName.toLowerCase();
}

const INSPECTED_STYLES = [
    'display', 'visibility', 'opacity', 'position', 'z-index', 'overflow', 'width', 'height',
    'margin', 'padding', 'color', 'background-color', 'font-size', 'pointer-events', 'cursor', 'transform'
];
const INSPECTED_ATTRIBUTES = ['disabled', 'href', 'type', 'role', 'name', 'aria-label', 'aria-hidden', 'aria-disabled'];

// Answers the backend visual agent's inspect_element tool: live details a screenshot
// can't show (computed styles, hidden/covered state, exact size and position).
function inspectElement(selector) {
    let elements;
    try {
        elements = [...document.querySelectorAll(selector)].slice(0, 5);
    } catch (e) {
        return `Error: invalid CSS selector '${selector}'`;
    }
    if (!elements.length) return `No elements match '${selector}'`;
    return elements.map(el => {
        const rect = el.getBoundingClientRect();
        const style = getComputedStyle(el);
        const styles = {};
        for (const prop of INSPECTED_STYLES) styles[prop] = style.getPropertyValue(prop);
        const attributes = {};
        for (const attr of INSPECTED_ATTRIBUTES) {
            if (el.hasAttribute(attr)) attributes[attr] = el.getAttribute(attr);
        }
        const centerX = rect.left + rect.width / 2;
        const centerY = rect.top + rect.height / 2;
        const topElement = document.elementFromPoint(centerX, centerY);
        return {
            selector: getSimplifiedSelector(el),
            text: (el.innerText || el.textContent || '').trim().substring(0, 150),
            rect: { x: Math.round(rect.x), y: Math.round(rect.y), width: Math.round(rect.width), height: Math.round(rect.height) },
            inViewport: rect.width > 0 && rect.height > 0 && rect.bottom > 0 && rect.top < window.innerHeight,
            // Another element on top of this one's center usually means it can't be clicked.
            coveredBy: topElement && topElement !== el && !el.contains(topElement) ? getSimplifiedSelector(topElement) : null,
            attributes,
            styles
        };
    });
}

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    if (request.action === "run_tool") {
        sendResponse(request.tool === "inspect_element"
            ? inspectElement(request.args.selector || '')
            : `Error: unknown tool '${request.tool}'`);
        return;
    }
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
