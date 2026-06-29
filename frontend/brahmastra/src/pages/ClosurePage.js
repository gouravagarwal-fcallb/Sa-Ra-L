import React, { useEffect, useState, useCallback } from 'react';
import { api, C, SH } from '../api';

/**
 * Daily Closure Report + No-Trade Audit (UI_FE_Pg2 PART X).
 * Answers, on demand and at EOD: what each strategy analysed, why it did/didn't
 * trade, the trade outcomes, and a usefulness verdict — so the day is never
 * "zero trades, zero explanation".
 */
const VERDICT_COLOR = (v = '') =>
  v.includes('Highly') ? C.green
    : v.includes('stood aside') ? '#0f8a3c'
    : v.includes('Over-Filtered') ? C.amber
    : v.includes('Recalibration') ? C.red
    : v.includes('Blind') || v.includes('Disable') ? C.red
    : C.dim;

export default function ClosurePage() {
  const [rep, setRep] = useState(null);
  const [err, setErr] = useState(null);
  const [open, setOpen] = useState(null);

  const load = useCallback(() => {
    api.dailyClosure().then(d => { setRep(d); setErr(d?.error || null); }).catch(e => setErr(String(e)));
  }, []);
  useEffect(() => { load(); const id = setInterval(load, 30000); return () => clearInterval(id); }, [load]);

  if (err) return <div style={{ color: C.red }}>{err}</div>;
  if (!rep) return <div style={{ color: C.dim, padding: 20 }}>Building closure report…</div>;

  const s = rep.summary || {};
  const ctx = rep.market_context || {};

  return (
    <div>
      <div style={S.head}>
        <h2 style={S.h2}>Daily Closure Report <span style={{ color: C.dim, fontWeight: 400, fontSize: 14 }}>· {rep.date}</span></h2>
        <div style={{ display: 'flex', gap: 6 }}>
          <a style={S.exp} href={api.closureExportUrl('markdown')} target="_blank" rel="noreferrer">⬇ Markdown</a>
          <a style={S.exp} href={api.closureExportUrl('csv')} target="_blank" rel="noreferrer">⬇ CSV</a>
          <a style={S.exp} href={api.closureExportUrl('json')} target="_blank" rel="noreferrer">⬇ JSON</a>
        </div>
      </div>

      {/* Executive summary */}
      <div style={S.kpis}>
        <Kpi label="Strategies" value={`${s.strategies_reported ?? '—'}`} sub={`${s.strategies_running ?? 0} running`} />
        <Kpi label="Analysis cycles" value={`${s.total_analysis_cycles ?? 0}`} sub={`${s.total_no_trade_cycles ?? 0} no-trade`} />
        <Kpi label="Trades" value={`${s.total_trades ?? 0}`} sub={`${s.wins ?? 0}W / ${s.losses ?? 0}L`} />
        <Kpi label="Win rate" value={s.win_rate != null ? `${s.win_rate}%` : '—'} />
        <Kpi label="Net P&L" value={`₹${(s.net_pnl ?? 0).toLocaleString('en-IN')}`}
             color={(s.net_pnl ?? 0) > 0 ? C.green : (s.net_pnl ?? 0) < 0 ? C.red : C.text} />
        <Kpi label="Data incidents" value={`${s.data_incidents ?? 0}`} color={(s.data_incidents ?? 0) > 0 ? C.amber : C.text} />
      </div>

      {/* Why no trades — the mandatory accountability section */}
      <div style={S.review}>
        <div style={S.cardTitle}>WHY NO TRADES / WHAT HAPPENED TODAY</div>
        {(rep.no_trade_review || []).map((r, i) => <div key={i} style={S.reviewRow}>• {r}</div>)}
        {ctx.available && (
          <div style={S.ctxRow}>
            Market context: <b>{ctx.direction}</b> ({ctx.conviction}, score {ctx.score}) · VIX {ctx.india_vix}
            {ctx.is_expiry_day ? ' · expiry day' : ''}
          </div>
        )}
      </div>

      {/* Per-strategy accountability table */}
      <div style={S.card}>
        <div style={S.cardTitle}>PER-STRATEGY ACCOUNTABILITY <span style={S.hint}>· click a row for no-trade reasons</span></div>
        <table style={S.table}>
          <thead><tr>{['Strategy', 'Verdict', 'Score', 'Analysis', 'No-trade', 'Trades', 'P&L'].map(h =>
            <th key={h} style={S.th}>{h}</th>)}</tr></thead>
          <tbody>
            {(rep.per_strategy || []).map(b => (
              <React.Fragment key={b.name}>
                <tr style={{ ...S.tr, cursor: 'pointer' }} onClick={() => setOpen(open === b.name ? null : b.name)}>
                  <td style={S.tdName}>
                    <span style={{ color: C.dim, marginRight: 6 }}>{open === b.name ? '▾' : '▸'}</span>{b.name}
                  </td>
                  <td style={{ ...S.td, color: VERDICT_COLOR(b.verdict), fontWeight: 700 }}>{b.verdict}</td>
                  <td style={S.td}>{b.usefulness_score}</td>
                  <td style={S.td}>{b.analysis_cycles}</td>
                  <td style={S.td}>{b.no_trade_cycles}</td>
                  <td style={S.td}>{b.trades} <span style={{ color: C.dim, fontSize: 11 }}>({b.wins}W/{b.losses}L)</span></td>
                  <td style={{ ...S.td, color: b.pnl > 0 ? C.green : b.pnl < 0 ? C.red : C.dim, fontWeight: 700 }}>
                    {b.pnl ? `₹${b.pnl.toLocaleString('en-IN')}` : '—'}
                  </td>
                </tr>
                {open === b.name && (
                  <tr><td colSpan={7} style={S.drill}>
                    <div style={{ marginBottom: 6, color: C.text }}>{b.headline}</div>
                    <div style={{ color: C.dim, fontSize: 12, marginBottom: 8 }}>
                      status {b.status} · mode {b.mode || '—'} · {b.running ? 'running' : 'idle'} · {b.log_lines} log lines · {b.errors} errors
                    </div>
                    {Object.keys(b.no_trade_reasons || {}).length > 0 ? (
                      <div>
                        <div style={S.subTitle}>No-trade reasons</div>
                        {Object.entries(b.no_trade_reasons).map(([k, v]) => (
                          <div key={k} style={S.reasonRow}><span>{k}</span><b>×{v}</b></div>
                        ))}
                      </div>
                    ) : <div style={{ color: C.dim, fontSize: 12 }}>No classified no-trade reasons logged.</div>}
                    {(b.exit_breakdown || []).length > 0 && (
                      <div style={{ marginTop: 8 }}>
                        <div style={S.subTitle}>Exit breakdown</div>
                        {b.exit_breakdown.map((e, i) => (
                          <div key={i} style={S.reasonRow}><span>{e.reason}</span><b>{e.count} · ₹{e.pnl}</b></div>
                        ))}
                      </div>
                    )}
                  </td></tr>
                )}
              </React.Fragment>
            ))}
          </tbody>
        </table>
      </div>

      {/* Top lessons */}
      {(rep.top_lessons || []).length > 0 && (
        <div style={S.card}>
          <div style={S.cardTitle}>TOP LESSONS OF THE DAY</div>
          {rep.top_lessons.map((l, i) => <div key={i} style={S.reviewRow}>• {l}</div>)}
        </div>
      )}

      {rep.caveat && <div style={S.caveat}>ℹ {rep.caveat}</div>}
    </div>
  );
}

