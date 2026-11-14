const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const flush = async () => { for (let i = 0; i < 6; i++) await new Promise(setImmediate); };

function harness(role = 'user') {
  const all = [], events = {}, requests = [], timers = new Map(); let nextTimer = 0;
  class Element {
    constructor(tag) { this.tagName = tag; this.children = []; this.dataset = {}; this.attributes = {}; this.listeners = {}; this.value = ''; this.open = false; all.push(this); }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children = nodes; }
    setAttribute(key, value) { this.attributes[key] = value; }
    addEventListener(type, fn) { this.listeners[type] = fn; }
    add(option) { this.append(option); if (!this.value) this.value = option.value; }
    focus() { this.focused = true; }
    showModal() { this.open = true; }
    close() { this.open = false; this.listeners.close?.(); }
    closest() { return this; }
  }
  const body = new Element('body');
  const document = { body, createElement: tag => new Element(tag), addEventListener: (type, fn) => events[type] = fn,
    dispatchEvent: event => events[event.type]?.(event) };
  const state = { items: [] };
  const api = { json: (method, data) => ({ method, data }), request: async (url, options) => {
    requests.push({ url, options });
    if (url === '/api/auth/session') return { role };
    if (url.endsWith('/review')) { state.items[0].review_state = options.data.state; return state.items[0]; }
    return state;
  } };
  const context = vm.createContext({ document, FRAApi: api, Date, Option: function(text, value) { this.textContent = text; this.value = value; },
    FormData: class { constructor(form) { this.form = form; } *[Symbol.iterator]() { for (const label of this.form.children) for (const input of label.children || []) if (input.name) yield [input.name, input.value]; } },
    CustomEvent: class { constructor(type) { this.type = type; } },
    setTimeout(fn) { const id = ++nextTimer; timers.set(id, fn); return id; }, clearTimeout(id) { timers.delete(id); } });
  vm.runInContext(fs.readFileSync('app/static/shared/land-cover.js', 'utf8'), context);
  const button = new Element('button'); button.dataset.landCoverEndpoint = '/api/user/plots/plot-1/land-cover';
  return { all, events, requests, state, timers, button,
    open: async () => { events.click({ target: button }); await flush(); },
    find: (tag, text) => all.find(e => e.tagName === tag && (text == null || e.textContent === text)) };
}

test('opening satellite evidence reads current records and submission sends selected dates', async () => {
  const h = harness(); await h.open();
  assert.equal(h.requests[0].url, '/api/user/plots/plot-1/land-cover');
  assert.equal(h.requests.filter(r => r.options?.method === 'POST').length, 0);
  h.all.find(e => e.name === 'start_date').value = '2026-01-01';
  h.all.find(e => e.name === 'end_date').value = '2026-01-31';
  await h.find('form').onsubmit({ preventDefault() {} });
  const request = h.requests.find(r => r.options?.method === 'POST');
  assert.equal(request.options.data.start_date, '2026-01-01');
  assert.equal(request.options.data.end_date, '2026-01-31');
  assert.equal(h.find('button', 'Request analysis').disabled, false);
});

test('queued results poll and closing the dialog cancels the refresh and restores focus', async () => {
  const h = harness(); h.state.items = [{ state: 'queued', review_state: 'unverified', model_version: 'baseline', summary: {} }];
  await h.open(); assert.equal(h.timers.size, 1);
  h.find('dialog').close(); assert.equal(h.timers.size, 0); assert.equal(h.button.focused, true);
});

test('only staff get final review controls with an explicit note and revision', async () => {
  function result() { return { id: 'analysis-1', state: 'completed', review_state: 'unverified', revision: 2, model_version: 'baseline', summary: {}, artifacts: {}, report_url: '/report', geojson_url: '/geojson' }; }
  const user = harness(); user.state.items = [result()]; await user.open();
  assert.equal(user.find('button', 'Record review'), undefined);
  const staff = harness('reviewer'); staff.state.items = [result()]; await staff.open();
  const review = staff.all.filter(e => e.tagName === 'form')[1];
  const note = staff.find('textarea'); assert.equal(note.required, true); note.value = 'Compared source scene and boundary';
  await review.onsubmit({ preventDefault() {} });
  const request = staff.requests.find(r => r.url.endsWith('/review'));
  assert.equal(request.options.data.expected_revision, 2);
  assert.equal(request.options.data.note, note.value);
  assert.equal(request.options.data.state, 'verified');
});

test('FRA, personal, and staff workspaces load the shared satellite module', () => {
  for (const directory of ['fra', 'user', 'staff-services']) {
    const html = fs.readFileSync(`app/static/${directory}/index.html`, 'utf8');
    assert.match(html, /\/static\/shared\/land-cover.js/);
    assert.match(html, /\/static\/shared\/land-cover.css/);
  }
});
