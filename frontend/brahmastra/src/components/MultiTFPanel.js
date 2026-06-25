import React from 'react';

function sigColor(val) {
  if (!val) return '#475569';
  const v = val.toString().toUpperCase();
  if (v.includes('BULL') || v.includes('UP') || v.includes('ABOVE') || v === 'YES') return '#22c55e';
  if (v.includes('BEAR') || v.includes('DOWN') || v.includes('BELOW') || v === 'NO') return '#ef4444';
  return '#94a3b8';
}

const ROWS = [
  {
    label: 'EMA',
    get:   (d) => d.ema_structure ? d.ema_structure.replace('_ALIGNED', '').slice(0, 8) : null,
    color: (d) => sigColor(d.ema_structure),
  },
  {
    label: 'SUPERT',
    get:   (d) => d.supertrend_dir,
    color: (d) => sigColor(d.supertrend_dir),
  },
  {
    label: 'ICHI',
    get:   (d) => d.ichimoku_bias,
    color: (d) => sigColor(d.ichimoku_bias),
  },
  {
    label: 'RSI',
    get:   (d) => d.rsi != null ? d.rsi.toFixed(1) : null,
    color: (d) => {
      const v = d.rsi;
      if (v == null) return '#475569';
      if (v > 70) return '#ef4444';
      if (v < 30) return '#22c55e';
      return '#94a3b8';
    },
  },
  {
    label: 'MACD',
    get:   (d) => d.macd_cross,
    color: (d) => sigColor(d.macd_cross),
  },
  {
    label: 'VWAP',
    get:   (d) => d.vwap_position,
    color: (d) => sigColor(d.vwap_position),
  },
  {
    label: 'ADX',
    get:   (d) => d.adx != null ? d.adx.toFixed(0) : null,
    color: (d) => {
      const v = d.adx;
      if (v == null) return '#475569';
      if (v > 40) return '#f59e0b';
      if (v > 25) return '#22c55e';
      return '#64748b';
    },
  },
  {
    label: 'SCORE',
    get:   (d) => d.confluence_score != null ? (d.confluence_score > 0 ? '+' : '') + d.confluence_score.toFixed(0) : null,
    color: (d) => {
      const v = d.confluence_score;
      if (v == null) return '#475569';
      if (v >= 65) return '#22c55e';
      if (v <= -65) return '#ef4444';
      return '#94a3b8';
    },
  },
];

export default function MultiTFPanel({ indicators }) {
  const instruments = indicators ? Object.keys(indicators) : [];

  return (
    <div style={styles.panel}>
      <div style={styles.header}>MULTI-INSTRUMENT ALIGNMENT</div>

      {instruments.length === 0
        ? <div style={styles.empty}>Waiting for indicator data…</div>
        : (
          <div style={{ ...styles.grid, gridTemplateColumns: `60px repeat(${instruments.length}, 1fr)` }}>
            {/* Column headers */}
            <div />
            {instruments.map(inst => (
              <div key={inst} style={styles.colHeader}>
                <span style={styles.colInst}>{inst}</span>
                <span style={styles.colTf}>{indicators[inst]?.timeframe || '5m'}</span>
              </div>
            ))}

            {/* Data rows */}
            {ROWS.map(row => (
              <React.Fragment key={row.label}>
                <div style={styles.rowLabel}>{row.label}</div>
                {instruments.map(inst => {
                  const snap  = indicators[inst] || {};
                  const val   = row.get(snap);
                  const color = row.color(snap);
                  return (
                    <div key={inst} style={{ ...styles.cell, color }}>
                      {val || '—'}
                    </div>
                  );
                })}
              </React.Fragment>
            ))}
          </div>
        )
      }
    </div>
  );
}

const styles = {
  panel: {
    background: '#0f172a', border: '1px solid #1e293b',
    borderRadius: 8, padding: 12,
    display: 'flex', flexDirection: 'column', gap: 10,
  },
  header: { fontSize: 10, fontWeight: 700, letterSpacing: 2, color: '#475569' },
  empty:  { color: '#475569', fontSize: 12, textAlign: 'center', padding: 16 },
  grid: {
    display: 'grid',
    gap: '3px 6px',
    alignItems: 'center',
  },
  colHeader: {
    display: 'flex', flexDirection: 'column', alignItems: 'center',
    background: '#1e293b', borderRadius: 4, padding: '3px 4px',
  },
  colInst: { fontSize: 9, fontWeight: 700, color: '#94a3b8', letterSpacing: 1 },
  colTf:   { fontSize: 8, color: '#475569', letterSpacing: 1 },
  rowLabel: { fontSize: 8, color: '#475569', letterSpacing: 1 },
  cell: {
    fontSize: 9, fontWeight: 700, textAlign: 'center',
    background: '#1e293b', borderRadius: 3, padding: '3px 2px',
    letterSpacing: 0.5,
  },
};
