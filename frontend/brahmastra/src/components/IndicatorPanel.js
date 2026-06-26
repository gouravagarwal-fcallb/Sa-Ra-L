import React from 'react';

export default function IndicatorPanel({ indicators }) {
  const instruments = indicators ? Object.keys(indicators) : [];

  return (
    <div style={styles.panel}>
      <div style={styles.headerRow}>
        <span style={styles.title}>INDICATORS</span>
        <span style={{ fontSize: 11, color: '#334155', letterSpacing: 1 }}>5m PRIMARY</span>
      </div>

      {instruments.length === 0
        ? <div style={styles.empty}>No indicator data yet</div>
        : instruments.map(inst => (
            <InstrumentBlock key={inst} instrument={inst}
                             data={indicators[inst] || {}} />
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
        {/* ── Trend ─────────────────────────────────────────────── */}
        <Row label="EMA 5m"      val={data.ema_structure}  kind="struct" />
        <Row label="EMA 1h"      val={data.ema_1h_bias}    kind="struct" />
        <Row label="EMA 1W"      val={data.ema_1w_bias}    kind="struct" />
        <Row label="Supertrend"  val={data.supertrend_dir} kind="signal" />
        <Row label="ST Flip"
             val={data.supertrend_flipped != null ? (data.supertrend_flipped ? '⚡ FLIP' : '-') : null}
             kind={data.supertrend_flipped ? 'warn' : 'ok'} />
        <Row label="Ichimoku"    val={data.ichimoku_bias}   kind="ichi" />
        <Row label="TK Cross"    val={data.tk_cross}        kind="signal" />
        <Row label="Cloud"       val={data.price_vs_cloud}  kind="cloud" />
        <Row label="Ichi Str"
             val={data.ichimoku_strength != null ? `${data.ichimoku_strength}/6` : null}
             kind={data.ichimoku_strength >= 4 ? 'bull' : data.ichimoku_strength <= 2 ? 'bear' : 'neutral'}
             raw={data.ichimoku_strength} />
        {/* ── Momentum ──────────────────────────────────────────── */}
        <Row label="RSI"
             val={data.rsi != null ? data.rsi.toFixed(1) : null}
             kind="rsi" raw={data.rsi} />
        <Row label="MACD Cross"  val={data.macd_cross}    kind="signal" />
        <Row label="MACD 0-line" val={data.macd_zero_cross} kind="signal" />
        <Row label="MACD Hist"
             val={data.macd_hist != null ? data.macd_hist.toFixed(2) : null}
             kind="num" raw={data.macd_hist} />
        <Row label="StochRSI K"
             val={data.stoch_rsi_k != null ? data.stoch_rsi_k.toFixed(1) : null}
             kind="rsi" raw={data.stoch_rsi_k} />
        <Row label="StochRSI D"
             val={data.stoch_rsi_d != null ? data.stoch_rsi_d.toFixed(1) : null}
             kind="rsi" raw={data.stoch_rsi_d} />
        <Row label="Stoch Cross" val={data.stoch_rsi_signal} kind="signal" />
        <Row label="ROC"
             val={data.roc != null ? (data.roc >= 0 ? '+' : '') + data.roc.toFixed(2) + '%' : null}
             kind="num" raw={data.roc} />
        {/* ── Volatility ────────────────────────────────────────── */}
        <Row label="BB %B"
             val={data.bb_pct_b != null ? data.bb_pct_b.toFixed(2) : null}
             kind="num" raw={data.bb_pct_b} />
        <Row label="BB Squeeze"
             val={data.bb_squeeze != null ? (data.bb_squeeze ? 'SQUEEZE' : 'NORMAL') : null}
             kind={data.bb_squeeze ? 'warn' : 'ok'} />
        <Row label="BB Breakout" val={data.bb_breakout}  kind="signal" />
        <Row label="ATR"
             val={data.atr != null ? data.atr.toFixed(1) : null} kind="neutral" />
        {/* ── Volume / S-R ──────────────────────────────────────── */}
        <Row label="VWAP"
             val={data.vwap_position} kind="signal" />
        <Row label="ADX"
             val={data.adx != null ? data.adx.toFixed(1) : null}
             kind="adx" raw={data.adx} />
        <Row label="ADX Trend"   val={data.adx_trend}    kind="struct" />
        <Row label="+DI / -DI"
             val={data.adx_plus_di != null && data.adx_minus_di != null
               ? `${data.adx_plus_di.toFixed(1)} / ${data.adx_minus_di.toFixed(1)}` : null}
             kind={data.adx_plus_di > data.adx_minus_di ? 'bull' : 'bear'} />
        <Row label="OBV Rising"
             val={data.obv_rising != null ? (data.obv_rising ? 'YES' : 'NO') : null}
             kind={data.obv_rising ? 'bull' : 'bear'} />
        {/* ── Pattern / Confluence ──────────────────────────────── */}
        <Row label="Pattern"     val={data.pattern_name}  kind="signal" />
        <Row label="Confluence"
             val={data.confluence_score != null ? data.confluence_score.toFixed(0) : null}
             kind="score" raw={data.confluence_score} />
        <Row label="Conf Str"    val={data.confluence_strength} kind="struct" />
        <Row label="Agreement"
             val={data.confluence_agreement != null
               ? (data.confluence_agreement * 100).toFixed(0) + '%' : null}
             kind="neutral" />
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
  title: { fontSize: 12, fontWeight: 700, letterSpacing: 2, color: '#475569' },
  empty: { color: '#475569', fontSize: 13, textAlign: 'center', padding: 16 },
  instBlock: {
    background: '#1e293b', borderRadius: 6, padding: 12,
    border: '1px solid #334155',
  },
  instLabel: { fontSize: 11, fontWeight: 700, letterSpacing: 2, color: '#475569', marginBottom: 8 },
  instEmpty: { color: '#475569', fontSize: 12, padding: '4px 0' },
  grid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fill, minmax(155px, 1fr))',
    gap: '5px 12px',
  },
  row: { display: 'flex', justifyContent: 'space-between', alignItems: 'center' },
  rowLabel: { fontSize: 11, color: '#475569', letterSpacing: 0.5 },
  rowVal: { fontSize: 12, fontWeight: 700 },
};
