import React, { useState, useEffect, useCallback, Component } from 'react';
import { connect, disconnect } from './ws';
import Header      from './components/Header';
import ScenarioPanel  from './components/ScenarioPanel';
import IndicatorPanel from './components/IndicatorPanel';
import TradePanel     from './components/TradePanel';
import LogStream      from './components/LogStream';
import EquityChart    from './components/EquityChart';

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
  const [state, setState]       = useState(null);
  const [wsStatus, setWsStatus] = useState('CONNECTING');
  const [lastUpdate, setLastUpdate] = useState(null);
  const [layout, setLayout]     = useState('full'); // 'full' | 'trade' | 'log'

  const onEvent = useCallback((msg) => {
    if (msg.type === 'snapshot' || msg.type === 'update') {
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
        {/* Left column: scenarios + indicators */}
        {(layout === 'full' || layout === 'left') && (
          <div style={styles.leftCol}>
            <ScenarioPanel scenarios={scenarios} />
            <IndicatorPanel indicators={indicators} />
          </div>
        )}

        {/* Right column: equity chart + trades + logs */}
        {(layout === 'full' || layout === 'right') && (
          <div style={styles.rightCol}>
            <EquityChart closedTrades={closedTrades} />
            <TradePanel openTrades={openTrades} closedTrades={closedTrades} />
            <LogStream logs={logs} />
          </div>
        )}

        {/* Single-panel views */}
        {layout === 'trade' && (
          <div style={styles.fullCol}>
            <TradePanel openTrades={openTrades} closedTrades={closedTrades} />
            <EquityChart closedTrades={closedTrades} />
          </div>
        )}
        {layout === 'log' && (
          <div style={styles.fullCol}>
            <LogStream logs={logs} />
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
        <span style={{ fontSize: 9, color, fontWeight: 700, letterSpacing: 1 }}>{status}</span>
        {age != null && (
          <span style={{ fontSize: 9, color: '#475569' }}>last update {age}s ago</span>
        )}
      </div>
      <div style={styles.layoutBtns}>
        {[['full', 'FULL'], ['left', 'SIGNALS'], ['right', 'TRADING'], ['trade', 'TRADES'], ['log', 'LOGS']].map(
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
    color: '#334155', fontSize: 8, fontWeight: 700,
    padding: '2px 8px', borderRadius: 3, cursor: 'pointer', letterSpacing: 1,
  },
  layoutBtnActive: { borderColor: '#475569', color: '#94a3b8' },
  body: {
    display: 'flex', gap: 10, padding: 10,
    flex: 1, overflow: 'hidden',
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
