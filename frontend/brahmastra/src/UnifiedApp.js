import React, { useState, useEffect, Component } from 'react';
import { api, C, SH } from './api';
import StrategiesGrid from './pages/StrategiesGrid';
import StrategyDetail from './pages/StrategyDetail';
import BacktestsPage from './pages/BacktestsPage';
import DailyAnalysisPage from './pages/DailyAnalysisPage';
import ReadinessPage from './pages/ReadinessPage';
import PreMarketPage from './pages/PreMarketPage';
import AboutPage from './pages/AboutPage';
import ActivityPage from './pages/ActivityPage';
import ClosurePage from './pages/ClosurePage';

class ErrorBoundary extends Component {
  constructor(p) { super(p); this.state = { error: null }; }
  static getDerivedStateFromError(e) { return { error: e }; }
  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: 40, color: C.red, fontFamily: 'monospace', background: C.bg, minHeight: '100vh' }}>
          <div style={{ fontSize: 18, fontWeight: 700, marginBottom: 16 }}>Sa-Ra-L — Render Error</div>
          <pre style={{ fontSize: 12, whiteSpace: 'pre-wrap' }}>{String(this.state.error?.stack || this.state.error)}</pre>
        </div>
      );
    }
    return this.props.children;
  }
}

function LiveClock() {
  const [t, setT] = useState('');
  useEffect(() => {
    const fmt = () => {
      const d = new Date();
      const p = (n, w = 2) => String(n).padStart(w, '0');
      // IST clock with millisecond precision (HH:MM:SS.mmm)
      const ist = new Date(d.getTime() + (d.getTimezoneOffset() + 330) * 60000);
      setT(`${p(ist.getHours())}:${p(ist.getMinutes())}:${p(ist.getSeconds())}.${p(ist.getMilliseconds(), 3)}`);
    };
    fmt();
    const id = setInterval(fmt, 50);
    return () => clearInterval(id);
  }, []);
  return (
    <span style={{ fontFamily: 'monospace', fontSize: 13, color: C.cyan, fontWeight: 700, letterSpacing: 0.3 }}
          title="IST clock (millisecond precision)">
      {t} <span style={{ color: C.dim, fontWeight: 400 }}>IST</span>
    </span>
  );
}

function MarketTicker() {
  const [m, setM] = useState(null);
  useEffect(() => {
    let alive = true;
    const load = () => api.marketSummary().then(d => { if (alive) setM(d); }).catch(() => {});
    load(); const id = setInterval(load, 5000);
    return () => { alive = false; clearInterval(id); };
  }, []);
  if (!m) return null;
  const Quote = ({ label, q }) => {
    if (!q || q.ltp == null) return null;
    const up = (q.change || 0) >= 0;
    const col = (q.change || 0) === 0 ? C.dim : up ? C.green : C.red;
    return (
      <span style={{ display: 'inline-flex', alignItems: 'baseline', gap: 5, marginRight: 16 }}>
        <span style={{ fontWeight: 700, color: C.text, fontSize: 12.5 }}>{label}</span>
        <span style={{ fontWeight: 700, color: C.text, fontSize: 13 }}>
          {q.ltp.toLocaleString('en-IN', { maximumFractionDigits: 2 })}</span>
        <span style={{ color: col, fontWeight: 700, fontSize: 11.5 }}>
          {up ? '▲' : '▼'} {Math.abs(q.change).toLocaleString('en-IN', { maximumFractionDigits: 2 })}
          {q.change_pct != null ? ` (${up ? '+' : ''}${q.change_pct}%)` : ''}</span>
      </span>
    );
  };
  return (
    <div style={S.ticker}>
      <Quote label="NIFTY" q={m.nifty} />
      <Quote label="SENSEX" q={m.sensex} />
      {/* VIX coloured like any quote — up = green, down = red — so it matches the
          broker terminal and is consistent with NIFTY/SENSEX beside it. */}
      <Quote label="VIX" q={m.vix != null ? { ltp: m.vix, change: m.vix_change } : null} />
      <span style={{ marginLeft: 'auto', fontSize: 10, color: C.dim }}>
        {m.source === 'kite' ? 'live · Kite' : m.source === 'yfinance' ? 'delayed · Yahoo' : 'no feed'}</span>
    </div>
  );
}

