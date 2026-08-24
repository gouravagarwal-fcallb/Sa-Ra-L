import React, { useEffect, useState, useCallback } from 'react';
import { api, C, SH } from '../api';

/**
 * Audit Desk — one place to see the fidelity audit of every strategy (the same
 * data as `python main.py --mode audit`) PLUS a long-run analysis of the strategies
 * that actually have an edge: how durable that edge is across years, windows,
 * instruments and volatility regimes. Honest metrics only — everything here is read
 * from each strategy's last backtest summary.json + a static scan of its code.
 */
const GRADE = {
  CLEAN:            { c: C.green,  label: 'Clean',            note: 'no fidelity flags; has an edge' },
  THIN_EDGE:        { c: C.amber,  label: 'Thin edge',        note: 'positive but concentrated / low-frequency' },
  NO_EDGE:          { c: C.red,    label: 'No edge',          note: 'PF < 1.3 on honest data — park it' },
  MODEL_ONLY:       { c: C.purple, label: 'Model only',        note: 'synthetic / assumed-skill projection — not a real track record' },
  FIX_NEEDED:       { c: '#b91c1c',label: 'Fix needed',       note: 'a modelling bug must be fixed first' },
  NO_BACKTEST_DATA: { c: C.dim,    label: 'No backtest',      note: 'gates never fired — forward-paper only' },
  CANT_AUDIT_HERE:  { c: C.purple, label: 'Standalone',       note: 'own backtest engine — audit separately' },
  ARCHIVED:         { c: '#94a3b8',label: 'Archived',         note: 'intentional' },
};

const fmt = (v, d = 2) => v == null ? '—'
  : (typeof v === 'number' ? v.toLocaleString('en-IN', { maximumFractionDigits: d }) : v);
