import React from 'react';

// ─── Wave colour helpers ──────────────────────────────────────────────────────

function waveColor(label, action) {
  const l = (label || '').toString().toUpperCase();
  const a = (action || '').toUpperCase();
  const isBull = a.includes('CE') || a.includes('BUY') || a.includes('LONG');

  if (l === '3') return '#22c55e';               // always bright green for wave 3
  if (l === '1' || l === '5') return isBull ? '#22c55e' : '#ef4444';
  if (l === '2') return isBull ? '#22c55e' : '#ef4444';
  if (l === '4') return '#f59e0b';               // consolidation amber
  if (l === 'A' || l === 'B' || l === 'C') return '#ef4444';
  return '#94a3b8';                              // "?" or unknown
}

function waveGlow(label) {
  const l = (label || '').toString().toUpperCase();
  if (l === '3') return '0 0 18px #22c55e55, 0 0 4px #22c55e88';
  return 'none';
}

function positionSizeColor(size) {
  switch ((size || '').toUpperCase()) {
    case 'FULL':    return { color: '#22c55e', bg: '#22c55e18' };
    case 'HALF':    return { color: '#f59e0b', bg: '#f59e0b18' };
    case 'QUARTER': return { color: '#3b82f6', bg: '#3b82f618' };
    default:        return { color: '#94a3b8', bg: '#94a3b818' };
  }
}

function waveTypeBadge(type) {
  switch ((type || '').toLowerCase()) {
    case 'impulse':    return { label: 'IMPULSE',    color: '#22c55e', bg: '#22c55e18' };
    case 'corrective': return { label: 'CORRECTIVE', color: '#f59e0b', bg: '#f59e0b18' };
    default:           return { label: 'UNKNOWN',    color: '#94a3b8', bg: '#94a3b818' };
  }
}

function confidenceColor(conf) {
  if (conf >= 75) return '#22c55e';
  if (conf >= 50) return '#f59e0b';
  return '#ef4444';
}

// ─── Main panel ──────────────────────────────────────────────────────────────

