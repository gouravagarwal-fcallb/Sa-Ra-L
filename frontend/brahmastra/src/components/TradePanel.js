import React, { useState } from 'react';

export default function TradePanel({ openTrades, closedTrades }) {
  const [tab, setTab] = useState('open');
  const open   = openTrades   ? Object.values(openTrades)   : [];
  const closed = closedTrades ? [...closedTrades].reverse()  : [];

  // Totals derived from the rows actually shown, so the total always reconciles
  // with the trades on screen (fixes "profit not matching / no total").
  const num = (x) => (typeof x === 'number' ? x : Number(x) || 0);
  const realised   = closed.reduce((s, t) => s + num(t.net_pnl ?? t.realised_pnl), 0);
  const unrealised = open.reduce((s, t) => s + num(t.unrealized_pnl ?? t.unrealised_pnl), 0);
  const totalPnl   = realised + unrealised;
  const wins   = closed.filter(t => num(t.net_pnl ?? t.realised_pnl) > 0).length;
  const losses = closed.filter(t => num(t.net_pnl ?? t.realised_pnl) < 0).length;
  const pcol = (v) => (v > 0 ? '#22c55e' : v < 0 ? '#ef4444' : '#5b6b82');

  return (
    <div style={styles.panel}>
      <div style={styles.headerRow}>
        <span style={styles.title}>TRADES</span>
        <div style={styles.tabs}>
          <Tab label={`OPEN (${open.length})`}   active={tab === 'open'}   onClick={() => setTab('open')} />
          <Tab label={`CLOSED (${closed.length})`} active={tab === 'closed'} onClick={() => setTab('closed')} />
        </div>
      </div>

      <div style={styles.totals}>
        <span>Total P&L <b style={{ color: pcol(totalPnl) }}>{totalPnl >= 0 ? '+' : ''}₹{Math.round(totalPnl).toLocaleString('en-IN')}</b></span>
        <span style={styles.totDim}>realised <b style={{ color: pcol(realised) }}>₹{Math.round(realised).toLocaleString('en-IN')}</b> · open <b style={{ color: pcol(unrealised) }}>₹{Math.round(unrealised).toLocaleString('en-IN')}</b></span>
        <span style={styles.totDim}>{closed.length} closed · {wins}W / {losses}L{closed.length ? ` · ${Math.round(wins / (wins + losses || 1) * 100)}% win` : ''}</span>
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

function Cell({ label, val, color = '#1f2a3a' }) {
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
  // Option premiums need decimals (e.g. 456.34) — rounding to whole rupees made the
  // operator unable to verify the entry/LTP/SL against the broker.
  return n.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
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
    background: '#ffffff', border: '1px solid #dde5ef',
    borderRadius: 10, padding: 14,
    display: 'flex', flexDirection: 'column', gap: 8,
    boxShadow: '0 1px 3px rgba(15,23,42,0.08)',
  },
  headerRow: { display: 'flex', alignItems: 'center', justifyContent: 'space-between' },
  totals: { display: 'flex', flexWrap: 'wrap', gap: 14, alignItems: 'baseline', fontSize: 13, color: '#1f2a3a', background: '#f4f7fc', border: '1px solid #dde5ef', borderRadius: 8, padding: '8px 12px' },
  totDim: { fontSize: 11.5, color: '#5b6b82' },
  title: { fontSize: 13, fontWeight: 700, letterSpacing: 1, color: '#1f2a3a' },
  tabs: { display: 'flex', gap: 4 },
  tab: {
    background: '#f4f7fc', border: '1px solid #dde5ef',
    color: '#5b6b82', fontSize: 12, fontWeight: 700,
    padding: '3px 11px', borderRadius: 5, cursor: 'pointer', letterSpacing: 0.5,
  },
  tabActive: { background: '#2563eb', borderColor: '#2563eb', color: '#fff' },
  list: { display: 'flex', flexDirection: 'column', gap: 6 },
  row: {
    background: '#f4f7fc', borderRadius: 8, padding: '10px 12px',
    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    border: '1px solid #dde5ef',
  },
  rowLeft: { display: 'flex', alignItems: 'center', gap: 8, minWidth: 160 },
  rowMid: { display: 'flex', gap: 14, flex: 1, justifyContent: 'center' },
  inst: { fontSize: 13, fontWeight: 700 },
  strike: { fontSize: 12, color: '#5b6b82' },
  qty: { fontSize: 11, color: '#5b6b82' },
  exitReason: { fontSize: 11, fontWeight: 700, letterSpacing: 0.5 },
  cell: { display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 1 },
  cellLabel: { fontSize: 10, color: '#5b6b82', letterSpacing: 0.5 },
  cellVal: { fontSize: 12, fontWeight: 700, color: '#1f2a3a' },
  pnl: { fontSize: 15, fontWeight: 700, minWidth: 90, textAlign: 'right' },
  empty: { color: '#5b6b82', fontSize: 13, textAlign: 'center', padding: 20 },
};
