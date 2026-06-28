import React, { useEffect, useState, useCallback, useRef } from 'react';
import { api, C, SH } from '../api';
import BacktestReport from './BacktestReport';
import NetBacktestPanel from '../components/NetBacktestPanel';

/** All strategies' backtest summaries in one table, with one-click run + a
 *  detailed per-strategy report (click the strategy name). */
export default function BacktestsPage() {
  const [rows, setRows] = useState([]);
  const [err, setErr]   = useState(null);
  const [reportFor, setReportFor] = useState(null);
  const [busy, setBusy] = useState({});     // name -> running?
  const polls = useRef({});

  const load = useCallback(() => {
    api.backtests().then(setRows).catch(e => setErr(String(e)));
  }, []);
  useEffect(() => { load(); return () => Object.values(polls.current).forEach(clearInterval); }, [load]);

  const runOne = (name) => {
    setBusy(b => ({ ...b, [name]: true }));
    api.runBacktest(name).then(() => {
      polls.current[name] = setInterval(() => {
        api.backtestStatus(name).then(s => {
          if (['done', 'error', 'idle'].includes(s.state)) {
            clearInterval(polls.current[name]);
            setBusy(b => ({ ...b, [name]: false }));
            load();
          }
        }).catch(() => {});
      }, 2500);
    }).catch(() => setBusy(b => ({ ...b, [name]: false })));
  };

  if (reportFor) return <BacktestReport name={reportFor} onBack={() => { setReportFor(null); load(); }} />;

  const fmt = (v) => v == null ? '—' : (typeof v === 'number' ? v.toLocaleString('en-IN', { maximumFractionDigits: 2 }) : v);
  const pnlColor = (v) => v == null ? C.dim : v >= 0 ? C.green : C.red;

  return (
    <div>
      <h2 style={S.h2}>Backtests</h2>
      <NetBacktestPanel />
      <h3 style={S.h3}>Per-strategy summaries</h3>
      {err && <div style={{ color: C.red }}>{err}</div>}
      <div style={S.tableWrap}>
        <table style={S.table}>
          <thead>
            <tr>
              {['Strategy', 'Source', 'Trades', 'Total P&L', 'Win %', 'Sharpe', 'Max DD', 'Period', ''].map(h =>
                <th key={h} style={S.th}>{h}</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.map(r => {
              const sum = r.summary || {};
              const period = (r.period || sum.period);
              return (
                <tr key={r.name} style={S.tr}>
                  <td style={S.tdName} onClick={() => setReportFor(r.name)} title="Open detailed report">
                    <span style={S.link}>{r.name}</span><div style={S.sub}>{r.status}</div>
                  </td>
                  <td style={S.td}>
                    {r.has_summary_json ? <span style={{ color: C.green }}>summary.json</span>
                      : r.has_csv ? <span style={{ color: C.amber }}>csv</span>
                      : <span style={{ color: C.red }}>none</span>}
                    {r.short_window && (
                      <div style={S.warn} title={`Only ${r.span_days} days of data — not a deep backtest`}>
                        ⚠ {r.span_days}d window{r.per_trade_budget ? ` · ₹${r.per_trade_budget.toLocaleString('en-IN')}/trade` : ''}
                      </div>
                    )}
                  </td>
                  <td style={S.td}>{fmt(r.total_trades ?? sum.total_trades)}</td>
                  <td style={{ ...S.td, color: pnlColor(r.total_pnl ?? sum.total_pnl) }}>{fmt(r.total_pnl ?? sum.total_pnl)}</td>
                  <td style={S.td}>{fmt(r.win_rate ?? sum.win_rate)}</td>
                  <td style={S.td}>{fmt(r.sharpe ?? sum.sharpe)}</td>
                  <td style={{ ...S.td, color: C.red }}>{fmt(r.max_drawdown ?? sum.max_drawdown)}</td>
                  <td style={S.td}>{period ? `${period.start || ''}→${period.end || ''}` : '—'}</td>
                  <td style={S.td}>
                    <button style={{ ...S.runBtn, opacity: busy[r.name] ? 0.6 : 1 }}
                            disabled={busy[r.name]} onClick={() => runOne(r.name)}>
                      {busy[r.name] ? 'Running…' : '▶ Run'}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div style={{ color: C.dim, fontSize: 12, marginTop: 10 }}>
        Click a strategy name for its full report. ▶ Run executes the backtest now
        (no market hours needed); it can take a minute and uses the configured data source.
      </div>
    </div>
  );
}

const S = {
  h2: { fontSize: 20, marginBottom: 14, color: C.text },
  h3: { fontSize: 15, margin: '4px 0 10px', color: C.text, fontWeight: 700 },
  tableWrap: { overflowX: 'auto', background: C.panel, border: `1px solid ${C.border}`, borderRadius: 10, boxShadow: SH.card },
  table: { width: '100%', borderCollapse: 'collapse', fontSize: 13 },
  th: { textAlign: 'left', padding: '11px 14px', color: C.dim, borderBottom: `2px solid ${C.border}`, fontWeight: 700 },
  tr: { borderBottom: `1px solid ${C.border}` },
  td: { padding: '9px 14px', color: C.text },
  tdName: { padding: '9px 14px', color: C.text, fontWeight: 700, cursor: 'pointer' },
  link: { color: C.blue, textDecoration: 'underline', textUnderlineOffset: 2 },
  sub: { fontSize: 10, color: C.dim, fontWeight: 400 },
  runBtn: { background: C.green, border: 'none', color: '#fff', fontSize: 12, fontWeight: 700, padding: '4px 12px', borderRadius: 5, cursor: 'pointer', whiteSpace: 'nowrap' },
  warn: { marginTop: 3, fontSize: 10, color: '#b45309', fontWeight: 700 },
};
