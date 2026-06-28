import React, { useEffect, useState, useCallback } from 'react';
import { api, C, SH } from '../api';

/**
 * Pre-Market Analysis (global basis) — the day's most important read.
 * Global markets (SGX Nifty, S&P, Dow, Nikkei, Hang Seng, Crude, Gold, USD/INR)
 * + India internals (VIX, PCR, Max Pain, FII) rolled into one tradeable bias,
 * with the full score breakdown, risk events and news.
 */
const dirColor = (d) => ({ UP: C.green, DOWN: C.red, FLAT: C.dim }[d] || C.dim);
const arrow = (d) => ({ UP: '▲', DOWN: '▼', FLAT: '■' }[d] || '·');

function biasColor(label = '') {
  const u = label.toUpperCase();
  if (u.includes('STRONGLY_BULL')) return '#0f8a3c';
  if (u.includes('BULL')) return C.green;
  if (u.includes('STRONGLY_BEAR')) return '#b91c1c';
  if (u.includes('BEAR')) return C.red;
  return C.dim;
}

export default function PreMarketPage() {
  const [data, setData] = useState(null);
  const [err, setErr]   = useState(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback((force) => {
    setLoading(true);
    api.premarket(force)
      .then(d => { setData(d); setErr(null); })
      .catch(e => setErr(String(e)))
      .finally(() => setLoading(false));
  }, []);
  useEffect(() => { load(false); }, [load]);

  const b = data?.briefing || {};
  const de = data?.direction_engine || {};
  const score = b.bias_score;
  const pct = score == null ? 50 : Math.max(0, Math.min(100, (score + 100) / 2));  // -100..100 → 0..100

  return (
    <div>
      <div style={S.headRow}>
        <h2 style={S.h2}>Pre-Market Analysis <span style={{ color: C.dim, fontWeight: 400, fontSize: 14 }}>· global basis · {data?.date || ''}</span></h2>
        <button style={S.refresh} disabled={loading} onClick={() => load(true)}>
          {loading ? 'Fetching…' : '↻ Refresh'}
        </button>
      </div>
      {err && <div style={{ color: C.red, marginBottom: 10 }}>{err}</div>}

      {/* Headline bias */}
      <div style={S.card}>
        <div style={S.cardTitle}>TODAY'S BIAS</div>
        {b.available ? (
          <>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 14 }}>
              <span style={{ fontSize: 26, fontWeight: 800, color: biasColor(b.bias_label) }}>{(b.bias_label || '—').replace(/_/g, ' ')}</span>
              <span style={{ fontSize: 16, color: C.dim }}>score <b style={{ color: biasColor(b.bias_label) }}>{score > 0 ? '+' : ''}{score}</b> / 100</span>
            </div>
            <div style={S.gaugeTrack}>
              <div style={{ ...S.gaugeFill, left: `${Math.min(pct, 50)}%`, width: `${Math.abs(pct - 50)}%`, background: biasColor(b.bias_label) }} />
              <div style={S.gaugeMid} />
            </div>
            <div style={S.gaugeLabels}><span>Bearish −100</span><span>Neutral 0</span><span>Bullish +100</span></div>
          </>
        ) : <div style={{ color: C.dim }}>{b.reason || 'unavailable (needs market data)'}</div>}
        {de.available && (
          <div style={{ marginTop: 10, fontSize: 12, color: C.dim }}>
            Direction engine (Dow/Gift/VIX/Sensex): <b style={{ color: biasColor(de.direction) }}>{de.direction}</b> · score {de.score} — {de.reason}
          </div>
        )}
      </div>

      {/* Global markets */}
      <div style={S.card}>
        <div style={S.cardTitle}>GLOBAL MARKETS</div>
        {b.available && b.global_markets?.length ? (
          <table style={S.table}>
            <thead><tr>{['Market', 'Symbol', 'Price', 'Change %', 'Signal'].map(h => <th key={h} style={S.th}>{h}</th>)}</tr></thead>
            <tbody>
              {b.global_markets.map(m => (
                <tr key={m.symbol} style={S.tr}>
                  <td style={S.tdName}>{m.name}</td>
                  <td style={S.tdDim}>{m.symbol}</td>
                  <td style={S.td}>{m.price != null ? m.price.toLocaleString('en-IN') : (m.error ? '—' : '—')}</td>
                  <td style={{ ...S.td, color: dirColor(m.direction), fontWeight: 700 }}>
                    {m.change_pct != null ? `${m.change_pct > 0 ? '+' : ''}${m.change_pct}%` : '—'}
                  </td>
                  <td style={{ ...S.td, color: dirColor(m.direction), fontWeight: 700 }}>{arrow(m.direction)} {m.direction}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <div style={{ color: C.dim }}>{b.reason || 'no global data'}</div>}
      </div>

      <div style={S.twoCol}>
        {/* India internals */}
        <div style={{ ...S.card, flex: 1 }}>
          <div style={S.cardTitle}>INDIA INTERNALS</div>
          <Metric label="India VIX" value={b.india_vix != null ? b.india_vix : '—'} note={b.vix_trend} />
          <Metric label="PCR (Put/Call)" value={b.pcr != null ? b.pcr : '—'} note={b.pcr_label} noteColor={biasColor(b.pcr_label)} />
          <Metric label="Max Pain" value={b.max_pain != null ? b.max_pain : '—'} />
          <Metric label="FII net (₹ cr)" value={b.fii_net_cr != null ? b.fii_net_cr : '—'}
                  valueColor={b.fii_net_cr == null ? C.text : b.fii_net_cr >= 0 ? C.green : C.red} />
        </div>

        {/* Score breakdown */}
        <div style={{ ...S.card, flex: 1 }}>
          <div style={S.cardTitle}>SCORE BREAKDOWN (each factor's points)</div>
          {b.score_breakdown && Object.keys(b.score_breakdown).length ? (
            <table style={S.table}>
              <tbody>
                {Object.entries(b.score_breakdown).map(([k, v]) => (
                  <tr key={k} style={S.tr}>
                    <td style={S.tdName}>{k.replace(/_/g, ' ')}</td>
                    <td style={{ ...S.td, textAlign: 'right', fontWeight: 700, color: v > 0 ? C.green : v < 0 ? C.red : C.dim }}>
                      {v > 0 ? '+' : ''}{v}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <div style={{ color: C.dim }}>—</div>}
        </div>
      </div>

      {/* Risk events + news */}
      <div style={S.twoCol}>
        <div style={{ ...S.card, flex: 1 }}>
          <div style={S.cardTitle}>HIGH-RISK EVENTS TODAY</div>
          {b.high_risk_events?.length
            ? b.high_risk_events.map((e, i) => <div key={i} style={S.eventRow}>⚠ {e}</div>)
            : <div style={{ color: C.dim }}>None flagged.</div>}
        </div>
        <div style={{ ...S.card, flex: 1 }}>
          <div style={S.cardTitle}>MARKET NEWS</div>
          {b.news?.length
            ? b.news.map((n, i) => <div key={i} style={S.newsRow}>{n.title || n.headline || String(n)}</div>)
            : <div style={{ color: C.dim }}>No headlines.</div>}
        </div>
      </div>
    </div>
  );
}

function Metric({ label, value, note, noteColor, valueColor }) {
  return (
    <div style={S.metric}>
      <span style={{ color: C.dim, fontSize: 13 }}>{label}</span>
      <span>
        <b style={{ fontSize: 16, color: valueColor || C.text }}>{value}</b>
        {note ? <span style={{ marginLeft: 8, fontSize: 11, fontWeight: 700, color: noteColor || C.dim }}>{note}</span> : null}
      </span>
    </div>
  );
}

const S = {
  headRow: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 },
  h2: { fontSize: 20, margin: 0, color: C.text },
  refresh: { background: C.blue, border: 'none', color: '#fff', fontWeight: 700, fontSize: 13, padding: '7px 16px', borderRadius: 6, cursor: 'pointer' },
  card: { background: C.panel, border: `1px solid ${C.border}`, borderRadius: 10, padding: 16, marginBottom: 12, boxShadow: SH.card },
  cardTitle: { fontSize: 13, fontWeight: 700, letterSpacing: 0.6, color: C.cyan, marginBottom: 12 },
  gaugeTrack: { position: 'relative', height: 12, background: '#eef2f8', borderRadius: 6, marginTop: 14, border: `1px solid ${C.border}` },
  gaugeFill: { position: 'absolute', top: 0, height: '100%', borderRadius: 6, opacity: 0.85 },
  gaugeMid: { position: 'absolute', left: '50%', top: -3, bottom: -3, width: 2, background: C.dim },
  gaugeLabels: { display: 'flex', justifyContent: 'space-between', fontSize: 10, color: C.dim, marginTop: 6 },
  twoCol: { display: 'flex', gap: 12, flexWrap: 'wrap' },
  table: { width: '100%', borderCollapse: 'collapse', fontSize: 13 },
  th: { textAlign: 'left', padding: '8px 10px', color: C.dim, borderBottom: `2px solid ${C.border}`, fontWeight: 700 },
  tr: { borderBottom: `1px solid ${C.border}` },
  td: { padding: '8px 10px', color: C.text },
  tdName: { padding: '8px 10px', color: C.text, fontWeight: 700 },
  tdDim: { padding: '8px 10px', color: C.dim, fontSize: 12 },
  metric: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '8px 0', borderBottom: `1px solid ${C.border}` },
  eventRow: { color: C.amber, fontSize: 13, padding: '4px 0', fontWeight: 600 },
  newsRow: { color: C.text, fontSize: 12.5, padding: '5px 0', borderBottom: `1px solid ${C.border}` },
};
