const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function deferred() { let resolve; let reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
async function flush() { for (let i = 0; i < 8; i++) await new Promise(setImmediate); }

function browser(scripts, { workspace = false, root = process.env.FRA_UI_TEST_ROOT || path.join(__dirname, '..', 'app', 'static', 'fra') } = {}) {
  const nodes = new Map(); const requests = []; const listeners = new Map();
  class Element {
    constructor(tag = 'div') { this.tagName = tag; this.children = []; this.dataset = {}; this.style = {}; this.value = ''; this.hidden = false; this.disabled = false; this.events = new Map(); this.attributes = {}; this._text = ''; this.classList = { add() {}, toggle() {}, remove() {} }; this.elements = new Proxy({}, { get: (target, key) => target[key] ||= new Element('input') }); }
    set textContent(value) { this._text = String(value); this.children = []; }
    get textContent() { return this._text + this.children.map((child) => child.textContent ?? child).join(''); }
    append(...children) { this.children.push(...children); }
    appendChild(child) { this.append(child); return child; }
    replaceChildren(...children) { this._text = ''; this.children = [...children]; if (this.tagName === 'select') this.value = children[0]?.value || ''; }
    add(child) { this.append(child); }
    setAttribute(key, value) { this.attributes[key] = value; }
    removeAttribute(key) { delete this.attributes[key]; }
    addEventListener(type, callback) { if (!this.events.has(type)) this.events.set(type, []); this.events.get(type).push(callback); }
    emit(type, extra = {}) { return Promise.all((this.events.get(type) || []).map((callback) => callback({ type, target: this, currentTarget: this, preventDefault() {}, ...extra }))); }
    click() { return this.emit('click'); }
    querySelector(selector) { return node(selector); }
    querySelectorAll(selector) { return selector.includes('layers') ? [{ value: 'claim' }, { value: 'asset' }] : []; }
    closest() { return new Element(); }
    focus() {}
    reset() { this.wasReset = true; }
  }
  function node(selector) { if (!nodes.has(selector)) nodes.set(selector, new Element()); return nodes.get(selector); }
  const document = {
    querySelector: node, querySelectorAll: (selector) => selector === '.reviewer-case-action' ? [node('button')] : [], createElement: (tag) => new Element(tag),
    createTextNode: (text) => ({ textContent: text }),
    addEventListener(type, callback) { if (!listeners.has(type)) listeners.set(type, []); listeners.get(type).push(callback); },
    dispatchEvent(event) { for (const callback of listeners.get(event.type) || []) callback(event); },
  };
  const context = vm.createContext({ URLSearchParams, setTimeout, clearTimeout,
    crypto: { randomUUID: () => 'test-request-id' }, fetch: () => Promise.resolve(),
    history: { replaceState() {} }, location: { hash: '' }, window: { location: { assign() {} }, open() {} },
    CustomEvent: function(type, options) { return { type, ...options }; },
    Option: function(text, value) { const option = new Element('option'); option.textContent = text; option.value = value; return option; },
    requestStub: { request(url, options) { const request = { url, options, ...deferred() }; requests.push(request); return request.promise; }, json(method, body) { return { method, headers: {}, body: JSON.stringify(body) }; } },
  });
  function run(name) { vm.runInContext(fs.readFileSync(path.join(root, name), 'utf8'), context, { filename: name }); }
  run('api.js'); vm.runInContext('FRAApi.request = requestStub.request;', context);
  if (workspace) { run('archive.js'); context.document = document; run('app.js'); }
  else { run('app.js'); context.document = document; }
  for (const script of scripts) run(script);
  return { node, document, context, requests, emit: (type, detail) => document.dispatchEvent({ type, detail }),
    pending(prefix) { return requests.filter((r) => r.url.startsWith(prefix)); } };
}

module.exports = { browser, flush };
