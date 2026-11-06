/**
 * @fileoverview Collects what a page does wrong while a TPM run drives it.
 *
 * The user's rule is that the app shows no error in the browser console, no failed request, no error toast and no
 * desktop notification at start-up. `watchPage` listens to a Puppeteer page for console errors, uncaught page
 * errors, responses with status 400 or more and requests that failed. `installToastListener` runs in the page and
 * records every in-app toast (`emitToast` dispatches a `window` CustomEvent named `astrometrics:toast`).
 *
 * A request that the browser cancelled (`net::ERR_ABORTED`, for example an image that scrolled away or a page that
 * was left) is kept apart as an aborted request and does not count as failed.
 */

/** The longest message text kept for one record. */
const MAX_TEXT_LENGTH = 500;

/**
 * Cuts a message to a length that is readable in a report.
 *
 * @param {unknown} text - The message.
 * @return {string} The message as text, at most `MAX_TEXT_LENGTH` characters.
 */
function clip(text) {
    return String(text ?? '').slice(0, MAX_TEXT_LENGTH);
}

/**
 * Starts listening to a page. The listeners stay on across navigations of the page.
 *
 * @param {import('puppeteer-core').Page} page - The page to watch.
 * @param {string} label - Where the page runs: `chrome` or `electron`.
 * @param {{consoleErrors: object[], failedRequests: object[], abortedRequests: object[]}} [records] - Lists to add to,
 *   so several pages can share one set of records.
 * @return {{consoleErrors: object[], failedRequests: object[], abortedRequests: object[]}} The records, filled as the
 *   page runs.
 */
export function watchPage(page, label, records = { consoleErrors: [], failedRequests: [], abortedRequests: [] }) {
    page.on('console', (message) => {
        if (message.type() !== 'error') return;
        records.consoleErrors.push({ text: clip(message.text()), url: message.location()?.url ?? null, kind: 'console', run: label });
    });
    page.on('pageerror', (error) => {
        records.consoleErrors.push({ text: clip(error?.message ?? error), url: null, kind: 'pageerror', run: label });
    });
    page.on('response', (response) => {
        if (response.status() >= 400) {
            records.failedRequests.push({ url: response.url(), status: response.status(), method: response.request().method(), run: label });
        }
    });
    page.on('requestfailed', (request) => {
        const failure = request.failure()?.errorText ?? 'unknown';
        const record = { url: request.url(), status: null, method: request.method(), failure, run: label };
        if (failure === 'net::ERR_ABORTED') records.abortedRequests.push(record);
        else records.failedRequests.push(record);
    });
    return records;
}

/**
 * Runs in the page (through `evaluateOnNewDocument` or `evaluate`): records each toast into `window.__tpmToasts`.
 * It uses only browser APIs, because Puppeteer sends its source text to the page.
 */
export function installToastListener() {
    if (window.__tpmToasts) return;
    window.__tpmToasts = [];
    window.addEventListener('astrometrics:toast', (event) => {
        const detail = event.detail ?? {};
        window.__tpmToasts.push({ text: String(detail.text ?? '').slice(0, 500), kind: String(detail.kind ?? ''), source: detail.source ?? null, at: performance.now() });
    });
}

/**
 * Reads the toasts a page has recorded.
 *
 * @param {import('puppeteer-core').Page} page - The page with `installToastListener` installed.
 * @param {string} label - Where the page runs: `chrome` or `electron`.
 * @return {Promise<object[]>} Every toast, with its `run`; an empty list if the page cannot be read.
 */
export async function readToasts(page, label) {
    try {
        const toasts = await page.evaluate(() => window.__tpmToasts ?? []);
        return toasts.map((toast) => ({ ...toast, run: label }));
    } catch {
        return [];
    }
}
