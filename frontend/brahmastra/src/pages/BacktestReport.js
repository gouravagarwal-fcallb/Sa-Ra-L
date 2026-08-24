import React, { useEffect, useState, useCallback, useRef } from 'react';
import { api, C, SH } from '../api';

/**
 * Detailed backtest report for ONE strategy: headline metrics, by-instrument /
 * exit-reason / window breakdowns, and the equity curve image. Includes a
 * one-click "Run backtest" button (the market needn't be open).
 */
export default function BacktestReport({ name, onBack }) {
  const [data, setData] = useState(null);
  const [err, setErr]   = useState(null);
  const [running, setRunning] = useState(false);
  const [imgKey, setImgKey] = useState(0);   // bust the equity-curve image cache
  const [frm, setFrm]  = useState('');       // custom period start (YYYY-MM-DD), '' = configured
  const [to, setTo]    = useState('');
  const [src, setSrc]  = useState('kite');   // deep history needs Kite
  const [runInfo, setRunInfo] = useState(null);
  const poll = useRef(null);

  const load = useCallback(() => {
    // Keep the last-good report and don't flash a scary error on a transient fetch
    // blip (a running backtest briefly makes the server busy). Genuine backtest
    // failures still surface via the status poll's s.state === 'error' path.
    api.backtestSummary(name).then(d => { setData(d); setErr(null); })
      .catch(() => {});
  }, [name]);
  useEffect(() => { load(); return () => clearInterval(poll.current); }, [load]);

  const run = () => {
    if ((frm && !to) || (!frm && to)) { setErr('Enter BOTH a from and a to date, or leave both blank.'); return; }
    setErr(null); setRunning(true);
    const opts = frm && to ? { from: frm, to, source: src } : {};
    setRunInfo(frm && to ? `Running ${frm} → ${to} (${src})…` : 'Running (configured period)…');
    api.runBacktest(name, opts).then(() => {
      poll.current = setInterval(() => {
        api.backtestStatus(name).then(s => {
          if (s.state === 'done' || s.state === 'error' || s.state === 'idle') {
            clearInterval(poll.current);
            setRunning(false);
            setRunInfo(s.state === 'done' ? `Done — ${s.period || 'configured period'}` : null);
            load();
            setImgKey(k => k + 1);
            if (s.state === 'error') setErr(s.error || 'backtest failed');
          } else if (s.period) {
            setRunInfo(`Running ${s.period} (${s.source || 'default'})…`);
          }
        }).catch(() => {});
      }, 2000);
    }).catch(e => { setErr(String(e)); setRunning(false); setRunInfo(null); });
  };

  const s = data?.summary || {};
  const num = (v, d = 0) => v == null ? '—' : Number(v).toLocaleString('en-IN', { maximumFractionDigits: d });
  const pnlCol = (v) => v == null ? C.dim : v >= 0 ? C.green : C.red;
  const period = data?.period || s.period;

  return (
    <div>
      <div style={S.bar}>
        <button style={S.back} onClick={onBack}>← All backtests</button>
        <div style={{ fontWeight: 700, fontSize: 18 }}>{name}</div>
        <div style={{ color: C.dim, fontSize: 12 }}>backtest report</div>
        <button style={{ ...S.run, opacity: running ? 0.6 : 1 }} disabled={running} onClick={run}>
          {running ? 'Running… (this can take a minute)' : '▶ Run backtest'}
        </button>
      </div>

      {/* Custom-period picker — choose any historical window and run it live */}
      <div style={S.period}>
        <span style={S.periodLbl}>Backtest a period:</span>
        <label style={S.periodLbl}>From <input type="date" value={frm} disabled={running}
               onChange={e => setFrm(e.target.value)} style={S.dateIn} /></label>
        <label style={S.periodLbl}>To <input type="date" value={to} disabled={running}
               onChange={e => setTo(e.target.value)} style={S.dateIn} /></label>
        <label style={S.periodLbl}>Source
          <select value={src} disabled={running} onChange={e => setSrc(e.target.value)} style={S.sel}>
            <option value="kite">Kite (deep history)</option>
            <option value="yahoo">Yahoo (~60 days)</option>
          </select>
        </label>
        <button style={{ ...S.runPeriod, opacity: running ? 0.6 : 1 }} disabled={running} onClick={run}>
          {running ? 'Running…' : '▶ Run for this period'}
        </button>
        {(frm || to) && !running &&
          <button style={S.clearBtn} onClick={() => { setFrm(''); setTo(''); }}>clear</button>}
        <span style={S.periodHint}>
          Leave dates blank to use the strategy's configured window. Old/intraday periods need <b>Kite</b>
          (Yahoo only serves ~60 days). The report below updates when the run finishes.
        </span>
      </div>
      {runInfo && <div style={{ color: running ? C.blue : C.green, marginBottom: 8, fontSize: 13 }}>{runInfo}</div>}
      {err && <div style={{ color: C.red, marginBottom: 10 }}>{err}</div>}

      {!data?.has_summary_json && !data?.has_csv ? (
        <div style={S.empty}>No backtest results yet. Click <b>Run backtest</b> above to generate them.</div>
      ) : (
        <>
          {/* Headline metrics */}
          <div style={S.metricGrid}>
            <Metric label="Total P&L" value={`Rs.${num(data?.total_pnl ?? s.total_pnl, 0)}`} color={pnlCol(data?.total_pnl ?? s.total_pnl)} big />
            <Metric label="Trades" value={num(data?.total_trades ?? s.total_trades)} />
            <Metric label="Win rate" value={`${num(data?.win_rate ?? s.win_rate, 1)}%`} />
            <Metric label="Profit factor" value={num(s.profit_factor, 2)} />
            <Metric label="Max drawdown" value={`Rs.${num(s.max_drawdown ?? data?.max_drawdown, 0)}`} color={C.red} />
            <Metric label="Sharpe" value={num(s.sharpe ?? data?.sharpe, 2)} />
            <Metric label="Avg win" value={`Rs.${num(s.avg_win, 0)}`} color={C.green} />
            <Metric label="Avg loss" value={`Rs.${num(s.avg_loss, 0)}`} color={C.red} />
          </div>
          <div style={{ color: C.dim, fontSize: 12, marginBottom: 14 }}>
            Source: {data?.has_summary_json ? 'summary.json' : 'csv'} ·
            {period ? ` ${period.start || '?'} → ${period.end || '?'}` : ' period n/a'}
            {s.run_kind ? ` · ${s.run_kind}` : ''}
            {s.generated_at ? ` · generated ${String(s.generated_at).slice(0, 16).replace('T', ' ')}` : ''}
          </div>

          {/* Equity curve */}
          <div style={S.card}>
            <div style={S.cardTitle}>EQUITY CURVE</div>
            <img key={imgKey} src={`${api.equityCurveUrl(name)}?v=${imgKey}`} alt="equity curve"
                 style={{ width: '100%', maxWidth: 900, borderRadius: 6, background: '#fff' }}
                 onError={(e) => { e.target.style.display = 'none'; }} />
          </div>

          {/* Breakdowns */}
          <div style={S.twoCol}>
            <Breakdown title="BY EXIT REASON" rows={s.by_exit_reason} cols={['reason', 'count', 'pnl']} num={num} pnlCol={pnlCol} />
            <Breakdown title="BY INSTRUMENT" rows={s.by_instrument} cols={['instrument', 'trades', 'win_pct', 'pnl']} num={num} pnlCol={pnlCol} />
          </div>
          {s.by_window?.length > 0 &&
            <Breakdown title="BY WINDOW" rows={s.by_window} cols={['window', 'trades', 'win_pct', 'pnl']} num={num} pnlCol={pnlCol} />}
        </>
      )}
    </div>
  );
}

