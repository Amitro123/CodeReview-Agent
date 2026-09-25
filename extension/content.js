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

// --- CI run pages: read the failure from the CI system's API, not the DOM ---------------
// Log viewers render only the visible lines, so scraped text misses most of the log. The
// APIs give the failing steps, the CI's own error annotations and the full step logs.
// Azure DevOps is read with the user's own session (same origin, no token needed);
// GitHub's API works without auth for public repos.

const MAX_FAILED_STEPS = 3;
const STEP_LOG_TAIL = 6000;

function ciSource() {
    const url = new URL(location.href);
    const host = url.hostname.toLowerCase();
    if ((host === 'dev.azure.com' || host.endsWith('.visualstudio.com')) && url.pathname.includes('/_build')) {
        return 'azure_devops';
    }
    if (host === 'github.com' && url.pathname.includes('/actions/runs/')) return 'github_actions';
    return null;
}

// Default credentials ("same-origin"): Azure DevOps' API is on the page's own origin, so the
// user's session goes along; api.github.com is cross-origin and must get no cookies, since it
// answers with "Access-Control-Allow-Origin: *", which browsers refuse for credentialed requests.
async function fetchChecked(url, asText = false) {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`${response.status} from ${url.split('?')[0]}`);
    return asText ? response.text() : response.json();
}

async function azureFailure() {
    const url = new URL(location.href);
    const buildId = url.searchParams.get('buildId');
    if (!buildId) throw new Error('open a specific build run (the URL needs ?buildId=)');
    const parts = url.pathname.split('/').filter(Boolean);
    // dev.azure.com/{org}/{project}/_build/... or {org}.visualstudio.com/{project}/_build/...
    const [org, project, base] = url.hostname === 'dev.azure.com'
        ? [parts[0], parts[1], `${url.origin}/${parts[0]}`]
        : [url.hostname.split('.')[0], parts[0], url.origin];
    const api = `${base}/${project}/_apis/build/builds/${buildId}`;
    const build = await fetchChecked(`${api}?api-version=7.1`);
    const timeline = await fetchChecked(`${api}/timeline?api-version=7.1`);
    const failed = (timeline.records || [])
        .filter(r => r.type === 'Task' && r.result === 'failed')
        .slice(0, MAX_FAILED_STEPS);
    const steps = [];
    for (const record of failed) {
        let logTail = '';
        if (record.log && record.log.url) {
            try {
                logTail = (await fetchChecked(record.log.url, true)).slice(-STEP_LOG_TAIL);
            } catch (e) {
                logTail = `(log not readable: ${e.message})`;
            }
        }
        steps.push({
            name: record.name,
            issues: (record.issues || []).filter(i => i.type === 'error').map(i => i.message).slice(0, 8),
            log_tail: logTail
        });
    }
    const repository = build.repository ? build.repository.name : '';
    return {
        repo: [decodeURIComponent(org), decodeURIComponent(project), repository].filter(Boolean).join('/'),
        ci: {
            provider: 'azure_devops',
            pipeline: build.definition ? build.definition.name : '',
            branch: build.sourceBranch,
            commit: build.sourceVersion,
            failed_steps: steps
        }
    };
}

function scrapedLog() {
    return document.querySelector('.log-viewer-container')?.innerText ||
        document.querySelector('.highlight.actions-log')?.innerText || '';
}

async function githubFailure() {
    const match = location.pathname.match(/^\/([^/]+)\/([^/]+)\/actions\/runs\/(\d+)/);
    const [, owner, repo, runId] = match;
    const api = `https://api.github.com/repos/${owner}/${repo}`;
    const shown = scrapedLog();
    const ci = { provider: 'github_actions', pipeline: '', failed_steps: [], log: shown };
    try {
        const run = await fetchChecked(`${api}/actions/runs/${runId}`);
        ci.pipeline = run.name;
        ci.branch = run.head_branch;
        ci.commit = run.head_sha;
        const jobs = (await fetchChecked(`${api}/actions/runs/${runId}/jobs?per_page=50`)).jobs || [];
        for (const job of jobs.filter(j => j.conclusion === 'failure').slice(0, MAX_FAILED_STEPS)) {
            const failedSteps = (job.steps || []).filter(s => s.conclusion === 'failure').map(s => s.name);
            // A job's check-run annotations are the error lines GitHub highlights.
            let issues = [];
            try {
                const annotations = await fetchChecked(`${api}/check-runs/${job.id}/annotations`);
                issues = annotations.filter(a => a.annotation_level === 'failure').map(a => a.message).slice(0, 8);
            } catch (e) { /* annotations are optional */ }
            ci.failed_steps.push({
                name: failedSteps.length ? `${job.name} › ${failedSteps.join(', ')}` : job.name,
                issues,
                // Full job logs need auth; use what the page shows for the job it's on.
                log_tail: ''
            });
        }
        if (ci.failed_steps.length && shown) ci.failed_steps[0].log_tail = shown.slice(-STEP_LOG_TAIL);
    } catch (e) {
        ci.api_error = e.message; // e.g. a private repo: fall back to the scraped log
    }
    return { repo: `${owner}/${repo}`, ci };
}

async function collectContext() {
    const dom = {
        selectedElement: getSelectedElementInfo(),
        pageTitle: document.title,
        url: window.location.href
    };
    const source = ciSource();
    // Screenshots are captured natively by the background service worker
    // (chrome.tabs.captureVisibleTab), not here - see background.js.
    const context = { dom, source, repo: window.location.pathname.split('/').slice(1, 3).join('/'), ci: null };
    if (!source) return context;
    try {
        const failure = source === 'azure_devops' ? await azureFailure() : await githubFailure();
        return { ...context, ...failure };
    } catch (e) {
        return { ...context, ci: { provider: source, failed_steps: [], log: scrapedLog() || document.body.innerText.slice(-15000), api_error: e.message } };
    }
}

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    if (request.action === "run_tool") {
        sendResponse(request.tool === "inspect_element"
            ? inspectElement(request.args.selector || '')
            : `Error: unknown tool '${request.tool}'`);
        return;
    }
    if (request.action === "get_context") {
        collectContext().then(sendResponse);
        return true; // async response
    }
});
