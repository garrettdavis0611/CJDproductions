export async function getJson(url, headers = {}, { timeoutMs = 20000, retries = 2 } = {}) {
  let lastErr;
  for (let attempt = 0; attempt <= retries; attempt++) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      const resp = await fetch(url, { headers, signal: ctrl.signal });
      if (!resp.ok) throw new Error(`HTTP ${resp.status} for ${url}`);
      return await resp.json();
    } catch (err) {
      lastErr = err;
      if (attempt < retries) await new Promise(r => setTimeout(r, 1000 * 2 ** attempt));
    } finally {
      clearTimeout(timer);
    }
  }
  throw lastErr;
}