function Metric({ label, value, color, big }) {
  return (
    <div style={S.metric}>
      <div style={{ fontSize: 11, color: C.dim }}>{label}</div>
      <div style={{ fontSize: big ? 20 : 16, fontWeight: 700, color: color || C.text }}>{value}</div>
    </div>
  );
}

function Breakdown({ title, rows, cols, num, pnlCol }) {
  if (!rows || !rows.length) return null;
  return (
    <div style={{ ...S.card, flex: 1 }}>
      <div style={S.cardTitle}>{title}</div>
      <table style={S.table}>
        <thead><tr>{cols.map(c => <th key={c} style={S.th}>{c.replace('_', ' ')}</th>)}</tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} style={S.tr}>
              {cols.map(c => (
                <td key={c} style={{ ...S.td, color: c === 'pnl' ? pnlCol(r[c]) : C.text, fontWeight: c === 'pnl' ? 700 : 400 }}>
                  {c === 'pnl' ? `Rs.${num(r[c], 0)}` : c === 'win_pct' ? `${num(r[c], 1)}%` : r[c]}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const S = {
  bar: { display: 'flex', alignItems: 'center', gap: 12, marginBottom: 14 },
  back: { background: 'transparent', border: `1px solid ${C.border}`, color: C.cyan, padding: '5px 12px', borderRadius: 5, cursor: 'pointer' },
  run: { marginLeft: 'auto', background: C.green, border: 'none', color: '#fff', fontWeight: 700, fontSize: 13, padding: '7px 16px', borderRadius: 6, cursor: 'pointer' },
  period: { display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', background: C.panel, border: `1px solid ${C.border}`, borderRadius: 8, padding: '10px 12px', marginBottom: 10 },
  periodLbl: { fontSize: 12.5, color: C.text, fontWeight: 600, display: 'flex', alignItems: 'center', gap: 5 },
  dateIn: { border: `1px solid ${C.border}`, borderRadius: 5, padding: '4px 7px', fontSize: 12.5, color: C.text },
  sel: { border: `1px solid ${C.border}`, borderRadius: 5, padding: '4px 7px', fontSize: 12.5, color: C.text, background: '#fff' },
  runPeriod: { background: C.blue, border: 'none', color: '#fff', fontWeight: 700, fontSize: 12.5, padding: '6px 14px', borderRadius: 6, cursor: 'pointer' },
  clearBtn: { background: 'transparent', border: `1px solid ${C.border}`, color: C.dim, fontSize: 12, padding: '5px 10px', borderRadius: 5, cursor: 'pointer' },
  periodHint: { flexBasis: '100%', color: C.dim, fontSize: 11.5, marginTop: 2 },
  empty: { color: C.dim, fontSize: 14, padding: 30, textAlign: 'center', background: C.panel, border: `1px dashed ${C.border}`, borderRadius: 10 },
  metricGrid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 10, marginBottom: 8 },
  metric: { background: C.panel, border: `1px solid ${C.border}`, borderRadius: 8, padding: '10px 14px', boxShadow: SH.card },
  card: { background: C.panel, border: `1px solid ${C.border}`, borderRadius: 10, padding: 16, marginBottom: 12, boxShadow: SH.card },
  cardTitle: { fontSize: 12, fontWeight: 700, letterSpacing: 0.6, color: C.cyan, marginBottom: 10 },
  twoCol: { display: 'flex', gap: 12, flexWrap: 'wrap' },
  table: { width: '100%', borderCollapse: 'collapse', fontSize: 13 },
  th: { textAlign: 'left', padding: '7px 10px', color: C.dim, borderBottom: `2px solid ${C.border}`, fontWeight: 700, textTransform: 'capitalize' },
  tr: { borderBottom: `1px solid ${C.border}` },
  td: { padding: '7px 10px', color: C.text },
};
