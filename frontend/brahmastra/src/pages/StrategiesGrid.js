import React, { useEffect, useState, useCallback } from 'react';
import { api, C, SH } from '../api';
import LiveGuardModal from '../components/LiveGuardModal';

const READY_COLOR = { READY: C.green, PARTIAL: C.amber, NOT_READY: C.red, PLANNED: C.dim, UNKNOWN: C.dim };
const STATUS_COLOR = { live: C.green, paper: C.blue, paused: C.purple, planned: C.amber, archived: C.dim, testing: C.cyan };

// What each status badge means (UI_FE_Pg2 item 2). Shown as a legend; the
// distinctions are deliberate — they gate real money, so they stay separate.
const STATUS_LEGEND = [
  ['live',     C.green,  'Cleared for REAL-money orders (still needs the arm + typed-confirm guard each time).'],
  ['paper',    C.blue,   'Runs live data but simulates fills — no real orders. The default safe mode.'],
  ['testing',  C.cyan,   'Under backtest / walk-forward validation — not yet trusted with capital.'],
  ['paused',   C.purple, 'Built and working but intentionally not running right now.'],
  ['archived', C.dim,    'Superseded by a newer strategy — kept for history, not run.'],
  ['planned',  C.amber,  'Designed but engine not built yet — concept only.'],
];

const TIER_META = {
  BEST:        { color: '#0f8a3c', label: 'BEST FIT' },
  SUITED:      { color: '#2563eb', label: 'SUITED' },
  ARMED:       { color: '#d97706', label: 'ARMED' },
  NEUTRAL:     { color: '#5b6b82', label: 'NEUTRAL' },
  LESS_SUITED: { color: '#b45309', label: 'LESS SUITED' },
  OFF:         { color: '#94a3b8', label: 'NOT TODAY' },
};
const TIER_RANK = { BEST: 0, SUITED: 1, ARMED: 2, NEUTRAL: 3, LESS_SUITED: 4, OFF: 5 };

