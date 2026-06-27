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

export const C = {
  bg:'#0a0e1a', panel:'#0f1626', panel2:'#131c30', border:'#1e293b',
  text:'#e2e8f0', dim:'#64748b', green:'#22c55e', red:'#ef4444',
  amber:'#f59e0b', blue:'#3b82f6', cyan:'#22d3ee', purple:'#a78bfa',
};
