import React from 'react';

// ─── Colour / style helpers ───────────────────────────────────────────────────

function signalMeta(signal) {
  switch ((signal || '').toUpperCase()) {
    case 'STRONG_BUY':
      return {
        label: 'STRONG BUY',
        color: '#22c55e',
        bg: '#22c55e22',
        glow: '0 0 22px #22c55e88, 0 0 6px #22c55ecc',
        border: '#22c55e66',
      };
    case 'BUY':
      return {
        label: 'BUY',
        color: '#22c55e',
        bg: '#22c55e18',
        glow: 'none',
        border: '#22c55e44',
      };
    case 'WEAK_BUY':
      return {
        label: 'WEAK BUY',
        color: '#86efac',
        bg: '#86efac12',
        glow: 'none',
        border: '#86efac33',
      };
    case 'WAIT':
      return {
        label: 'WAIT',
        color: '#94a3b8',
        bg: '#94a3b815',
        glow: 'none',
        border: '#94a3b833',
      };
    case 'WEAK_SELL':
      return {
        label: 'WEAK SELL',
        color: '#fca5a5',
        bg: '#fca5a512',
        glow: 'none',
        border: '#fca5a533',
      };
    case 'SELL':
      return {
        label: 'SELL',
        color: '#ef4444',
        bg: '#ef444418',
        glow: 'none',
        border: '#ef444444',
      };
    case 'STRONG_SELL':
      return {
        label: 'STRONG SELL',
        color: '#ef4444',
        bg: '#ef444422',
        glow: '0 0 22px #ef444488, 0 0 6px #ef4444cc',
        border: '#ef444466',
      };
    default:
      return {
        label: signal || '—',
        color: '#94a3b8',
        bg: '#94a3b815',
        glow: 'none',
        border: '#94a3b833',
      };
  }
}

function zoneChipMeta(zone_signal) {
  switch ((zone_signal || '').toUpperCase()) {
    case 'BUY':  return { color: '#22c55e', bg: '#22c55e18' };
    case 'SELL': return { color: '#ef4444', bg: '#ef444418' };
    default:     return { color: '#94a3b8', bg: '#94a3b815' };
  }
}

function phaseChipMeta(zone_phase) {
  switch ((zone_phase || '').toUpperCase()) {
    case 'COMPRESSION':  return { color: '#f59e0b', bg: '#f59e0b18' };
    case 'BREAKOUT':     return { color: '#22c55e', bg: '#22c55e18' };
    case 'ACCUMULATION': return { color: '#3b82f6', bg: '#3b82f618' };
    case 'TRAP':         return { color: '#a855f7', bg: '#a855f718' };
    case 'TRENDING':     return { color: '#22c55e', bg: '#22c55e15' };
    default:             return { color: '#94a3b8', bg: '#94a3b815' };
  }
}

function waveChipMeta(wave_label) {
  const l = (wave_label || '').toString().toUpperCase();
  if (l === '4')                           return { color: '#f59e0b', bg: '#f59e0b18' };
  if (['1', '2', '3'].includes(l))         return { color: '#22c55e', bg: '#22c55e18' };
  if (['A', 'B', 'C', '5'].includes(l))   return { color: '#ef4444', bg: '#ef444418' };
  return { color: '#94a3b8', bg: '#94a3b815' };
}

function volChipMeta(vol_ratio) {
  const v = Number(vol_ratio) || 0;
  if (v >= 1.5) return { color: '#22c55e', bg: '#22c55e18' };
  if (v >= 1.0) return { color: '#f59e0b', bg: '#f59e0b18' };
  return { color: '#94a3b8', bg: '#94a3b815' };
}

function atrBarColor(atr_ratio) {
  const r = Number(atr_ratio) || 0;
  if (r < 0.65) return '#ef4444';
  if (r < 0.80) return '#f59e0b';
  return '#22c55e';
}

function phaseDisplay(zone_phase) {
  switch ((zone_phase || '').toUpperCase()) {
    case 'ACCUMULATION': return { icon: '⚡', label: 'ACCUMULATING',  color: '#3b82f6' };
    case 'COMPRESSION':  return { icon: '●', label: 'COMPRESSION',    color: '#f59e0b' };
    case 'TRAP':         return { icon: '⚠', label: 'TRAP DETECTED',  color: '#a855f7' };
    case 'BREAKOUT':     return { icon: '▲', label: 'BREAKOUT',       color: '#22c55e' };
    case 'TRENDING':     return { icon: '→', label: 'TRENDING',       color: '#22c55e' };
    default:             return { icon: '?', label: zone_phase || 'UNKNOWN', color: '#94a3b8' };
  }
}

