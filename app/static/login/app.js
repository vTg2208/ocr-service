(() => {
  const $ = (id) => document.getElementById(id);
  let mode = 'login';
  fetch('/api/auth/config').then(response=>{if(!response.ok)throw new Error('Sign-in configuration unavailable');return response.json();}).then(config=>{
    $('oidcSignIn').hidden=!config.oidc_enabled;
    if(config.oidc_enabled)$('oidcSignInLink').href=config.oidc_login_url;
    document.querySelectorAll('[data-mode]').forEach(button=>{button.hidden=button.dataset.mode==='staff'?!config.demo_auth_enabled:!config.local_auth_enabled;});
    if(!config.local_auth_enabled){$('loginForm').hidden=true;document.querySelector('.login-modes').hidden=true;}
  }).catch(()=>{$('loginStatus').textContent='Sign-in settings could not be loaded. Refresh to retry.';});
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
