/** REST helpers for the unified Sa-Ra-L dashboard. */
const BASE = process.env.NODE_ENV === 'production' ? '' : 'http://localhost:8000';

async function j(path, opts) {
  const r = await fetch(BASE + path, opts);
  if (!r.ok) {
    // Surface the backend's actual error detail (FastAPI puts it in JSON {detail}),
    // not just the status text — e.g. capital validation / arm-live rejections.
    let detail = '';
    try {
      const ct = r.headers.get('content-type') || '';
      if (ct.includes('application/json')) { const b = await r.json(); detail = b.detail || b.reason || ''; }
    } catch (_) {}
    throw new Error(detail ? `${detail}` : `${r.status} ${r.statusText}`);
  }
  // If the request fell through to the SPA page-server we get back index.html
  // (starts with "<!doctype"). That means the running backend is older than this
  // route — surface a clear, actionable message instead of a raw JSON parse error.
  const ctype = r.headers.get('content-type') || '';
  if (!ctype.includes('application/json')) {
    const head = (await r.text()).slice(0, 40).replace(/\s+/g, ' ');
    if (head.toLowerCase().includes('<!doctype') || head.startsWith('<')) {
      throw new Error(
        `Backend route ${path.split('?')[0]} returned the web page, not data — ` +
        `the running server is out of date. Restart it: stop the dashboard ` +
        `(Ctrl-C) and run  python main.py --mode unified`
      );
    }
    throw new Error(`Unexpected non-JSON response from ${path.split('?')[0]}`);
  }
  return r.json();
}

export const api = {
  strategies:      ()        => j('/api/strategies'),
  snapshot:        (n)       => j(`/api/strategy/${n}/snapshot`),
  readiness:       (n)       => j(`/api/strategy/${n}/readiness`),
  backtestSummary: (n)       => j(`/api/strategy/${n}/backtest-summary`),
  backtests:       ()        => j('/api/backtests'),
  netBacktest:     ()        => j('/api/net-backtest'),
  version:         ()        => j('/api/version'),
  marketSummary:   ()        => j('/api/market/summary'),
  marketInternals: ()        => j('/api/market/internals'),
  news:            (limit=20) => j(`/api/news?limit=${limit}`),
  regimeCurrent:   ()        => j('/api/regime/current'),
  eodRun:          (day)     => j(`/api/eod/run${day ? `?day=${day}` : ''}`, { method:'POST' }),
  trustHistory:    (n)       => j(`/api/trust/${n}`),
  dailyClosure:    (day)     => j(`/api/daily-closure${day ? `?day=${day}` : ''}`),
  closureDates:    ()        => j('/api/daily-closure/dates'),
  closureExportUrl:(fmt, day)=> `${BASE}/api/daily-closure/export?format=${fmt}${day ? `&day=${day}` : ''}`,
  botsStatus:      ()        => j('/api/bots/status'),
  botsOutbound:    ()        => j('/api/bots/outbound'),
  botsInbound:     ()        => j('/api/bots/inbound'),
  contextEffective:()        => j('/api/context'),
  activity:        (cat)     => j(`/api/activity${cat && cat !== 'ALL' ? `?category=${cat}` : ''}`),
  runBacktest:     (n)       => j(`/api/strategy/${n}/run-backtest`, { method:'POST' }),
  backtestStatus:  (n)       => j(`/api/strategy/${n}/backtest-status`),
  equityCurveUrl:  (n)       => `${BASE}/api/strategy/${n}/equity-curve`,
  dailyAnalysis:   ()        => j('/api/daily-analysis'),
  premarket:       (force)   => j(`/api/premarket${force ? '?force=true' : ''}`),
  chart:           (inst, tf)=> j(`/api/market/${inst}/chart?tf=${tf}`),
  setCapital: (n, amount) => j(`/api/strategy/${n}/capital`, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({capital_allocated_rs: amount}) }),
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
