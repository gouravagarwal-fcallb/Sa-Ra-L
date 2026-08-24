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

function yesterdayIST() {
  const d = new Date(Date.now() + (new Date().getTimezoneOffset() + 330) * 60000 - 86400000);
  return d.toISOString().slice(0, 10);
}

export default function ClosurePage() {
  const [rep, setRep] = useState(null);
  const [err, setErr] = useState(null);
  const [open, setOpen] = useState(null);
  const [day, setDay] = useState('');          // '' = today
  const [dates, setDates] = useState([]);
  const [eod, setEod] = useState(null);
  const [eodBusy, setEodBusy] = useState(false);

  const runEod = () => {
    setEodBusy(true); setEod(null);
    api.eodRun(day || undefined)
      .then(r => setEod(r)).catch(e => setEod({ error: String(e) }))
      .finally(() => setEodBusy(false));
  };

  const load = useCallback(() => {
    api.dailyClosure(day || undefined).then(d => { setRep(d); setErr(d?.error || null); }).catch(e => setErr(String(e)));
  }, [day]);
  useEffect(() => { load(); const id = setInterval(load, 30000); return () => clearInterval(id); }, [load]);
  useEffect(() => { api.closureDates().then(d => setDates(d.dates || [])).catch(() => {}); }, []);

  if (err) return <div style={{ color: C.red }}>{err}</div>;
  if (!rep) return <div style={{ color: C.dim, padding: 20 }}>Building closure report…</div>;

  const s = rep.summary || {};
  const ctx = rep.market_context || {};

  return (
    <div>
      <div style={S.head}>
        <h2 style={S.h2}>Daily Closure Report <span style={{ color: C.dim, fontWeight: 400, fontSize: 14 }}>· {rep.date}</span></h2>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
          <button style={{ ...S.dayBtn, ...(day === '' ? S.dayOn : {}) }} onClick={() => setDay('')}>Today</button>
          <button style={{ ...S.dayBtn, ...(day === yesterdayIST() ? S.dayOn : {}) }} onClick={() => setDay(yesterdayIST())}>Yesterday</button>
          <select style={S.daySel} value={day} onChange={e => setDay(e.target.value)}>
            <option value="">— pick a date —</option>
            {dates.map(d => <option key={d} value={d}>{d}</option>)}
          </select>
          <a style={S.exp} href={api.closureExportUrl('markdown', day)} target="_blank" rel="noreferrer">⬇ MD</a>
          <a style={S.exp} href={api.closureExportUrl('csv', day)} target="_blank" rel="noreferrer">⬇ CSV</a>
          <a style={S.exp} href={api.closureExportUrl('json', day)} target="_blank" rel="noreferrer">⬇ JSON</a>
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

      {/* Live regime ribbon (today only) + EOD commit */}
      {rep.regime?.indices && (
        <div style={S.regimeRibbon}>
          <span style={{ fontWeight: 800, color: C.cyan, fontSize: 12 }}>REGIME</span>
          {Object.entries(rep.regime.indices).map(([ix, r]) => (
            <span key={ix} style={S.regimeChip}>
              {ix}: <b style={{ color: /trend_up|expansion/.test(r.regime) ? C.green : /trend_down/.test(r.regime) ? C.red : C.text }}>{r.regime}</b>
              {r.confidence ? <span style={{ color: C.dim }}> ({r.confidence})</span> : null}
            </span>
          ))}
          {rep.regime.event_risk && <span style={{ ...S.regimeChip, color: C.amber, fontWeight: 700 }}>⚠ event-risk</span>}
          <button style={{ ...S.eodBtn, marginLeft: 'auto' }} disabled={eodBusy} onClick={runEod}>
            {eodBusy ? 'Committing…' : 'Commit trust (EOD)'}
          </button>
        </div>
      )}
      {eod && (
        <div style={eod.error ? S.telemetry : S.okEod}>
          {eod.error ? `EOD failed: ${eod.error}`
            : `EOD committed for ${eod.date}: ${eod.trust_committed} trust records updated, ${eod.trust_frozen} frozen (no evidence). ${eod.blind_recommend_demote?.length ? 'Recommend demote: ' + eod.blind_recommend_demote.join(', ') : ''}`}
        </div>
      )}

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

      {/* Benchmark — did the market even offer an edge? */}
      {rep.benchmarks?.available && (
        <div style={S.panel}>
          <div style={S.cardTitle}>BENCHMARK · was there an edge to capture?</div>
          {rep.benchmarks.headline && <div style={{ fontSize: 13, color: C.text, marginBottom: 8 }}>{rep.benchmarks.headline}</div>}
          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
            {Object.values(rep.benchmarks.indices).filter(b => b.available).map(b => (
              <div key={b.index} style={S.bench}>
                <b>{b.index}</b>: buy‑hold <span style={{ color: b.buy_hold_pct >= 0 ? C.green : C.red, fontWeight: 700 }}>{b.buy_hold_pct > 0 ? '+' : ''}{b.buy_hold_pct}%</span>
                {' · '}range {b.session_range_pct}% · ORB {b.orb_R ?? '—'}R · trend {b.trend_strength ?? '—'}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Telemetry health — blind strategies are a failure condition */}
      {rep.telemetry?.blind?.length > 0 && (
        <div style={S.telemetry}>
          <div style={{ ...S.cardTitle, color: C.red }}>⚠ TELEMETRY FAILURE · blind strategies</div>
          <div style={{ fontSize: 12.5, color: C.text }}>
            These emitted <b>no verifiable analysis trail</b>: {rep.telemetry.blind.join(', ')}.
          </div>
          <div style={{ fontSize: 11.5, color: C.dim, marginTop: 4 }}>{rep.telemetry.action}</div>
        </div>
      )}

      {/* Over-filtering diagnostics (graded) */}
      {rep.scoring?.verifiable && (rep.scoring.filter_audit || []).length > 0 && (
        <div style={S.panel}>
          <div style={S.cardTitle}>OVER-FILTERING DIAGNOSTICS <span style={S.hint}>· graded vs actual index moves</span></div>
          <table style={S.table}>
            <thead><tr>{['Rejection reason', 'Fires', 'Justified', 'Unjustified', 'Justification rate'].map(h => <th key={h} style={S.th}>{h}</th>)}</tr></thead>
            <tbody>
              {rep.scoring.filter_audit.map(f => (
                <tr key={f.reason} style={S.tr}>
                  <td style={S.tdName}>{f.reason}</td>
                  <td style={S.td}>{f.fires}</td>
                  <td style={{ ...S.td, color: C.green }}>{f.justified}</td>
                  <td style={{ ...S.td, color: f.unjustified > 0 ? C.red : C.dim, fontWeight: 700 }}>{f.unjustified}</td>
                  <td style={{ ...S.td, fontWeight: 700, color: (f.justification_rate ?? 1) >= 0.6 ? C.green : C.amber }}>
                    {f.justification_rate != null ? `${Math.round(f.justification_rate * 100)}%` : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {rep.scoring && !rep.scoring.verifiable && (
        <div style={S.caveat}>ℹ Grading unavailable for this day — {rep.scoring.note}</div>
      )}

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
                      {b.telemetry_ok === false && <span style={{ color: C.red, fontWeight: 700 }}>  · ⚠ telemetry blind</span>}
                      {b.trust_score != null && <span>  · trust {b.trust_score}{b.trust_delta ? ` (${b.trust_delta > 0 ? '+' : ''}${b.trust_delta})` : ''}</span>}
                    </div>
                    {b.nearest_miss && b.trades === 0 && (
                      <div style={S.nearMiss} title="How close this strategy came to actually taking a trade — its peak signal score vs the score it needs to fire.">
                        🎯 Nearest miss: peak score <b>{b.nearest_miss.peak_direction ? `${b.nearest_miss.peak_direction} ` : ''}{b.nearest_miss.peak_score}{b.nearest_miss.threshold ? `/${b.nearest_miss.threshold}` : ''}</b>
                        {b.nearest_miss.threshold && (
                          b.nearest_miss.would_have_fired
                            ? <span style={{ color: C.green, fontWeight: 700 }}> — {b.nearest_miss.peak_direction ? 'score bar met ✓' : 'would have fired ✓'}</span>
                            : <span> — {b.nearest_miss.reached_pct}% of the bar, came within {b.nearest_miss.gap}</span>
                        )}
                        <span style={{ color: C.dim }}> · over {b.nearest_miss.samples} scored cycles</span>
                      </div>
                    )}
                    {b.scored?.labels && (
                      <div style={{ marginBottom: 8 }}>
                        <div style={S.subTitle}>Graded vs actual index path</div>
                        {Object.entries(b.scored.labels).map(([k, v]) => (
                          <div key={k} style={S.reasonRow}>
                            <span style={{ color: /Correct/.test(k) ? C.green : /Missed|Over|Premature/.test(k) ? C.red : C.dim }}>{k}</span><b>×{v}</b>
                          </div>
                        ))}
                        {b.scored.no_trade_correctness != null && (
                          <div style={{ fontSize: 11.5, color: C.dim, marginTop: 3 }}>
                            no‑trade correctness {Math.round(b.scored.no_trade_correctness * 100)}% · over‑filtered {Math.round((b.scored.over_filtered_rate || 0) * 100)}% · missed {Math.round((b.scored.missed_opportunity_rate || 0) * 100)}%
                          </div>
                        )}
                      </div>
                    )}
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
  dayBtn: { background: C.panel, border: `1px solid ${C.border}`, color: C.dim, fontSize: 12, fontWeight: 700, padding: '5px 10px', borderRadius: 6, cursor: 'pointer' },
  dayOn: { background: C.blue, color: '#fff', borderColor: C.blue },
  daySel: { background: C.panel, border: `1px solid ${C.border}`, color: C.text, fontSize: 12, fontWeight: 600, padding: '5px 8px', borderRadius: 6, cursor: 'pointer' },
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
  nearMiss: { fontSize: 12.5, color: C.text, background: '#fffbeb', border: '1px solid #fde68a', borderRadius: 6, padding: '6px 10px', margin: '2px 0 8px' },
  subTitle: { fontSize: 11, fontWeight: 800, letterSpacing: 0.4, color: C.dim, textTransform: 'uppercase', marginBottom: 4 },
  reasonRow: { display: 'flex', justifyContent: 'space-between', fontSize: 12.5, color: C.text, padding: '2px 0', maxWidth: 460 },
  caveat: { fontSize: 11.5, color: C.dim, lineHeight: 1.5, background: C.panel2, borderRadius: 8, padding: '10px 12px' },
  bench: { fontSize: 12.5, color: C.text, background: C.panel2, borderRadius: 8, padding: '8px 12px' },
  telemetry: { background: '#fef2f2', border: '1px solid #fecaca', borderLeft: `5px solid ${C.red}`, borderRadius: 10, padding: 14, marginBottom: 12, boxShadow: SH.card },
  regimeRibbon: { display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', background: C.panel, border: `1px solid ${C.border}`, borderRadius: 10, padding: '8px 14px', marginBottom: 12, boxShadow: SH.card },
  regimeChip: { fontSize: 12, color: C.text, background: C.panel2, borderRadius: 9, padding: '3px 9px' },
  eodBtn: { background: C.blue, border: 'none', color: '#fff', fontSize: 12, fontWeight: 700, padding: '5px 12px', borderRadius: 6, cursor: 'pointer' },
  okEod: { background: '#f0fdf4', border: '1px solid #bbf7d0', color: '#15803d', fontSize: 12.5, fontWeight: 600, borderRadius: 8, padding: '10px 12px', marginBottom: 12 },
};
