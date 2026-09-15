const FRADashboardUI = (() => {
  const verifierWork = [
    { total: 'failed_or_overdue_jobs', queue: 'processing_failures', title: 'Resolve failed or overdue processing', description: 'A background task is blocking current FRA work.', action: 'Inspect affected case', section: 'cases' },
    { total: 'archive_records_needing_review', queue: 'archive_review', title: 'Review extracted legacy records', description: 'Confirm source fields before records enter the native FRA register.', action: 'Review records', section: 'archive' },
    { total: 'claims_awaiting_review', queue: 'claims_review', title: 'Advance pending FRA case reviews', description: 'Record the next authorized step in the claim lifecycle.', action: 'Review cases', section: 'cases' },
    { total: 'spatial_findings_awaiting_disposition', queue: 'spatial_disposition', title: 'Record spatial review dispositions', description: 'Document how supporting spatial findings were considered.', action: 'Review boundaries', section: 'cases' },
    { total: 'intake_awaiting_triage', queue: 'intake_triage', title: 'Classify registry intake', description: 'Decide whether incoming registry records belong in the FRA workflow.', action: 'Open intake', section: 'cases' },
    { total: 'unverified_observations', queue: 'unverified_observations', title: 'Verify supporting observations', description: 'Human review is required before observations inform village profiles.', action: 'Review observations', section: 'assets' },
  ];

  function query(context = {}) {
    const params = new URLSearchParams();
    ['district', 'block', 'village'].forEach((key) => {
      const value = String(context[key] || '').trim();
      if (value) params.set(key, value);
    });
    return params.toString();
  }

  function queuePresentation(item = {}) {
    return {
      title: item.reference || 'Work item',
      meta: [item.district, item.reason].filter(Boolean).join(' · '),
      status: String(item.status || item.job_state || 'pending').replaceAll('_', ' '),
      workspace: item.workspace || '/fra#cases',
    };
  }

  function metricLabel(key) {
    const labels = {
      claims: 'Claims', titles: 'Titles', pending_cases: 'Pending cases', decisions: 'Decisions',
      fra_area_sqm: 'FRA area (m²)', granted_area_sqm: 'Granted area (m²)', villages_covered: 'Villages covered',
      active_titles: 'Active titles', verified_assets: 'Verified assets', deficit_counts: 'Recorded deficits',
      insufficient_data_cases: 'Insufficient-data cases', potentially_eligible: 'Potential candidates',
      not_indicated: 'Not indicated', not_evaluated: 'Not evaluated', insufficient_data: 'Insufficient data',
    };
    return labels[key] || String(key || '').replaceAll('_', ' ').replace(/^./, (value) => value.toUpperCase());
  }

  function buildPriorities(verifier, planner = {}) {
    if (verifier) {
      return verifierWork.map((definition) => ({
        ...definition,
        count: Number(verifier.totals?.[definition.total] || 0),
        item: verifier.queues?.[definition.queue]?.[0] || null,
      })).filter((item) => item.count > 0).slice(0, 5);
    }
    const insufficient = Number(planner.dss?.insufficient_data_cases || 0);
    const missing = (planner.missing_inputs || []).reduce((sum, item) => sum + Number(item.count || 0), 0);
    const pending = Number(planner.fra?.pending_cases || 0);
    return [
      { title: 'Complete evidence for scheme screening', description: `${missing.toLocaleString('en-IN')} required fact observations are currently missing.`, action: 'Inspect evidence gaps', section: 'planner', count: insufficient },
      { title: 'Inspect pending FRA cases', description: 'Review programme progress and open the appropriate case records.', action: 'Open case register', section: 'cases', count: pending },
    ].filter((item) => item.count > 0);
  }

  function attentionTotal(verifier, planner = {}) {
    if (verifier) return Object.values(verifier.totals || {}).reduce((sum, value) => sum + Number(value || 0), 0);
    return Number(planner.dss?.insufficient_data_cases || 0) + Number(planner.fra?.pending_cases || 0);
  }

  function disclaimer() {
    return 'These are privacy-minimized operational summaries for human planning and verification; they do not determine legal validity or approve benefits.';
  }

  return { attentionTotal, buildPriorities, disclaimer, metricLabel, query, queuePresentation };
})();

if (typeof module !== 'undefined' && module.exports) module.exports = FRADashboardUI;