const NAV = [
  ['premarket', 'Pre-Market'],
  ['strategies', 'Strategies'],
  ['backtests', 'Backtests'],
  ['activity', 'Activity'],
  ['closure', 'Closure Report'],
  ['daily', 'Daily Analysis'],
  ['readiness', 'Readiness'],
  ['about', 'About'],
];

export default function UnifiedApp() {
  const [page, setPage]   = useState('strategies');
  const [detail, setDetail] = useState(null);    // strategy name or null
  const [strategies, setStrategies] = useState([]);
  const [anyLive, setAnyLive] = useState(false);
  const [loadErr, setLoadErr] = useState(null);   // /api/strategies failing
  const [stopMsg, setStopMsg] = useState(null);   // STOP ALL result/error banner
  const [stopping, setStopping] = useState(false);
  const [drift, setDrift] = useState(false);      // running server older than this page

  const load = React.useCallback(() => api.strategies()
    .then(rows => {
      setStrategies(rows);
      setAnyLive(rows.some(r => r.runtime?.running && r.runtime?.mode === 'live'));
      setLoadErr(null);
    })
    .catch(e => setLoadErr(String(e))), []);

  useEffect(() => {
    load(); const id = setInterval(load, 6000); return () => clearInterval(id);
  }, [load]);

  // Version-drift guard: if the JS bundle the server serves differs from the one
  // this page actually loaded, the running server is out of date — warn loudly.
  useEffect(() => {
    let alive = true;
    api.version().then(v => {
      if (!alive || !v?.bundle) return;
      const loaded = Array.from(document.querySelectorAll('script[src]'))
        .map(s => s.getAttribute('src')).find(s => s && s.includes('/static/js/main.'));
      if (loaded && v.bundle && !loaded.includes(v.bundle.split('/').pop())) setDrift(true);
    }).catch(() => {});
    return () => { alive = false; };
  }, []);

  const stopAll = () => {
    if (!window.confirm('Stop ALL running strategies?')) return;
    setStopping(true); setStopMsg(null);
    api.stopAll()
      .then(() => { setStopMsg({ ok: true, text: 'STOP ALL sent — verifying…' }); return load(); })
      .then(() => setStopMsg({ ok: true, text: 'STOP ALL acknowledged. Confirm each card shows idle.' }))
      .catch(e => setStopMsg({ ok: false, text: 'STOP ALL FAILED: ' + String(e) + ' — retry, or Ctrl-C the server NOW.' }))
      .finally(() => setStopping(false));
  };

  const meta = detail ? strategies.find(s => s.name === detail) : null;
  const open = (name) => { setDetail(name); };
  const back = () => setDetail(null);

  return (
    <ErrorBoundary>
      <div style={S.app}>
        <div style={S.header}>
          <span style={S.brand}><span style={S.gold}>The Wealth Fortress</span> <span style={{ color: C.dim, fontWeight: 400 }}>· Sa-Ra-L</span></span>
          <LiveClock />
          <nav style={S.nav}>
            {NAV.map(([k, label]) => (
              <button key={k}
                      style={{ ...S.navBtn, ...((page === k && !detail) ? S.navOn : {}) }}
                      onClick={() => { setDetail(null); setPage(k); }}>{label}</button>
            ))}
          </nav>
          <button style={{ ...S.kill, opacity: stopping ? 0.6 : 1 }} disabled={stopping} onClick={stopAll}>
            {stopping ? '⏳ STOPPING…' : '⏹ STOP ALL'}
          </button>
        </div>

        <MarketTicker />
        {drift && <div style={S.driftBanner}>⚠ The running server is OLDER than this page — restart it (Ctrl-C, then <code>python main.py --mode unified</code>) so controls match the backend.</div>}
        {stopMsg && <div style={stopMsg.ok ? S.okBanner : S.errBanner} onClick={() => setStopMsg(null)}>{stopMsg.text} <span style={{ float: 'right', cursor: 'pointer' }}>✕</span></div>}
        {loadErr && <div style={S.errBanner}>Dashboard data error: {loadErr} — the backend may be down or restarting.</div>}
        {anyLive && <div style={S.liveBanner}>● LIVE — real-money orders are active. Use STOP ALL to halt.</div>}

        <div style={S.body}>
          {detail
            ? <StrategyDetail name={detail} meta={meta} onBack={back} />
            : page === 'premarket' ? <PreMarketPage onOpen={open} />
            : page === 'strategies' ? <StrategiesGrid onOpen={open} />
            : page === 'backtests' ? <BacktestsPage onOpen={open} />
            : page === 'activity' ? <ActivityPage onOpen={open} />
            : page === 'closure' ? <ClosurePage />
            : page === 'daily' ? <DailyAnalysisPage onOpen={open} />
            : page === 'about' ? <AboutPage />
            : <ReadinessPage onOpen={open} />}
        </div>
      </div>
    </ErrorBoundary>
  );
}

