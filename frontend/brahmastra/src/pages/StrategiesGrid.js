import React, { useEffect, useState, useCallback } from 'react';
import { api, C } from '../api';
import LiveGuardModal from '../components/LiveGuardModal';

const READY_COLOR = { READY: C.green, PARTIAL: C.amber, NOT_READY: C.red, PLANNED: C.dim, UNKNOWN: C.dim };
const STATUS_COLOR = { live: C.green, paper: C.blue, paused: C.purple, planned: C.amber, archived: C.dim, testing: C.cyan };

function Light({ ok, label }) {
  const col = ok === true ? C.green : ok === false ? C.red : C.dim;
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 3, fontSize: 9, color: C.dim }}>
      <span style={{ width: 7, height: 7, borderRadius: '50%', background: col, display: 'inline-block' }} />
      {label}
    </span>
  );
}

export default function StrategiesGrid({ onOpen }) {
  const [rows, setRows] = useState([]);
  const [err, setErr]   = useState(null);
  const [armFor, setArmFor] = useState(null);

  const load = useCallback(() => {
    api.strategies().then(setRows).catch(e => setErr(String(e)));
  }, []);
  useEffect(() => { load(); const id = setInterval(load, 5000); return () => clearInterval(id); }, [load]);

  const pnl = (rt) => {
    const v = (rt?.real_pnl || 0) + (rt?.paper_pnl || 0);
    return <span style={{ color: v > 0 ? C.green : v < 0 ? C.red : C.dim }}>{v ? `Rs.${v.toLocaleString('en-IN')}` : '—'}</span>;
  };

  return (
    <div>
      {err && <div style={{ color: C.red, marginBottom: 10 }}>API error: {err} — is the backend running?</div>}
      <div style={S.grid}>
        {rows.map(s => {
          const r = s.readiness || {};
          return (
            <div key={s.name} style={S.card}>
              <div style={S.cardHead} onClick={() => onOpen(s.name)}>
                <div>
                  <div style={S.name}>{s.name}</div>
                  <div style={S.full}>{s.full_name}</div>
                </div>
                <span style={{ ...S.statusPill, background: STATUS_COLOR[s.status] || C.dim }}>{s.status}</span>
              </div>
              <div style={S.metaRow}>
                <span style={{ color: READY_COLOR[r.overall] || C.dim, fontWeight: 700, fontSize: 11 }}>● {r.overall || '—'}</span>
                <span style={{ color: C.dim, fontSize: 11 }}>{(s.instruments || []).join(', ')}</span>
              </div>
              <div style={S.lights}>
                <Light ok={r.backtest_ok} label="backtest" />
                <Light ok={r.backfill_ok} label="backfill" />
                <Light ok={r.ticks_ok} label="ticks" />
                <Light ok={r.config_audit_ok} label="config" />
              </div>
              <div style={S.foot}>
                <span style={{ fontSize: 11, color: C.dim }}>
                  {s.runtime?.running ? <span style={{ color: C.green }}>● {s.runtime.state}</span> : <span>idle</span>}
                  {'  '}P&L {pnl(s.runtime)}
                </span>
                <div style={{ display: 'flex', gap: 5 }}>
                  {s.runtime?.running
                    ? <button style={S.stop} onClick={() => api.stop(s.name).then(load)}>Stop</button>
                    : <button style={S.run} onClick={() => api.run(s.name, 'paper').then(load)}>Paper</button>}
                  <button style={S.live}
                          disabled={!s.capital_allocated_rs}
                          title={s.capital_allocated_rs ? 'Arm real-money trading' : 'No capital allocated'}
                          onClick={() => s.capital_allocated_rs && setArmFor(s.name)}>Live</button>
                </div>
              </div>
            </div>
          );
        })}
      </div>
      {armFor && <LiveGuardModal strategy={armFor} onClose={() => setArmFor(null)} onLive={load} />}
    </div>
  );
}

const S = {
  grid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 10 },
  card: { background: C.panel, border: `1px solid ${C.border}`, borderRadius: 8, padding: 12 },
  cardHead: { display: 'flex', justifyContent: 'space-between', cursor: 'pointer', marginBottom: 8 },
  name: { fontWeight: 700, fontSize: 14, color: C.text },
  full: { fontSize: 10, color: C.dim, marginTop: 2, maxWidth: 210 },
  statusPill: { fontSize: 9, fontWeight: 700, color: '#000', padding: '2px 7px', borderRadius: 10, height: 'fit-content' },
  metaRow: { display: 'flex', justifyContent: 'space-between', marginBottom: 6 },
  lights: { display: 'flex', gap: 10, flexWrap: 'wrap', paddingBottom: 8, borderBottom: `1px solid ${C.border}`, marginBottom: 8 },
  foot: { display: 'flex', justifyContent: 'space-between', alignItems: 'center' },
  run: { background: C.blue, border: 'none', color: '#fff', fontSize: 11, fontWeight: 700, padding: '4px 10px', borderRadius: 4, cursor: 'pointer' },
  stop: { background: C.amber, border: 'none', color: '#000', fontSize: 11, fontWeight: 700, padding: '4px 10px', borderRadius: 4, cursor: 'pointer' },
  live: { background: 'transparent', border: `1px solid ${C.red}`, color: C.red, fontSize: 11, fontWeight: 700, padding: '4px 10px', borderRadius: 4, cursor: 'pointer' },
};