if (typeof document !== 'undefined') (() => {
  const panel = document.querySelector('#dashboardPanel');
  if (!panel) return;
  const requests = FRAApi.requestGate();
  let bottleneck = null;

  function context() {
    return {
      district: document.querySelector('#contextDistrict')?.value,
      block: document.querySelector('#contextBlock')?.value,
      village: document.querySelector('#contextVillage')?.value,
    };
  }

  function number(value) { return Number(value || 0).toLocaleString('en-IN'); }
  function percentage(value, total) { return total > 0 ? Math.round((Number(value || 0) / total) * 100) : 0; }
  function formatArea(value) {
    const sqm = Number(value || 0);
    return sqm >= 10000 ? `${(sqm / 10000).toLocaleString('en-IN', { maximumFractionDigits: 1 })} ha` : `${number(Math.round(sqm))} m²`;
  }
  function setText(selector, value) { document.querySelector(selector).textContent = value; }

  function openWorkspace(section, item = null) {
    if (section === 'archive') document.dispatchEvent(new CustomEvent('fra:open-legacy-records'));
    else document.querySelector(`[data-section="${section}"]`)?.click();
    if (item?.claim_id) setTimeout(() => document.dispatchEvent(new CustomEvent('fra:open-case', { detail: { claimId: item.claim_id } })), 0);
  }

  function renderPriorities(verifier, planner) {
    const priorities = FRADashboardUI.buildPriorities(verifier, planner);
    const total = FRADashboardUI.attentionTotal(verifier, planner);
    setText('#dashboardAttentionTotal', number(total));
    setText('#dashboardAttentionLabel', total === 1 ? 'item needs attention' : 'items need attention');
    setText('#dashboardAccessMode', verifier ? 'Reviewer view' : 'Planning view');
    setText('#dashboardAttentionSummary', total
      ? 'Start with the highest-priority work in the selected administrative area.'
      : 'There is no immediate review work in the selected administrative area.');

    const list = document.querySelector('#dashboardPriorityQueue');
    list.replaceChildren();
    priorities.forEach((priority, index) => {
      const row = document.createElement('li');
      row.className = 'priority-item';
      const order = document.createElement('span');
      order.className = 'priority-order';
      order.textContent = String(index + 1).padStart(2, '0');
      const copy = document.createElement('div');
      const title = document.createElement('strong');
      const description = document.createElement('p');
      title.textContent = priority.title;
      description.textContent = priority.description;
      copy.append(title, description);
      const count = document.createElement('span');
      count.className = 'priority-count';
      count.textContent = number(priority.count);
      count.setAttribute('aria-label', `${number(priority.count)} work items`);
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'priority-action';
      button.textContent = priority.action;
      button.addEventListener('click', () => openWorkspace(priority.section, priority.item));
      row.append(order, copy, count, button);
      list.appendChild(row);
    });
    if (!priorities.length) {
      const empty = document.createElement('li');
      empty.className = 'priority-empty';
      const title = document.createElement('strong');
      const detail = document.createElement('span');
      title.textContent = 'No immediate work is waiting';
      detail.textContent = 'Use the readiness summary below to inspect programme coverage.';
      empty.append(title, detail);
      list.appendChild(empty);
    }

    bottleneck = priorities.reduce((largest, item) => !largest || item.count > largest.count ? item : largest, null);
    const action = document.querySelector('#dashboardBottleneckAction');
    if (bottleneck) {
      setText('#dashboardBottleneck', `${number(bottleneck.count)} · ${bottleneck.title}`);
      action.textContent = bottleneck.action;
      action.hidden = false;
    } else {
      setText('#dashboardBottleneck', 'No immediate operational bottleneck');
      action.hidden = true;
    }
  }

  function renderPosition(planner) {
    const fra = planner.fra || {};
    const statuses = planner.claims_by_status || {};
    const total = Number(fra.claims || 0);
    const granted = Number(statuses.granted || 0);
    const pending = Number(fra.pending_cases || 0);
    const rejected = Number(statuses.rejected || 0);
    const other = Math.max(total - granted - pending - rejected, 0);
    setText('#dashboardTotalClaims', number(total));
    setText('#dashboardGrantedCases', number(granted));
    setText('#dashboardPendingCases', number(pending));
    setText('#dashboardRejectedCases', number(rejected));
    setText('#dashboardStatusGranted', number(granted));
    setText('#dashboardStatusPending', number(pending));
    setText('#dashboardActiveTitles', number(fra.active_titles));
    const bar = document.querySelector('#dashboardStatusBar');
    bar.querySelector('.status-granted').style.width = `${percentage(granted, total)}%`;
    bar.querySelector('.status-pending').style.width = `${percentage(pending, total)}%`;
    bar.querySelector('.status-rejected').style.width = `${percentage(rejected, total)}%`;
    bar.querySelector('.status-other').style.width = `${percentage(other, total)}%`;
    bar.setAttribute('aria-label', `${number(granted)} granted, ${number(pending)} pending, ${number(rejected)} rejected, ${number(other)} in other states`);
  }

  function renderReadiness(planner) {
    const fra = planner.fra || {};
    const spatial = planner.spatial || {};
    const dss = planner.dss || {};
    const claims = Number(fra.claims || 0);
    const titles = Number(fra.active_titles || 0);
    const spatialized = Number(spatial.spatialized_claims || 0);
    const candidates = Number(dss.scheme_convergence?.potentially_eligible || 0);
    const insufficient = Number(dss.insufficient_data_cases || 0);
    const assetCount = Object.values(spatial.asset_distribution || {}).reduce((sum, value) => sum + Number(value || 0), 0);
    const missingFacts = (planner.missing_inputs || []).reduce((sum, item) => sum + Number(item.count || 0), 0);
    setText('#dashboardRightsReadiness', `${number(titles)} active titles · ${percentage(titles, claims)}% of cases`);
    setText('#dashboardRightsDetail', `${number(planner.claims_by_status?.granted || 0)} granted cases · ${number(fra.decisions)} recorded decisions`);
    setText('#dashboardSpatializedClaims', `${number(spatialized)} of ${number(claims)}`);
    setText('#dashboardVillagesCovered', number(spatial.villages_covered));
    setText('#dashboardSpatialHeadline', `${percentage(spatialized, claims)}% of claims have a usable boundary`);
    setText('#dashboardSpatialDetail', `${formatArea(spatial.fra_area_sqm)} claimed · ${formatArea(spatial.granted_area_sqm)} titled · ${number(spatial.villages_covered)} villages`);
    setText('#dashboardPlanningReadiness', `${number(candidates)} potential candidates · ${number(insufficient)} need more evidence`);
    setText('#dashboardPlanningDetail', `${number(assetCount)} reviewed assets · ${number(missingFacts)} missing fact observations`);
    setText('#dashboardPotentialCandidates', number(candidates));
    setText('#dashboardInsufficientCases', number(insufficient));
    document.querySelector('#dashboardSpatialBar').style.width = `${percentage(spatialized, claims)}%`;
  }

  function renderRightsMix(planner) {
    const counts = planner.claims_by_right_type || {};
    const total = Math.max(Number(planner.fra?.claims || 0), 1);
    ['IFR', 'CR', 'CFR'].forEach((rightType) => {
      const value = Number(counts[rightType] || 0);
      setText(`#dashboard${rightType}Count`, number(value));
      document.querySelector(`#dashboard${rightType}Bar`).style.width = `${percentage(value, total)}%`;
    });
  }

  function renderScope() {
    const values = context();
    const scope = [values.district, values.block, values.village].filter(Boolean);
    setText('#dashboardScope', scope.length ? `Tamil Nadu · ${scope.join(' · ')}` : 'Tamil Nadu · All available records');
  }

  async function load() {
    const current = requests.begin();
    const suffix = FRADashboardUI.query(context());
    const status = document.querySelector('#dashboardStatus');
    status.textContent = 'Refreshing programme briefing…';
    panel.setAttribute('aria-busy', 'true');
    renderScope();
    const plannerPromise = FRAApi.request(`/api/fra/dashboard/planner${suffix ? `?${suffix}` : ''}`);
    const verifierPromise = FRAApi.request(`/api/fra/dashboard/verifier${suffix ? `?${suffix}` : ''}`).catch((error) => error.status === 403 ? null : Promise.reject(error));
    try {
      const [planner, verifier] = await Promise.all([plannerPromise, verifierPromise]);
      if (!current()) return;
      renderPriorities(verifier, planner);
      renderPosition(planner);
      renderReadiness(planner);
      renderRightsMix(planner);
      status.textContent = 'Current data loaded';
    } catch (error) {
      if (current()) status.textContent = `The briefing could not be refreshed. ${error.message}`;
    } finally {
      if (current()) panel.removeAttribute('aria-busy');
    }
  }

  document.addEventListener('fra:section', (event) => { if (event.detail.section === 'dashboard') load(); });
  document.addEventListener('fra:context', () => { requests.invalidate(); if (!panel.hidden) load(); });
  document.querySelector('#refreshDashboard').addEventListener('click', load);
  document.querySelector('#dashboardBottleneckAction').addEventListener('click', () => { if (bottleneck) openWorkspace(bottleneck.section, bottleneck.item); });
  document.querySelectorAll('[data-overview-target]').forEach((button) => button.addEventListener('click', () => openWorkspace(button.dataset.overviewTarget)));
})();
