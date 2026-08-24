import React, { useEffect, useState, useCallback } from 'react';
import { api, C, SH } from '../api';

/**
 * Portfolio Risk — observe-only book view. Rolls every running strategy's open
 * option positions into net Greeks, exposure, premium-at-risk (the true max loss
 * for long options), a stress ladder, and a 1-day VaR. Places NO orders.
 */
const rup = (n) => (n == null ? '—' : `${n < 0 ? '-' : ''}₹${Math.abs(Math.round(n)).toLocaleString('en-IN')}`);
const pcol = (v) => (v > 0 ? C.green : v < 0 ? C.red : C.dim);

export default function PortfolioRiskPage() {
  const [d, setD] = useState(null);
  const [err, setErr] = useState(null);

  const load = useCallback(() => {
    api.portfolioRisk().then(setD).catch(e => setErr(String(e)));
  }, []);
  useEffect(() => { load(); const id = setInterval(load, 6000); return () => clearInterval(id); }, [load]);

  if (err) return <div style={{ color: C.red }}>Risk data error: {err}</div>;
  if (!d) return <div style={{ color: C.dim }}>Loading book risk…</div>;

  const g = d.net_greeks || {};
  const empty = !d.positions;

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
        <h2 style={S.h2}>Portfolio Risk</h2>
        <span style={S.obs}>OBSERVE-ONLY · places no orders</span>
        <span style={{ marginLeft: 'auto', fontSize: 11, color: C.dim }}>as of {d.generated_at}</span>
      </div>

      {empty ? (
        <div style={S.note}>No open option positions across running strategies right now. This view
          populates live as strategies open positions.</div>
      ) : (
        <>
          {/* Headline risk numbers */}
          <div style={S.cards}>
            <Card title="Premium at risk" hint="Max loss if every long option expired worthless — the true worst case for an option-buyer book."
                  value={rup(d.premium_at_risk)} color={C.red} />
            <Card title="1-day VaR (95%)" hint="Delta-normal estimate: the loss a normal day should not exceed 19 times out of 20. Approximate — the hard cap is 'premium at risk'."
                  value={rup(d.var_1d_95)} color={C.amber} />
            <Card title="Directional lean" hint="Net delta sign across the book — which way the book profits if the index rises."
                  value={d.directional_lean}
                  color={d.directional_lean === 'BULLISH' ? C.green : d.directional_lean === 'BEARISH' ? C.red : C.dim} />
            <Card title="Δ per +1% index" hint="Approx book P&L for a 1% up-move in the underlying (positive = gains on a rally)."
                  value={rup(d.delta_rupees_per_1pct)} color={pcol(d.delta_rupees_per_1pct)} />
            <Card title="Open positions" value={String(d.positions)} color={C.text} />
          </div>

          {/* Net Greeks */}
          <div style={S.panel}>
            <div style={S.pTitle}>NET GREEKS <span style={S.dim}>(book totals · IV proxied from VIX {d.inputs?.iv_used_pct}%)</span></div>
            <div style={S.greekRow}>
              <Greek label="Delta" val={g.delta} hint="Book sensitivity to a 1-point index move (in option units)." />
              <Greek label="Gamma" val={g.gamma} hint="How fast delta itself changes as the index moves — high near expiry/ATM." />
              <Greek label="Vega"  val={g.vega} suffix=" /1% vol" hint="P&L change per 1% move in implied volatility." />
              <Greek label="Theta" val={g.theta} suffix=" /day" hint="Time decay — daily P&L bleed if nothing else moves (usually negative for buyers)." color />
            </div>
          </div>

          {/* Stress ladder */}
          <div style={S.panel}>
            <div style={S.pTitle}>STRESS TEST <span style={S.dim}>(full option reprice at each index shock — captures gamma)</span></div>
            <div style={S.stressRow}>
              {(d.stress || []).map(s => (
                <div key={s.move_pct} style={S.stressCell}>
                  <div style={{ fontSize: 12, color: C.dim }}>{s.move_pct > 0 ? '+' : ''}{s.move_pct}%</div>
                  <div style={{ fontSize: 15, fontWeight: 800, color: pcol(s.pnl) }}>{rup(s.pnl)}</div>
                </div>
              ))}
            </div>
          </div>

          {/* Breakdown by underlying + strategy */}
          <div style={S.two}>
            <div style={S.panel}>
              <div style={S.pTitle}>BY UNDERLYING</div>
              <table style={S.table}>
                <thead><tr>{['Index', 'Pos', 'CE/PE', 'Premium@risk', 'Net Δ'].map(h => <th key={h} style={S.th}>{h}</th>)}</tr></thead>
                <tbody>
                  {Object.entries(d.by_underlying || {}).map(([u, v]) => (
                    <tr key={u} style={S.tr}>
                      <td style={S.td}>{u}</td><td style={S.td}>{v.positions}</td>
                      <td style={S.td}>{v.ce}/{v.pe}</td>
                      <td style={S.td}>{rup(v.premium_at_risk)}</td>
                      <td style={{ ...S.td, color: pcol(v.net_delta) }}>{v.net_delta}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div style={S.panel}>
              <div style={S.pTitle}>BY STRATEGY</div>
              <table style={S.table}>
                <thead><tr>{['Strategy', 'Pos', 'Premium@risk', 'Net Δ'].map(h => <th key={h} style={S.th}>{h}</th>)}</tr></thead>
                <tbody>
                  {Object.entries(d.by_strategy || {}).map(([s, v]) => (
                    <tr key={s} style={S.tr}>
                      <td style={S.td}>{s}</td><td style={S.td}>{v.positions}</td>
                      <td style={S.td}>{rup(v.premium_at_risk)}</td>
                      <td style={{ ...S.td, color: pcol(v.net_delta) }}>{v.net_delta}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* Legs */}
          <div style={S.panel}>
            <div style={S.pTitle}>OPEN LEGS <span style={S.dim}>(largest premium-at-risk first)</span></div>
            <table style={S.table}>
              <thead><tr>{['Strategy', 'Leg', 'Qty', 'Premium', 'Premium@risk', 'Δ', 'Γ', 'Θ/day'].map(h => <th key={h} style={S.th}>{h}</th>)}</tr></thead>
              <tbody>
                {(d.legs || []).map((l, i) => (
                  <tr key={i} style={S.tr}>
                    <td style={S.td}>{l.strategy}</td>
                    <td style={S.td}>{l.underlying} {l.strike} {l.option_type}</td>
                    <td style={S.td}>{l.qty}</td>
                    <td style={S.td}>{l.premium?.toFixed(2)}</td>
                    <td style={S.td}>{rup(l.premium_at_risk)}</td>
                    <td style={{ ...S.td, color: pcol(l.delta) }}>{l.delta?.toFixed(1)}</td>
                    <td style={S.td}>{l.gamma?.toFixed(3)}</td>
                    <td style={{ ...S.td, color: pcol(l.theta) }}>{Math.round(l.theta)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {/* Assumptions — always shown so the numbers are never mistaken for broker-exact */}
      <div style={S.assume}>
        <b>How to read this (and what it assumes):</b>
        <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>
          {(d.assumptions || []).map((a, i) => <li key={i} style={{ fontSize: 11.5, color: C.dim, marginBottom: 2 }}>{a}</li>)}
        </ul>
      </div>
    </div>
  );
}

function Card({ title, value, color, hint }) {
  return (
    <div style={S.card} title={hint || ''}>
      <div style={{ fontSize: 11.5, color: C.dim, marginBottom: 5 }}>{title}{hint ? ' ⓘ' : ''}</div>
      <div style={{ fontSize: 19, fontWeight: 800, color }}>{value}</div>
    </div>
  );
}

function Greek({ label, val, suffix = '', hint, color }) {
  const c = color ? pcol(val) : C.text;
  return (
    <div style={S.greek} title={hint || ''}>
      <div style={{ fontSize: 12, color: C.dim }}>{label} ⓘ</div>
      <div style={{ fontSize: 17, fontWeight: 800, color: c }}>
        {val == null ? '—' : (Math.abs(val) >= 100 ? Math.round(val).toLocaleString('en-IN') : val.toFixed(2))}
        <span style={{ fontSize: 10, color: C.dim, fontWeight: 400 }}>{suffix}</span>
      </div>
    </div>
  );
}

const S = {
  h2: { fontSize: 20, margin: '0 0 12px', color: C.text },
  obs: { fontSize: 10.5, fontWeight: 800, color: C.green, background: '#f0fdf4', border: '1px solid #bbf7d0', borderRadius: 9, padding: '2px 9px', letterSpacing: 0.4 },
  note: { background: C.panel, border: `1px solid ${C.border}`, borderRadius: 10, padding: 16, color: C.dim, fontSize: 13 },
  cards: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))', gap: 10, marginBottom: 12 },
  card: { background: C.panel, border: `1px solid ${C.border}`, borderRadius: 10, padding: 14, boxShadow: SH.card, cursor: 'help' },
  panel: { background: C.panel, border: `1px solid ${C.border}`, borderRadius: 10, padding: 14, marginBottom: 12, boxShadow: SH.card },
  pTitle: { fontSize: 13, fontWeight: 700, letterSpacing: 0.6, color: C.cyan, marginBottom: 10 },
  dim: { fontSize: 10.5, color: C.dim, fontWeight: 400, letterSpacing: 0 },
  greekRow: { display: 'flex', gap: 22, flexWrap: 'wrap' },
  greek: { minWidth: 110, cursor: 'help' },
  stressRow: { display: 'flex', gap: 8, flexWrap: 'wrap' },
  stressCell: { flex: 1, minWidth: 90, textAlign: 'center', padding: '8px 6px', background: C.panel2, border: `1px solid ${C.border}`, borderRadius: 8 },
  two: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 12 },
  table: { width: '100%', borderCollapse: 'collapse', fontSize: 12.5 },
  th: { textAlign: 'left', padding: '6px 8px', color: C.dim, borderBottom: `2px solid ${C.border}`, fontWeight: 700 },
  tr: { borderBottom: `1px solid ${C.border}` },
  td: { padding: '6px 8px', color: C.text },
  assume: { background: C.panel2, border: `1px solid ${C.border}`, borderRadius: 8, padding: '10px 14px', marginTop: 12, fontSize: 12, color: C.text },
};
