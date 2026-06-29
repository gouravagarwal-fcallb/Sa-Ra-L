import React from 'react';
import ReactDOM from 'react-dom/client';
import UnifiedApp from './UnifiedApp';

// Global safety net: never let a failure vanish silently during market hours.
// Surface unhandled promise rejections / errors as a dismissable banner so the
// operator knows the dashboard hit a problem instead of trusting stale data.
function flash(msg) {
  try {
    let bar = document.getElementById('saral-global-error');
    if (!bar) {
      bar = document.createElement('div');
      bar.id = 'saral-global-error';
      bar.style.cssText = 'position:fixed;bottom:0;left:0;right:0;z-index:99999;background:#7f1d1d;color:#fff;'
        + 'font:600 12.5px system-ui;padding:8px 14px;cursor:pointer;box-shadow:0 -2px 8px rgba(0,0,0,.25)';
      bar.onclick = () => bar.remove();
      document.body.appendChild(bar);
    }
    bar.textContent = '⚠ ' + msg + '  (click to dismiss — verify trades on the broker if unsure)';
  } catch (_) {}
}
window.addEventListener('unhandledrejection', (e) => flash('Unhandled error: ' + (e.reason?.message || e.reason)));
window.addEventListener('error', (e) => { if (e.message) flash('Error: ' + e.message); });

const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(<React.StrictMode><UnifiedApp /></React.StrictMode>);
