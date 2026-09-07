const FRAArchiveUI = (() => {
  const QUERY_ORDER = ['district', 'block', 'village', 'right_type', 'claim_status', 'review_state', 'claim_year', 'query'];
  const REVIEW_FIELDS = [
    'holder_name', 'holder_type', 'household_name', 'community_name', 'claim_number',
    'state', 'state_code', 'district', 'block', 'village', 'survey_number', 'subdivision_number',
    'right_type', 'claim_status', 'claim_year', 'claimed_area', 'claimed_area_sqm',
    'granted_area', 'granted_area_sqm', 'area_unit', 'gram_sabha_status', 'sdlc_status',
    'dlc_status', 'decision_authority', 'decision_date', 'title_number', 'latitude',
    'longitude', 'coordinates',
  ];
  const REQUIRED_REVIEW_FIELDS = new Set(['holder_name', 'district', 'block', 'village', 'right_type', 'claim_status']);
  const REVIEW_FIELD_CONFIG = {
    holder_type: { options: ['individual', 'household', 'community', 'gram_sabha'] },
    right_type: { options: ['IFR', 'CR', 'CFR'] },
    claim_status: { options: ['draft', 'submitted', 'gram_sabha_verified', 'sdlc_review', 'dlc_decided', 'granted', 'rejected', 'remanded', 'withdrawn'] },
    area_unit: { options: ['sqm', 'hectare', 'acre', 'cent'] },
    gram_sabha_status: { options: ['pending', 'verified', 'rejected', 'remanded'] },
    sdlc_status: { options: ['pending', 'under_review', 'recommended', 'not_recommended', 'remanded'] },
    dlc_status: { options: ['pending', 'granted', 'rejected', 'remanded'] },
    claim_year: { type: 'number', min: 1900, max: new Date().getFullYear() },
    decision_date: { type: 'date' },
    claimed_area: { type: 'number', min: 0, step: 'any', inputmode: 'decimal' },
    claimed_area_sqm: { type: 'number', min: 0, step: 'any', inputmode: 'decimal' },
    granted_area: { type: 'number', min: 0, step: 'any', inputmode: 'decimal' },
    granted_area_sqm: { type: 'number', min: 0, step: 'any', inputmode: 'decimal' },
    latitude: { type: 'number', min: -90, max: 90, step: 'any', inputmode: 'decimal' },
    longitude: { type: 'number', min: -180, max: 180, step: 'any', inputmode: 'decimal' },
  };
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
  function fieldReviewMeta(evidence = {}) {
    const parts = [];
    if (evidence.source_page) parts.push(`Page ${evidence.source_page}`);
    else if (evidence.source_row) parts.push(`Row ${evidence.source_row}`);
    if (evidence.confidence !== undefined && evidence.confidence !== null) parts.push(`${Math.round(Number(evidence.confidence) * 100)}% confidence`);
    if (evidence.source_header) parts.push(`Source: ${evidence.source_header}`);
    if (evidence.ambiguous) {
      const candidates = (evidence.candidates || []).map((item) => item.value ?? item.source_value).filter((value) => value !== undefined && value !== null && String(value).trim());
      parts.push(`Ambiguous${candidates.length ? `: ${candidates.join(' | ')}` : ''}`);
    } else if (evidence.validation_error) parts.push('Invalid extraction; correction required');
    return parts.join(' · ');
  }
  function chainValue(value, fallback = 'Not recorded') { if (value === undefined || value === null || value === '') return fallback; return typeof value === 'object' ? JSON.stringify(value) : String(value); }
  function fieldEvidenceRows(chain = {}) {
    const locator = chain.locator || {}; const extraction = chain.extraction || {}; const reviewer = chain.reviewer;
    const location = locator.page ? `Page ${locator.page}` : locator.row ? `Row ${locator.row}${locator.header ? ` · ${locator.header}` : ''}` : 'Not recorded';
    const extractionLabel = [extraction.method, extraction.model_version, extraction.confidence == null ? null : `${Math.round(Number(extraction.confidence) * 100)}% confidence`].filter(Boolean).join(' · ') || 'Not recorded';
    const reviewerLabel = reviewer ? [reviewer.display_name, reviewer.review_state, reviewer.reviewed_at].filter(Boolean).join(' · ') : 'Not reviewed';
    return [['Value', chainValue(chain.value)], ['Source', chainValue(chain.source)], ['Document', chainValue(chain.document?.filename)], ['Page / row', location], ['Extraction', extractionLabel], ['Reviewer', reviewerLabel], ['Final value', chainValue(chain.final_value, 'Pending reviewer approval')]];
  }
  function appendEvidenceChain(row, chain, doc) {
    const details = element(doc, 'details', 'field-provenance'); const summary = element(doc, 'summary', '', 'View evidence chain'); const list = element(doc, 'dl', 'provenance-chain');
    fieldEvidenceRows(chain).forEach(([term, value]) => { const item = element(doc, 'div'); item.append(element(doc, 'dt', '', term), element(doc, 'dd', '', value)); list.appendChild(item); });
    details.append(summary, list); row.appendChild(details);
  }
  function reviewControl(key, value, doc) {
    const config = REVIEW_FIELD_CONFIG[key] || {};
    const control = doc.createElement(config.options ? 'select' : 'input');
    control.id = `review-field-${key}`;
    control.name = key;
    if (config.options) {
      const prompt = element(doc, 'option', '', `Select ${key.replaceAll('_', ' ')}`); prompt.value = '';
      control.appendChild(prompt);
      config.options.forEach((optionValue) => {
        const words = optionValue.replaceAll('_', ' '); const optionLabel = optionValue === optionValue.toUpperCase() ? optionValue : `${words[0].toUpperCase()}${words.slice(1)}`;
        const option = element(doc, 'option', '', optionLabel); option.value = optionValue; control.appendChild(option);
      });
      if (value && !config.options.includes(String(value))) {
        const retained = element(doc, 'option', '', `${value} (source value)`); retained.value = value; control.appendChild(retained);
      }
    } else {
      control.type = config.type || 'text';
      ['min', 'max', 'step', 'inputmode'].forEach((attribute) => {
        if (config[attribute] !== undefined) control.setAttribute(attribute, String(config[attribute]));
      });
    }
    control.value = value ?? '';
    return control;
  }
  function renderFields(container, values, evidence = {}, fieldReviews = [], doc = document) {
    container.replaceChildren();
    const reviews = new Map((fieldReviews || []).map((item) => [item.field_name, item]));
    REVIEW_FIELDS.forEach((key) => {
      const fieldEvidence = evidence?.[key] || {};
      const requiresCorrection = fieldEvidence.ambiguous || fieldEvidence.validation_error;
      const row = element(doc, 'div', `field-row${requiresCorrection ? ' needs-correction' : ''}`);
      const label = element(doc, 'label', '', key.replaceAll('_', ' '));
      const control = reviewControl(key, values?.[key], doc);
      label.htmlFor = control.id;
      if (REQUIRED_REVIEW_FIELDS.has(key)) {
        control.required = true;
        const requiredMark = element(doc, 'span', 'required-mark', 'Required'); requiredMark.setAttribute('aria-hidden', 'true'); label.append(requiredMark);
      }
      if (requiresCorrection && control.tagName?.toLowerCase() !== 'select') control.placeholder = 'Reviewer correction required';
      const metaText = fieldReviewMeta(fieldEvidence);
      row.append(label, control);
      if (metaText) row.append(element(doc, 'small', 'field-evidence', metaText));
      const chain = reviews.get(key)?.evidence_chain;
      if (chain) appendEvidenceChain(row, chain, doc);
      container.appendChild(row);
    });
  }
  function formValues(form) {
    const values = Object.fromEntries(new FormData(form).entries());
    Object.keys(values).forEach((key) => { if (!String(values[key]).trim() && !REQUIRED_REVIEW_FIELDS.has(key)) delete values[key]; });
    if (values.claim_year) values.claim_year = Number(values.claim_year);
    return values;
  }
  function canUploadBatch({ fileCount = 0, sourceOffice = '', district = '', uploading = false } = {}) {
    return !uploading && Number(fileCount) > 0 && Boolean(String(sourceOffice).trim()) && Boolean(String(district).trim());
  }
  function batchSummary(result = {}) {
    const accepted = Number(result.accepted || 0); const rejected = Number(result.rejected || 0);
    if (result.replayed) return `Existing batch restored: ${accepted} ${accepted === 1 ? 'file' : 'files'} already queued.`;
    const queued = `${accepted} ${accepted === 1 ? 'file' : 'files'} queued`;
    return rejected ? `${queued}; ${rejected} ${rejected === 1 ? 'file' : 'files'} rejected.` : `${queued}.`;
  }
  function canUploadTabular({ filename = '', sourceOffice = '', district = '', uploading = false } = {}) {
    return !uploading && /\.(csv|xlsx)$/i.test(String(filename).trim()) && Boolean(String(sourceOffice).trim()) && Boolean(String(district).trim());
  }
  function tabularSummary(result = {}) {
    const accepted = Number(result.accepted || 0);
    if (result.replayed) return `Existing register restored: ${accepted} ${accepted === 1 ? 'row is' : 'rows are'} already in the review queue.`;
    return `${accepted} ${accepted === 1 ? 'row' : 'rows'} added to the review queue.`;
  }
  function renderBatchFiles(container, files, doc = document) {
    container.replaceChildren();
    (files || []).forEach((file) => {
      const item = element(doc, 'li', `upload-result ${file.status || 'ready'}`);
      const copy = element(doc, 'span'); copy.append(element(doc, 'strong', '', file.filename), element(doc, 'small', '', file.message || file.legacy_reference || 'Ready to upload'));
      item.append(copy, element(doc, 'span', 'record-state', file.status || 'ready')); container.appendChild(item);
    });
  }
  return { REVIEW_FIELDS, REVIEW_FIELD_CONFIG, batchSummary, canUploadBatch, canUploadTabular, emptyState, fieldEvidenceRows, fieldReviewMeta, formValues, query, renderBatchFiles, renderFields, renderRecords, tabularSummary };
})();
if (typeof module !== 'undefined' && module.exports) module.exports = FRAArchiveUI;
