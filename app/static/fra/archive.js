const FRAArchiveUI = (() => {
  const QUERY_ORDER = ['district', 'block', 'village', 'right_type', 'claim_status', 'review_state', 'claim_year', 'query'];
  const REVIEW_FIELDS = ['holder_name', 'claim_number', 'district', 'block', 'village', 'right_type', 'claim_status', 'claim_year'];
  function emptyState(allRecords, search) {
    if ((allRecords || []).length && search) return 'No matching records';
    if (!(allRecords || []).length) return 'No archive records';
    return '';
  }
  function query(filters = {}) {
    const values = new URLSearchParams();
    QUERY_ORDER.forEach((key) => { const value = filters[key]; if (value !== undefined && value !== null && String(value).trim()) values.set(key, String(value).trim()); });
    return values.toString();
  }
  function element(doc, tag, className, text) { const node = doc.createElement(tag); if (className) node.className = className; if (text !== undefined) node.textContent = String(text); return node; }
  function renderRecords(container, records, selectedId, onSelect, doc = document) {
    container.replaceChildren();
    (records || []).forEach((record) => {
      const item = element(doc, 'li', 'archive-record'); const button = element(doc, 'button', record.id === selectedId ? 'active' : ''); button.type = 'button';
      button.append(element(doc, 'strong', '', record.claim_number || record.legacy_reference), element(doc, 'span', 'record-state', String(record.review_state || 'pending').replaceAll('_', ' ')));
      const meta = element(doc, 'span', 'record-meta');
      meta.append(element(doc, 'small', '', record.holder_display_name || 'Holder pending review'), element(doc, 'small', '', [record.district, record.village].filter(Boolean).join(' · ') || 'Location pending'), element(doc, 'small', '', record.right_type || 'Right pending'));
      button.append(meta); button.addEventListener('click', () => onSelect(record)); item.appendChild(button); container.appendChild(item);
    });
  }
  function renderFields(container, values, doc = document) {
    container.replaceChildren();
    REVIEW_FIELDS.forEach((key) => { const row = element(doc, 'div', 'field-row'); const label = element(doc, 'label', '', key.replaceAll('_', ' ')); const input = doc.createElement('input'); input.name = key; input.value = values?.[key] ?? ''; if (key === 'claim_year') input.type = 'number'; row.append(label, input); container.appendChild(row); });
  }
  function formValues(form) { const values = Object.fromEntries(new FormData(form).entries()); if (values.claim_year) values.claim_year = Number(values.claim_year); return values; }
  return { REVIEW_FIELDS, emptyState, formValues, query, renderFields, renderRecords };
})();
if (typeof module !== 'undefined' && module.exports) module.exports = FRAArchiveUI;
