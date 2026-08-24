import React, { useEffect, useState, useCallback } from 'react';
import { api, C, SH } from '../api';
import MultiTFChartPanel from '../components/MultiTFChartPanel';
import ForwardImpactPanel from '../components/ForwardImpactPanel';
import TradePanel from '../components/TradePanel';
import LogStream from '../components/LogStream';

/**
 * Per-strategy detail: the always-on multi-timeframe charts (with Bollinger
 * Bands) and the forward-impact projection sit on top, followed by the reused
 * BRAHMASTRA trade/log panels fed from this strategy's live snapshot.
 */
export default function StrategyDetail({ name, meta, onBack }) {
  const [snap, setSnap] = useState(null);
  const [err, setErr]   = useState(null);

  const load = useCallback(() => {
    api.snapshot(name).then(setSnap).catch(e => setErr(String(e)));
  }, [name]);
  useEffect(() => { load(); const id = setInterval(load, 3000); return () => clearInterval(id); }, [load]);

  const instruments = meta?.instruments?.length ? meta.instruments : ['NIFTY'];
  const sess = snap?.session || {};

  return (
    <div>
      <div style={S.bar}>
        <button style={S.back} onClick={onBack}>← All strategies</button>
        <div style={{ fontWeight: 700, fontSize: 16 }}>{name}</div>
        <div style={{ color: C.dim, fontSize: 12 }}>{meta?.full_name}</div>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
          {snap?.running
            ? <button style={S.stop} onClick={() => api.stop(name).then(load).catch(e => setErr(`STOP FAILED: ${e} — retry or Ctrl-C the server`))}>Stop</button>
            : <button style={S.run} onClick={() => api.run(name, 'paper').then(load).catch(e => setErr(`Start failed: ${e}`))}>Run paper</button>}
        </div>
      </div>
      {err && <div style={{ background: '#fef2f2', color: '#b91c1c', border: '1px solid #fecaca', borderRadius: 6, padding: '8px 12px', marginBottom: 8, fontWeight: 700, fontSize: 13, cursor: 'pointer' }} onClick={() => setErr(null)}>{err} <span style={{ float: 'right' }}>✕</span></div>}

      {/* In-page section index — click a heading to jump to it */}
      <div style={S.indexBar}>
        <span style={S.indexLabel}>Jump to:</span>
        {[['sec-charts', 'Charts'], ['sec-impact', 'Forward Impact'], ['sec-summary', 'Summary'], ['sec-trades', 'Trades'], ['sec-logs', 'Logs']].map(([id, label]) => (
          <button key={id} style={S.indexBtn} onClick={() => jump(id)}>{label}</button>
        ))}
      </div>

      {/* Always-on charts + forward impact */}
      <section id="sec-charts" style={S.section}>
        <MultiTFChartPanel instruments={instruments} />
      </section>
      <section id="sec-impact" style={S.section}>
        <ForwardImpactPanel
          instruments={instruments}
          narrator={snap?.narrator}
          scenarios={snap?.scenarios}
          indicators={snap?.indicators}
        />
      </section>

      {/* Session summary */}
      <section id="sec-summary" style={S.section}>
        <div style={S.statRow}>
          <Stat label="State" value={snap?.running ? (sess.phase || 'RUNNING') : 'idle'} color={snap?.running ? C.green : C.dim} />
          <Stat label="Mode" value={(sess.mode || meta?.status || '—').toUpperCase()} />
          <Stat label="Session P&L" value={`Rs.${Math.round(sess.session_pnl || 0).toLocaleString('en-IN')}`} color={(sess.session_pnl || 0) >= 0 ? C.green : C.red} />
          <Stat label="Trades" value={sess.total_trades ?? 0} />
          <Stat label="Wins" value={sess.wins ?? 0} />
        </div>
      </section>

      <div style={S.cols}>
        <section id="sec-trades" style={{ ...S.section, ...S.col }}>
          <TradePanel openTrades={(snap?.open_trades) || []} closedTrades={snap?.closed_trades || []} />
        </section>
        <section id="sec-logs" style={{ ...S.section, ...S.col }}>
          <LogStream logs={snap?.log_lines || []} />
        </section>
      </div>
    </div>
  );
}

function jump(id) {
  const el = document.getElementById(id);
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function Stat({ label, value, color }) {
  return (
    <div style={S.stat}>
      <div style={{ fontSize: 10, color: C.dim }}>{label}</div>
      <div style={{ fontSize: 15, fontWeight: 700, color: color || C.text }}>{value}</div>
    </div>
  );
}

const S = {
  bar: { display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 },
  back: { background: 'transparent', border: `1px solid ${C.border}`, color: C.cyan, padding: '4px 10px', borderRadius: 4, cursor: 'pointer' },
  run: { background: C.blue, border: 'none', color: '#fff', fontWeight: 700, padding: '5px 12px', borderRadius: 4, cursor: 'pointer' },
  stop: { background: C.amber, border: 'none', color: '#000', fontWeight: 700, padding: '5px 12px', borderRadius: 4, cursor: 'pointer' },
  statRow: { display: 'flex', gap: 10, marginBottom: 12 },
  stat: { flex: 1, background: C.panel, border: `1px solid ${C.border}`, borderRadius: 8, padding: '10px 14px', boxShadow: SH.card },
  cols: { display: 'flex', gap: 10, flexWrap: 'wrap' },
  col: { flex: 1, minWidth: 320 },
  indexBar: { position: 'sticky', top: 56, zIndex: 30, display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', background: C.panel, border: `1px solid ${C.border}`, borderRadius: 8, padding: '7px 12px', marginBottom: 12, boxShadow: SH.card },
  indexLabel: { fontSize: 11, fontWeight: 700, color: C.dim, letterSpacing: 0.4, marginRight: 2 },
  indexBtn: { background: '#e8f1fb', border: `1px solid ${C.border}`, color: C.blue, fontSize: 12, fontWeight: 700, padding: '4px 12px', borderRadius: 14, cursor: 'pointer' },
  section: { scrollMarginTop: 108 },
};
