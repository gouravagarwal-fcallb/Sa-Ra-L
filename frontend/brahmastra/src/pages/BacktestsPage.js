import React, { useEffect, useState } from 'react';
import { api, C } from '../api';

/** All strategies' backtest summaries in one table (summary.json, CSV fallback). */
export default function BacktestsPage() {
  const [rows, setRows] = useState([]);
  const [err, setErr]   = useState(null);
  useEffect(() => { api.backtests().then(setRows).catch(e => setErr(String(e))); }, []);

  const fmt = (v) => v == null ? '—' : (typeof v === 'number' ? v.toLocaleString('en-IN') : v);
  const pnlColor = (v) => v == null ? C.dim : v >= 0 ? C.green : C.red;

  return (
    <div>
      <h2 style={S.h2}>Backtest Summaries</h2>
      {err && <div style={{ color: C.red }}>{err}</div>}
      <div style={S.tableWrap}>
        <table style={S.table}>
          <thead>
            <tr>
              {['Strategy', 'Source', 'Trades', 'Total P&L', 'Win %', 'Sharpe', 'Max DD', 'Period'].map(h =>
                <th key={h} style={S.th}>{h}</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.map(r => {
              const sum = r.summary || {};
              const period = (r.period || sum.period);
              return (
                <tr key={r.name} style={S.tr}>
                  <td style={S.tdName}>{r.name}<div style={S.sub}>{r.status}</div></td>
                  <td style={S.td}>
                    {r.has_summary_json ? <span style={{ color: C.green }}>summary.json</span>
                      : r.has_csv ? <span style={{ color: C.amber }}>csv</span>
                      : <span style={{ color: C.red }}>none — run backtest</span>}
                  </td>
                  <td style={S.td}>{fmt(r.total_trades ?? sum.total_trades)}</td>
                  <td style={{ ...S.td, color: pnlColor(r.total_pnl ?? sum.total_pnl) }}>{fmt(r.total_pnl ?? sum.total_pnl)}</td>
                  <td style={S.td}>{fmt(r.win_rate ?? sum.win_rate)}</td>
                  <td style={S.td}>{fmt(r.sharpe ?? sum.sharpe)}</td>
                  <td style={{ ...S.td, color: C.red }}>{fmt(r.max_drawdown ?? sum.max_drawdown)}</td>
                  <td style={S.td}>{period ? `${period.start || ''}→${period.end || ''}` : '—'}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

const S = {
  h2: { fontSize: 18, marginBottom: 12 },
  tableWrap: { overflowX: 'auto', background: C.panel, border: `1px solid ${C.border}`, borderRadius: 8 },
  table: { width: '100%', borderCollapse: 'collapse', fontSize: 12 },
  th: { textAlign: 'left', padding: '10px 12px', color: C.dim, borderBottom: `1px solid ${C.border}`, fontWeight: 700 },
  tr: { borderBottom: `1px solid ${C.border}` },
  td: { padding: '8px 12px', color: C.text },
  tdName: { padding: '8px 12px', color: C.text, fontWeight: 700 },
  sub: { fontSize: 9, color: C.dim, fontWeight: 400 },
};
