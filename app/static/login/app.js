(() => {
  const $ = (id) => document.getElementById(id);
  let mode = 'login';
  document.querySelectorAll('[data-mode]').forEach((button) => button.addEventListener('click', () => {
    mode = button.dataset.mode;
    document.querySelectorAll('[data-mode]').forEach((b) => b.setAttribute('aria-pressed', String(b === button)));
    $('nameGroup').hidden = mode !== 'register'; $('accountGroup').hidden = mode === 'staff'; $('staffGroup').hidden = mode !== 'staff';
    $('username').required = $('password').required = mode !== 'staff'; $('displayName').required = mode === 'register'; $('accessCode').required = mode === 'staff';
    $('password').minLength = mode === 'register' ? 10 : 1; $('password').autocomplete = mode === 'register' ? 'new-password' : 'current-password';
    $('loginButton').textContent = mode === 'register' ? 'Create my account' : 'Sign in'; $('loginStatus').textContent = '';
  }));
  $('loginForm').addEventListener('submit', async (event) => {
    event.preventDefault(); const button = $('loginButton'); const label = button.textContent;
    if (mode === 'register' && !$('displayName').value.trim()) { $('displayName').focus(); return; }
    const payload = mode === 'staff' ? { access_code: $('accessCode').value } : { username: $('username').value.trim().toLowerCase(), password: $('password').value, ...(mode === 'register' ? { display_name: $('displayName').value.trim() } : {}) };
    button.disabled = true; $('loginStatus').textContent = 'Signing in…';
    try {
      const response = await fetch(`/api/auth/${mode === 'staff' ? 'demo-login' : mode}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      const body = await response.json();
      if (!response.ok) throw new Error(typeof body.message === 'string' ? body.message : 'Check your account details and try again.');
      window.location.assign(body.redirect || '/fra');
    } catch (error) { $('loginStatus').textContent = error.message; }
    finally { button.disabled = false; button.textContent = label; }
  });
})();