function positionSizeMeta(position_size) {
  switch ((position_size || '').toUpperCase()) {
    case 'FULL':    return { color: '#22c55e', bg: '#22c55e18' };
    case 'HALF':    return { color: '#f59e0b', bg: '#f59e0b18' };
    case 'QUARTER': return { color: '#3b82f6', bg: '#3b82f618' };
    default:        return { color: '#94a3b8', bg: '#94a3b815' };
  }
}

function directionArrow(direction) {
  switch ((direction || '').toUpperCase()) {
    case 'BULL':    return { arrow: '▲', color: '#22c55e' };
    case 'bear':
    case 'BEAR':    return { arrow: '▼', color: '#ef4444' };
    default:        return { arrow: '◆', color: '#94a3b8' };
  }
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function Chip({ label, color, bg, style }) {
  return (
    <span
      style={{
        display: 'inline-block',
        padding: '2px 7px',
        borderRadius: 4,
        fontSize: 10,
        fontFamily: '"Courier New", monospace',
        fontWeight: 700,
        letterSpacing: '0.06em',
        color,
        background: bg,
        border: `1px solid ${color}33`,
        ...style,
      }}
    >
      {label}
    </span>
  );
}

function StrengthBar({ strength, color }) {
  const pct = Math.max(0, Math.min(100, Number(strength) || 0));
  return (
    <div style={{ width: '100%' }}>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          fontSize: 10,
          color: '#64748b',
          fontFamily: '"Courier New", monospace',
          marginBottom: 3,
        }}
      >
        <span>STRENGTH</span>
        <span style={{ color }}>{pct}%</span>
      </div>
      <div
        style={{
          width: '100%',
          height: 5,
          background: '#1e293b',
          borderRadius: 3,
          overflow: 'hidden',
        }}
      >
        <div
          style={{
            width: `${pct}%`,
            height: '100%',
            background: color,
            borderRadius: 3,
            transition: 'width 0.6s ease',
          }}
        />
      </div>
    </div>
  );
}

function ATRBar({ atr_ratio }) {
  const r = Math.max(0, Math.min(1.5, Number(atr_ratio) || 0));
  const pct = (r / 1.5) * 100;
  const color = atrBarColor(r);
  const label =
    r < 0.65 ? 'TIGHT COMPRESSION' : r < 0.80 ? 'COMPRESSING' : 'NORMAL';
  return (
    <div style={{ width: '100%' }}>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          fontSize: 10,
          color: '#64748b',
          fontFamily: '"Courier New", monospace',
          marginBottom: 3,
        }}
      >
        <span>ATR RATIO</span>
        <span style={{ color }}>
          {r.toFixed(2)} — {label}
        </span>
      </div>
      <div
        style={{
          width: '100%',
          height: 4,
          background: '#1e293b',
          borderRadius: 3,
          overflow: 'hidden',
        }}
      >
        <div
          style={{
            width: `${pct}%`,
            height: '100%',
            background: color,
            borderRadius: 3,
            transition: 'width 0.6s ease',
          }}
        />
      </div>
    </div>
  );
}

// ─── Per-instrument card ──────────────────────────────────────────────────────