const S = {
  app: { minHeight: '100vh', background: C.bg, color: C.text, fontFamily: '"Segoe UI", system-ui, -apple-system, sans-serif' },
  header: { display: 'flex', alignItems: 'center', gap: 22, padding: '12px 22px', background: C.panel, borderBottom: `1px solid ${C.border}`, boxShadow: SH.card, position: 'sticky', top: 0, zIndex: 50 },
  brand: { fontSize: 17, fontWeight: 800, letterSpacing: 0.4, color: C.text },
  // Metallic gold for the product name (UI_FE_Pg2 item 1). Gradient clip for the
  // sheen; a solid-gold color fallback for engines without background-clip:text.
  gold: {
    color: '#b8860b',
    backgroundImage: 'linear-gradient(92deg,#9a6f08 0%,#caa12a 28%,#f4e08a 50%,#caa12a 72%,#9a6f08 100%)',
    WebkitBackgroundClip: 'text', backgroundClip: 'text',
    WebkitTextFillColor: 'transparent',
    fontWeight: 900, letterSpacing: 0.5,
  },
  nav: { display: 'flex', gap: 4 },
  navBtn: { background: 'transparent', border: 'none', color: C.dim, fontSize: 14, fontWeight: 600, padding: '7px 14px', borderRadius: 6, cursor: 'pointer' },
  navOn: { background: '#e8f1fb', color: C.blue },
  kill: { marginLeft: 'auto', background: '#fff', border: `1px solid ${C.red}`, color: C.red, fontWeight: 700, fontSize: 12.5, padding: '6px 14px', borderRadius: 6, cursor: 'pointer' },
  liveBanner: { background: C.red, color: '#fff', fontWeight: 700, fontSize: 13, textAlign: 'center', padding: '6px', letterSpacing: 0.3 },
  ticker: { display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 4, background: C.panel, borderBottom: `1px solid ${C.border}`, padding: '6px 18px' },
  errBanner: { background: '#fef2f2', color: '#b91c1c', border: '1px solid #fecaca', fontWeight: 700, fontSize: 12.5, padding: '8px 14px', cursor: 'pointer' },
  okBanner: { background: '#f0fdf4', color: '#15803d', border: '1px solid #bbf7d0', fontWeight: 700, fontSize: 12.5, padding: '8px 14px', cursor: 'pointer' },
  driftBanner: { background: '#fff7ed', color: '#b45309', border: '1px solid #fed7aa', fontWeight: 700, fontSize: 12.5, padding: '8px 14px' },
  body: { padding: 18, maxWidth: 1500, margin: '0 auto' },
};
