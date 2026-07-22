import React, { useEffect, useState } from 'react';
import { api, C, SH } from '../api';

/** Fixed-pot portfolio simulation: how every strategy performs on a shared capital
 *  (default ₹1L), with a per-strategy TRUST flag and a verified-only rollup so
 *  mixed-quality backtests aren't blended into one misleading headline number. */
export default function PortfolioSim() {
  const [cap, setCap] = useState(100000);
  const [d, setD] = useState(null);
  const [err, setErr] = useState(null);
  const [open, setOpen] = useState(true);

  const load = (c) => api.portfolioSim(c).then(setD).catch(e => setErr(String(e)));
  useEffect(() => { load(cap); /* eslint-disable-line */ }, []);

  const inr = (v) => (v == null ? '—' : (Math.abs(v) >= 1e7
    ? '₹' + (v / 1e7).toFixed(2) + ' Cr'
    : Math.abs(v) >= 1e5 ? '₹' + (v / 1e5).toFixed(2) + ' L'
    : '₹' + Math.round(v).toLocaleString('en-IN')));
  const rup = (v) => (v == null ? '—' : '₹' + Math.round(v).toLocaleString('en-IN'));

  const TRUST = {
    verified:   { c: C.green, t: 'Backtest verified to match the live engine — trustworthy.' },
    unverified: { c: C.amber, t: 'Backtest NOT yet verified vs the live engine — indicative only.' },
    no_data:    { c: C.dim,   t: '0 backtest trades (OI-blocked / never fired) — cannot judge.' },
    modelled:   { c: C.purple,t: 'Synthetic / structural model — not real fills.' },
    separate:   { c: C.cyan,  t: 'Separate engine (currency futures).' },
    archived:   { c: C.dim,   t: 'Archived — not traded.' },
  };

  if (err) return <div style={{ color: C.red, margin: '10px 0' }}>Portfolio sim error: {err}</div>;
  if (!d) return <div style={{ color: C.dim, margin: '10px 0' }}>Building portfolio simulation…</div>;

  const Roll = ({ title, r, trustworthy }) => (
    <div style={{ ...S.roll, ...(trustworthy ? S.rollGood : {}) }}>
      <div style={S.rollTitle}>{title} <span style={{ color: C.dim, fontWeight: 400 }}>· {r.count} strategies</span></div>
      <div style={S.rollGrid}>
        <div><div style={S.k}>Start</div><div style={S.v}>{inr(r.start_capital)}</div></div>
        <div><div style={S.k}>Net P&L</div><div style={{ ...S.v, color: r.total_pnl >= 0 ? C.green : C.red }}>{inr(r.total_pnl)} <span style={{ fontSize: 12 }}>({r.return_pct >= 0 ? '+' : ''}{r.return_pct}%)</span></div></div>
        <div><div style={S.k}>Profit withdrawn</div><div style={{ ...S.v, color: C.green }}>{inr(r.profit_withdrawn)}</div></div>
        <div><div style={S.k}>Loss retained (base eaten)</div><div style={{ ...S.v, color: C.red }}>{inr(r.loss_retained)}</div></div>
        <div><div style={S.k}>Base for next year</div><div style={S.v}>{inr(r.capital_after_withdrawal)}</div></div>
      </div>
    </div>
  );

  return (
    <div style={S.wrap}>
      <div style={S.head}>
        <div>
          <div style={S.title}>Portfolio Simulation — fixed pot, no fresh capital</div>
          <div style={S.sub}>{d.rules}</div>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <label style={S.capLbl}>Pot ₹
            <input type="number" step="10000" value={cap}
                   onChange={e => setCap(Number(e.target.value))}
                   onBlur={() => load(cap)} style={S.capIn} />
          </label>
          <a href={api.portfolioSimExportUrl(cap)} target="_blank" rel="noreferrer" style={S.exp}>↓ MD</a>
          <button style={S.toggle} onClick={() => setOpen(o => !o)}>{open ? 'Hide' : 'Show'}</button>
        </div>
      </div>

      {open && <>
        {/* the two rollups — verified first, loud */}
        <div style={S.rolls}>
          <Roll title="✅ VERIFIED-ONLY (trust this)" r={d.portfolio_verified} trustworthy />
          <Roll title="⚠ ALL tradeable (mixed trust — read with care)" r={d.portfolio_all} />
        </div>

        <div style={S.tableWrap}>
          <table style={S.table}>
            <thead><tr>{['Strategy', 'Trust', 'Trades', 'Win %', 'PF', 'Net P&L', 'Max DD', 'Period'].map(h =>
              <th key={h} style={S.th}>{h}</th>)}</tr></thead>
            <tbody>
              {d.strategies.map(s => {
                const tr = TRUST[s.trust] || { c: C.dim, t: s.trust_note };
                const p = s.period || {};
                return (
                  <tr key={s.name} style={{ ...S.tr, opacity: s.tradeable ? 1 : 0.55 }}>
                    <td style={{ ...S.td, fontWeight: 700 }}>{s.name}</td>
                    <td style={S.td}><span style={{ ...S.pill, background: tr.c + '22', color: tr.c }} title={s.trust_note}>{s.trust}</span></td>
                    <td style={S.td}>{s.trades.toLocaleString('en-IN')}</td>
                    <td style={S.td}>{s.win_rate ? `${s.win_rate}%` : '—'}</td>
                    <td style={S.td}>{s.profit_factor ? s.profit_factor.toFixed(2) : '—'}</td>
                    <td style={{ ...S.td, color: s.pnl >= 0 ? C.green : C.red, fontWeight: 700 }}>{s.trades ? rup(s.pnl) : '—'}</td>
                    <td style={{ ...S.td, color: C.red }}>{s.max_drawdown ? rup(s.max_drawdown) : '—'}</td>
                    <td style={{ ...S.td, fontSize: 11, color: C.dim }}>{p.start ? `${p.start}→${p.end}` : '—'}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        <div style={S.caveats}>
          <div style={S.caveatsHd}>⚠ READ BEFORE ACTING</div>
          {d.caveats.map((c, i) => <div key={i} style={S.caveat}>• {c}</div>)}
        </div>
      </>}
    </div>
  );
}

const S = {
  wrap: { background: C.panel, border: `1px solid ${C.border}`, borderRadius: 10, boxShadow: SH.card, padding: 16, marginBottom: 16 },
  head: { display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 10, flexWrap: 'wrap' },
  title: { fontSize: 16, fontWeight: 700, color: C.text },
  sub: { fontSize: 12, color: C.dim, marginTop: 3, maxWidth: 720 },
  capLbl: { fontSize: 11, color: C.dim, fontWeight: 700, display: 'flex', alignItems: 'center', gap: 4 },
  capIn: { width: 100, border: `1px solid ${C.border}`, borderRadius: 5, padding: '4px 6px', fontSize: 13 },
  exp: { fontSize: 12, color: C.cyan, border: `1px solid ${C.border}`, borderRadius: 5, padding: '4px 8px', textDecoration: 'none' },
  toggle: { background: 'transparent', border: `1px solid ${C.border}`, color: C.cyan, fontSize: 12, padding: '4px 10px', borderRadius: 5, cursor: 'pointer' },
  rolls: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(300px,1fr))', gap: 12, marginTop: 12 },
  roll: { border: `1px solid ${C.border}`, borderRadius: 8, padding: 12, background: '#fff' },
  rollGood: { border: `2px solid ${C.green}`, background: '#f0fdf4' },
  rollTitle: { fontSize: 13, fontWeight: 800, color: C.text, marginBottom: 8 },
  rollGrid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(120px,1fr))', gap: 8 },
  k: { fontSize: 10.5, color: C.dim, fontWeight: 600 },
  v: { fontSize: 15, fontWeight: 800, color: C.text },
  tableWrap: { overflowX: 'auto', marginTop: 12, border: `1px solid ${C.border}`, borderRadius: 8 },
  table: { width: '100%', borderCollapse: 'collapse', fontSize: 12.5 },
  th: { textAlign: 'left', padding: '8px 12px', color: C.dim, borderBottom: `2px solid ${C.border}`, fontWeight: 700, whiteSpace: 'nowrap' },
  tr: { borderBottom: `1px solid ${C.border}` },
  td: { padding: '7px 12px', color: C.text, whiteSpace: 'nowrap' },
  pill: { fontSize: 10, fontWeight: 700, padding: '2px 7px', borderRadius: 9, textTransform: 'uppercase', cursor: 'help' },
  caveats: { marginTop: 12, background: '#fff7ed', border: '1px solid #fed7aa', borderRadius: 8, padding: '10px 12px' },
  caveatsHd: { fontSize: 11, fontWeight: 800, color: '#b45309', letterSpacing: 0.4, marginBottom: 5 },
  caveat: { fontSize: 12, color: '#7c2d12', margin: '3px 0', lineHeight: 1.5 },
};
