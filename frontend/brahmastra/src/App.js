import React, { useState, useEffect, useCallback, Component } from 'react';
import { connect, disconnect } from './ws';
import Header         from './components/Header';
import ScenarioPanel  from './components/ScenarioPanel';
import IndicatorPanel from './components/IndicatorPanel';
import TradePanel     from './components/TradePanel';
import LogStream      from './components/LogStream';
import EquityChart    from './components/EquityChart';
import ControlPanel   from './components/ControlPanel';
import PreMarketPanel from './components/PreMarketPanel';
import MultiTFPanel   from './components/MultiTFPanel';
import ConfluenceBar  from './components/ConfluenceBar';
import OptionsPanel   from './components/OptionsPanel';
import NarratorPanel  from './components/NarratorPanel';
import PendingSignalPanel from './components/PendingSignalPanel';

class ErrorBoundary extends Component {
  constructor(props) { super(props); this.state = { error: null }; }
  static getDerivedStateFromError(e) { return { error: e }; }
  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: 40, color: '#ef4444', fontFamily: 'monospace', background: '#0a0e1a', minHeight: '100vh' }}>
          <div style={{ fontSize: 18, fontWeight: 700, marginBottom: 16 }}>BRAHMASTRA — Render Error</div>
          <pre style={{ fontSize: 12, color: '#f87171', whiteSpace: 'pre-wrap' }}>
            {this.state.error.toString()}{'\n'}{this.state.error.stack}
          </pre>
          <div style={{ marginTop: 16, fontSize: 11, color: '#64748b' }}>
            Open browser console (F12) for full details. Refresh to retry.
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

const RECONNECT_BANNER_MS = 3000;

export default function App() {
  const [state, setState]             = useState(null);
  const [wsStatus, setWsStatus]       = useState('CONNECTING');
  const [lastUpdate, setLastUpdate]   = useState(null);
  const [layout, setLayout]           = useState('full'); // 'full'|'left'|'right'|'trade'|'log'|'pre'|'narrator'
  const [narratorData, setNarratorData] = useState({});   // { NIFTY: [{...with detail}], SENSEX: [...] }
  const [pendingSignals, setPendingSignals] = useState({});

  const onEvent = useCallback((msg) => {
    if (msg.type === 'snapshot') {
      setState({ ...msg.data });
      setNarratorData(msg.data.narrator || {});
      setPendingSignals(msg.data.pending_signals || {});
      setLastUpdate(Date.now());
      setWsStatus('LIVE');
    } else if (msg.type === 'update') {
      setState(prev => ({ ...(prev || {}), ...msg.data }));
      setLastUpdate(Date.now());
      setWsStatus('LIVE');
    } else if (msg.type === 'tick') {
      setState(prev => {
        if (!prev) return prev;
        return {
          ...prev,
          ticks: { ...(prev.ticks || {}), ...msg.data },
        };
      });
    } else if (msg.type === 'narrator') {
      setNarratorData(prev => {
        const inst     = msg.instrument;
        const existing = prev[inst] || [];
        const entry    = { ...msg.data, instrument: inst };
        return { ...prev, [inst]: [entry, ...existing].slice(0, 20) };
      });
    } else if (msg.type === 'pending_signals') {
      setPendingSignals(msg.data || {});
    } else if (msg.type === 'scenarios') {
      setState(prev => prev ? { ...prev, scenarios: { ...(prev.scenarios || {}), [msg.instrument]: msg.data } } : prev);
    } else if (msg.type === 'indicators') {
      setState(prev => prev ? { ...prev, indicators: { ...(prev.indicators || {}), [msg.instrument]: msg.data } } : prev);
    } else if (msg.type === 'trade') {
      // Refresh snapshot on trade events
      setState(prev => prev ? { ...prev, _trade_event: Date.now() } : prev);
    } else if (msg.type === 'log') {
      setState(prev => prev ? { ...prev, log_lines: [...(prev.log_lines || []).slice(-200), msg.data] } : prev);
    } else if (msg.type === 'session') {
      setState(prev => prev ? { ...prev, session: { ...(prev.session || {}), ...msg.data } } : prev);
    } else if (msg.type === 'approved' || msg.type === 'rejected') {
      setPendingSignals(prev => {
        const next = { ...prev };
        delete next[msg.instrument];
        return next;
      });
    } else if (msg.type === 'pong') {
      // keep-alive — no state update needed
    }
  }, []);

  useEffect(() => {
    connect(onEvent);
    return () => disconnect();
  }, [onEvent]);

  // Detect stale connection (no update > 5s during market hours)
  useEffect(() => {
    const interval = setInterval(() => {
      if (lastUpdate && Date.now() - lastUpdate > 5000) {
        setWsStatus('STALE');
      }
    }, 2000);
    return () => clearInterval(interval);
  }, [lastUpdate]);

  const session      = state?.session      ?? {};
  const ticks        = state?.ticks        ?? {};
  const scenarios    = state?.scenarios    ?? {};
  const indicators   = state?.indicators   ?? {};
  const openTrades   = state?.open_trades  ?? {};
  const closedTrades = state?.closed_trades ?? [];
  const logs         = state?.log_lines    ?? [];

  return (
    <ErrorBoundary>
    <div style={styles.app}>
      <Header session={session} ticks={ticks} />

      {/* Connection status bar */}
      <StatusBar status={wsStatus} lastUpdate={lastUpdate} layout={layout} setLayout={setLayout} />

      <div style={styles.body}>

        {/* ── FULL layout: control+pre on top, signals left, trading right ── */}
        {layout === 'full' && (
          <>
            <div style={styles.leftCol}>
              <PreMarketPanel session={session} ticks={ticks} />
              <ScenarioPanel scenarios={scenarios} />
              <ConfluenceBar indicators={indicators} />
              <MultiTFPanel  indicators={indicators} />
              <OptionsPanel  indicators={indicators} />
              <IndicatorPanel indicators={indicators} />
            </div>
            <div style={styles.rightCol}>
              <ControlPanel session={session} />
              <PendingSignalPanel pendingSignals={pendingSignals} executionMode={session.execution_mode} />
              <NarratorPanel narrator={narratorData} />
              <EquityChart closedTrades={closedTrades} />
              <TradePanel openTrades={openTrades} closedTrades={closedTrades} />
              <LogStream logs={logs} />
            </div>
          </>
        )}

        {/* ── SIGNALS layout: all signal/analysis panels ── */}
        {layout === 'left' && (
          <div style={styles.fullCol}>
            <ScenarioPanel scenarios={scenarios} />
            <ConfluenceBar indicators={indicators} />
            <MultiTFPanel  indicators={indicators} />
            <OptionsPanel  indicators={indicators} />
            <IndicatorPanel indicators={indicators} />
          </div>
        )}

        {/* ── TRADING layout: control + trades + logs ── */}
        {layout === 'right' && (
          <div style={styles.fullCol}>
            <ControlPanel session={session} />
            <PendingSignalPanel pendingSignals={pendingSignals} executionMode={session.execution_mode} />
            <NarratorPanel narrator={narratorData} />
            <EquityChart closedTrades={closedTrades} />
            <TradePanel openTrades={openTrades} closedTrades={closedTrades} />
            <LogStream logs={logs} />
          </div>
        )}

        {/* ── PRE-MARKET layout ── */}
        {layout === 'pre' && (
          <div style={styles.fullCol}>
            <PreMarketPanel session={session} ticks={ticks} />
            <ConfluenceBar  indicators={indicators} />
            <MultiTFPanel   indicators={indicators} />
          </div>
        )}

        {/* ── TRADES layout ── */}
        {layout === 'trade' && (
          <div style={styles.fullCol}>
            <ControlPanel session={session} />
            <TradePanel openTrades={openTrades} closedTrades={closedTrades} />
            <EquityChart closedTrades={closedTrades} />
          </div>
        )}

        {/* ── LOG layout ── */}
        {layout === 'log' && (
          <div style={styles.fullCol}>
            <LogStream logs={logs} />
          </div>
        )}

        {/* ── NARRATOR layout ── */}
        {layout === 'narrator' && (
          <div style={styles.fullCol}>
            <PendingSignalPanel pendingSignals={pendingSignals} executionMode={session.execution_mode} />
            <NarratorPanel narrator={narratorData} />
          </div>
        )}

      </div>
    </div>
    </ErrorBoundary>
  );
}

