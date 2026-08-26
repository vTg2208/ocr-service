(() => {
  const form = document.querySelector('#loginForm');
  const input = document.querySelector('#accessCode');
  const button = document.querySelector('#loginButton');
  const status = document.querySelector('#loginStatus');

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    status.textContent = '';
    if (!/^\d{4}$/.test(input.value)) {
      status.textContent = 'Enter the four-digit access code.';
      input.focus();
      return;
    }
    button.disabled = true;
    button.textContent = 'Signing in…';
    try {
      const response = await fetch('/api/auth/demo-login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ access_code: input.value }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.message || 'The access code was not accepted.');
      }
      window.location.assign('/land-mapping');
    } catch (error) {
      status.textContent = error.message;
      input.select();
      button.disabled = false;
      button.textContent = 'Continue';
    }
  });
})();