function Kpi({ label, value, sub, color }) {
  return (
    <div style={S.kpi}>
      <div style={S.kpiLabel}>{label}</div>
      <div style={{ ...S.kpiValue, color: color || C.text }}>{value}</div>
      {sub ? <div style={S.kpiSub}>{sub}</div> : null}
    </div>
  );
}

const S = {
  head: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8, marginBottom: 12 },
  h2: { fontSize: 20, margin: 0, color: C.text },
  exp: { background: C.panel, border: `1px solid ${C.border}`, color: C.blue, fontSize: 12, fontWeight: 700, padding: '5px 10px', borderRadius: 6, textDecoration: 'none' },
  kpis: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: 10, marginBottom: 12 },
  kpi: { background: C.panel, border: `1px solid ${C.border}`, borderRadius: 10, padding: '12px 14px', boxShadow: SH.card },
  kpiLabel: { fontSize: 10.5, fontWeight: 700, letterSpacing: 0.4, color: C.dim, textTransform: 'uppercase' },
  kpiValue: { fontSize: 22, fontWeight: 800, marginTop: 4 },
  kpiSub: { fontSize: 11, color: C.dim, marginTop: 2 },
  review: { background: '#fffef5', border: `1px solid #f2e9c6`, borderLeft: `5px solid ${C.amber}`, borderRadius: 10, padding: 16, marginBottom: 12, boxShadow: SH.card },
  reviewRow: { fontSize: 13, color: C.text, lineHeight: 1.6, padding: '2px 0' },
  ctxRow: { marginTop: 8, paddingTop: 8, borderTop: `1px solid ${C.border}`, fontSize: 12.5, color: C.dim },
  card: { background: C.panel, border: `1px solid ${C.border}`, borderRadius: 10, padding: 16, marginBottom: 12, boxShadow: SH.card },
  cardTitle: { fontSize: 13, fontWeight: 700, letterSpacing: 0.6, color: C.cyan, marginBottom: 12 },
  hint: { fontWeight: 400, fontSize: 10.5, color: C.dim, letterSpacing: 0 },
  table: { width: '100%', borderCollapse: 'collapse', fontSize: 13 },
  th: { textAlign: 'left', padding: '8px 10px', color: C.dim, borderBottom: `2px solid ${C.border}`, fontWeight: 700 },
  tr: { borderBottom: `1px solid ${C.border}` },
  td: { padding: '8px 10px', color: C.text },
  tdName: { padding: '8px 10px', color: C.text, fontWeight: 700 },
  drill: { padding: '10px 14px 14px 26px', background: C.panel2, borderBottom: `1px solid ${C.border}` },
  subTitle: { fontSize: 11, fontWeight: 800, letterSpacing: 0.4, color: C.dim, textTransform: 'uppercase', marginBottom: 4 },
  reasonRow: { display: 'flex', justifyContent: 'space-between', fontSize: 12.5, color: C.text, padding: '2px 0', maxWidth: 460 },
  caveat: { fontSize: 11.5, color: C.dim, lineHeight: 1.5, background: C.panel2, borderRadius: 8, padding: '10px 12px' },
};
