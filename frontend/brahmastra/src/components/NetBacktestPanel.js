import React, { useEffect, useState, useRef } from 'react';
import { api, C, SH } from '../api';

/** Consolidated net-backtest result + the capital picture behind it.
 *  Reads reports/net_backtest_<date>.json via /api/net-backtest, and lets the
 *  operator RUN a fresh net backtest for any chosen period (one common period
 *  selector that re-runs every strategy over the same window). */
export default function NetBacktestPanel() {
  const [d, setD]   = useState(null);
  const [err, setErr] = useState(null);
  const load = () => api.netBacktest().then(setD).catch(e => setErr(String(e)));
  useEffect(() => { load(); }, []);

  if (err) return (
    <div style={S.wrap}>
      <RunAllBar onDone={() => { setErr(null); load(); }} />
      <div style={{ color: C.red, marginTop: 12 }}>{err}</div>
    </div>
  );
  if (!d)  return <div style={S.wrap}><RunAllBar onDone={load} /></div>;

  if (!d.available) {
    return (
      <div style={S.wrap}>
        <div style={S.title}>Net Backtest</div>
        <RunAllBar onDone={load} />
        <div style={{ color: C.dim, fontSize: 13, marginTop: 10 }}>{d.note}</div>
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

      <RunAllBar onDone={load} />

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

/** One common period selector that re-runs EVERY strategy over the same window.
 *  Leave the dates blank for the full-history deep run; pick a range for a
 *  scenario slice (e.g. a crash month, an expiry week, a trending quarter). */
function RunAllBar({ onDone }) {
  const [frm, setFrm] = useState('');
  const [to, setTo]   = useState('');
  const [src, setSrc] = useState('kite');
  const [msg, setMsg] = useState(null);
  const [running, setRunning] = useState(false);
  const poll = useRef(null);

  useEffect(() => {
    // Resume the status line if a run is already in flight (e.g. after a reload).
    api.netBacktestStatus().then(s => {
      if (s && s.state === 'running') { setRunning(true); watch(); }
    }).catch(() => {});
    return () => poll.current && clearInterval(poll.current);
  }, []); // eslint-disable-line

  const watch = () => {
    let fails = 0;
    poll.current && clearInterval(poll.current);
    poll.current = setInterval(() => {
      api.netBacktestStatus().then(s => {
        fails = 0;
        if (!s || s.state === 'idle') return;
        if (s.state === 'running') { setMsg(`Running (${s.period || 'full history'}, ${s.source || 'kite'})…`); return; }
        clearInterval(poll.current); setRunning(false);
        if (s.state === 'done') { setMsg(`✓ Done — ${s.period || 'full history'}. Refreshing…`); onDone && onDone(); }
        else if (s.state === 'error') setMsg(`✗ Failed: ${s.error || 'unknown error'}`);
      }).catch(() => { if (++fails >= 5) { clearInterval(poll.current); setRunning(false); setMsg('Lost contact while running — re-check the report.'); } });
    }, 3000);
  };

  const run = () => {
    setMsg(null);
    if ((frm && !to) || (to && !frm)) { setMsg('Enter BOTH dates, or leave both blank for full history.'); return; }
    if (frm && to && frm > to)        { setMsg("'From' must be on or before 'To'."); return; }
    setRunning(true); setMsg('Starting…');
    api.runNetBacktest({ from: frm || undefined, to: to || undefined, source: src })
      .then(r => { if (r.started === false) { setMsg(r.reason || 'Already running'); } watch(); })
      .catch(e => { setRunning(false); setMsg(String(e)); });
  };

  return (
    <div style={S.runBar}>
      <div style={S.runTitle}>▶ Run net backtest — one period, all strategies</div>
      <div style={S.runRow}>
        <label style={S.runLbl}>From
          <input type="date" value={frm} onChange={e => setFrm(e.target.value)} style={S.dateIn} disabled={running} />
        </label>
        <label style={S.runLbl}>To
          <input type="date" value={to} onChange={e => setTo(e.target.value)} style={S.dateIn} disabled={running} />
        </label>
        <label style={S.runLbl}>Source
          <select value={src} onChange={e => setSrc(e.target.value)} style={S.sel} disabled={running}>
            <option value="kite">Kite (deep history)</option>
            <option value="yahoo">yfinance (~60 days)</option>
          </select>
        </label>
        <button style={{ ...S.runAllBtn, opacity: running ? 0.6 : 1 }} disabled={running} onClick={run}>
          {running ? 'Running…' : 'Run all'}
        </button>
        {(frm || to) && !running &&
          <button style={S.clearBtn} onClick={() => { setFrm(''); setTo(''); }}>Full history</button>}
      </div>
      <div style={S.runHint}>
        Leave both dates blank for the <b>full-history</b> deep run, or pick a window to test a
        specific scenario (a crash month, an expiry week, a trending quarter). Kite is required
        for history older than ~60 days; the run happens on the machine hosting the dashboard.
      </div>
      {msg && <div style={{ ...S.runMsg, color: msg.startsWith('✗') || msg.startsWith('Lost') ? C.red : msg.startsWith('✓') ? C.green : C.dim }}>{msg}</div>}
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
  runBar: { background: '#f0f7ff', border: '1px solid #cfe0f5', borderRadius: 10, padding: '12px 14px', margin: '12px 0' },
  runTitle: { fontSize: 13.5, fontWeight: 800, color: C.blue, marginBottom: 8 },
  runRow: { display: 'flex', flexWrap: 'wrap', alignItems: 'flex-end', gap: 10 },
  runLbl: { display: 'flex', flexDirection: 'column', fontSize: 11, color: C.dim, fontWeight: 700, gap: 3 },
  dateIn: { border: `1px solid ${C.border}`, borderRadius: 6, padding: '5px 8px', fontSize: 13, color: C.text, background: '#fff' },
  sel: { border: `1px solid ${C.border}`, borderRadius: 6, padding: '5px 8px', fontSize: 13, color: C.text, background: '#fff' },
  runAllBtn: { background: C.blue, border: 'none', color: '#fff', fontSize: 13, fontWeight: 800, padding: '7px 18px', borderRadius: 6, cursor: 'pointer', whiteSpace: 'nowrap' },
  clearBtn: { background: 'transparent', border: `1px solid ${C.border}`, color: C.dim, fontSize: 12, fontWeight: 700, padding: '6px 12px', borderRadius: 6, cursor: 'pointer', whiteSpace: 'nowrap' },
  runHint: { fontSize: 11.5, color: C.dim, lineHeight: 1.5, marginTop: 8, maxWidth: 900 },
  runMsg: { fontSize: 12.5, fontWeight: 700, marginTop: 8 },
};
