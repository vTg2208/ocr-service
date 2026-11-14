/* Real satellite analysis over an authorised, immutable boundary snapshot. */
(() => {
  'use strict';
  const el = (tag, text, cls) => {
    const node = document.createElement(tag);
    if (text != null) node.textContent = String(text);
    if (cls) node.className = cls;
    return node;
  };
  let dialog, endpoint, selection = 0, map, layers, timer, opener, selectedAnalysisId = null;
  const nodes = {};
  function stop() { clearTimeout(timer); timer = null; }
  function message(value) { nodes.status.textContent = value; }
  function dateString(days) {
    const date = new Date(); date.setUTCDate(date.getUTCDate() - days);
    return date.toISOString().slice(0, 10);
  }
  function setup() {
    if (dialog) return;
    dialog = el('dialog', null, 'land-cover-dialog');
    dialog.setAttribute('aria-labelledby', 'landCoverHeading');
    const header = el('header');
    const title = el('h2', 'Satellite land cover'); title.id = 'landCoverHeading';
    const close = el('button', 'Close', 'secondary-action'); close.type = 'button';
    close.onclick = () => dialog.close(); header.append(title, close);
    const warning = el('p', '10 m land-cover estimates support human review. Model probability is not measured accuracy, ownership, or scheme eligibility.', 'muted');
    nodes.status = el('p'); nodes.status.setAttribute('role', 'status');
    const form = el('form', null, 'land-cover-request');
    for (const [name, label, days] of [['start_date', 'From', 90], ['end_date', 'To', 1]]) {
      const field = el('label', label); const input = el('input');
      input.type = 'date'; input.name = name; input.required = true; input.value = dateString(days); input.max = dateString(0);
      field.append(input); form.append(field);
    }
    nodes.start = el('button', 'Request analysis', 'primary-action'); nodes.start.type = 'submit';
    form.append(nodes.start);
    form.onsubmit = async event => {
      event.preventDefault(); const version = selection; const path = endpoint;
      nodes.start.disabled = true;
      try {
        await FRAApi.request(path, FRAApi.json('POST', Object.fromEntries(new FormData(form))));
        if (version !== selection) return;
        message('Analysis requested. The background worker will retrieve imagery and run the model.');
        selectedAnalysisId = null;
        await load(version);
      } catch (error) { if (version === selection) message(error.message); }
      finally { if (version === selection) nodes.start.disabled = false; }
    };
    nodes.results = el('section'); nodes.results.setAttribute('aria-label', 'Analysis results');
    dialog.append(header, warning, form, nodes.status, nodes.results);
    dialog.addEventListener('close', () => { selection++; stop(); if (map) { map.remove(); map = null; } opener?.focus(); });
    document.body.append(dialog);
  }
  function link(host, title, url) {
    const node = el('a', title, 'secondary-action'); node.href = url;
    node.target = '_blank'; node.rel = 'noopener'; host.append(node);
  }
  function render(items, staff, version) {
    if (map) { map.remove(); map = null; }
    nodes.results.replaceChildren();
    if (!items.length) { nodes.results.append(el('p', 'No analysis has been requested for this approved boundary.')); return; }
    const analysis = items.find(item => item.id === selectedAnalysisId) || items[0];
    if (items.length > 1) {
      const label = el('label', 'Analysis history '); const history = el('select');
      items.forEach(item => history.add(new Option(`${item.parameters.start_date} to ${item.parameters.end_date} · ${item.state.replaceAll('_', ' ')}`, item.id)));
      history.value = analysis.id; history.onchange = () => { selectedAnalysisId = history.value; render(items, staff, version); };
      label.append(history); nodes.results.append(label);
    }
    const summary = analysis.summary || {};
    nodes.results.append(el('h3', `${analysis.state.replaceAll('_', ' ')} · ${analysis.review_state.replaceAll('_', ' ')}`));
    nodes.results.append(el('p', `${analysis.model_version} · ${analysis.provenance?.acquired_at ? new Date(analysis.provenance.acquired_at).toLocaleDateString('en-IN') : 'Acquisition pending'}`, 'muted'));
    if (analysis.error) nodes.results.append(el('p', analysis.error));
    if (analysis.current_boundary === false) nodes.results.append(el('p', 'This result describes an older boundary. Retain it as historical evidence; request analysis of the current approved boundary.'));
    if (analysis.can_retry && staff) {
      const retry = el('button', 'Retry processing', 'secondary-action');
      retry.onclick = async () => {
        retry.disabled = true;
        try { await FRAApi.request(`/api/fra/jobs/${analysis.job_id}/retry`, FRAApi.json('POST', {})); if (version === selection) await load(version); }
        catch (error) { if (version === selection) message(error.message); }
      }; nodes.results.append(retry);
    }
    (summary.warnings || []).forEach(text => nodes.results.append(el('p', text, 'muted')));
    if (summary.target_pixels != null) nodes.results.append(el('p', `${summary.valid_pixels || 0} clear pixel centres / ${summary.target_pixels} in the boundary. Fractions below use clear pixels only.`));
    if (analysis.features?.features?.length && typeof L !== 'undefined') {
      const canvas = el('div', null, 'land-cover-map'); canvas.setAttribute('aria-label', 'Predicted land-cover map');
      nodes.results.append(canvas); map = L.map(canvas);
      L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '© OpenStreetMap contributors' }).addTo(map);
      layers = L.geoJSON(analysis.features, { style: feature => ({ color: feature.properties.color, weight: 1, fillOpacity: .55 }),
        onEachFeature: (feature, layer) => layer.bindTooltip(el('span', feature.properties.class_name.replaceAll('_', ' '))) }).addTo(map);
      map.fitBounds(layers.getBounds(), { padding: [15, 15], maxZoom: 17 });
    }
    if (summary.classes) {
      const table = el('table', null, 'land-cover-table');
      const head = el('tr'); ['Land cover', 'Estimated m²', 'Clear-pixel share', 'Mean model probability'].forEach(text => head.append(el('th', text)));
      const thead = el('thead'); thead.append(head); table.append(thead);
      const body = el('tbody');
      summary.classes.forEach(row => {
        const tr = el('tr');
        [row.class_name.replaceAll('_', ' '), Math.round(row.estimated_area_sqm).toLocaleString('en-IN'), `${(row.fraction * 100).toFixed(1)}%`, row.mean_probability == null ? '—' : row.mean_probability.toFixed(3)].forEach(text => tr.append(el('td', text)));
        body.append(tr);
      }); table.append(body); nodes.results.append(table);
    }
    if (analysis.state === 'completed') {
      const downloads = el('p', null, 'land-cover-downloads');
      link(downloads, 'Printable report', analysis.report_url);
      link(downloads, 'GeoJSON', analysis.geojson_url);
      if (analysis.artifacts.labels) link(downloads, 'GeoTIFF labels', analysis.artifacts.labels);
      if (analysis.artifacts.preview) link(downloads, 'Preview', analysis.artifacts.preview);
      if (analysis.artifacts.input) link(downloads, 'Prepared bands', analysis.artifacts.input);
      if (analysis.artifacts.validity) link(downloads, 'Validity mask', analysis.artifacts.validity);
      nodes.results.append(downloads);
    }
    if (analysis.review_note) nodes.results.append(el('p', `Staff review: ${analysis.review_note}`));
    if (staff && analysis.state === 'completed' && analysis.review_state === 'unverified' && analysis.current_boundary !== false) {
      const review = el('form', null, 'portal-form');
      const selectLabel = el('label', 'Review outcome'); const select = el('select');
      select.add(new Option('Verify supporting observation', 'verified')); select.add(new Option('Reject observation', 'rejected'));
      selectLabel.append(select);
      const noteLabel = el('label', 'Evidence checked and review reasons'); const note = el('textarea'); note.required = true; note.maxLength = 4000; note.rows = 3; noteLabel.append(note);
      const save = el('button', 'Record review', 'primary-action'); save.type = 'submit'; review.append(selectLabel, noteLabel, save);
      review.onsubmit = async event => {
        event.preventDefault(); save.disabled = true;
        try {
          await FRAApi.request(`/api/land-cover/${analysis.id}/review`, FRAApi.json('POST', { state: select.value, note: note.value, expected_revision: analysis.revision }));
          if (version !== selection) return;
          message('Review recorded. Verified observations are available in the owning workspace.');
          document.dispatchEvent(new CustomEvent('land-cover:reviewed'));
          await load(version);
        } catch (error) { if (version === selection) message(error.message); }
        finally { save.disabled = false; }
      }; nodes.results.append(review);
    }
  }
  async function load(version) {
    stop(); const path = endpoint;
    try {
      const [data, user] = await Promise.all([FRAApi.request(path), FRAApi.request('/api/auth/session')]);
      if (version !== selection || !dialog.open) return;
      render(data.items, ['reviewer', 'admin'].includes(user.role), version);
      if (data.items.some(item => ['queued', 'running'].includes(item.state))) timer = setTimeout(() => load(version), 10000);
    } catch (error) { if (version === selection) message(error.message); }
  }
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-land-cover-endpoint]');
    if (!button) return;
    setup(); stop(); opener = button; endpoint = button.dataset.landCoverEndpoint; selectedAnalysisId = null;
    const version = ++selection; nodes.start.disabled = false; nodes.results.replaceChildren(); message('Loading satellite evidence…');
    if (!dialog.open) dialog.showModal(); load(version);
  });
})();
