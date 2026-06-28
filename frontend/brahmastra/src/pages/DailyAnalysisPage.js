import React, { useEffect, useState, useCallback } from 'react';
import { api, C, SH } from '../api';

/** One place for the day: premarket bias, per-strategy signals, today's trades. */
export default function DailyAnalysisPage({ onOpen }) {
  const [data, setData] = useState(null);
  const [err, setErr]   = useState(null);
  const load = useCallback(() => { api.dailyAnalysis().then(setData).catch(e => setErr(String(e))); }, []);
  useEffect(() => { load(); const id = setInterval(load, 10000); return () => clearInterval(id); }, [load]);

  if (err) return <div style={{ color: C.red }}>Daily analysis error: {err}</div>;
  if (!data) return <div style={{ color: C.dim }}>Loading…</div>;
  const pm = data.premarket || {};

  return (
    <div>
      <h2 style={S.h2}>Daily Analysis — {data.date}</h2>

      <div style={S.card}>
        <div style={S.cardTitle}>Pre-market bias</div>
        {pm.available ? (
          <div style={{ display: 'flex', gap: 24, alignItems: 'center' }}>
            <Big label="Bias" value={pm.bias_label || '—'} />
            <Big label="Score" value={pm.score ?? '—'} />
            <Big label="India VIX" value={pm.india_vix ?? '—'} />
          </div>
        ) : <div style={{ color: C.dim }}>{pm.reason || 'unavailable (needs market data)'}</div>}
      </div>

      <div style={S.card}>
        <div style={S.cardTitle}>Live & paper strategies — latest signal</div>
        {(data.per_strategy || []).length === 0 ? <div style={{ color: C.dim }}>No live/paper strategies running.</div> :
          (data.per_strategy || []).map(s => (
            <div key={s.name} style={S.sigRow}>
              <div style={{ width: 180, fontWeight: 700, cursor: 'pointer' }} onClick={() => onOpen && onOpen(s.name)} title="Open strategy">
                <span style={{ color: C.blue, textDecoration: 'underline', textUnderlineOffset: 2 }}>{s.name}</span>
                {' '}<span style={{ color: C.dim, fontWeight: 400, fontSize: 10 }}>{s.status}</span>
              </div>
              <div style={{ flex: 1, color: C.text, fontSize: 12 }}>{s.last_signal || '—'}</div>
              <div style={{ width: 90, textAlign: 'right', color: ((s.real_pnl + s.paper_pnl) >= 0) ? C.green : C.red }}>
                Rs.{((s.real_pnl || 0) + (s.paper_pnl || 0)).toLocaleString('en-IN')}
              </div>
            </div>
          ))}
      </div>

      <div style={S.card}>
        <div style={S.cardTitle}>Today's trades ({(data.trades_today || []).length})</div>
        {(data.trades_today || []).length === 0 ? <div style={{ color: C.dim }}>No trades yet today.</div> : (
          <table style={S.table}>
            <thead><tr>{['Time', 'Strategy', 'Event', 'Instrument', 'Type', 'Strike', 'Price', 'P&L'].map(h => <th key={h} style={S.th}>{h}</th>)}</tr></thead>
            <tbody>
              {(data.trades_today || []).slice().reverse().map((t, i) => (
                <tr key={i} style={S.tr}>
                  <td style={S.td}>{t.time}</td><td style={S.td}>{t.strategy}</td>
                  <td style={S.td}>{t.event}</td><td style={S.td}>{t.instrument}</td>
                  <td style={S.td}>{t.option_type}</td><td style={S.td}>{t.strike}</td>
                  <td style={S.td}>{t.price}</td>
                  <td style={{ ...S.td, color: (parseFloat(t.pnl) || 0) >= 0 ? C.green : C.red }}>{t.pnl}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

function Big({ label, value }) {
  return <div><div style={{ fontSize: 10, color: C.dim }}>{label}</div><div style={{ fontSize: 20, fontWeight: 700 }}>{value}</div></div>;
}

const S = {
  h2: { fontSize: 20, marginBottom: 14, color: C.text },
  card: { background: C.panel, border: `1px solid ${C.border}`, borderRadius: 10, padding: 16, marginBottom: 12, boxShadow: SH.card },
  cardTitle: { fontSize: 13, fontWeight: 700, letterSpacing: 0.6, color: C.cyan, marginBottom: 12 },
  sigRow: { display: 'flex', alignItems: 'center', gap: 10, padding: '6px 0', borderBottom: `1px solid ${C.border}` },
  table: { width: '100%', borderCollapse: 'collapse', fontSize: 11 },
  th: { textAlign: 'left', padding: '6px 8px', color: C.dim, borderBottom: `1px solid ${C.border}` },
  tr: { borderBottom: `1px solid ${C.border}` },
  td: { padding: '5px 8px', color: C.text },
};
