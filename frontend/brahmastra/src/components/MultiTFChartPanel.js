import React, { useEffect, useState, useCallback } from 'react';
import {
  ComposedChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
} from 'recharts';
import { api, C, SH } from '../api';

/**
 * Multi-timeframe charts with Bollinger Bands ALWAYS overlaid.
 * Renders every major timeframe at once (1m, 5m, 15m, 1h, 1D, 1W) so the
 * periodic structure that drives the strategies is visible together on screen.
 */
const TIMEFRAMES = ['1m', '5m', '15m', '1h', '1d', '1w'];

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

function TFChart({ instrument, tf }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);

  const load = useCallback(() => {
    api.chart(instrument, tf)
      .then(c => { setData(buildSeries(c)); setErr(c.source === 'error' ? c.reason : null); })
      .catch(e => setErr(String(e)));
  }, [instrument, tf]);

  useEffect(() => {
    load();
    const intraday = ['1m', '5m', '15m', '1h'].includes(tf);
    const id = setInterval(load, intraday ? 15000 : 60000);
    return () => clearInterval(id);
  }, [load, tf]);

  return (
    <div style={S.chartBox}>
      <div style={S.chartHead}>
        <span style={{ fontWeight: 700, color: C.cyan }}>{tf.toUpperCase()}</span>
        <span style={{ color: C.dim, fontSize: 10 }}>
          {instrument} · BB(20,2σ){data ? ` · ${data.length} bars` : ''}
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

  return (
    <div style={S.panel}>
      <div style={S.head}>
        <span style={S.title}>MULTI-TIMEFRAME CHARTS + BOLLINGER BANDS</span>
        <div style={{ display: 'flex', gap: 4 }}>
          {list.map(i => (
            <button key={i} onClick={() => setInst(i)}
                    style={{ ...S.instBtn, ...(inst === i ? S.instBtnOn : {}) }}>{i}</button>
          ))}
        </div>
      </div>
      <div style={S.grid}>
        {TIMEFRAMES.map(tf => <TFChart key={tf} instrument={inst} tf={tf} />)}
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
  // All six timeframes in a single row, side by side, for at-a-glance comparison.
  // Each keeps a sensible min width and the row scrolls horizontally if the
  // screen is too narrow to fit all six.
  grid: { display: 'grid', gridTemplateColumns: 'repeat(6, minmax(190px, 1fr))', gap: 8, overflowX: 'auto', paddingBottom: 4 },
  chartBox: { background: C.panel2, border: `1px solid ${C.border}`, borderRadius: 8, padding: 8 },
  chartHead: { display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 4 },
  empty: { height: 160, display: 'flex', alignItems: 'center', justifyContent: 'center', color: C.dim, fontSize: 12 },
};
