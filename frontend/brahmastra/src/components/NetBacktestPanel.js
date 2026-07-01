import React, { useEffect, useState } from 'react';
import { api, C, SH } from '../api';

/** Consolidated net-backtest result + the capital picture behind it.
 *  Reads reports/net_backtest_<date>.json via /api/net-backtest. */
export default function NetBacktestPanel() {
  const [d, setD]   = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => { api.netBacktest().then(setD).catch(e => setErr(String(e))); }, []);

  if (err) return <div style={{ color: C.red, marginBottom: 16 }}>{err}</div>;
  if (!d)  return null;

  if (!d.available) {
    return (
      <div style={S.wrap}>
        <div style={S.title}>Net Backtest</div>
        <div style={{ color: C.dim, fontSize: 13 }}>{d.note}</div>
      </div>
    );
  }

  const cap = d.capital || {};
  const inr = (v) => v == null ? '—' : '₹' + Number(v).toLocaleString('en-IN', { maximumFractionDigits: 2 });
  const lakh = (v) => v == null ? '—' : (Math.abs(v) >= 1e7
    ? '₹' + (v / 1e7).toFixed(2) + ' Cr'
    : '₹' + (v / 1e5).toFixed(2) + ' L');
  const pnlColor = (v) => v == null ? C.dim : v >= 0 ? C.green : C.red;
  const sorted = [...(d.strategies || [])].sort((a, b) => (b.pnl || 0) - (a.pnl || 0));

  return (
    <div style={S.wrap}>
      <div style={S.headRow}>
        <div style={S.title}>Net Backtest — all strategies</div>
        <div style={S.meta}>{d.source} · {d.generated} · {d.report_file}</div>
      </div>
      <div style={S.lead}>
        One <b>consolidated deep run</b> — every strategy below traded on the <b>same</b> Kite
        history &amp; period, so these figures are comparable. (The per-strategy table further
        down shows each strategy's own separate run, which may differ.)
      </div>

      {/* Period-covered banner — makes the window impossible to miss, so a short
          test can never be mistaken for the full-history run. */}
      {d.period_covered && (
        <div style={{ ...S.window, ...(d.is_short_window ? S.windowShort : S.windowFull) }}>
          📅 <b>Period covered:</b> {d.period_covered.start} → {d.period_covered.end}
          {d.period_covered.days != null && <span> · <b>{d.period_covered.days.toLocaleString('en-IN')} days</b></span>}
          {d.is_short_window
            ? <span style={{ fontWeight: 700 }}> — ⚠ SHORT window (a quick test, <u>not</u> your full-history deep run). Re-run without --from/--to for the full record.</span>
            : <span> · full-history deep run</span>}
        </div>
      )}

      {/* headline KPIs */}
      <div style={S.kpis}>
        <Kpi label="Portfolio Net P&L" value={lakh(d.portfolio_net_pnl)} color={pnlColor(d.portfolio_net_pnl)} big />
        <Kpi label="Total Trades" value={Number(d.portfolio_trades).toLocaleString('en-IN')} />
        <Kpi label="Avg P&L / Trade" value={inr(cap.avg_pnl_per_trade_rs)} color={pnlColor(cap.avg_pnl_per_trade_rs)} />
        <Kpi label="Peak Capital Deployed" value={inr(cap.peak_simultaneous_rs)} hint="one open position per strategy" />
      </div>

      {/* per-strategy net table */}
      <div style={S.tableWrap}>
        <table style={S.table}>
          <thead><tr>
            {['Strategy', 'Trades', 'Net P&L', 'Win %', 'Sharpe', 'Max DD', 'Per-trade capital', 'Period'].map(h =>
              <th key={h} style={S.th}>{h}</th>)}
          </tr></thead>
          <tbody>
            {sorted.map(s => {
              const ct = (cap.per_trade_rs || []).find(p => p.name === s.name);
              const period = s.period || {};
              const zero = (s.trades || 0) === 0;
              const tag = s.status === 'error' ? 'ERROR'
                : s.status === 'separate_engine' ? 'separate engine'
                : zero ? '0 trades' : null;
              return (
                <tr key={s.name} style={S.tr}>
                  <td style={S.tdName}>{s.name}
                    {tag && <span style={{ ...S.zeroTag, ...(s.status === 'error' ? { color: C.red, background: '#fef2f2', borderColor: '#fecaca' } : {}) }}
                                  title={s.note || s.error || ''}>{tag}</span>}</td>
                  <td style={S.td}>{(s.trades || 0).toLocaleString('en-IN')}</td>
                  <td style={{ ...S.td, color: pnlColor(s.pnl), fontWeight: 700 }}>{zero ? '—' : inr(s.pnl)}</td>
                  <td style={S.td}>{zero ? '—' : (s.win_rate ?? '—') + '%'}</td>
                  <td style={S.td}>{zero ? '—' : s.sharpe}</td>
                  <td style={{ ...S.td, color: C.red }}>{zero ? '—' : inr(s.max_drawdown)}</td>
                  <td style={S.td}>{ct && ct.per_trade_rs ? inr(ct.per_trade_rs) : '—'}</td>
                  <td style={{ ...S.td, color: C.dim, fontSize: 11 }}>{period.start ? `${period.start}→${period.end}` : '—'}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* capital model + honest caveats */}
      <div style={S.capRow}>
        <div style={S.capBox}>
          <div style={S.capTitle}>How the capital works</div>
          <div style={S.capText}>
            {cap.model}. Each strategy holds ~one position at a time, so the working
            capital is the <b>per-trade budget</b> (₹10K–20K), with peak simultaneous
            deployment of about <b>{inr(cap.peak_simultaneous_rs)}</b> — <i>not</i> the
            headline P&L. The {lakh(d.portfolio_net_pnl)} is cumulative profit booked
            across {Number(d.portfolio_trades).toLocaleString('en-IN')} trades over the
            full history.
          </div>
        </div>
        <div style={{ ...S.capBox, background: '#fff7ed', borderColor: '#fed7aa' }}>
          <div style={{ ...S.capTitle, color: '#b45309' }}>⚠ Read these before scaling up</div>
          <ul style={S.caveats}>
            {(cap.caveats || []).map((c, i) => <li key={i} style={{ marginBottom: 3 }}>{c}</li>)}
          </ul>
        </div>
      </div>
      {d.zero_trade && d.zero_trade.length > 0 && (
        <div style={S.zeroNote}>
          Fired 0 trades on Kite index spot (volume = 0 → volume-surge gate never triggers):
          <b> {d.zero_trade.join(', ')}</b>. Re-run with <code style={S.code}>--futures-volume</code> to test them on real futures volume.
        </div>
      )}
    </div>
  );
}

function Kpi({ label, value, color, big, hint }) {
  return (
    <div style={S.kpi}>
      <div style={S.kpiLabel}>{label}</div>
      <div style={{ ...S.kpiValue, color: color || C.text, fontSize: big ? 26 : 19 }}>{value}</div>
      {hint && <div style={S.kpiHint}>{hint}</div>}
    </div>
  );
}

const S = {
  wrap: { background: C.panel, border: `1px solid ${C.border}`, borderRadius: 12, boxShadow: SH.card, padding: 18, marginBottom: 22 },
  headRow: { display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', flexWrap: 'wrap', gap: 8 },
  title: { fontSize: 18, fontWeight: 800, color: C.text },
  meta: { fontSize: 11, color: C.dim, fontFamily: 'monospace' },
  lead: { fontSize: 12, color: C.dim, lineHeight: 1.5, marginTop: 6, maxWidth: 920 },
  window: { fontSize: 13, borderRadius: 8, padding: '8px 12px', marginTop: 10, lineHeight: 1.5 },
  windowFull: { background: '#f0fdf4', border: '1px solid #bbf7d0', color: '#15803d' },
  windowShort: { background: '#fffbeb', border: '1px solid #fde68a', color: '#b45309' },
  kpis: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(150px,1fr))', gap: 12, margin: '14px 0' },
  kpi: { background: C.bg, border: `1px solid ${C.border}`, borderRadius: 9, padding: '10px 14px' },
  kpiLabel: { fontSize: 11, color: C.dim, fontWeight: 600, marginBottom: 4 },
  kpiValue: { fontWeight: 800 },
  kpiHint: { fontSize: 10, color: C.dim, marginTop: 2 },
  tableWrap: { overflowX: 'auto', borderRadius: 9, border: `1px solid ${C.border}` },
  table: { width: '100%', borderCollapse: 'collapse', fontSize: 13 },
  th: { textAlign: 'left', padding: '9px 12px', color: C.dim, borderBottom: `2px solid ${C.border}`, fontWeight: 700, whiteSpace: 'nowrap' },
  tr: { borderBottom: `1px solid ${C.border}` },
  td: { padding: '8px 12px', color: C.text, whiteSpace: 'nowrap' },
  tdName: { padding: '8px 12px', color: C.text, fontWeight: 700, whiteSpace: 'nowrap' },
  zeroTag: { marginLeft: 6, fontSize: 9, color: '#b45309', background: '#fff7ed', border: '1px solid #fed7aa', borderRadius: 4, padding: '1px 5px', fontWeight: 700 },
  capRow: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(260px,1fr))', gap: 12, marginTop: 14 },
  capBox: { background: C.bg, border: `1px solid ${C.border}`, borderRadius: 9, padding: '12px 14px' },
  capTitle: { fontSize: 13, fontWeight: 700, color: C.text, marginBottom: 6 },
  capText: { fontSize: 12.5, color: C.text, lineHeight: 1.55 },
  caveats: { margin: 0, paddingLeft: 18, fontSize: 12.5, color: '#7c2d12', lineHeight: 1.5 },
  zeroNote: { marginTop: 12, fontSize: 12, color: C.text, background: C.bg, border: `1px dashed ${C.border}`, borderRadius: 8, padding: '9px 12px' },
  code: { background: '#eef2ff', padding: '1px 5px', borderRadius: 4, fontFamily: 'monospace', fontSize: 11.5 },
};
