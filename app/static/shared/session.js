/* Logout stays available even if a map or page-specific module fails to initialise. */
(() => {
  document.querySelectorAll('[data-logout]').forEach((button) => {
    button.addEventListener('click', async () => {
      if (button.disabled) return;
      if (window.UserPlotMap?.canLeave && !window.UserPlotMap.canLeave()) return;
      const label=button.textContent;button.disabled=true;button.textContent='Signing out…';
      try {
        const response=await fetch('/api/auth/logout',{method:'POST',credentials:'same-origin'});
        if(!response.ok && response.status!==401) throw new Error('Could not sign out. Please try again.');
        window.location.assign('/login');
      } catch(error) {
        window.UserPlotMap?.cancelLeave?.();
        const status=document.getElementById('status')||document.getElementById('staffStatus')||document.getElementById('workspaceStatus');
        if(status){status.textContent=error.message;status.dataset.error='true';}else window.alert(error.message);
        button.disabled=false;button.textContent=label;
      }
    });
  });
})();
