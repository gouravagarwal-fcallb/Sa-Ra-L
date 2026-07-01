import React, { useEffect, useState, useCallback } from 'react';
import {
  ComposedChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
  ReferenceArea,
} from 'recharts';
import { api, C, SH } from '../api';

/**
 * Multi-timeframe charts with Bollinger Bands ALWAYS overlaid.
 * Renders every major timeframe at once (1m, 3m, 5m, 15m, 1h, 1D, 1W) so the
 * periodic structure that drives the strategies is visible together on screen.
 *
 * OBSERVE-ONLY GTI zone overlay: horizontal demand (green) / supply (red) bands
 * from the 15-minute detector — the ONE timeframe whose zone edge validated on
 * both NIFTY & SENSEX (cost-viable). A price level is the same on every chart,
 * so the 15m zones are drawn across all timeframes as shared context. Read-only:
 * this draws lines, it places no orders and feeds no strategy.
 */
const TIMEFRAMES = ['1m', '3m', '5m', '15m', '1h', '1d', '1w'];

// Demand = support (bullish, green); Supply = resistance (bearish, red).
function zoneColor(side) { return side === 'demand' ? C.green : C.red; }

// Keep only zones whose band overlaps this chart's visible price range, so a
// far-away level doesn't stretch the auto Y-axis and squash the candles flat.
function visibleZones(zones, data) {
  if (!zones || !zones.length || !data || !data.length) return [];
  let lo = Infinity, hi = -Infinity;
  for (const d of data) {
    if (d.low != null) lo = Math.min(lo, d.low);
    if (d.high != null) hi = Math.max(hi, d.high);
  }
  if (!isFinite(lo) || !isFinite(hi)) return [];
  return zones.filter(z => {
    const zLo = Math.min(z.proximal, z.distal), zHi = Math.max(z.proximal, z.distal);
    return zHi >= lo && zLo <= hi;               // band intersects the view
  });
}

function buildSeries(chart) {
  const bars = chart.bars || [];
  const bb = chart.bb || {};
  const up = bb.upper || [], mid = bb.mid || [], lo = bb.lower || [];
  // align BB arrays (which span all bars) to the (last 250) bars we render
  const offset = Math.max(0, (up.length || bars.length) - bars.length);
  return bars.map((b, i) => ({
    t: (b.t || '').slice(-5),
    close: b.c, high: b.h, low: b.l,
    bbU: up[offset + i] ?? null,
    bbM: mid[offset + i] ?? null,
    bbL: lo[offset + i] ?? null,
  }));
}

