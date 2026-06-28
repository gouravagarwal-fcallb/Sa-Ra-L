import React, { useState, useEffect, Component } from 'react';
import { api, C, SH } from './api';
import StrategiesGrid from './pages/StrategiesGrid';
import StrategyDetail from './pages/StrategyDetail';
import BacktestsPage from './pages/BacktestsPage';
import DailyAnalysisPage from './pages/DailyAnalysisPage';
import ReadinessPage from './pages/ReadinessPage';
import PreMarketPage from './pages/PreMarketPage';

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

const NAV = [
  ['premarket', 'Pre-Market'],
  ['strategies', 'Strategies'],
  ['backtests', 'Backtests'],
  ['daily', 'Daily Analysis'],
  ['readiness', 'Readiness'],
];

export default function UnifiedApp() {
  const [page, setPage]   = useState('strategies');
  const [detail, setDetail] = useState(null);    // strategy name or null
  const [strategies, setStrategies] = useState([]);
  const [anyLive, setAnyLive] = useState(false);

  useEffect(() => {
    const load = () => api.strategies()
      .then(rows => { setStrategies(rows); setAnyLive(rows.some(r => r.runtime?.running && r.runtime?.mode === 'live')); })
      .catch(() => {});
    load(); const id = setInterval(load, 6000); return () => clearInterval(id);
  }, []);

  const meta = detail ? strategies.find(s => s.name === detail) : null;
  const open = (name) => { setDetail(name); };
  const back = () => setDetail(null);

  return (
    <ErrorBoundary>
      <div style={S.app}>
        <div style={S.header}>
          <span style={S.brand}>Sa-Ra-L <span style={{ color: C.dim, fontWeight: 400 }}>· Unified Control</span></span>
          <nav style={S.nav}>
            {NAV.map(([k, label]) => (
              <button key={k}
                      style={{ ...S.navBtn, ...((page === k && !detail) ? S.navOn : {}) }}
                      onClick={() => { setDetail(null); setPage(k); }}>{label}</button>
            ))}
          </nav>
          <button style={S.kill} onClick={() => { if (window.confirm('Stop ALL running strategies?')) api.stopAll(); }}>
            ⏹ STOP ALL
          </button>
        </div>

        {anyLive && <div style={S.liveBanner}>● LIVE — real-money orders are active. Use STOP ALL to halt.</div>}

        <div style={S.body}>
          {detail
            ? <StrategyDetail name={detail} meta={meta} onBack={back} />
            : page === 'premarket' ? <PreMarketPage />
            : page === 'strategies' ? <StrategiesGrid onOpen={open} />
            : page === 'backtests' ? <BacktestsPage onOpen={open} />
            : page === 'daily' ? <DailyAnalysisPage onOpen={open} />
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
  body: { padding: 18, maxWidth: 1500, margin: '0 auto' },
};
