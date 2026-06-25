import React, { useState } from 'react';

const TIMEFRAMES = ['5m', '15m'];

export default function IndicatorPanel({ indicators }) {
  const [tf, setTf] = useState('5m');

  const instruments = indicators ? Object.keys(indicators) : [];

  return (
    <div style={styles.panel}>
      <div style={styles.headerRow}>
        <span style={styles.title}>INDICATORS</span>
        <div style={styles.tfTabs}>
          {TIMEFRAMES.map(t => (
            <button key={t} style={{ ...styles.tab, ...(tf === t ? styles.tabActive : {}) }}
                    onClick={() => setTf(t)}>
              {t}
            </button>
          ))}
        </div>
      </div>

      {instruments.length === 0
        ? <div style={styles.empty}>No indicator data yet</div>
        : instruments.map(inst => (
            <InstrumentBlock key={inst} instrument={inst}
                             data={(indicators[inst] || {})[tf] || {}} />
          ))
      }
    </div>
  );
}

function InstrumentBlock({ instrument, data }) {
  if (!data || Object.keys(data).length === 0) {
    return <div style={styles.instEmpty}>{instrument}: warming up…</div>;
  }
  return (
    <div style={styles.instBlock}>
      <div style={styles.instLabel}>{instrument}</div>
      <div style={styles.grid}>
        <Row label="EMA Structure" val={data.ema_structure} kind="struct" />
        <Row label="RSI" val={data.rsi != null ? data.rsi.toFixed(1) : null}
             kind="rsi" raw={data.rsi} />
        <Row label="MACD" val={data.macd_cross} kind="signal" />
        <Row label="MACD Hist" val={data.macd_hist != null ? data.macd_hist.toFixed(2) : null}
             kind="num" raw={data.macd_hist} />
        <Row label="BB %B" val={data.bb_pct_b != null ? data.bb_pct_b.toFixed(2) : null}
             kind="num" raw={data.bb_pct_b} />
        <Row label="BB Squeeze" val={data.bb_squeeze != null ? (data.bb_squeeze ? 'YES' : 'NO') : null}
             kind={data.bb_squeeze ? 'warn' : 'ok'} />
        <Row label="VWAP Pos" val={data.vwap_position} kind="signal" />
        <Row label="ADX" val={data.adx != null ? data.adx.toFixed(1) : null}
             kind="adx" raw={data.adx} />
        <Row label="ADX Trend" val={data.adx_trend} kind="struct" />
        <Row label="Supertrend" val={data.supertrend_dir} kind="signal" />
        <Row label="ST Flip" val={data.supertrend_flipped != null ? (data.supertrend_flipped ? 'YES' : '-') : null}
             kind={data.supertrend_flipped ? 'warn' : 'ok'} />
        <Row label="Ichimoku" val={data.ichimoku_bias} kind="ichi" />
        <Row label="TK Cross" val={data.tk_cross} kind="signal" />
        <Row label="Cloud" val={data.price_vs_cloud} kind="cloud" />
        <Row label="OBV Rising" val={data.obv_rising != null ? (data.obv_rising ? 'YES' : 'NO') : null}
             kind={data.obv_rising ? 'bull' : 'bear'} />
        <Row label="ROC" val={data.roc != null ? (data.roc >= 0 ? '+' : '') + data.roc.toFixed(2) + '%' : null}
             kind="num" raw={data.roc} />
        <Row label="Pattern" val={data.pattern_name} kind="signal" />
        <Row label="Confluence" val={data.confluence_score != null ? data.confluence_score.toFixed(0) : null}
             kind="score" raw={data.confluence_score} />
        <Row label="ATR" val={data.atr != null ? data.atr.toFixed(1) : null} kind="neutral" />
      </div>
    </div>
  );
}

function Row({ label, val, kind, raw }) {
  if (val == null || val === '' || val === undefined) return null;
  const color = kindColor(kind, raw, val);
  return (
    <div style={styles.row}>
      <span style={styles.rowLabel}>{label}</span>
      <span style={{ ...styles.rowVal, color }}>{val}</span>
    </div>
  );
}

function kindColor(kind, raw, val) {
  switch (kind) {
    case 'bull':    return '#22c55e';
    case 'bear':    return '#ef4444';
    case 'ok':      return '#22c55e';
    case 'warn':    return '#f59e0b';
    case 'rsi':
      if (raw == null) return '#94a3b8';
      if (raw > 70) return '#ef4444';
      if (raw < 30) return '#22c55e';
      return '#94a3b8';
    case 'adx':
      if (raw == null) return '#94a3b8';
      if (raw > 40) return '#f59e0b';
      if (raw > 25) return '#22c55e';
      return '#64748b';
    case 'score':
      if (raw == null) return '#94a3b8';
      if (raw >= 65) return '#22c55e';
      if (raw <= -65) return '#ef4444';
      return '#94a3b8';
    case 'struct':
    case 'ichi':
    case 'signal':
    case 'cloud':
      if (!val) return '#94a3b8';
      const v = val.toUpperCase();
      if (v.includes('BULL') || v.includes('UP') || v.includes('ABOVE')) return '#22c55e';
      if (v.includes('BEAR') || v.includes('DOWN') || v.includes('BELOW')) return '#ef4444';
      return '#94a3b8';
    case 'num':
      if (raw == null) return '#94a3b8';
      return raw > 0 ? '#22c55e' : raw < 0 ? '#ef4444' : '#94a3b8';
    default:
      return '#94a3b8';
  }
}

const styles = {
  panel: {
    background: '#0f172a', border: '1px solid #1e293b',
    borderRadius: 8, padding: 12,
    display: 'flex', flexDirection: 'column', gap: 10,
  },
  headerRow: { display: 'flex', alignItems: 'center', justifyContent: 'space-between' },
  title: { fontSize: 10, fontWeight: 700, letterSpacing: 2, color: '#475569' },
  tfTabs: { display: 'flex', gap: 4 },
  tab: {
    background: '#1e293b', border: '1px solid #334155',
    color: '#64748b', fontSize: 9, fontWeight: 700,
    padding: '2px 8px', borderRadius: 4, cursor: 'pointer',
    letterSpacing: 1,
  },
  tabActive: { background: '#3b82f6', borderColor: '#3b82f6', color: '#fff' },
  empty: { color: '#475569', fontSize: 12, textAlign: 'center', padding: 16 },
  instBlock: {
    background: '#1e293b', borderRadius: 6, padding: 10,
    border: '1px solid #334155',
  },
  instLabel: { fontSize: 9, fontWeight: 700, letterSpacing: 2, color: '#475569', marginBottom: 8 },
  instEmpty: { color: '#475569', fontSize: 11, padding: '4px 0' },
  grid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))',
    gap: '4px 12px',
  },
  row: { display: 'flex', justifyContent: 'space-between', alignItems: 'center' },
  rowLabel: { fontSize: 9, color: '#475569', letterSpacing: 0.5 },
  rowVal: { fontSize: 10, fontWeight: 700 },
};
