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
            ? <button style={S.stop} onClick={() => api.stop(name).then(load)}>Stop</button>
            : <button style={S.run} onClick={() => api.run(name, 'paper').then(load)}>Run paper</button>}
        </div>
      </div>
      {err && <div style={{ color: C.red, marginBottom: 8 }}>snapshot error: {err}</div>}

      {/* Always-on charts + forward impact */}
      <MultiTFChartPanel instruments={instruments} />
      <ForwardImpactPanel
        instruments={instruments}
        narrator={snap?.narrator}
        scenarios={snap?.scenarios}
        indicators={snap?.indicators}
      />

      {/* Session summary */}
      <div style={S.statRow}>
        <Stat label="State" value={snap?.running ? (sess.phase || 'RUNNING') : 'idle'} color={snap?.running ? C.green : C.dim} />
        <Stat label="Mode" value={(sess.mode || meta?.status || '—').toUpperCase()} />
        <Stat label="Session P&L" value={`Rs.${(sess.session_pnl || 0).toLocaleString('en-IN')}`} color={(sess.session_pnl || 0) >= 0 ? C.green : C.red} />
        <Stat label="Trades" value={sess.total_trades ?? 0} />
        <Stat label="Wins" value={sess.wins ?? 0} />
      </div>

      <div style={S.cols}>
        <div style={S.col}>
          <TradePanel openTrades={(snap?.open_trades) || []} closedTrades={snap?.closed_trades || []} />
        </div>
        <div style={S.col}>
          <LogStream logs={snap?.log_lines || []} />
        </div>
      </div>
    </div>
  );
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
};
