import React, { useEffect, useState, useCallback } from 'react';
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from 'recharts';
import { api, C, SH } from '../api';

/**
 * Scoreboard (Phase 2) — rolling trust per strategy (↑/↓) + book-vs-benchmark.
 * Read-only view of what the EOD commit job persisted; empty until one EOD runs.
 */
const CLASS_COLOR = (v = '') =>
  v.includes('Trusted') ? C.green
    : v.includes('over-filtered') ? C.amber
    : v.includes('Recalibration') || v.includes('Disable') ? C.red
    : C.dim;

function Spark({ history }) {
  const data = (history || []).map((h, i) => ({ i, v: h.trust_score }));
  if (data.length < 2) return <span style={{ color: C.dim, fontSize: 11 }}>—</span>;
  const first = data[0].v, last = data[data.length - 1].v;
  const col = last >= first ? C.green : C.red;
  return (
    <div style={{ width: 90, height: 28 }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 4, right: 2, bottom: 0, left: 2 }}>
          <Line type="monotone" dataKey="v" stroke={col} dot={false} strokeWidth={1.6} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

export default function ScoreboardPage() {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const load = useCallback(() => {
    api.scoreboard(20).then(setData).catch(e => setErr(String(e)));
  }, []);
  useEffect(() => { load(); const id = setInterval(load, 20000); return () => clearInterval(id); }, [load]);

  if (err) return <div style={{ color: C.red }}>{err}</div>;
  if (!data) return <div style={{ color: C.dim, padding: 20 }}>Loading scoreboard…</div>;

  const empty = (!data.strategies || data.strategies.length === 0) && (!data.book || data.book.length === 0);

  return (
    <div>
      <h2 style={S.h2}>Scoreboard <span style={{ color: C.dim, fontWeight: 400, fontSize: 14 }}>· rolling {data.window} sessions</span></h2>
      {empty && <div style={S.note}>{data.note || 'No committed sessions yet. Run the EOD commit on a trading day (Closure Report → "Commit trust (EOD)").'}</div>}

      {/* Book vs benchmark */}
      {data.book?.length > 0 && (
        <div style={S.panel}>
          <div style={S.title}>BOOK P&L vs INDEX (per committed session)</div>
          <ResponsiveContainer width="100%" height={200}>
            <LineChart data={data.book} margin={{ top: 6, right: 10, bottom: 0, left: -8 }}>
              <CartesianGrid stroke={C.border} strokeDasharray="2 4" />
              <XAxis dataKey="date" tick={{ fontSize: 10, fill: C.dim }} />
              <YAxis tick={{ fontSize: 10, fill: C.dim }} />
              <Tooltip contentStyle={{ fontSize: 12, borderRadius: 6, border: `1px solid ${C.border}` }} />
              <Line type="monotone" dataKey="net_pnl" name="Book net P&L (₹)" stroke={C.blue} dot strokeWidth={2} isAnimationActive={false} />
              <Line type="monotone" dataKey="index_buy_hold_pct" name="NIFTY buy-hold %" stroke={C.amber} dot strokeWidth={1.5} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Trust table */}
      {data.strategies?.length > 0 && (
        <div style={S.panel}>
          <div style={S.title}>STRATEGY TRUST (EWMA, rolling)</div>
          <table style={S.table}>
            <thead><tr>{['Strategy', 'Trust', 'Δ', 'Trend', 'Weight', 'Classification', 'Sessions'].map(h => <th key={h} style={S.th}>{h}</th>)}</tr></thead>
            <tbody>
              {data.strategies.map(s => (
                <tr key={s.name} style={S.tr}>
                  <td style={S.tdName}>{s.name}</td>
                  <td style={{ ...S.td, fontWeight: 800 }}>{s.trust_score ?? '—'}</td>
                  <td style={{ ...S.td, color: (s.trust_delta || 0) > 0 ? C.green : (s.trust_delta || 0) < 0 ? C.red : C.dim, fontWeight: 700 }}>
                    {s.trust_delta > 0 ? '▲' : s.trust_delta < 0 ? '▼' : '·'} {s.trust_delta != null ? Math.abs(s.trust_delta) : ''}
                  </td>
                  <td style={S.td}><Spark history={s.history} /></td>
                  <td style={S.td}>{s.trust_weight ?? '—'}</td>
                  <td style={{ ...S.td, color: CLASS_COLOR(s.classification), fontWeight: 700 }}>{s.classification}</td>
                  <td style={{ ...S.td, color: C.dim }}>{s.sessions}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div style={{ fontSize: 11, color: C.dim, marginTop: 8 }}>
            Trust is a slow EWMA (α=0.3) over committed sessions — one day nudges, it doesn't whipsaw.
            "Weight" is the advisory capital-attention multiplier (0–1). Advisory only; it never arms live.
          </div>
        </div>
      )}
    </div>
  );
}

const S = {
  h2: { fontSize: 20, marginBottom: 12, color: C.text },
  note: { background: '#fff7ed', border: '1px solid #fed7aa', color: '#b45309', fontSize: 12.5, borderRadius: 8, padding: '12px 14px', marginBottom: 12 },
  panel: { background: C.panel, border: `1px solid ${C.border}`, borderRadius: 10, padding: 14, marginBottom: 12, boxShadow: SH.card },
  title: { fontSize: 13, fontWeight: 700, letterSpacing: 0.6, color: C.cyan, marginBottom: 10 },
  table: { width: '100%', borderCollapse: 'collapse', fontSize: 12.5 },
  th: { textAlign: 'left', padding: '7px 9px', color: C.dim, borderBottom: `2px solid ${C.border}`, fontWeight: 700 },
  tr: { borderBottom: `1px solid ${C.border}` },
  td: { padding: '7px 9px', color: C.text, verticalAlign: 'middle' },
  tdName: { padding: '7px 9px', color: C.text, fontWeight: 700 },
};
