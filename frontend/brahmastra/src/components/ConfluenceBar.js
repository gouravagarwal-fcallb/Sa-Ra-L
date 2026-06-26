import React from 'react';

// Signals extracted from indicator snapshot and mapped to visual chips
const SIGNAL_MAP = [
  { key: 'ema_structure',     label: 'EMA',     bull: ['BULL', 'UP'],  bear: ['BEAR', 'DOWN'] },
  { key: 'supertrend_dir',    label: 'ST',      bull: ['BULL', 'UP'],  bear: ['BEAR', 'DOWN'] },
  { key: 'ichimoku_bias',     label: 'ICHI',    bull: ['BULL', 'UP', 'ABOVE'], bear: ['BEAR', 'DOWN', 'BELOW'] },
  { key: 'macd_cross',        label: 'MACD',    bull: ['BULL', 'UP'],  bear: ['BEAR', 'DOWN'] },
  { key: 'vwap_position',     label: 'VWAP',    bull: ['ABOVE'],       bear: ['BELOW'] },
  { key: 'adx_trend',         label: 'ADX',     bull: ['STRONG'],      bear: [] },
  { key: 'obv_rising',        label: 'OBV',     bull: [true],          bear: [false] },
  { key: 'confluence_dir',    label: 'TOTAL',   bull: ['BULL'],        bear: ['BEAR'] },
];

function classify(val, bull, bear) {
  if (val == null || val === '') return 'neutral';
  const v = typeof val === 'string' ? val.toUpperCase() : val;
  if (bull.some(b => (typeof b === 'string' ? v.includes(b) : v === b))) return 'bull';
  if (bear.some(b => (typeof b === 'string' ? v.includes(b) : v === b))) return 'bear';
  return 'neutral';
}

const CLS_COLOR = { bull: '#22c55e', bear: '#ef4444', neutral: '#334155' };
const CLS_BG    = { bull: '#22c55e18', bear: '#ef444418', neutral: '#1e293b' };

export default function ConfluenceBar({ indicators }) {
  const instruments = indicators ? Object.keys(indicators) : [];

  return (
    <div style={styles.panel}>
      <div style={styles.header}>CONFLUENCE BREAKDOWN</div>

      {instruments.length === 0
        ? <div style={styles.empty}>Waiting for indicator data…</div>
        : instruments.map(inst => {
            const snap  = indicators[inst] || {};
            const score = snap.confluence_score ?? 0;
            const dir   = snap.confluence_dir   ?? 'NEUTRAL';
            const maxScore = 100;
            const pct   = Math.min(100, Math.abs(score) / maxScore * 100);
            const color = score > 0 ? '#22c55e' : score < 0 ? '#ef4444' : '#64748b';

            return (
              <div key={inst} style={styles.instBlock}>
                {/* Instrument + total score */}
                <div style={styles.instRow}>
                  <span style={styles.instName}>{inst}</span>
                  <span style={{
                    ...styles.dirBadge,
                    color, background: color + '22',
                  }}>
                    {dir}
                  </span>
                  <span style={{ fontSize: 16, fontWeight: 700, color, marginLeft: 'auto' }}>
                    {score > 0 ? '+' : ''}{typeof score === 'number' ? score.toFixed(0) : score}
                  </span>
                </div>

                {/* Total score bar */}
                <div style={styles.totalTrack}>
                  <div style={{ ...styles.totalFill, width: pct + '%', background: color }} />
                </div>

                {/* Signal chips */}
                <div style={styles.chipGrid}>
                  {SIGNAL_MAP.map(sig => {
                    const val = snap[sig.key];
                    const cls = classify(val, sig.bull, sig.bear);
                    return (
                      <div key={sig.key} style={{
                        ...styles.chip,
                        color:      CLS_COLOR[cls],
                        background: CLS_BG[cls],
                        border:     `1px solid ${CLS_COLOR[cls]}44`,
                      }}>
                        <span style={styles.chipLabel}>{sig.label}</span>
                        <span style={styles.chipDot}>
                          {cls === 'bull' ? '▲' : cls === 'bear' ? '▼' : '—'}
                        </span>
                      </div>
                    );
                  })}
                </div>

                {/* RSI + ATR inline */}
                <div style={styles.metaRow}>
                  {snap.rsi != null && (
                    <MetaVal label="RSI" val={snap.rsi.toFixed(1)}
                      color={snap.rsi > 70 ? '#ef4444' : snap.rsi < 30 ? '#22c55e' : '#94a3b8'} />
                  )}
                  {snap.atr != null && (
                    <MetaVal label="ATR" val={snap.atr.toFixed(1)} color="#94a3b8" />
                  )}
                  {snap.adx != null && (
                    <MetaVal label="ADX" val={snap.adx.toFixed(0)}
                      color={snap.adx > 40 ? '#f59e0b' : snap.adx > 25 ? '#22c55e' : '#64748b'} />
                  )}
                  {snap.bb_pct_b != null && (
                    <MetaVal label="BB%B" val={snap.bb_pct_b.toFixed(2)}
                      color={snap.bb_pct_b > 1 ? '#ef4444' : snap.bb_pct_b < 0 ? '#22c55e' : '#94a3b8'} />
                  )}
                </div>
              </div>
            );
          })
      }
    </div>
  );
}

function MetaVal({ label, val, color }) {
  return (
    <div style={styles.meta}>
      <span style={styles.metaLabel}>{label}</span>
      <span style={{ ...styles.metaVal, color }}>{val}</span>
    </div>
  );
}

const styles = {
  panel: {
    background: '#0f172a', border: '1px solid #1e293b',
    borderRadius: 8, padding: 12,
    display: 'flex', flexDirection: 'column', gap: 10,
  },
  header: { fontSize: 12, fontWeight: 700, letterSpacing: 2, color: '#475569' },
  empty:  { color: '#475569', fontSize: 13, textAlign: 'center', padding: 16 },
  instBlock: {
    background: '#1e293b', borderRadius: 6, padding: 12,
    border: '1px solid #334155',
    display: 'flex', flexDirection: 'column', gap: 8,
  },
  instRow: { display: 'flex', alignItems: 'center', gap: 8 },
  instName: { fontSize: 13, fontWeight: 700, color: '#e2e8f0' },
  dirBadge: {
    fontSize: 11, fontWeight: 700, letterSpacing: 1,
    padding: '2px 9px', borderRadius: 99,
  },
  totalTrack: {
    height: 6, background: '#0f172a', borderRadius: 99, overflow: 'hidden',
  },
  totalFill: {
    height: 6, borderRadius: 99, transition: 'width 0.5s ease',
  },
  chipGrid: {
    display: 'flex', flexWrap: 'wrap', gap: 5,
  },
  chip: {
    display: 'flex', alignItems: 'center', gap: 4,
    borderRadius: 4, padding: '3px 8px',
  },
  chipLabel: { fontSize: 11, letterSpacing: 1, fontWeight: 700 },
  chipDot:   { fontSize: 11 },
  metaRow: { display: 'flex', gap: 14, flexWrap: 'wrap' },
  meta: { display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 1 },
  metaLabel: { fontSize: 10, color: '#475569', letterSpacing: 1 },
  metaVal:   { fontSize: 12, fontWeight: 700 },
};