// Precise runtime status taxonomy → colour (never a bare "Blind").
const CLASS_COLOR = (c = '') => ({
  ACTIVE_NO_TRADE: C.green, WARMING_UP: C.amber, DATA_UNAVAILABLE: C.amber,
  TELEMETRY_BROKEN: C.red, RUNTIME_FAILURE: C.red, MARKET_CLOSED: C.dim,
  PAUSED_BY_OPERATOR: C.purple, STOPPED_BY_OPERATOR: C.purple,
  PAPER_ONLY_BY_OPERATOR: C.blue, ARCHIVED: C.dim, INACTIVE_NOT_STARTED: C.dim,
}[c] || C.dim);

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
  const [tierMap, setTierMap] = useState({});   // name -> { tier, reason } from pre-market fit
  const [showLegend, setShowLegend] = useState(false);

  const load = useCallback(() => {
    api.strategies().then(setRows).catch(e => setErr(String(e)));
  }, []);
  useEffect(() => { load(); const id = setInterval(load, 5000); return () => clearInterval(id); }, [load]);

  // Pre-market fit tiers drive card ordering (UI_FE_Pg2 item 3). Refresh every
  // 10 min so the order re-adjusts as the scenario shifts intraday.
  const loadTiers = useCallback(() => {
    api.premarket(false).then(d => {
      const items = d?.conclusion?.strategy_fit?.items || [];
      setTierMap(Object.fromEntries(items.map(it => [it.name, { tier: it.tier, reason: it.reason }])));
    }).catch(() => {});
  }, []);
  useEffect(() => { loadTiers(); const id = setInterval(loadTiers, 600000); return () => clearInterval(id); }, [loadTiers]);

  const sortedRows = React.useMemo(() => {
    const haveTiers = Object.keys(tierMap).length > 0;
    return [...rows].sort((a, b) => {
      const aT = TIER_RANK[tierMap[a.name]?.tier] ?? 9;
      const bT = TIER_RANK[tierMap[b.name]?.tier] ?? 9;
      return aT !== bT ? aT - bT : a.name.localeCompare(b.name);
    }).map(s => ({ ...s, _fit: haveTiers ? tierMap[s.name] : null }));
  }, [rows, tierMap]);

  const pnl = (rt) => {
    const v = (rt?.real_pnl || 0) + (rt?.paper_pnl || 0);
    return <span style={{ color: v > 0 ? C.green : v < 0 ? C.red : C.dim }}>{v ? `Rs.${v.toLocaleString('en-IN')}` : '—'}</span>;
  };

  return (
    <div>
      {err && <div style={{ background: '#fef2f2', color: '#b91c1c', border: '1px solid #fecaca', borderRadius: 6, padding: '8px 12px', marginBottom: 10, fontWeight: 700, fontSize: 13, cursor: 'pointer' }} onClick={() => setErr(null)}>{err} <span style={{ float: 'right' }}>✕</span></div>}

      <div style={S.toolbar}>
        <span style={{ fontSize: 12, color: C.dim }}>
          {Object.keys(tierMap).length > 0
            ? <>Ordered by today's pre-market fit · <b style={{ color: C.text }}>best-fit first</b></>
            : 'Ordered by name (pre-market fit unavailable yet)'}
        </span>
        <button style={S.legendBtn} onClick={() => setShowLegend(v => !v)}>
          {showLegend ? 'Hide status guide' : 'What do the statuses mean?'}
        </button>
      </div>
      {showLegend && (
        <div style={S.legend}>
          {STATUS_LEGEND.map(([k, col, desc]) => (
            <div key={k} style={S.legendRow}>
              <span style={{ ...S.statusPill, background: col, flexShrink: 0 }}>{k}</span>
              <span style={{ fontSize: 12, color: C.text }}>{desc}</span>
            </div>
          ))}
          <div style={S.legendNote}>
            ℹ Statuses are kept distinct on purpose — <b>live</b> means real money. A strategy is never
            auto-promoted to live; you arm it with the typed-confirm guard each time.
          </div>
        </div>
      )}

      <div style={S.grid}>
        {sortedRows.map(s => {
          const r = s.readiness || {};
          const fit = s._fit ? TIER_META[s._fit.tier] : null;
          return (
            <div key={s.name} style={S.card}>
              <div style={S.cardHead} onClick={() => onOpen(s.name)}>
                <div>
                  <div style={S.name}>{s.name}</div>
                  <div style={S.full}>{s.full_name}</div>
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 4 }}>
                  <span style={{ ...S.statusPill, background: STATUS_COLOR[s.status] || C.dim }}>{s.status}</span>
                  {fit && <span style={{ ...S.tierPill, color: fit.color, borderColor: fit.color }} title={s._fit.reason}>{fit.label}</span>}
                </div>
              </div>
              <div style={S.metaRow}>
                <span style={{ color: READY_COLOR[r.overall] || C.dim, fontWeight: 700, fontSize: 11 }}>● {r.overall || '—'}</span>
                <span style={{ color: C.dim, fontSize: 11 }}>{(s.instruments || []).join(', ')}</span>
              </div>
              {s.status_label && (
                <div style={S.runtimeRow} title={s.status_label}>
                  <span style={{ ...S.classDot, background: CLASS_COLOR(s.status_class) }} />
                  <span style={{ color: CLASS_COLOR(s.status_class), fontWeight: 700 }}>{s.status_label}</span>
                  {s.status === 'live' && (s.live_eligible
                    ? <span style={S.eligible}>LIVE‑ELIGIBLE · arm to trade</span>
                    : s.live_blocked
                      ? <span style={S.blocked}>LIVE BLOCKED</span>
                      : <span style={S.blocked}>live gated → paper</span>)}
                </div>
              )}
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
                    ? <button style={S.stop} onClick={() => api.stop(s.name).then(load).catch(e => setErr(`STOP ${s.name} FAILED: ${e} — retry or Ctrl-C the server`))}>Stop</button>
                    : <button style={S.run} onClick={() => api.run(s.name, 'paper').then(load).catch(e => setErr(`Start ${s.name} failed: ${e}`))}>Paper</button>}
                  <button style={{ ...S.live, opacity: s.live_eligible ? 1 : 0.4, cursor: s.live_eligible ? 'pointer' : 'not-allowed' }}
                          disabled={!s.live_eligible}
                          title={s.live_eligible ? 'Arm real-money trading'
                            : s.live_blocked ? 'Live blocked (bug/archived)'
                            : `Not live-eligible (status: ${s.status})`}
                          onClick={() => s.live_eligible && setArmFor(s.name)}>Live</button>
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
  toolbar: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10, flexWrap: 'wrap', gap: 8 },
  legendBtn: { background: C.panel, border: `1px solid ${C.border}`, color: C.blue, fontSize: 12, fontWeight: 700, padding: '5px 11px', borderRadius: 6, cursor: 'pointer' },
  legend: { background: C.panel, border: `1px solid ${C.border}`, borderRadius: 10, padding: 14, marginBottom: 12, boxShadow: SH.card },
  legendRow: { display: 'flex', alignItems: 'center', gap: 10, padding: '4px 0' },
  legendNote: { fontSize: 11.5, color: C.dim, marginTop: 8, paddingTop: 8, borderTop: `1px solid ${C.border}`, lineHeight: 1.5 },
  tierPill: { fontSize: 9, fontWeight: 800, letterSpacing: 0.3, padding: '1px 7px', borderRadius: 9, border: '1px solid', textTransform: 'uppercase' },
  grid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(310px, 1fr))', gap: 12 },
  card: { background: C.panel, border: `1px solid ${C.border}`, borderRadius: 10, padding: 14, boxShadow: SH.card },
  cardHead: { display: 'flex', justifyContent: 'space-between', cursor: 'pointer', marginBottom: 10 },
  name: { fontWeight: 700, fontSize: 15, color: C.text },
  full: { fontSize: 11, color: C.dim, marginTop: 3, maxWidth: 210 },
  statusPill: { fontSize: 10, fontWeight: 700, color: '#fff', padding: '2px 9px', borderRadius: 10, height: 'fit-content', textTransform: 'uppercase', letterSpacing: 0.3 },
  metaRow: { display: 'flex', justifyContent: 'space-between', marginBottom: 8 },
  runtimeRow: { display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', fontSize: 11, marginBottom: 8 },
  classDot: { width: 7, height: 7, borderRadius: '50%', display: 'inline-block', flexShrink: 0 },
  eligible: { fontSize: 9, fontWeight: 800, color: '#fff', background: C.green, padding: '1px 6px', borderRadius: 8, letterSpacing: 0.3 },
  blocked: { fontSize: 9, fontWeight: 800, color: '#fff', background: C.amber, padding: '1px 6px', borderRadius: 8, letterSpacing: 0.3 },
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