function TFChart({ instrument, tf, zones, showZones }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);

  const load = useCallback(() => {
    api.chart(instrument, tf)
      .then(c => { setData(buildSeries(c)); setErr(c.source === 'error' ? c.reason : null); })
      .catch(e => setErr(String(e)));
  }, [instrument, tf]);

  useEffect(() => {
    load();
    const intraday = ['1m', '3m', '5m', '15m', '1h'].includes(tf);
    const id = setInterval(load, intraday ? 15000 : 60000);
    return () => clearInterval(id);
  }, [load, tf]);

  const zb = showZones ? visibleZones(zones, data) : [];

  return (
    <div style={S.chartBox}>
      <div style={S.chartHead}>
        <span style={{ fontWeight: 700, color: C.cyan }}>{tf.toUpperCase()}</span>
        <span style={{ color: C.dim, fontSize: 10 }}>
          {instrument} · BB(20,2σ){data ? ` · ${data.length} bars` : ''}
          {zb.length ? ` · ${zb.length} zone${zb.length > 1 ? 's' : ''}` : ''}
        </span>
      </div>
      {(!data || data.length === 0) ? (
        <div style={S.empty}>{err ? `no data (${err.slice(0, 40)})` : 'loading…'}</div>
      ) : (
        <ResponsiveContainer width="100%" height={160}>
          <ComposedChart data={data} margin={{ top: 4, right: 6, bottom: 0, left: -14 }}>
            <CartesianGrid stroke={C.border} strokeDasharray="2 4" />
            <XAxis dataKey="t" tick={{ fontSize: 10, fill: C.dim }} minTickGap={28} />
            <YAxis domain={['auto', 'auto']} tick={{ fontSize: 10, fill: C.dim }} width={48} />
            <Tooltip contentStyle={{ background: C.panel, border: `1px solid ${C.border}`, fontSize: 12, borderRadius: 6, boxShadow: SH.raised }}
                     labelStyle={{ color: C.dim }} />
            {/* GTI 15m demand/supply zones — drawn first so candles/BB sit on top.
                Fresh (untested) zones are more opaque; freshness is what the
                research found matters, NOT the strength score. */}
            {zb.map((z, k) => (
              <ReferenceArea key={`z${k}`} y1={z.distal} y2={z.proximal}
                             fill={zoneColor(z.side)} fillOpacity={z.fresh ? 0.16 : 0.07}
                             stroke={zoneColor(z.side)} strokeOpacity={z.fresh ? 0.45 : 0.2}
                             strokeDasharray={z.fresh ? undefined : '3 3'} ifOverflow="hidden" />
            ))}
            <Line type="monotone" dataKey="bbU" stroke={C.band} dot={false} strokeWidth={1.2} strokeDasharray="3 3" isAnimationActive={false} name="BB upper" />
            <Line type="monotone" dataKey="bbM" stroke={C.mid}  dot={false} strokeWidth={1} isAnimationActive={false} name="BB mid" />
            <Line type="monotone" dataKey="bbL" stroke={C.band} dot={false} strokeWidth={1.2} strokeDasharray="3 3" isAnimationActive={false} name="BB lower" />
            <Line type="monotone" dataKey="close" stroke={C.line} dot={false} strokeWidth={2} isAnimationActive={false} name="Close" />
          </ComposedChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}

export default function MultiTFChartPanel({ instruments }) {
  const list = instruments && instruments.length ? instruments : ['NIFTY'];
  const [inst, setInst] = useState(list[0]);
  const [zones, setZones] = useState([]);
  const [showZones, setShowZones] = useState(true);

  // Fetch the validated 15m zones ONCE per instrument (a price level is the same
  // on every chart) and share them across all timeframe panels. Observe-only.
  const loadZones = useCallback(() => {
    api.zones(inst, '15m')
      .then(z => setZones(z && z.zones ? z.zones : []))
      .catch(() => setZones([]));
  }, [inst]);

  useEffect(() => {
    loadZones();
    const id = setInterval(loadZones, 60000);
    return () => clearInterval(id);
  }, [loadZones]);

  const fresh = zones.filter(z => z.fresh).length;

  return (
    <div style={S.panel}>
      <div style={S.head}>
        <span style={S.title}>MULTI-TIMEFRAME CHARTS + BOLLINGER BANDS</span>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <button onClick={() => setShowZones(v => !v)}
                  title="Observe-only GTI 15m demand/supply zones (validated NIFTY+SENSEX). Draws nothing to the order engine."
                  style={{ ...S.zoneBtn, ...(showZones ? S.zoneBtnOn : {}) }}>
            {showZones ? '◧ GTI zones ON' : '▢ GTI zones OFF'}
          </button>
          <div style={{ display: 'flex', gap: 4 }}>
            {list.map(i => (
              <button key={i} onClick={() => setInst(i)}
                      style={{ ...S.instBtn, ...(inst === i ? S.instBtnOn : {}) }}>{i}</button>
            ))}
          </div>
        </div>
      </div>
      {showZones && (
        <div style={S.legend}>
          <span style={{ ...S.chip, background: 'rgba(22,163,74,0.16)', borderColor: C.green, color: C.green }}>■ demand (support)</span>
          <span style={{ ...S.chip, background: 'rgba(220,38,38,0.16)', borderColor: C.red, color: C.red }}>■ supply (resistance)</span>
          <span style={{ color: C.dim }}>solid = fresh · dashed = tested · GTI 15m (observe-only)</span>
          <span style={{ color: C.dim, marginLeft: 'auto' }}>
            {zones.length} near price · {fresh} fresh
          </span>
        </div>
      )}
      <div style={S.grid}>
        {TIMEFRAMES.map(tf => (
          <TFChart key={tf} instrument={inst} tf={tf} zones={zones} showZones={showZones} />
        ))}
      </div>
    </div>
  );
}

const S = {
  panel: { background: C.panel, border: `1px solid ${C.border}`, borderRadius: 10, padding: 12, marginBottom: 12, boxShadow: SH.card },
  head: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 },
  title: { fontSize: 13, fontWeight: 700, letterSpacing: 0.6, color: C.text },
  instBtn: { background: C.panel2, border: `1px solid ${C.border}`, color: C.dim, fontSize: 11, fontWeight: 700, padding: '3px 10px', borderRadius: 4, cursor: 'pointer' },
  instBtnOn: { borderColor: C.cyan, color: '#fff', background: C.cyan },
  zoneBtn: { background: C.panel2, border: `1px solid ${C.border}`, color: C.dim, fontSize: 11, fontWeight: 700, padding: '3px 10px', borderRadius: 4, cursor: 'pointer' },
  zoneBtnOn: { borderColor: C.purple, color: '#fff', background: C.purple },
  legend: { display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', fontSize: 10, marginBottom: 8, color: C.dim },
  chip: { fontSize: 10, fontWeight: 700, padding: '2px 7px', borderRadius: 4, border: '1px solid' },
  // All seven timeframes in a single row, side by side, for at-a-glance comparison.
  // Each keeps a sensible min width and the row scrolls horizontally if the
  // screen is too narrow to fit all seven.
  grid: { display: 'grid', gridTemplateColumns: 'repeat(7, minmax(180px, 1fr))', gap: 8, overflowX: 'auto', paddingBottom: 4 },
  chartBox: { background: C.panel2, border: `1px solid ${C.border}`, borderRadius: 8, padding: 8 },
  chartHead: { display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 4 },
  empty: { height: 160, display: 'flex', alignItems: 'center', justifyContent: 'center', color: C.dim, fontSize: 12 },
};
