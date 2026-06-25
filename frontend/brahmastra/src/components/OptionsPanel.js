/**
 * OptionsPanel — Layer 5 Options Intelligence
 * Displays PCR, Max Pain, IV Percentile, OI Buildup for each instrument.
 */
import React from 'react';

const INSTRUMENTS = ['NIFTY', 'SENSEX'];

export default function OptionsPanel({ indicators }) {
  return (
    <div style={s.card}>
      <div style={s.title}>OPTIONS INTELLIGENCE</div>
      <div style={s.grid}>
        {INSTRUMENTS.map(inst => (
          <InstrumentOptions key={inst} inst={inst} data={indicators[inst] || {}} />
        ))}
      </div>
    </div>
  );
}

function InstrumentOptions({ inst, data }) {
  const pcr       = data.options_pcr;
  const maxPain   = data.options_max_pain;
  const ivPct     = data.options_iv_pct;
  const ivLabel   = data.options_iv_label   || 'NORMAL';
  const oiBuild   = data.options_oi_buildup || 'NEUTRAL';
  const callStrikes = data.options_call_strikes || [];
  const putStrikes  = data.options_put_strikes  || [];
  const spot      = data.options_spot || data.close || 0;

  const pcrColor = !pcr ? '#64748b'
    : pcr > 1.3 ? '#22c55e'
    : pcr < 0.8 ? '#ef4444'
    : '#f59e0b';

  const pcrLabel = !pcr ? 'N/A'
    : pcr > 1.3 ? 'BULLISH'
    : pcr < 0.8 ? 'BEARISH'
    : 'NEUTRAL';

  const ivBarColor = !ivPct ? '#475569'
    : ivPct > 90 ? '#ef4444'
    : ivPct > 75 ? '#f59e0b'
    : ivPct > 25 ? '#22c55e'
    : '#3b82f6';

  const oiColor = oiBuild === 'PUT_BUILDUP'  ? '#22c55e'
               : oiBuild === 'CALL_BUILDUP' ? '#ef4444'
               : '#64748b';

  return (
    <div style={s.instBox}>
      <div style={s.instTitle}>{inst}</div>

      {/* PCR Row */}
      <div style={s.row}>
        <span style={s.label}>PCR</span>
        <span style={{ ...s.badge, color: pcrColor, borderColor: pcrColor }}>
          {pcr != null ? pcr.toFixed(2) : '—'}
        </span>
        <span style={{ ...s.tag, color: pcrColor }}>{pcrLabel}</span>
      </div>

      {/* Max Pain Row */}
      <div style={s.row}>
        <span style={s.label}>MAX PAIN</span>
        <span style={s.val}>{maxPain ?? '—'}</span>
        {maxPain && spot ? (
          <span style={{ ...s.tag, color: spot > maxPain ? '#ef4444' : '#22c55e' }}>
            {spot > maxPain ? `↑ ${(((spot - maxPain) / maxPain) * 100).toFixed(1)}% above` :
                              `↓ ${(((maxPain - spot) / maxPain) * 100).toFixed(1)}% below`}
          </span>
        ) : null}
      </div>

      {/* IV Percentile */}
      <div style={s.row}>
        <span style={s.label}>IV PCTILE</span>
        <span style={s.val}>{ivPct != null ? `${ivPct.toFixed(0)}%` : '—'}</span>
        <span style={{ ...s.tag, color: ivBarColor }}>{ivLabel}</span>
      </div>
      {ivPct != null && (
        <div style={s.barTrack}>
          <div style={{ ...s.barFill, width: `${ivPct}%`, background: ivBarColor }} />
        </div>
      )}

      {/* OI Buildup */}
      <div style={s.row}>
        <span style={s.label}>OI SIGNAL</span>
        <span style={{ ...s.tag, color: oiColor }}>{oiBuild.replace('_', ' ')}</span>
      </div>

      {/* Strike walls */}
      {(callStrikes.length > 0 || putStrikes.length > 0) && (
        <div style={s.strikeRow}>
          <div style={s.strikeCol}>
            <div style={s.strikeHeader}>CALL WALL</div>
            {callStrikes.slice(0, 3).map(k => (
              <span key={k} style={{ ...s.strikePill, color: '#ef4444', borderColor: '#ef444433' }}>{k}</span>
            ))}
          </div>
          <div style={s.strikeCol}>
            <div style={s.strikeHeader}>PUT FLOOR</div>
            {putStrikes.slice(0, 3).map(k => (
              <span key={k} style={{ ...s.strikePill, color: '#22c55e', borderColor: '#22c55e33' }}>{k}</span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

const s = {
  card: {
    background: '#0f172a',
    border: '1px solid #1e293b',
    borderRadius: 6,
    padding: '10px 14px',
    fontFamily: '"Courier New", monospace',
  },
  title: {
    fontSize: 9,
    fontWeight: 700,
    color: '#475569',
    letterSpacing: 2,
    marginBottom: 10,
    borderBottom: '1px solid #1e293b',
    paddingBottom: 6,
  },
  grid: {
    display: 'grid',
    gridTemplateColumns: '1fr 1fr',
    gap: 12,
  },
  instBox: {
    display: 'flex',
    flexDirection: 'column',
    gap: 4,
  },
  instTitle: {
    fontSize: 10,
    fontWeight: 700,
    color: '#94a3b8',
    letterSpacing: 1,
    marginBottom: 4,
  },
  row: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
  },
  label: {
    fontSize: 8,
    color: '#475569',
    letterSpacing: 1,
    width: 60,
    flexShrink: 0,
  },
  val: {
    fontSize: 11,
    color: '#e2e8f0',
    fontWeight: 700,
  },
  badge: {
    fontSize: 11,
    fontWeight: 700,
    border: '1px solid',
    borderRadius: 3,
    padding: '0 4px',
  },
  tag: {
    fontSize: 8,
    fontWeight: 700,
    letterSpacing: 1,
  },
  barTrack: {
    height: 3,
    background: '#1e293b',
    borderRadius: 2,
    overflow: 'hidden',
    margin: '2px 0 4px 66px',
  },
  barFill: {
    height: '100%',
    borderRadius: 2,
    transition: 'width 0.5s ease',
  },
  strikeRow: {
    display: 'flex',
    gap: 8,
    marginTop: 6,
  },
  strikeCol: {
    flex: 1,
    display: 'flex',
    flexDirection: 'column',
    gap: 2,
  },
  strikeHeader: {
    fontSize: 7,
    color: '#334155',
    letterSpacing: 1,
    marginBottom: 2,
  },
  strikePill: {
    fontSize: 9,
    fontWeight: 700,
    border: '1px solid',
    borderRadius: 3,
    padding: '1px 4px',
    display: 'inline-block',
  },
};
