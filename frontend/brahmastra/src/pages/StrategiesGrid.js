import React, { useEffect, useState, useCallback } from 'react';
import { api, C, SH } from '../api';
import LiveGuardModal from '../components/LiveGuardModal';

const READY_COLOR = { READY: C.green, PARTIAL: C.amber, NOT_READY: C.red, PLANNED: C.dim, UNKNOWN: C.dim };
const STATUS_COLOR = { live: C.green, paper: C.blue, paused: C.purple, planned: C.amber, archived: C.dim, testing: C.cyan };

function Light({ ok, label }) {
  const col = ok === true ? C.green : ok === false ? C.red : C.dim;
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 11, color: C.dim }}>
      <span style={{ width: 8, height: 8, borderRadius: '50%', background: col, display: 'inline-block' }} />
      {label}
    </span>
  );
}

/** Live, editable trading-capital control. Persists via /api/strategy/{name}/capital;
 *  does NOT start trading — live still needs the arm+confirm guard. */
function CapitalControl({ s, onSaved }) {
  const [val, setVal]   = useState(String(s.capital_allocated_rs ?? 0));
  const [save, setSave] = useState('idle');   // idle | saving | saved | err
  useEffect(() => { setVal(String(s.capital_allocated_rs ?? 0)); }, [s.capital_allocated_rs]);

  const dirty = String(s.capital_allocated_rs ?? 0) !== val.trim();
  const commit = () => {
    const amt = Number(val);
    if (!Number.isFinite(amt) || amt < 0) { setSave('err'); return; }
    setSave('saving');
    api.setCapital(s.name, amt)
      .then(() => { setSave('saved'); onSaved && onSaved(); setTimeout(() => setSave('idle'), 1500); })
      .catch(() => setSave('err'));
  };
  return (
    <div style={S.capWrap} onClick={(e) => e.stopPropagation()}>
      <span style={S.capLabel}>Trading capital</span>
      <div style={S.capRow}>
        <span style={S.rupee}>₹</span>
        <input
          style={{ ...S.capInput, borderColor: dirty ? C.blue : C.border }}
          value={val} inputMode="numeric"
          onChange={(e) => setVal(e.target.value.replace(/[^0-9.]/g, ''))}
          onKeyDown={(e) => e.key === 'Enter' && dirty && commit()}
        />
        <button style={{ ...S.capSave, opacity: dirty && save !== 'saving' ? 1 : 0.5 }}
                disabled={!dirty || save === 'saving'} onClick={commit}>
          {save === 'saving' ? '…' : save === 'saved' ? '✓' : 'Set'}
        </button>
      </div>
      <div style={S.capHint}>
        {save === 'err' ? <span style={{ color: C.red }}>invalid amount</span>
          : s.capital_overridden ? <span style={{ color: C.blue }}>adjusted · target ₹{(s.capital_target_rs || 0).toLocaleString('en-IN')}</span>
          : <span>target ₹{(s.capital_target_rs || 0).toLocaleString('en-IN')}</span>}
      </div>
    </div>
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
              <CapitalControl s={s} onSaved={load} />
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
  grid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(310px, 1fr))', gap: 12 },
  card: { background: C.panel, border: `1px solid ${C.border}`, borderRadius: 10, padding: 14, boxShadow: SH.card },
  cardHead: { display: 'flex', justifyContent: 'space-between', cursor: 'pointer', marginBottom: 10 },
  name: { fontWeight: 700, fontSize: 15, color: C.text },
  full: { fontSize: 11, color: C.dim, marginTop: 3, maxWidth: 210 },
  statusPill: { fontSize: 10, fontWeight: 700, color: '#fff', padding: '2px 9px', borderRadius: 10, height: 'fit-content', textTransform: 'uppercase', letterSpacing: 0.3 },
  metaRow: { display: 'flex', justifyContent: 'space-between', marginBottom: 8 },
  lights: { display: 'flex', gap: 12, flexWrap: 'wrap', paddingBottom: 10, borderBottom: `1px solid ${C.border}`, marginBottom: 10 },
  capWrap: { paddingBottom: 10, borderBottom: `1px solid ${C.border}`, marginBottom: 10 },
  capLabel: { fontSize: 10, fontWeight: 700, color: C.dim, textTransform: 'uppercase', letterSpacing: 0.4 },
  capRow: { display: 'flex', alignItems: 'center', gap: 5, marginTop: 4 },
  rupee: { fontSize: 14, color: C.dim, fontWeight: 700 },
  capInput: { flex: 1, fontSize: 14, fontWeight: 700, color: C.text, padding: '5px 8px', border: `1px solid ${C.border}`, borderRadius: 5, outline: 'none', width: '100%', background: '#fff' },
  capSave: { background: C.blue, border: 'none', color: '#fff', fontSize: 12, fontWeight: 700, padding: '5px 11px', borderRadius: 5, cursor: 'pointer' },
  capHint: { fontSize: 10, color: C.dim, marginTop: 3 },
  foot: { display: 'flex', justifyContent: 'space-between', alignItems: 'center' },
  run: { background: C.blue, border: 'none', color: '#fff', fontSize: 12, fontWeight: 700, padding: '5px 12px', borderRadius: 5, cursor: 'pointer' },
  stop: { background: C.amber, border: 'none', color: '#fff', fontSize: 12, fontWeight: 700, padding: '5px 12px', borderRadius: 5, cursor: 'pointer' },
  live: { background: '#fff', border: `1px solid ${C.red}`, color: C.red, fontSize: 12, fontWeight: 700, padding: '5px 12px', borderRadius: 5, cursor: 'pointer' },
};
