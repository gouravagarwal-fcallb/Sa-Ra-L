import React, { useEffect, useState, useCallback } from 'react';
import { api, C, SH } from '../api';

/**
 * Pre-Open Preflight — one-glance GO / NO-GO before the session. Confirms the
 * things that must work for a safe day: trading day, session timing (before the
 * open for ORB strategies), market data feed, Kite login, and per-strategy
 * readiness. Read-only.
 */
const VERDICT = {
  GO:              { text: 'GO — cleared for the open', bg: '#f0fdf4', bd: '#bbf7d0', fg: '#15803d', dot: C.green },
  GO_WITH_CAUTION: { text: 'GO — with cautions', bg: '#fffbeb', bd: '#fde68a', fg: '#b45309', dot: C.amber },
  NO_GO:           { text: 'NO-GO — fix before starting', bg: '#fef2f2', bd: '#fecaca', fg: '#b91c1c', dot: C.red },
  UNKNOWN:         { text: 'Preflight unavailable', bg: C.panel2, bd: C.border, fg: C.dim, dot: C.dim },
};
const mark = (ok) => (ok === true ? { c: C.green, t: '✓' } : ok === false ? { c: C.red, t: '✗' } : { c: C.dim, t: '–' });

export default function PreflightPanel() {
  const [d, setD] = useState(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => { setBusy(true); api.preflight().then(setD).catch(() => {}).finally(() => setBusy(false)); }, []);
  useEffect(() => { load(); }, [load]);

  if (!d) return <div style={{ ...S.card, color: C.dim }}>Running preflight…</div>;
  const v = VERDICT[d.verdict] || VERDICT.UNKNOWN;

  return (
    <div style={{ ...S.card, background: v.bg, borderColor: v.bd }}>
      <div style={S.head}>
        <span style={{ ...S.dot, background: v.dot }} />
        <span style={{ fontWeight: 800, color: v.fg, fontSize: 15 }}>Preflight · {v.text}</span>
        <span style={{ marginLeft: 'auto', fontSize: 11, color: C.dim }}>{d.generated_at?.slice(11, 16)}</span>
        <button style={S.refresh} disabled={busy} onClick={load}>{busy ? '…' : '↻ recheck'}</button>
      </div>

      <div style={S.checks}>
        {(d.checks || []).map(c => {
          const m = mark(c.ok);
          return (
            <div key={c.key} style={S.check}>
              <span style={{ color: m.c, fontWeight: 800 }}>{m.t}</span>
              <span style={{ fontWeight: 700 }}>{c.label}</span>
              <span style={{ color: C.dim }}>{c.detail}</span>
            </div>
          );
        })}
      </div>

      {(d.blockers?.length > 0) && (
        <div style={S.list}>
          {d.blockers.map((b, i) => <div key={i} style={{ color: '#b91c1c', fontSize: 12 }}>✗ {b}</div>)}
        </div>
      )}
      {(d.cautions?.length > 0) && (
        <div style={S.list}>
          {d.cautions.map((c, i) => <div key={i} style={{ color: '#b45309', fontSize: 12 }}>⚠ {c}</div>)}
        </div>
      )}

      {(d.strategies?.length > 0) && (
        <div style={S.strats}>
          {d.strategies.map(s => (
            <span key={s.name} style={S.strat} title={`config ${s.config_ok} · backtest ${s.backtest_ok} · backfill ${s.backfill_ok}`}>
              <b>{s.name}</b>
              <span style={{ color: mark(s.config_ok).c }}> cfg</span>
              <span style={{ color: mark(s.backtest_ok).c }}> bt</span>
              <span style={{ color: mark(s.backfill_ok).c }}> bf</span>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

const S = {
  card: { border: `1px solid ${C.border}`, borderRadius: 10, padding: 14, marginBottom: 12, boxShadow: SH.card },
  head: { display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 },
  dot: { width: 11, height: 11, borderRadius: '50%', display: 'inline-block' },
  refresh: { background: '#fff', border: `1px solid ${C.border}`, color: C.dim, fontSize: 11, fontWeight: 700, padding: '3px 9px', borderRadius: 5, cursor: 'pointer' },
  checks: { display: 'flex', flexWrap: 'wrap', gap: '6px 18px' },
  check: { display: 'flex', alignItems: 'center', gap: 6, fontSize: 12.5, color: C.text },
  list: { marginTop: 8, display: 'flex', flexDirection: 'column', gap: 3 },
  strats: { marginTop: 10, display: 'flex', flexWrap: 'wrap', gap: 8 },
  strat: { fontSize: 11, color: C.text, background: '#fff', border: `1px solid ${C.border}`, borderRadius: 8, padding: '3px 9px', cursor: 'help' },
};
