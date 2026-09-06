const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

test('successful staff sign-in enters the FRA platform', async () => {
  const elements = new Map();
  class Element {
    constructor() { this.value = ''; this.disabled = false; this.textContent = ''; this.listeners = {}; }
    addEventListener(type, listener) { this.listeners[type] = listener; }
    focus() {}
    select() {}
  }
  const element = (selector) => {
    if (!elements.has(selector)) elements.set(selector, new Element());
    return elements.get(selector);
  };
  element('#accessCode').value = '1234';
  let destination = null;
  const context = vm.createContext({
    document: { querySelector: element },
    fetch: async () => ({ ok: true }),
    window: { location: { assign: (url) => { destination = url; } } },
  });
  const source = fs.readFileSync(path.join(__dirname, '..', 'app', 'static', 'login', 'app.js'), 'utf8');
  vm.runInContext(source, context, { filename: 'login/app.js' });

  await element('#loginForm').listeners.submit({ preventDefault() {} });

  assert.equal(destination, '/fra');
});