export default function ElliottWavePanel({ elliottWave }) {
  const instruments = elliottWave ? Object.keys(elliottWave) : [];

  // Count of valid (non-null) wave entries for the chip
  const validCount = instruments.filter(k => elliottWave[k] && elliottWave[k].current_wave).length;

  return (
    <div style={styles.panel}>
      {/* Header */}
      <div style={styles.headerRow}>
        <span style={styles.title}>ELLIOTT WAVE</span>
        {validCount > 0 && (
          <span style={styles.countChip}>{validCount} ACTIVE</span>
        )}
      </div>

      {/* Body */}
      {instruments.length === 0 ? (
        <div style={styles.empty}>Waiting for wave data…</div>
      ) : (
        <div style={styles.cardGrid}>
          {instruments.map(inst => (
            <WaveCard
              key={inst}
              instrument={inst}
              data={elliottWave[inst]}
              single={instruments.length === 1}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ─── Per-instrument card ──────────────────────────────────────────────────────

function WaveCard({ instrument, data, single }) {
  // Graceful null handling
  if (!data || !data.current_wave) {
    return (
      <div style={{ ...styles.card, ...(single ? styles.cardSingle : {}) }}>
        <div style={styles.instLabel}>{instrument}</div>
        <div style={styles.empty}>Waiting for wave data…</div>
      </div>
    );
  }

  const {
    current_wave,
    wave_type,
    confidence,
    action,
    strike_guidance,
    position_size,
    reasoning,
    completed_waves,
  } = data;

  const conf      = Math.max(0, Math.min(100, confidence ?? 0));
  const wColor    = waveColor(current_wave, action);
  const glow      = waveGlow(current_wave);
  const confColor = confidenceColor(conf);
  const psStyle   = positionSizeColor(position_size);
  const typeBadge = waveTypeBadge(wave_type);
  const shortReason = reasoning
    ? (reasoning.length > 120 ? reasoning.slice(0, 117) + '…' : reasoning)
    : null;

  return (
    <div style={{ ...styles.card, ...(single ? styles.cardSingle : {}) }}>

      {/* Instrument label + wave chip */}
      <div style={styles.cardTop}>
        <span style={styles.instLabel}>{instrument}</span>
        {/* Wave type badge */}
        <span style={{
          ...styles.badge,
          color: typeBadge.color,
          background: typeBadge.bg,
          border: `1px solid ${typeBadge.color}44`,
        }}>
          {typeBadge.label}
        </span>
        {/* Position size chip */}
        <span style={{
          ...styles.badge,
          color: psStyle.color,
          background: psStyle.bg,
          border: `1px solid ${psStyle.color}44`,
          marginLeft: 'auto',
        }}>
          {(position_size || 'NONE').toUpperCase()}
        </span>
      </div>

      {/* Large wave number */}
      <div style={styles.waveCenterRow}>
        <div style={{ ...styles.waveBig, color: wColor, textShadow: glow }}>
          {current_wave}
        </div>
        <div style={styles.waveSubLabel}>
          WAVE
        </div>
      </div>

      {/* Confidence bar */}
      <div>
        <div style={styles.confLabelRow}>
          <span style={styles.confLabel}>CONFIDENCE</span>
          <span style={{ ...styles.confPct, color: confColor }}>{conf.toFixed(0)}%</span>
        </div>
        <div style={styles.barTrack}>
          <div style={{
            ...styles.barFill,
            width: conf + '%',
            background: confColor,
          }} />
        </div>
      </div>

      {/* Action box */}
      {action && (
        <div style={styles.actionBox}>
          <span style={styles.actionLabel}>ACTION</span>
          <span style={styles.actionText}>{action}</span>
        </div>
      )}

      {/* Strike guidance */}
      {strike_guidance && (
        <div style={styles.strikeText}>{strike_guidance}</div>
      )}

      {/* Wave ladder */}
      {completed_waves && completed_waves.length > 0 && (
        <WaveLadder waves={completed_waves} />
      )}

      {/* Reasoning */}
      {shortReason && (
        <div style={styles.reasoning}>{shortReason}</div>
      )}
    </div>
  );
}

// ─── Wave ladder (mini bar chart) ────────────────────────────────────────────

function WaveLadder({ waves }) {
  // Normalise bar heights relative to largest absolute move
  const maxAbs = Math.max(...waves.map(w => Math.abs(w.move_pct || 0)), 1);

  return (
    <div>
      <div style={styles.ladderTitle}>COMPLETED WAVES</div>
      <div style={styles.ladderRow}>
        {waves.map((w, i) => {
          const pct    = w.move_pct || 0;
          const isUp   = pct >= 0;
          const barH   = Math.max(4, Math.round((Math.abs(pct) / maxAbs) * 36));
          const color  = isUp ? '#22c55e' : '#ef4444';
          const retStr = w.retrace_of != null
            ? ` ${w.retrace_of.toFixed(2)}x`
            : '';

          return (
            <div key={i} style={styles.ladderItem}>
              {/* Fib check — shown above if up-bar, below if down-bar */}
              {isUp && w.fib_valid && (
                <span style={styles.fibCheck}>&#10003;</span>
              )}

              {/* Bar column */}
              <div style={{ ...styles.ladderBarCol, height: barH, background: color }} />

              {/* Move pct */}
              <div style={{ ...styles.ladderPct, color }}>
                {isUp ? '+' : ''}{pct.toFixed(1)}%
              </div>

              {/* Wave label + retrace */}
              <div style={styles.ladderWaveLabel}>
                W{w.label}{retStr}
              </div>

              {/* Fib check — below for down-bars */}
              {!isUp && w.fib_valid && (
                <span style={styles.fibCheck}>&#10003;</span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ─── Styles ───────────────────────────────────────────────────────────────────

const styles = {
  panel: {
    background: '#0f172a',
    border: '1px solid #1e293b',
    borderRadius: 8,
    padding: 12,
    display: 'flex',
    flexDirection: 'column',
    gap: 10,
    fontFamily: '"Courier New", Courier, monospace',
  },
  headerRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
  },
  title: {
    fontSize: 12,
    fontWeight: 700,
    letterSpacing: 2,
    color: '#475569',
  },
  countChip: {
    fontSize: 11,
    fontWeight: 700,
    letterSpacing: 1,
    background: '#1e293b',
    color: '#94a3b8',
    borderRadius: 99,
    padding: '1px 8px',
    border: '1px solid #334155',
  },
  cardGrid: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: 10,
  },
  card: {
    background: '#1e293b',
    border: '1px solid #334155',
    borderRadius: 6,
    padding: 14,
    flex: '1 1 220px',
    minWidth: 220,
    display: 'flex',
    flexDirection: 'column',
    gap: 10,
    fontFamily: '"Courier New", Courier, monospace',
  },
  cardSingle: {
    flex: '1 1 100%',
    maxWidth: '100%',
  },
  cardTop: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    flexWrap: 'wrap',
  },
  instLabel: {
    fontSize: 13,
    fontWeight: 700,
    letterSpacing: 2,
    color: '#e2e8f0',
  },
  badge: {
    fontSize: 10,
    fontWeight: 700,
    letterSpacing: 1,
    padding: '2px 7px',
    borderRadius: 99,
  },
  waveCenterRow: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    padding: '4px 0 6px',
  },
  waveBig: {
    fontSize: 56,
    fontWeight: 900,
    lineHeight: 1,
    letterSpacing: -2,
    transition: 'color 0.3s, text-shadow 0.3s',
  },
  waveSubLabel: {
    fontSize: 10,
    letterSpacing: 3,
    color: '#475569',
    marginTop: 2,
    fontWeight: 700,
  },
  confLabelRow: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'baseline',
    marginBottom: 4,
  },
  confLabel: {
    fontSize: 10,
    letterSpacing: 1.5,
    color: '#475569',
    fontWeight: 700,
  },
  confPct: {
    fontSize: 12,
    fontWeight: 700,
  },
  barTrack: {
    height: 5,
    background: '#0a0e1a',
    borderRadius: 99,
    overflow: 'hidden',
  },
  barFill: {
    height: 5,
    borderRadius: 99,
    transition: 'width 0.5s ease, background 0.4s',
    minWidth: 2,
  },
  actionBox: {
    background: '#0a0e1a',
    border: '1px solid #334155',
    borderRadius: 4,
    padding: '6px 10px',
    display: 'flex',
    flexDirection: 'column',
    gap: 2,
  },
  actionLabel: {
    fontSize: 9,
    letterSpacing: 2,
    color: '#475569',
    fontWeight: 700,
  },
  actionText: {
    fontSize: 12,
    fontWeight: 700,
    color: '#f1f5f9',
    letterSpacing: 0.3,
  },
  strikeText: {
    fontSize: 11,
    color: '#64748b',
    letterSpacing: 0.3,
    lineHeight: 1.4,
    paddingLeft: 2,
  },
  ladderTitle: {
    fontSize: 9,
    letterSpacing: 2,
    color: '#475569',
    fontWeight: 700,
    marginBottom: 6,
  },
  ladderRow: {
    display: 'flex',
    gap: 8,
    alignItems: 'flex-end',
  },
  ladderItem: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: 2,
    flex: '1 1 0',
  },
  ladderBarCol: {
    width: '100%',
    minWidth: 10,
    maxWidth: 32,
    borderRadius: 2,
    transition: 'height 0.4s ease',
  },
  ladderPct: {
    fontSize: 10,
    fontWeight: 700,
  },
  ladderWaveLabel: {
    fontSize: 9,
    color: '#64748b',
    letterSpacing: 0.5,
    fontWeight: 700,
  },
  fibCheck: {
    fontSize: 10,
    color: '#22c55e',
    fontWeight: 900,
    lineHeight: 1,
  },
  reasoning: {
    fontSize: 11,
    color: '#64748b',
    lineHeight: 1.5,
    letterSpacing: 0.2,
    borderTop: '1px solid #1e293b',
    paddingTop: 8,
    marginTop: 2,
  },
  empty: {
    color: '#475569',
    fontSize: 12,
    textAlign: 'center',
    padding: '20px 0',
    letterSpacing: 0.5,
  },
};
