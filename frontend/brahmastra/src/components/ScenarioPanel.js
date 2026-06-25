import React from 'react';

const STATE_COLOR = {
  WATCHING:  '#64748b',
  ARMED:     '#f59e0b',
  READY:     '#fb923c',
  CONFIRMED: '#22c55e',
  ACTIVE:    '#3b82f6',
  PROFIT:    '#10b981',
  STOPPED:   '#ef4444',
  ABORTED:   '#64748b',
  DEAD:      '#374151',
  EXPIRED:   '#374151',
};

const STATE_GLOW = {
  CONFIRMED: '0 0 8px #22c55e66',
  ACTIVE:    '0 0 8px #3b82f666',
  ARMED:     '0 0 6px #f59e0b44',
  READY:     '0 0 8px #fb923c55',
};

export default function ScenarioPanel({ scenarios }) {
  if (!scenarios || Object.keys(scenarios).length === 0) {
    return (
      <div style={styles.panel}>
        <div style={styles.header}>SCENARIO ENGINE</div>
        <div style={styles.empty}>Waiting for market data…</div>
      </div>
    );
  }

  const allScenarios = [];
  Object.entries(scenarios).forEach(([instrument, list]) => {
    (list || []).forEach(s => allScenarios.push({ instrument, ...s }));
  });

  return (
    <div style={styles.panel}>
      <div style={styles.header}>SCENARIO ENGINE <span style={styles.count}>{allScenarios.length}</span></div>
      <div style={styles.grid}>
        {allScenarios.map((s, i) => <ScenarioCard key={i} s={s} />)}
      </div>
    </div>
  );
}

function ScenarioCard({ s }) {
  const color = STATE_COLOR[s.state] ?? '#64748b';
  const glow  = STATE_GLOW[s.state];
  const conf  = Math.max(0, Math.min(100, s.confidence ?? 0));
  const dirColor = s.direction === 'BULL' ? '#22c55e' : '#ef4444';
  const isDead = ['ABORTED', 'DEAD', 'EXPIRED', 'STOPPED', 'PROFIT'].includes(s.state);

  return (
    <div style={{ ...styles.card, opacity: isDead ? 0.45 : 1, boxShadow: glow }}>
      {/* Top row */}
      <div style={styles.cardTop}>
        <span style={{ ...styles.instrument, color: dirColor }}>{s.instrument}</span>
        <span style={{ ...styles.direction, color: dirColor }}>
          {s.direction === 'BULL' ? '▲ BULL' : '▼ BEAR'}
        </span>
        <span style={{ ...styles.stateBadge, background: color + '22', color }}>
          {s.state}
        </span>
      </div>

      {/* Timeframe + hypothesis */}
      <div style={styles.hypothesis}>
        <span style={styles.tf}>{s.timeframe || '5m'}</span>
        <span style={styles.hyp}>{s.name || '—'}</span>
      </div>

      {/* Confidence bar */}
      <div style={styles.barTrack}>
        <div style={{
          ...styles.barFill,
          width: conf + '%',
          background: conf >= 85 ? '#22c55e' : conf >= 75 ? '#f59e0b' : '#3b82f6',
        }} />
        <span style={styles.barLabel}>{conf.toFixed(1)}%</span>
      </div>

      {/* Price levels */}
      {s.entry_price != null && (
        <div style={styles.levels}>
          <Level label="ENTRY" val={s.entry_price} color="#e2e8f0" />
          <Level label="SL"    val={s.sl_price}    color="#ef4444" />
          <Level label="T1"    val={s.target1}     color="#22c55e" />
          <Level label="T2"    val={s.target2}     color="#10b981" />
        </div>
      )}

      {/* PnL if active */}
      {s.unrealized_pnl != null && (
        <div style={{ ...styles.pnl, color: s.unrealized_pnl >= 0 ? '#22c55e' : '#ef4444' }}>
          {s.unrealized_pnl >= 0 ? '+' : ''}₹{Math.round(s.unrealized_pnl).toLocaleString()}
        </div>
      )}
    </div>
  );
}

function Level({ label, val, color }) {
  if (val == null) return null;
  return (
    <div style={styles.levelItem}>
      <span style={styles.levelLabel}>{label}</span>
      <span style={{ ...styles.levelVal, color }}>{val.toLocaleString('en-IN', { maximumFractionDigits: 0 })}</span>
    </div>
  );
}

const styles = {
  panel: {
    background: '#0f172a',
    border: '1px solid #1e293b',
    borderRadius: 8,
    padding: 12,
    display: 'flex',
    flexDirection: 'column',
    gap: 10,
  },
  header: {
    fontSize: 10, fontWeight: 700, letterSpacing: 2,
    color: '#475569', display: 'flex', alignItems: 'center', gap: 8,
  },
  count: {
    background: '#1e293b', color: '#94a3b8',
    borderRadius: 99, padding: '1px 6px', fontSize: 9,
  },
  empty: { color: '#475569', fontSize: 12, textAlign: 'center', padding: 24 },
  grid: { display: 'flex', flexWrap: 'wrap', gap: 10 },
  card: {
    background: '#1e293b', borderRadius: 6, padding: 10,
    minWidth: 200, flex: '1 1 200px',
    display: 'flex', flexDirection: 'column', gap: 6,
    border: '1px solid #334155',
    transition: 'box-shadow 0.3s',
  },
  cardTop: { display: 'flex', alignItems: 'center', gap: 6 },
  instrument: { fontSize: 12, fontWeight: 700 },
  direction: { fontSize: 11, fontWeight: 700 },
  stateBadge: {
    fontSize: 9, fontWeight: 700, letterSpacing: 1,
    padding: '2px 7px', borderRadius: 99, marginLeft: 'auto',
  },
  hypothesis: { display: 'flex', alignItems: 'center', gap: 6 },
  tf: {
    fontSize: 9, fontWeight: 700, letterSpacing: 1,
    background: '#0f172a', color: '#64748b',
    padding: '1px 5px', borderRadius: 3,
  },
  hyp: { fontSize: 10, color: '#94a3b8', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
  barTrack: {
    height: 6, background: '#0f172a', borderRadius: 99,
    position: 'relative', overflow: 'visible',
    display: 'flex', alignItems: 'center',
  },
  barFill: {
    height: 6, borderRadius: 99,
    transition: 'width 0.4s ease, background 0.4s',
  },
  barLabel: {
    position: 'absolute', right: 0, top: -1,
    fontSize: 9, color: '#94a3b8', fontWeight: 700,
    background: '#1e293b', paddingLeft: 4,
  },
  levels: { display: 'flex', gap: 8, flexWrap: 'wrap' },
  levelItem: { display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 1 },
  levelLabel: { fontSize: 8, color: '#475569', letterSpacing: 1 },
  levelVal: { fontSize: 10, fontWeight: 700 },
  pnl: { fontSize: 13, fontWeight: 700, textAlign: 'right', marginTop: 2 },
};