function InstrumentCard({ name, data }) {
  const sm = signalMeta(data.signal);
  const zm = zoneChipMeta(data.zone_signal);
  const pm = phaseChipMeta(data.zone_phase);
  const wm = waveChipMeta(data.wave_label);
  const vm = volChipMeta(data.vol_ratio);
  const phaseDsp = phaseDisplay(data.zone_phase);
  const psMeta = positionSizeMeta(data.position_size);
  const dir = directionArrow(data.direction);
  const isExpiry = !!data.is_expiry_day;
  const reasons = (data.reasoning || []).slice(0, 3);

  const cardStyle = {
    background: '#0f172a',
    border: isExpiry
      ? `1px solid #f59e0b55`
      : `1px solid #1e293b`,
    boxShadow: isExpiry
      ? '0 0 18px #f59e0b22, inset 0 0 0 1px #f59e0b22'
      : '0 2px 8px #00000044',
    borderRadius: 8,
    padding: '14px 16px',
    display: 'flex',
    flexDirection: 'column',
    gap: 10,
    flex: '1 1 280px',
    minWidth: 260,
    maxWidth: 380,
    fontFamily: '"Courier New", monospace',
    position: 'relative',
  };

  return (
    <div style={cardStyle}>
      {/* Card header: instrument name + direction + expiry badge */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span
            style={{
              fontSize: 13,
              fontWeight: 700,
              color: '#e2e8f0',
              letterSpacing: '0.1em',
            }}
          >
            {name}
          </span>
          <span style={{ fontSize: 13, color: dir.color }}>{dir.arrow}</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          {data.indecision_candle && (
            <Chip label="INDECISION" color="#f59e0b" bg="#f59e0b15" />
          )}
          {isExpiry && (
            <span
              style={{
                padding: '2px 8px',
                borderRadius: 4,
                fontSize: 10,
                fontWeight: 700,
                color: '#f59e0b',
                background: '#f59e0b18',
                border: '1px solid #f59e0b55',
                animation: 'gti-pulse 1.4s ease-in-out infinite',
              }}
            >
              EXPIRY DAY
            </span>
          )}
        </div>
      </div>

      {/* Large signal badge */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          padding: '10px 0',
          background: sm.bg,
          border: `1px solid ${sm.border}`,
          borderRadius: 6,
          boxShadow: sm.glow !== 'none' ? sm.glow : undefined,
        }}
      >
        <span
          style={{
            fontSize: 20,
            fontWeight: 700,
            color: sm.color,
            letterSpacing: '0.12em',
            textShadow: sm.glow !== 'none' ? sm.glow : undefined,
          }}
        >
          {sm.label}
        </span>
      </div>

      {/* Strength bar */}
      <StrengthBar strength={data.strength} color={sm.color} />

      {/* Four-layer chip row */}
      <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap' }}>
        <Chip
          label={`ZONE ${(data.zone_signal || 'WAIT').toUpperCase()}`}
          color={zm.color}
          bg={zm.bg}
        />
        <Chip
          label={(data.zone_phase || 'UNKNOWN').toUpperCase()}
          color={pm.color}
          bg={pm.bg}
        />
        <Chip
          label={`W${data.wave_label || '?'}`}
          color={wm.color}
          bg={wm.bg}
        />
        <Chip
          label={`VOL ${Number(data.vol_ratio || 0).toFixed(1)}×`}
          color={vm.color}
          bg={vm.bg}
        />
      </div>

      {/* Market phase large display */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '7px 10px',
          background: `${phaseDsp.color}10`,
          border: `1px solid ${phaseDsp.color}33`,
          borderRadius: 6,
        }}
      >
        <span style={{ fontSize: 16, lineHeight: 1 }}>{phaseDsp.icon}</span>
        <span
          style={{
            fontSize: 13,
            fontWeight: 700,
            color: phaseDsp.color,
            letterSpacing: '0.1em',
          }}
        >
          {phaseDsp.label}
        </span>
        {data.wave_type && (
          <span
            style={{
              marginLeft: 'auto',
              fontSize: 10,
              color: '#64748b',
              letterSpacing: '0.06em',
            }}
          >
            {(data.wave_type || '').toUpperCase()}
            {data.wave_confidence != null
              ? ` (${data.wave_confidence}%)`
              : ''}
          </span>
        )}
      </div>

      {/* Zone levels */}
      <div style={{ display: 'flex', gap: 12 }}>
        <div
          style={{
            flex: 1,
            background: '#22c55e0d',
            border: '1px solid #22c55e22',
            borderRadius: 5,
            padding: '5px 8px',
            display: 'flex',
            flexDirection: 'column',
            gap: 1,
          }}
        >
          <span style={{ fontSize: 9, color: '#64748b', letterSpacing: '0.08em' }}>D: DEMAND</span>
          <span style={{ fontSize: 13, fontWeight: 700, color: '#22c55e' }}>
            {data.demand_zone_mid != null
              ? Number(data.demand_zone_mid).toLocaleString('en-IN')
              : '—'}
          </span>
          {data.at_demand && (
            <span style={{ fontSize: 9, color: '#22c55e', letterSpacing: '0.06em' }}>
              ● AT ZONE
            </span>
          )}
        </div>
        <div
          style={{
            flex: 1,
            background: '#ef44440d',
            border: '1px solid #ef444422',
            borderRadius: 5,
            padding: '5px 8px',
            display: 'flex',
            flexDirection: 'column',
            gap: 1,
          }}
        >
          <span style={{ fontSize: 9, color: '#64748b', letterSpacing: '0.08em' }}>S: SUPPLY</span>
          <span style={{ fontSize: 13, fontWeight: 700, color: '#ef4444' }}>
            {data.supply_zone_mid != null
              ? Number(data.supply_zone_mid).toLocaleString('en-IN')
              : '—'}
          </span>
          {data.at_supply && (
            <span style={{ fontSize: 9, color: '#ef4444', letterSpacing: '0.06em' }}>
              ● AT ZONE
            </span>
          )}
        </div>
      </div>

      {/* ATR bar */}
      <ATRBar atr_ratio={data.atr_ratio} />

      {/* Breakout direction (if present) */}
      {data.breakout_dir && (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            padding: '5px 10px',
            background: data.breakout_dir === 'UP' ? '#22c55e12' : '#ef444412',
            border: `1px solid ${data.breakout_dir === 'UP' ? '#22c55e44' : '#ef444444'}`,
            borderRadius: 5,
          }}
        >
          <span
            style={{
              fontSize: 13,
              color: data.breakout_dir === 'UP' ? '#22c55e' : '#ef4444',
            }}
          >
            {data.breakout_dir === 'UP' ? '▲' : '▼'}
          </span>
          <span
            style={{
              fontSize: 11,
              fontWeight: 700,
              color: data.breakout_dir === 'UP' ? '#22c55e' : '#ef4444',
              letterSpacing: '0.08em',
            }}
          >
            BREAKOUT {data.breakout_dir}
          </span>
        </div>
      )}

      {/* Action + Strike guidance box */}
      <div
        style={{
          background: '#1e293b',
          border: '1px solid #334155',
          borderRadius: 6,
          padding: '9px 11px',
          display: 'flex',
          flexDirection: 'column',
          gap: 5,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 6 }}>
          <span
            style={{
              fontSize: 11,
              fontWeight: 700,
              color: sm.color,
              letterSpacing: '0.06em',
              flexShrink: 1,
            }}
          >
            {data.action || '—'}
          </span>
          <Chip
            label={(data.position_size || 'NONE').toUpperCase()}
            color={psMeta.color}
            bg={psMeta.bg}
          />
        </div>
        {data.strike_guidance && (
          <span
            style={{
              fontSize: 11,
              color: '#cbd5e1',
              letterSpacing: '0.04em',
            }}
          >
            {data.strike_guidance}
          </span>
        )}
      </div>

      {/* Reasoning bullets */}
      {reasons.length > 0 && (
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: 3,
            padding: '6px 10px',
            background: '#0a0e1a',
            borderRadius: 5,
            border: '1px solid #1e293b',
          }}
        >
          {reasons.map((r, i) => (
            <div
              key={i}
              style={{
                display: 'flex',
                alignItems: 'flex-start',
                gap: 6,
                fontSize: 10,
                color: '#94a3b8',
                lineHeight: 1.4,
              }}
            >
              <span style={{ color: '#475569', flexShrink: 0 }}>▸</span>
              <span>{r}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── Main panel ───────────────────────────────────────────────────────────────

export default function GTIPanel({ gtiData }) {
  const instruments = gtiData ? Object.keys(gtiData) : [];
  const hasData = instruments.length > 0;

  return (
    <div
      style={{
        background: '#0a0e1a',
        border: '1px solid #1e293b',
        borderRadius: 10,
        padding: '14px 16px',
        fontFamily: '"Courier New", monospace',
        display: 'flex',
        flexDirection: 'column',
        gap: 14,
      }}
    >
      {/* Keyframe injection for pulse animation */}
      <style>{`
        @keyframes gti-pulse {
          0%, 100% { opacity: 1; box-shadow: 0 0 4px #f59e0b44; }
          50%       { opacity: 0.65; box-shadow: 0 0 10px #f59e0b88; }
        }
      `}</style>

      {/* Panel header */}
      <div
        style={{
          display: 'flex',
          alignItems: 'baseline',
          gap: 8,
          borderBottom: '1px solid #1e293b',
          paddingBottom: 10,
        }}
      >
        <span
          style={{
            fontSize: 13,
            fontWeight: 700,
            color: '#e2e8f0',
            letterSpacing: '0.12em',
          }}
        >
          GTI SIGNAL
        </span>
        <span
          style={{
            fontSize: 10,
            color: '#475569',
            letterSpacing: '0.08em',
          }}
        >
          Ghost Trader Intelligence
        </span>
        {hasData && (
          <span
            style={{
              marginLeft: 'auto',
              fontSize: 10,
              color: '#22c55e',
              background: '#22c55e15',
              border: '1px solid #22c55e33',
              borderRadius: 4,
              padding: '1px 7px',
              letterSpacing: '0.06em',
            }}
          >
            {instruments.length} ACTIVE
          </span>
        )}
      </div>

      {/* Content */}
      {!hasData ? (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: '40px 0',
            color: '#334155',
            fontSize: 12,
            letterSpacing: '0.1em',
          }}
        >
          Waiting for GTI data…
        </div>
      ) : (
        <div
          style={{
            display: 'flex',
            flexWrap: 'wrap',
            gap: 12,
          }}
        >
          {instruments.map((name) => {
            const data = gtiData[name];
            if (!data) return null;
            return <InstrumentCard key={name} name={name} data={data} />;
          })}
        </div>
      )}
    </div>
  );
}
