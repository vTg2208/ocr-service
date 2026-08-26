const FRAReportsUI = (() => {
  const SUBJECTS = new Set(['villages', 'archive', 'claims']);
  function reportUrl(subject, id) { if (!SUBJECTS.has(subject) || !String(id || '').trim()) return null; return `/api/fra/reports/${subject}/${encodeURIComponent(String(id).trim())}`; }
  return { reportUrl };
})();
if (typeof module !== 'undefined' && module.exports) module.exports = FRAReportsUI;

if (typeof document !== 'undefined') (() => {
  const villageSelect = document.querySelector('#reportVillage'); if (!villageSelect) return; let initialized = false;
  async function loadVillages() { const data = await FRAApi.request('/api/fra/villages'); villageSelect.replaceChildren(new Option('Choose a village', '')); data.items.forEach((item) => villageSelect.add(new Option(`${item.village_name} — ${item.district_name}`, item.id))); }
  function open(subject, input) { const url = FRAReportsUI.reportUrl(subject, input.value); if (url) window.open(url, '_blank', 'noopener,noreferrer'); else input.focus(); }
  document.querySelector('#openVillageReport').addEventListener('click', () => open('villages', villageSelect)); document.querySelector('#openArchiveReport').addEventListener('click', () => open('archive', document.querySelector('#reportArchive')));
  document.addEventListener('fra:section', async (event) => { if (event.detail.section === 'reports' && !initialized) { initialized = true; try { await loadVillages(); } catch (_) { villageSelect.replaceChildren(new Option('Village list unavailable', '')); } } });
})();
