import React, { useState } from 'react';

export default function TradePanel({ openTrades, closedTrades }) {
  const [tab, setTab] = useState('open');
  const open   = openTrades   ? Object.values(openTrades)   : [];
  const closed = closedTrades ? [...closedTrades].reverse()  : [];

  return (
    <div style={styles.panel}>
      <div style={styles.headerRow}>
        <span style={styles.title}>TRADES</span>
        <div style={styles.tabs}>
          <Tab label={`OPEN (${open.length})`}   active={tab === 'open'}   onClick={() => setTab('open')} />
          <Tab label={`CLOSED (${closed.length})`} active={tab === 'closed'} onClick={() => setTab('closed')} />
        </div>
      </div>

      {tab === 'open' && (
        open.length === 0
          ? <Empty msg="No open positions" />
          : <div style={styles.list}>
              {open.map((t, i) => <OpenRow key={i} t={t} />)}
            </div>
      )}

      {tab === 'closed' && (
        closed.length === 0
          ? <Empty msg="No closed trades this session" />
          : <div style={styles.list}>
              {closed.slice(0, 50).map((t, i) => <ClosedRow key={i} t={t} />)}
            </div>
      )}
    </div>
  );
}

function Tab({ label, active, onClick }) {
  return (
    <button style={{ ...styles.tab, ...(active ? styles.tabActive : {}) }} onClick={onClick}>
      {label}
    </button>
  );
}

function OpenRow({ t }) {
  const pnl = t.unrealized_pnl ?? 0;
  const pnlColor = pnl >= 0 ? '#22c55e' : '#ef4444';
  const dirColor = t.direction === 'BULL' ? '#22c55e' : '#ef4444';

  return (
    <div style={styles.row}>
      <div style={styles.rowLeft}>
        <span style={{ ...styles.inst, color: dirColor }}>{t.instrument}</span>
        <span style={styles.strike}>{t.strike} {t.option_type}</span>
        <span style={styles.qty}>×{t.quantity ?? t.lots}</span>
      </div>
      <div style={styles.rowMid}>
        <Cell label="ENTRY" val={fmt(t.entry_price)} />
        <Cell label="LTP" val={fmt(t.ltp)} />
        <Cell label="SL" val={fmt(t.sl_price)} color="#ef4444" />
        <Cell label="T1" val={fmt(t.target1)} color="#22c55e" />
      </div>
      <div style={{ ...styles.pnl, color: pnlColor }}>
        {pnl >= 0 ? '+' : ''}₹{Math.round(pnl).toLocaleString()}
      </div>
    </div>
  );
}

function ClosedRow({ t }) {
  const pnl = t.net_pnl ?? 0;
  const pnlColor = pnl >= 0 ? '#22c55e' : '#ef4444';
  const dirColor = t.direction === 'BULL' ? '#22c55e' : '#ef4444';

  return (
    <div style={{ ...styles.row, opacity: 0.8 }}>
      <div style={styles.rowLeft}>
        <span style={{ ...styles.inst, color: dirColor }}>{t.instrument}</span>
        <span style={styles.strike}>{t.strike} {t.option_type}</span>
        <span style={{ ...styles.exitReason, color: reasonColor(t.exit_reason) }}>
          {t.exit_reason ?? 'CLOSED'}
        </span>
      </div>
      <div style={styles.rowMid}>
        <Cell label="ENTRY" val={fmt(t.entry_price)} />
        <Cell label="EXIT" val={fmt(t.exit_price)} />
      </div>
      <div style={{ ...styles.pnl, color: pnlColor }}>
        {pnl >= 0 ? '+' : ''}₹{Math.round(pnl).toLocaleString()}
      </div>
    </div>
  );
}

function Cell({ label, val, color = '#e2e8f0' }) {
  if (!val) return null;
  return (
    <div style={styles.cell}>
      <span style={styles.cellLabel}>{label}</span>
      <span style={{ ...styles.cellVal, color }}>{val}</span>
    </div>
  );
}

function Empty({ msg }) {
  return <div style={styles.empty}>{msg}</div>;
}

function fmt(n) {
  if (n == null) return null;
  return n.toLocaleString('en-IN', { maximumFractionDigits: 0 });
}

function reasonColor(reason) {
  if (!reason) return '#94a3b8';
  const r = reason.toUpperCase();
  if (r.includes('TARGET') || r.includes('PROFIT')) return '#22c55e';
  if (r.includes('SL') || r.includes('STOP')) return '#ef4444';
  if (r.includes('EOD') || r.includes('EXPIRE')) return '#f59e0b';
  return '#64748b';
}

const styles = {
  panel: {
    background: '#0f172a', border: '1px solid #1e293b',
    borderRadius: 8, padding: 12,
    display: 'flex', flexDirection: 'column', gap: 8,
  },
  headerRow: { display: 'flex', alignItems: 'center', justifyContent: 'space-between' },
  title: { fontSize: 10, fontWeight: 700, letterSpacing: 2, color: '#475569' },
  tabs: { display: 'flex', gap: 4 },
  tab: {
    background: '#1e293b', border: '1px solid #334155',
    color: '#64748b', fontSize: 9, fontWeight: 700,
    padding: '2px 8px', borderRadius: 4, cursor: 'pointer', letterSpacing: 1,
  },
  tabActive: { background: '#3b82f6', borderColor: '#3b82f6', color: '#fff' },
  list: { display: 'flex', flexDirection: 'column', gap: 6 },
  row: {
    background: '#1e293b', borderRadius: 6, padding: '8px 10px',
    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    border: '1px solid #334155',
  },
  rowLeft: { display: 'flex', alignItems: 'center', gap: 8, minWidth: 150 },
  rowMid: { display: 'flex', gap: 12, flex: 1, justifyContent: 'center' },
  inst: { fontSize: 11, fontWeight: 700 },
  strike: { fontSize: 10, color: '#94a3b8' },
  qty: { fontSize: 9, color: '#64748b' },
  exitReason: { fontSize: 9, fontWeight: 700, letterSpacing: 1 },
  cell: { display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 1 },
  cellLabel: { fontSize: 8, color: '#475569', letterSpacing: 1 },
  cellVal: { fontSize: 10, fontWeight: 700 },
  pnl: { fontSize: 13, fontWeight: 700, minWidth: 80, textAlign: 'right' },
  empty: { color: '#475569', fontSize: 12, textAlign: 'center', padding: 20 },
};
