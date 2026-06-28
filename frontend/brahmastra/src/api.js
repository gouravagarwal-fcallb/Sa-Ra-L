/** REST helpers for the unified Sa-Ra-L dashboard. */
const BASE = process.env.NODE_ENV === 'production' ? '' : 'http://localhost:8000';

async function j(path, opts) {
  const r = await fetch(BASE + path, opts);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

export const api = {
  strategies:      ()        => j('/api/strategies'),
  snapshot:        (n)       => j(`/api/strategy/${n}/snapshot`),
  readiness:       (n)       => j(`/api/strategy/${n}/readiness`),
  backtestSummary: (n)       => j(`/api/strategy/${n}/backtest-summary`),
  backtests:       ()        => j('/api/backtests'),
  dailyAnalysis:   ()        => j('/api/daily-analysis'),
  chart:           (inst, tf)=> j(`/api/market/${inst}/chart?tf=${tf}`),
  run:   (n, mode='paper') => j(`/api/strategy/${n}/run`,  { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({mode}) }),
  stop:  (n)              => j(`/api/strategy/${n}/stop`, { method:'POST' }),
  armLive:     (n)        => j(`/api/strategy/${n}/arm-live`,     { method:'POST' }),
  confirmLive: (n, token, typed) => j(`/api/strategy/${n}/confirm-live`, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({token, typed_confirmation: typed}) }),
  stopAll: () => j('/api/control', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({command:'stop'}) }),
};

// Light, professional, soothing palette — white cards on a calm blue-grey,
// strong readable text, vivid-but-not-harsh accents.
export const C = {
  bg:'#eef2f8',        // soft blue-grey background (soothing)
  panel:'#ffffff',     // white cards
  panel2:'#f4f7fc',    // very light nested surface
  border:'#dde5ef',    // soft border
  text:'#1f2a3a',      // strong dark slate — high contrast, not harsh black
  dim:'#5b6b82',       // medium slate (still clearly readable)
  green:'#16a34a', red:'#dc2626', amber:'#d97706',
  blue:'#2563eb', cyan:'#0e7aa6', purple:'#7c3aed',
  // chart line colors chosen for contrast on white
  line:'#2563eb', band:'#d97706', mid:'#94a3b8',
};

// Soft shadows for a polished, professional card look.
export const SH = {
  card: '0 1px 3px rgba(15,23,42,0.08), 0 1px 2px rgba(15,23,42,0.04)',
  raised: '0 4px 12px rgba(15,23,42,0.10)',
};
