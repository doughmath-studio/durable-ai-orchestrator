// Outbound HTTP with a timeout and bounded retries (only for transient failures).

export class FetchError extends Error {
  constructor(message, { status = null, transient = false, body = '' } = {}) {
    super(message);
    this.name = 'FetchError';
    this.status = status;
    this.transient = transient;
    this.body = body;
  }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function isTransientStatus(status) {
  return status === 408 || status === 425 || status === 429 || status >= 500;
}

/**
 * fetch() with timeout and exponential backoff. Retries network errors, timeouts, 408/425/429 and 5xx.
 * Never retries other 4xx (they will not fix themselves). Returns the Response on 2xx.
 */
export async function fetchWithRetry(url, init = {}, opts = {}) {
  const { attempts = 3, timeoutMs = 10000, baseDelayMs = 500, fetchImpl = globalThis.fetch, sleepImpl = sleep } = opts;
  let lastErr;
  for (let i = 0; i < attempts; i++) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      const res = await fetchImpl(url, { ...init, signal: ctrl.signal });
      if (res.ok) return res;
      const body = (await res.text().catch(() => '')).slice(0, 500);
      lastErr = new FetchError(`HTTP ${res.status}`, { status: res.status, transient: isTransientStatus(res.status), body });
    } catch (e) {
      if (e instanceof FetchError) throw e;
      lastErr = new FetchError(e?.name === 'AbortError' ? `timeout after ${timeoutMs}ms` : `network error: ${e?.message || e}`, {
        transient: true,
      });
    } finally {
      clearTimeout(timer);
    }
    if (!lastErr.transient || i === attempts - 1) break;
    await sleepImpl(baseDelayMs * 2 ** i);
  }
  throw lastErr;
}