function StatusBar({ status, lastUpdate, layout, setLayout }) {
  const color = status === 'LIVE' ? '#22c55e' : status === 'STALE' ? '#f59e0b' : '#ef4444';
  const age   = lastUpdate ? Math.round((Date.now() - lastUpdate) / 1000) : null;

  return (
    <div style={styles.statusBar}>
      <div style={styles.statusLeft}>
        <span style={{ ...styles.dot, background: color }} />
        <span style={{ fontSize: 11, color, fontWeight: 700, letterSpacing: 1 }}>{status}</span>
        {age != null && (
          <span style={{ fontSize: 11, color: '#475569' }}>last update {age}s ago</span>
        )}
      </div>
      <div style={styles.layoutBtns}>
        {[['full', 'FULL'], ['pre', 'PRE-MKT'], ['left', 'SIGNALS'], ['right', 'TRADING'], ['trade', 'TRADES'], ['log', 'LOGS'], ['narrator', 'NARRATOR']].map(
          ([key, label]) => (
            <button key={key}
                    style={{ ...styles.layoutBtn, ...(layout === key ? styles.layoutBtnActive : {}) }}
                    onClick={() => setLayout(key)}>
              {label}
            </button>
          )
        )}
      </div>
    </div>
  );
}

const styles = {
  app: {
    display: 'flex', flexDirection: 'column',
    minHeight: '100vh',
    background: '#0a0e1a',
    color: '#e2e8f0',
    fontFamily: '"Courier New", monospace',
  },
  statusBar: {
    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    padding: '4px 16px',
    background: '#020617',
    borderBottom: '1px solid #0f172a',
  },
  statusLeft: { display: 'flex', alignItems: 'center', gap: 8 },
  dot: { width: 6, height: 6, borderRadius: '50%', display: 'inline-block' },
  layoutBtns: { display: 'flex', gap: 4 },
  layoutBtn: {
    background: 'transparent', border: '1px solid #1e293b',
    color: '#475569', fontSize: 11, fontWeight: 700,
    padding: '3px 10px', borderRadius: 3, cursor: 'pointer', letterSpacing: 1,
  },
  layoutBtnActive: { borderColor: '#64748b', color: '#cbd5e1' },
  body: {
    display: 'flex', gap: 10, padding: 10,
    flex: 1, overflow: 'auto',
  },
  leftCol: {
    display: 'flex', flexDirection: 'column', gap: 10,
    flex: '0 0 55%', overflowY: 'auto',
  },
  rightCol: {
    display: 'flex', flexDirection: 'column', gap: 10,
    flex: 1, overflowY: 'auto',
  },
  fullCol: {
    display: 'flex', flexDirection: 'column', gap: 10,
    flex: 1, overflowY: 'auto',
  },
};