const inr = (v) => v == null ? '—' : `₹${Number(v).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;

function Pill({ grade }) {
  const g = GRADE[grade] || { c: C.dim, label: grade };
  return <span style={{ ...S.pill, background: g.c + '18', color: g.c, border: `1px solid ${g.c}44` }}>{g.label}</span>;
}

export default function AuditDeskPage() {
  const [audit, setAudit] = useState(null);
  const [bt, setBt]       = useState({});      // name -> summary row
  const [err, setErr]     = useState(null);
  const [open, setOpen]   = useState(null);    // expanded strategy name

  const load = useCallback(() => {
    Promise.all([api.strategyAudit(), api.backtests()])
      .then(([a, b]) => {
        setAudit(a);
        const map = {};
        (b || []).forEach(r => { map[r.name] = r; });
        setBt(map);
      })
      .catch(e => setErr(String(e)));
  }, []);
  useEffect(() => { load(); }, [load]);

  if (err) return <div style={S.err}>Couldn't load the audit ({err}). Run <code>python main.py --mode audit</code> once, then reload.</div>;
  if (!audit) return <div style={{ color: C.dim, padding: 24 }}>Loading audit…</div>;

  const counts = {};
  audit.forEach(a => { counts[a.grade] = (counts[a.grade] || 0) + 1; });

  // "Working" = strategies with a real/positive edge worth studying over the long run.
  const working = audit.filter(a => a.grade === 'CLEAN' || a.grade === 'THIN_EDGE');

  return (
    <div>
      <div style={S.head}>
        <h2 style={S.h2}>Audit Desk</h2>
        <button style={S.refresh} onClick={load}>↻ Refresh</button>
      </div>
      <p style={S.sub}>
        Fidelity audit of all {audit.length} strategies + long-run analysis of the ones with a real edge.
        Grades come from a static code scan (overshoot clamp, real-VIX pricing, phantom-instrument guard)
        and each strategy's last honest backtest. <b>Re-run a backtest to refresh its numbers.</b>
      </p>

      {/* grade summary chips */}
      <div style={S.chips}>
        {Object.entries(GRADE).filter(([k]) => counts[k]).map(([k, g]) => (
          <div key={k} style={{ ...S.chip, borderColor: g.c + '55' }}>
            <span style={{ ...S.chipN, color: g.c }}>{counts[k]}</span>
            <span style={S.chipL}>{g.label}</span>
          </div>
        ))}
      </div>

      {/* ── Working strategies — long-run analysis ─────────────────────────── */}
      <h3 style={S.h3}>Working strategies — long-run analysis</h3>
      {working.length === 0 && <div style={S.empty}>No strategy currently clears the edge bar on honest data.</div>}
      <div style={S.grid}>
        {working.map(a => <LongRunCard key={a.name} audit={a} summary={bt[a.name]?.summary} />)}
      </div>

      {/* ── Full audit table ───────────────────────────────────────────────── */}
      <h3 style={S.h3}>Full audit — every strategy</h3>
      <div style={S.tableWrap}>
        <table style={S.table}>
          <thead>
            <tr>
              {['Strategy', 'Grade', 'PF', 'Sharpe', 'Trades/yr', 'Key issues', 'Next action'].map(h =>
                <th key={h} style={S.th}>{h}</th>)}
            </tr>
          </thead>
          <tbody>
            {audit.map(a => {
              const m = a.metrics || {};
              const isOpen = open === a.name;
              return (
                <React.Fragment key={a.name}>
                  <tr style={S.tr} onClick={() => setOpen(isOpen ? null : a.name)}>
                    <td style={S.tdName}>{a.name}<div style={S.tdType}>{a.strategy_type} · {a.status}</div></td>
                    <td style={S.td}><Pill grade={a.grade} /></td>
                    <td style={{ ...S.td, color: m.pf == null ? C.dim : m.pf >= 1.3 ? C.green : C.red, fontWeight: 700 }}>{fmt(m.pf)}</td>
                    <td style={S.td}>{fmt(m.sharpe)}</td>
                    <td style={S.td}>{m.trades_per_year != null ? fmt(m.trades_per_year, 1) : '—'}</td>
                    <td style={S.tdIssue}>{(a.issues || []).slice(0, 2).join('; ') || '—'}</td>
                    <td style={S.tdNext}>{a.next_action}</td>
                  </tr>
                  {isOpen && (
                    <tr><td colSpan={7} style={S.detailCell}>
                      <AuditDetail audit={a} summary={bt[a.name]?.summary} />
                    </td></tr>
                  )}
                </React.Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
      <p style={S.foot}>
        Click any row for its full findings. <b>NO_EDGE</b> = park it (edge is below costs).
        <b>Model only</b> = the PF comes from a synthetic / assumed-skill simulation (e.g. PASHUPATASTRA's
        Monte-Carlo), NOT real market data — a feasibility projection to confirm by forward paper, never
        a track record to fund. <b>Standalone</b> strategies
        (INRUSD, PASHUPATASTRA, BRAHMASTRA) run their own backtest engine and still need the same overshoot audit —
        treat their numbers as unconfirmed. <b>No backtest</b> = OI-dependent / very selective; evidence comes from forward paper.
      </p>
    </div>
  );
}

/* One long-run card for a working strategy: durability across the sample. */
function LongRunCard({ audit, summary }) {
  const m = audit.metrics || {};
  const s = summary || {};
  const period = s.period || {};
  const span = (period.start && period.end) ? `${String(period.start).slice(0, 10)} → ${String(period.end).slice(0, 10)}` : '—';
  const conc = (audit.issues || []).filter(i => i.startsWith('concentration'));
  return (
    <div style={S.card}>
      <div style={S.cardTop}>
        <span style={S.cardName}>{audit.name}</span>
        <Pill grade={audit.grade} />
      </div>
      <div style={S.cardStats}>
        <Stat label="Profit factor" value={fmt(m.pf)} good={m.pf >= 1.3} />
        <Stat label="Sharpe" value={fmt(m.sharpe)} good={m.sharpe >= 1} />
        <Stat label="Win rate" value={s.win_rate != null ? `${fmt(s.win_rate, 1)}%` : '—'} />
        <Stat label="Total P&L" value={inr(s.total_pnl)} good={(s.total_pnl || 0) >= 0} />
        <Stat label="Trades / yr" value={fmt(m.trades_per_year, 1)} />
        <Stat label="Max DD" value={inr(s.max_drawdown)} bad />
      </div>
      <div style={S.spanRow}>📅 {span} · {m.years || '—'} yrs · {s.total_trades ?? '—'} trades</div>
      {conc.length > 0 && (
        <div style={S.concBox}>
          <div style={S.concHead}>⚠ Concentration (durability risk)</div>
          {conc.map((c, i) => <div key={i} style={S.concLine}>{c.replace('concentration — ', '')}</div>)}
        </div>
      )}
      {Array.isArray(s.by_vix_bucket) && s.by_vix_bucket.length > 0 && (
        <MiniBreakdown title="By VIX regime" rows={s.by_vix_bucket}
          cols={[['vix_band', 'band'], ['days', 'days'], ['win_day_pct', 'win-day%'], ['pnl', 'P&L']]} />
      )}
    </div>
  );
}

function Stat({ label, value, good, bad }) {
  const color = good ? C.green : bad ? C.red : C.text;
  return <div style={S.stat}><div style={S.statL}>{label}</div><div style={{ ...S.statV, color }}>{value}</div></div>;
}

function MiniBreakdown({ title, rows, cols }) {
  return (
    <div style={{ marginTop: 10 }}>
      <div style={S.mbTitle}>{title}</div>
      <table style={S.mb}>
        <thead><tr>{cols.map(([, h]) => <th key={h} style={S.mbTh}>{h}</th>)}</tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {cols.map(([k]) => {
                const v = r[k];
                const isPnl = k === 'pnl';
                return <td key={k} style={{ ...S.mbTd, color: isPnl ? (v >= 0 ? C.green : C.red) : C.text }}>
                  {isPnl ? inr(v) : (k.endsWith('pct') && v != null ? `${fmt(v, 0)}%` : fmt(v, 0))}
                </td>;
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* Row-expansion detail in the full table. */
function AuditDetail({ audit, summary }) {
  const st = audit.static || {};
  const s = summary || {};
  return (
    <div style={S.detail}>
      <div style={S.detCol}>
        <div style={S.detH}>Fidelity checks</div>
        <Check ok={!st.flat_vix} label="Real VIX pricing (not flat 15)" />
        <Check ok={!st.phantom_guard_missing} label="Phantom-instrument guard" />
        <Check ok={st.overshoot_state === 'CLAMPED' || st.overshoot_state === 'DELEGATED_PRICER' || st.overshoot_state === 'N/A'}
               label={`Target-overshoot clamp (${st.overshoot_state || 'N/A'})`} />
        {(audit.issues || []).length > 0 && (
          <>
            <div style={{ ...S.detH, marginTop: 10 }}>Findings</div>
            {audit.issues.map((it, i) => <div key={i} style={S.finding}>• {it}</div>)}
          </>
        )}
        <div style={S.nextBox}>Next: {audit.next_action}</div>
      </div>
      <div style={S.detCol}>
        {Array.isArray(s.by_window) && s.by_window.length > 0 &&
          <MiniBreakdown title="By window" rows={s.by_window}
            cols={[['window', 'window'], ['trades', 'trades'], ['win_pct', 'win%'], ['pnl', 'P&L']]} />}
        {Array.isArray(s.by_instrument) && s.by_instrument.length > 0 &&
          <MiniBreakdown title="By instrument" rows={s.by_instrument}
            cols={[['instrument', 'inst'], ['trades', 'trades'], ['win_pct', 'win%'], ['pnl', 'P&L']]} />}
        {Array.isArray(s.worst_days) && s.worst_days.length > 0 && (
          <div style={{ marginTop: 10 }}>
            <div style={S.mbTitle}>Worst days (VIX-tagged)</div>
            {s.worst_days.slice(0, 5).map((d, i) =>
              <div key={i} style={S.worstLine}>{d.date} · {inr(d.pnl)} · VIX {fmt(d.vix, 1)}</div>)}
          </div>
        )}
      </div>
    </div>
  );
}

function Check({ ok, label }) {
  return <div style={S.check}><span style={{ color: ok ? C.green : C.red, fontWeight: 700 }}>{ok ? '✓' : '✕'}</span> {label}</div>;
}

const S = {
  head: { display: 'flex', alignItems: 'center', justifyContent: 'space-between' },
  h2: { fontSize: 22, fontWeight: 800, color: C.text, margin: '4px 0' },
  h3: { fontSize: 16, fontWeight: 700, color: C.text, margin: '22px 0 10px' },
  sub: { color: C.dim, fontSize: 13.5, lineHeight: 1.5, maxWidth: 900, margin: '0 0 14px' },
  refresh: { background: C.panel, border: `1px solid ${C.border}`, color: C.blue, fontWeight: 600, fontSize: 13, padding: '6px 12px', borderRadius: 6, cursor: 'pointer' },
  chips: { display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 6 },
  chip: { background: C.panel, border: '1px solid', borderRadius: 8, padding: '8px 14px', display: 'flex', flexDirection: 'column', alignItems: 'center', minWidth: 74, boxShadow: SH.card },
  chipN: { fontSize: 20, fontWeight: 800 },
  chipL: { fontSize: 11.5, color: C.dim, fontWeight: 600 },
  grid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 14 },
  card: { background: C.panel, border: `1px solid ${C.border}`, borderRadius: 10, padding: 16, boxShadow: SH.card },
  cardTop: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 },
  cardName: { fontSize: 15.5, fontWeight: 800, color: C.text },
  cardStats: { display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 10 },
  stat: { background: C.panel2, borderRadius: 8, padding: '8px 10px' },
  statL: { fontSize: 11, color: C.dim, fontWeight: 600 },
  statV: { fontSize: 15, fontWeight: 800, marginTop: 2 },
  spanRow: { fontSize: 12, color: C.dim, marginTop: 12, fontWeight: 600 },
  concBox: { marginTop: 12, background: '#fff7ed', border: '1px solid #fed7aa', borderRadius: 8, padding: '8px 10px' },
  concHead: { fontSize: 12, fontWeight: 700, color: C.amber, marginBottom: 4 },
  concLine: { fontSize: 12, color: '#9a3412' },
  mbTitle: { fontSize: 12, fontWeight: 700, color: C.dim, marginBottom: 4 },
  mb: { width: '100%', borderCollapse: 'collapse' },
  mbTh: { textAlign: 'right', fontSize: 10.5, color: C.dim, fontWeight: 600, padding: '2px 6px', borderBottom: `1px solid ${C.border}` },
  mbTd: { textAlign: 'right', fontSize: 12, padding: '3px 6px' },
  tableWrap: { overflowX: 'auto', background: C.panel, border: `1px solid ${C.border}`, borderRadius: 10, boxShadow: SH.card },
  table: { width: '100%', borderCollapse: 'collapse', minWidth: 820 },
  th: { textAlign: 'left', fontSize: 11.5, color: C.dim, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.3, padding: '10px 12px', borderBottom: `1px solid ${C.border}`, whiteSpace: 'nowrap' },
  tr: { borderBottom: `1px solid ${C.border}`, cursor: 'pointer' },
  td: { fontSize: 13.5, color: C.text, padding: '9px 12px', whiteSpace: 'nowrap' },
  tdName: { fontSize: 13.5, fontWeight: 700, color: C.text, padding: '9px 12px' },
  tdType: { fontSize: 11, color: C.dim, fontWeight: 400, marginTop: 1 },
  tdIssue: { fontSize: 12.5, color: '#9a3412', padding: '9px 12px', maxWidth: 260 },
  tdNext: { fontSize: 12.5, color: C.dim, padding: '9px 12px', maxWidth: 240 },
  pill: { fontSize: 11.5, fontWeight: 700, padding: '2px 9px', borderRadius: 999, whiteSpace: 'nowrap' },
  detailCell: { background: C.panel2, padding: 0 },
  detail: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 18, padding: '14px 16px' },
  detCol: {},
  detH: { fontSize: 12.5, fontWeight: 700, color: C.text, marginBottom: 6 },
  check: { fontSize: 12.5, color: C.text, padding: '2px 0' },
  finding: { fontSize: 12.5, color: '#9a3412', padding: '1px 0' },
  nextBox: { marginTop: 10, fontSize: 12.5, color: C.blue, fontWeight: 600, background: '#eff6ff', borderRadius: 6, padding: '6px 8px' },
  worstLine: { fontSize: 12, color: C.text, padding: '1px 0' },
  empty: { color: C.dim, fontSize: 13, padding: '8px 0' },
  foot: { color: C.dim, fontSize: 12.5, lineHeight: 1.5, maxWidth: 920, marginTop: 14 },
  err: { color: C.red, background: '#fef2f2', border: '1px solid #fecaca', borderRadius: 8, padding: 16, margin: 12 },
};
