import React, { useState, useEffect, Component } from 'react';
import { api, C, SH } from './api';
import StrategiesGrid from './pages/StrategiesGrid';
import StrategyDetail from './pages/StrategyDetail';
import BacktestsPage from './pages/BacktestsPage';
import DailyAnalysisPage from './pages/DailyAnalysisPage';
import ReadinessPage from './pages/ReadinessPage';
import PreMarketPage from './pages/PreMarketPage';
import AboutPage from './pages/AboutPage';

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

const NAV = [
  ['premarket', 'Pre-Market'],
  ['strategies', 'Strategies'],
  ['backtests', 'Backtests'],
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
          <span style={S.brand}>Sa-Ra-L <span style={{ color: C.dim, fontWeight: 400 }}>· Unified Control</span></span>
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
  nav: { display: 'flex', gap: 4 },
  navBtn: { background: 'transparent', border: 'none', color: C.dim, fontSize: 14, fontWeight: 600, padding: '7px 14px', borderRadius: 6, cursor: 'pointer' },
  navOn: { background: '#e8f1fb', color: C.blue },
  kill: { marginLeft: 'auto', background: '#fff', border: `1px solid ${C.red}`, color: C.red, fontWeight: 700, fontSize: 12.5, padding: '6px 14px', borderRadius: 6, cursor: 'pointer' },
  liveBanner: { background: C.red, color: '#fff', fontWeight: 700, fontSize: 13, textAlign: 'center', padding: '6px', letterSpacing: 0.3 },
  errBanner: { background: '#fef2f2', color: '#b91c1c', border: '1px solid #fecaca', fontWeight: 700, fontSize: 12.5, padding: '8px 14px', cursor: 'pointer' },
  okBanner: { background: '#f0fdf4', color: '#15803d', border: '1px solid #bbf7d0', fontWeight: 700, fontSize: 12.5, padding: '8px 14px', cursor: 'pointer' },
  driftBanner: { background: '#fff7ed', color: '#b45309', border: '1px solid #fed7aa', fontWeight: 700, fontSize: 12.5, padding: '8px 14px' },
  body: { padding: 18, maxWidth: 1500, margin: '0 auto' },
};
