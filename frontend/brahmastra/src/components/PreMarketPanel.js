import React from 'react';

const BIAS_COLOR = {
  STRONG_BULL: '#22c55e',
  BULL:        '#4ade80',
  NEUTRAL:     '#94a3b8',
  BEAR:        '#f87171',
  STRONG_BEAR: '#ef4444',
};

function vixInfo(vix) {
  if (vix == null) return { label: '—',       color: '#475569' };
  if (vix < 15)   return { label: 'CALM',     color: '#22c55e' };
  if (vix < 20)   return { label: 'NORMAL',   color: '#4ade80' };
  if (vix < 25)   return { label: 'ELEVATED', color: '#f59e0b' };
  if (vix < 30)   return { label: 'HIGH',     color: '#fb923c' };
  return              { label: 'EXTREME',  color: '#ef4444' };
}

export default function PreMarketPanel({ session, ticks }) {
  const vix       = session?.india_vix;
  const biasLabel = session?.bias_label ?? 'NEUTRAL';
  const biasScore = session?.bias_score ?? 0;
  const biasColor = BIAS_COLOR[biasLabel] ?? '#94a3b8';
  const vi        = vixInfo(vix);
  const phase     = session?.phase ?? 'INIT';
  const mode      = session?.mode  ?? 'OBSERVE';

  // Compute gap from ticks if available
  const niftyPrice = ticks?.NIFTY?.price;

  const scorePct = Math.min(100, Math.abs(biasScore));

  return (
    <div style={styles.panel}>
      <div style={styles.header}>PRE-MARKET BIAS</div>

      <div style={styles.row}>
        {/* Bias tile */}
        <div style={styles.tile}>
          <div style={styles.tileLabel}>BIAS</div>
          <div style={{ ...styles.tileBig, color: biasColor }}>
            {biasLabel.replace('_', ' ')}
          </div>
          <div style={styles.barTrack}>
            <div style={{ ...styles.barFill, width: scorePct + '%', background: biasColor }} />
          </div>
          <div style={{ fontSize: 9, color: biasColor, textAlign: 'center' }}>
            {biasScore !== 0 ? biasScore.toFixed(0) : '—'} / 100
          </div>
        </div>

        {/* VIX tile */}
        <div style={styles.tile}>
          <div style={styles.tileLabel}>INDIA VIX</div>
          <div style={{ ...styles.tileBig, color: vi.color }}>
            {vix != null ? vix.toFixed(2) : '—'}
          </div>
          <div style={{ ...styles.badge, color: vi.color, background: vi.color + '22' }}>
            {vi.label}
          </div>
        </div>

        {/* NIFTY tile */}
        <div style={styles.tile}>
          <div style={styles.tileLabel}>NIFTY</div>
          <div style={{ ...styles.tileBig, color: '#e2e8f0', fontSize: 13 }}>
            {niftyPrice != null ? niftyPrice.toLocaleString('en-IN', { maximumFractionDigits: 0 }) : '—'}
          </div>
          <div style={{ ...styles.badge, color: '#475569', background: '#1e293b' }}>
            {mode}
          </div>
        </div>
      </div>

      {/* Checklist */}
      <div style={styles.checkTitle}>READINESS</div>
      <div style={styles.checkGrid}>
        <CheckItem label="Pre-market loaded"  ok={biasScore !== 0} />
        <CheckItem label="India VIX fetched"  ok={vix != null} />
        <CheckItem label="VIX below danger"   ok={vix != null && vix < 25} warn={vix != null && vix >= 25} />
        <CheckItem label="Session entered"    ok={phase === 'ACTIVE' || phase === 'TRADING'} />
        <CheckItem label="Bias available"     ok={biasLabel !== 'NEUTRAL'} />
      </div>
    </div>
  );
}

function CheckItem({ label, ok, warn }) {
  const icon  = ok ? '✓' : warn ? '⚠' : '○';
  const color = ok ? '#22c55e' : warn ? '#f59e0b' : '#334155';
  return (
    <div style={styles.checkItem}>
      <span style={{ ...styles.checkIcon, color }}>{icon}</span>
      <span style={{ fontSize: 10, color: ok || warn ? '#94a3b8' : '#334155' }}>{label}</span>
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
  row: { display: 'flex', gap: 8 },
  tile: {
    flex: 1, background: '#1e293b', borderRadius: 6, padding: '8px 10px',
    display: 'flex', flexDirection: 'column', gap: 4, alignItems: 'center',
    border: '1px solid #334155',
  },
  tileLabel: { fontSize: 8, color: '#475569', letterSpacing: 2 },
  tileBig:   { fontSize: 15, fontWeight: 700, letterSpacing: 1 },
  barTrack: {
    width: '80%', height: 4, background: '#0f172a',
    borderRadius: 99, overflow: 'hidden',
  },
  barFill:  { height: 4, borderRadius: 99, transition: 'width 0.5s' },
  badge: {
    fontSize: 9, fontWeight: 700, letterSpacing: 1,
    padding: '2px 8px', borderRadius: 99,
  },
  checkTitle: {
    fontSize: 9, fontWeight: 700, letterSpacing: 2, color: '#334155',
  },
  checkGrid: { display: 'flex', flexDirection: 'column', gap: 3 },
  checkItem: { display: 'flex', alignItems: 'center', gap: 8 },
  checkIcon: { fontSize: 10, width: 14, textAlign: 'center', fontWeight: 700, flexShrink: 0 },
};
