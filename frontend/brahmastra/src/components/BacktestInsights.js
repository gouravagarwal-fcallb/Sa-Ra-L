import React, { useEffect, useState } from 'react';
import { api, C, SH } from '../api';

/** Analysis report across all strategies' backtests: ranking (who's best),
 *  per-strategy strengths / weaknesses / best-worst scenario / guardrails /
 *  missing-plugin ideas, and fleet-level lessons. Advisory, derived from the
 *  recorded backtest metrics. */
export default function BacktestInsights() {
  const [d, setD] = useState(null);
  const [err, setErr] = useState(null);
  const [open, setOpen] = useState(true);

  const load = () => api.backtestInsights().then(setD).catch(e => setErr(String(e)));
  useEffect(() => { load(); }, []);

  const rup = (v) => v == null ? '—' : `₹${Number(v).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
  const vcol = (v) => v === 'Strong' ? C.green : v === 'Promising' ? C.blue : v === 'Marginal' ? C.amber : C.red;

  if (err) return <div style={{ color: C.red, margin: '10px 0' }}>Insights error: {err}</div>;
  if (!d) return <div style={{ color: C.dim, margin: '10px 0' }}>Building strategy analysis…</div>;

  return (
    <div style={S.wrap}>
      <div style={S.head}>
        <div>
          <div style={S.title}>Strategy Analysis — which works, where, and how to sharpen it</div>
          <div style={S.sub}>{d.generated_note} · {d.count} tradeable strategies · best on evidence: <b>{d.best_overall || '—'}</b></div>
        </div>
        <button style={S.toggle} onClick={() => setOpen(o => !o)}>{open ? 'Hide' : 'Show'}</button>
      </div>

      {open && <>
        {/* Ranking */}
        <div style={S.rankWrap}>
          <table style={S.table}>
            <thead><tr>{['#', 'Strategy', 'Quality', 'Verdict', 'Net P&L', 'PF', 'Sharpe', 'Win %'].map(h =>
              <th key={h} style={S.th}>{h}</th>)}</tr></thead>
            <tbody>
              {d.ranking.map(r => (
                <tr key={r.name} style={S.tr}>
                  <td style={S.td}>{r.rank}</td>
                  <td style={{ ...S.td, fontWeight: 700 }}>{r.name}</td>
                  <td style={S.td}><b>{r.quality}</b>/100</td>
                  <td style={{ ...S.td, color: vcol(r.verdict), fontWeight: 700 }}>{r.verdict}</td>
                  <td style={{ ...S.td, color: (r.pnl || 0) >= 0 ? C.green : C.red }}>{rup(r.pnl)}</td>
                  <td style={S.td}>{r.profit_factor ?? '—'}</td>
                  <td style={S.td}>{r.sharpe ?? '—'}</td>
                  <td style={S.td}>{r.win_rate != null ? `${r.win_rate}%` : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Fleet lessons */}
        <div style={S.lessons}>
          <div style={S.lessonsHd}>FLEET LESSONS</div>
          {d.fleet_lessons.map((l, i) => <div key={i} style={S.lesson}>• {mdBold(l)}</div>)}
        </div>

        {/* Per-strategy cards */}
        <div style={S.cards}>
          {d.strategies.filter(a => a.tradeable).map(a => (
            <div key={a.name} style={S.card}>
              <div style={S.cardHd}>
                <span style={{ fontWeight: 700 }}>{a.name}</span>
                <span style={{ ...S.pill, background: vcol(a.verdict) + '22', color: vcol(a.verdict) }}>{a.verdict} · {a.quality}/100</span>
              </div>
              <div style={S.cardMeta}>
                {a.trades} trades · {rup(a.pnl)} · PF {a.profit_factor ?? '—'} · Sharpe {a.sharpe ?? '—'}
                {a.rr ? ` · R:R ${a.rr}:1` : ''}
              </div>
              {a.best_scenario && <div style={S.line}><b style={{ color: C.green }}>Best:</b> {a.best_scenario.name} ({a.best_scenario.kind}) {rup(a.best_scenario.pnl)}</div>}
              {a.worst_scenario && a.worst_scenario.pnl < 0 && <div style={S.line}><b style={{ color: C.red }}>Worst:</b> {a.worst_scenario.name} ({a.worst_scenario.kind}) {rup(a.worst_scenario.pnl)}</div>}
              <Block label="Strengths" items={a.strengths} color={C.green} />
              <Block label="Weaknesses / where it fails" items={a.weaknesses} color={C.red} />
              <Block label="Guardrails to add" items={a.guardrails} color={C.amber} />
              <Block label="Missing — build this to sharpen it" items={a.missing} color={C.blue} />
            </div>
          ))}
          {/* Non-tradeable notes */}
          {d.strategies.filter(a => !a.tradeable).map(a => (
            <div key={a.name} style={{ ...S.card, opacity: 0.7 }}>
              <div style={S.cardHd}><span style={{ fontWeight: 700 }}>{a.name}</span><span style={S.pillDim}>no backtest trades</span></div>
              <div style={S.line}>{a.note}</div>
            </div>
          ))}
        </div>
      </>}
    </div>
  );
}

function Block({ label, items, color }) {
  if (!items || !items.length) return null;
  return (
    <div style={{ marginTop: 6 }}>
      <div style={{ fontSize: 11, fontWeight: 700, color, letterSpacing: 0.3 }}>{label.toUpperCase()}</div>
      {items.map((t, i) => <div key={i} style={{ fontSize: 12, color: '#1f2a3a', margin: '2px 0' }}>• {mdBold(t)}</div>)}
    </div>
  );
}

// tiny **bold** renderer
function mdBold(s) {
  const parts = String(s).split(/\*\*(.+?)\*\*/g);
  return parts.map((p, i) => i % 2 ? <b key={i}>{p}</b> : <React.Fragment key={i}>{p}</React.Fragment>);
}

const S = {
  wrap: { background: C.panel, border: `1px solid ${C.border}`, borderRadius: 10, boxShadow: SH.card, padding: 16, marginBottom: 16 },
  head: { display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 10 },
  title: { fontSize: 16, fontWeight: 700, color: C.text },
  sub: { fontSize: 12, color: C.dim, marginTop: 3 },
  toggle: { background: 'transparent', border: `1px solid ${C.border}`, color: C.cyan, fontSize: 12, padding: '4px 10px', borderRadius: 5, cursor: 'pointer' },
  rankWrap: { overflowX: 'auto', marginTop: 12, border: `1px solid ${C.border}`, borderRadius: 8 },
  table: { width: '100%', borderCollapse: 'collapse', fontSize: 12.5 },
  th: { textAlign: 'left', padding: '8px 12px', color: C.dim, borderBottom: `2px solid ${C.border}`, fontWeight: 700 },
  tr: { borderBottom: `1px solid ${C.border}` },
  td: { padding: '7px 12px', color: C.text },
  lessons: { marginTop: 12, background: '#f4f7fc', border: `1px solid ${C.border}`, borderRadius: 8, padding: '10px 12px' },
  lessonsHd: { fontSize: 11, fontWeight: 700, color: C.dim, letterSpacing: 0.5, marginBottom: 4 },
  lesson: { fontSize: 12.5, color: C.text, margin: '3px 0' },
  cards: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))', gap: 12, marginTop: 12 },
  card: { border: `1px solid ${C.border}`, borderRadius: 8, padding: 12, background: '#fff' },
  cardHd: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 },
  cardMeta: { fontSize: 11.5, color: C.dim, margin: '4px 0 6px' },
  pill: { fontSize: 10.5, fontWeight: 700, padding: '2px 8px', borderRadius: 10 },
  pillDim: { fontSize: 10.5, fontWeight: 700, padding: '2px 8px', borderRadius: 10, background: '#eef2f8', color: C.dim },
  line: { fontSize: 12, color: '#1f2a3a', margin: '2px 0' },
};
