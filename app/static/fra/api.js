const FRAApi = (() => {
  function errorMessage(body) {
    const value = body?.message ?? body?.detail ?? body;
    if (typeof value === 'string') return value;
    if (value && typeof value.message === 'string') return value.message;
    return 'The request could not be completed.';
  }
  async function request(url, options = {}, fetchImpl = fetch) {
    const response = await fetchImpl(url, { credentials: 'same-origin', ...options, headers: { Accept: 'application/json', ...(options.headers || {}) } });
    let body = {}; try { body = await response.json(); } catch (_) { body = {}; }
    if (!response.ok) { const error = new Error(errorMessage(body)); error.status = response.status; error.body = body; throw error; }
    return body;
  }
  function json(method, body) { return { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }; }
  function requestGate() { let revision = 0; return { begin() { const token = ++revision; return () => revision === token; }, invalidate() { revision++; } }; }
  function contextQuery() { const params = new URLSearchParams(); ['district', 'block', 'village'].forEach((key) => { const value = document.querySelector(`#context${key[0].toUpperCase()}${key.slice(1)}`)?.value?.trim(); if (value) params.set(key, value); }); return params.toString(); }
  return { contextQuery, errorMessage, json, request, requestGate };
})();
if (typeof module !== 'undefined' && module.exports) module.exports = FRAApi;
