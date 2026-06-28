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
  const poll = useRef(null);

  const load = useCallback(() => {
    api.backtestSummary(name).then(d => { setData(d); setErr(null); })
      .catch(e => setErr(String(e)));
  }, [name]);
  useEffect(() => { load(); return () => clearInterval(poll.current); }, [load]);

  const run = () => {
    setRunning(true);
    api.runBacktest(name).then(() => {
      poll.current = setInterval(() => {
        api.backtestStatus(name).then(s => {
          if (s.state === 'done' || s.state === 'error' || s.state === 'idle') {
            clearInterval(poll.current);
            setRunning(false);
            load();
            setImgKey(k => k + 1);
            if (s.state === 'error') setErr(s.error || 'backtest failed');
          }
        }).catch(() => {});
      }, 2000);
    }).catch(e => { setErr(String(e)); setRunning(false); });
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
