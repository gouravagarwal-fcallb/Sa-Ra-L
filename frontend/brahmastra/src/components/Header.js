import React from 'react';

const C = {
  BULL: '#22c55e',
  BEAR: '#ef4444',
  NEUTRAL: '#94a3b8',
  GOLD: '#f59e0b',
};

export default function Header({ session, ticks }) {
  const nifty   = ticks?.NIFTY;
  const sensex  = ticks?.SENSEX;
  const pnl     = session?.session_pnl ?? 0;
  const phase   = session?.phase ?? 'INIT';
  const mode    = session?.mode ?? '';
  const vix     = session?.india_vix;
  const bias    = session?.bias_score;

  const pnlColor  = pnl >= 0 ? C.BULL : C.BEAR;
  const modeColor = mode === 'live' ? '#ef4444' : '#38bdf8';

  return (
    <div style={styles.header}>
      {/* Brand */}
      <div style={styles.brand}>
        <span style={styles.logo}>⚡ BRAHMASTRA</span>
        <span style={styles.v1}>v1</span>
        <span style={{ ...styles.pill, background: modeColor + '22', color: modeColor }}>
          {mode.toUpperCase() || 'INIT'}
        </span>
        <span style={{ ...styles.pill, background: '#334155', color: '#94a3b8' }}>
          {phase}
        </span>
      </div>

      {/* Live prices */}
      <div style={styles.prices}>
        {nifty && <PriceChip label="NIFTY" data={nifty} />}
        {sensex && <PriceChip label="SENSEX" data={sensex} />}
      </div>

      {/* Stats */}
      <div style={styles.stats}>
        {vix != null && (
          <Stat label="VIX" value={vix.toFixed(1)}
                color={vix > 20 ? C.BEAR : vix < 14 ? C.BULL : C.NEUTRAL} />
        )}
        {bias != null && (
          <Stat label="BIAS" value={(bias >= 0 ? '+' : '') + bias}
                color={bias > 20 ? C.BULL : bias < -20 ? C.BEAR : C.NEUTRAL} />
        )}
        <Stat label="P&L" value={`₹${pnl >= 0 ? '+' : ''}${Math.round(pnl).toLocaleString()}`}
              color={pnlColor} />
        <Stat label="TRADES" value={session?.total_trades ?? 0} />
        <Stat label="WIN%" value={session?.win_rate != null ? session.win_rate.toFixed(0) + '%' : '-'} />
      </div>
    </div>
  );
}

function PriceChip({ label, data }) {
  const up = data.change_pct >= 0;
  return (
    <div style={styles.priceChip}>
      <span style={styles.priceLabel}>{label}</span>
      <span style={styles.priceVal}>{data.price?.toLocaleString('en-IN', { maximumFractionDigits: 0 })}</span>
      <span style={{ color: up ? '#22c55e' : '#ef4444', fontSize: 11 }}>
        {up ? '▲' : '▼'} {Math.abs(data.change_pct ?? 0).toFixed(2)}%
      </span>
    </div>
  );
}

function Stat({ label, value, color = '#94a3b8' }) {
  return (
    <div style={styles.stat}>
      <span style={styles.statLabel}>{label}</span>
      <span style={{ ...styles.statVal, color }}>{value}</span>
    </div>
  );
}

const styles = {
  header: {
    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    padding: '8px 16px',
    background: '#0f172a',
    borderBottom: '1px solid #1e293b',
    flexWrap: 'wrap', gap: 8,
  },
  brand: { display: 'flex', alignItems: 'center', gap: 8 },
  logo: { fontSize: 18, fontWeight: 700, color: '#f59e0b', letterSpacing: 2 },
  v1: { fontSize: 11, color: '#475569', marginLeft: -4 },
  pill: {
    fontSize: 10, fontWeight: 700, padding: '2px 8px',
    borderRadius: 99, letterSpacing: 1,
  },
  prices: { display: 'flex', gap: 16 },
  priceChip: {
    display: 'flex', flexDirection: 'column', alignItems: 'flex-end',
    background: '#1e293b', borderRadius: 6, padding: '4px 10px',
  },
  priceLabel: { fontSize: 9, color: '#64748b', letterSpacing: 1 },
  priceVal: { fontSize: 16, fontWeight: 700, color: '#e2e8f0' },
  stats: { display: 'flex', gap: 16, alignItems: 'center' },
  stat: { display: 'flex', flexDirection: 'column', alignItems: 'center' },
  statLabel: { fontSize: 9, color: '#475569', letterSpacing: 1 },
  statVal: { fontSize: 14, fontWeight: 700 },
};
