import React, { useEffect, useState, useCallback } from 'react';
import { api, C, SH } from '../api';

/**
 * Bots admin (bot spec Part 9): health, outbound delivery records + states,
 * inbound news impacts, and the advisory strategy-context adjustments the News
 * Desk has applied. Read-only operations view.
 */
const STATE_COLOR = {
  DELIVERED: C.green, QUEUED: C.blue, SENDING: C.cyan, RETRY_SCHEDULED: C.amber,
  FAILED: C.red, EXHAUSTED: C.red, PENDING: C.dim, CANCELLED: C.dim,
};

export default function BotsPage() {
  const [status, setStatus] = useState(null);
  const [outbound, setOutbound] = useState([]);
  const [inbound, setInbound] = useState([]);
  const [ctx, setCtx] = useState(null);
  const [err, setErr] = useState(null);

  const load = useCallback(() => {
    Promise.all([
      api.botsStatus().then(setStatus).catch(() => {}),
      api.botsOutbound().then(d => setOutbound(d.records || [])).catch(() => {}),
      api.botsInbound().then(d => setInbound(d.impacts || [])).catch(() => {}),
      api.contextEffective().then(setCtx).catch(() => {}),
    ]).catch(e => setErr(String(e)));
  }, []);
  useEffect(() => { load(); const id = setInterval(load, 8000); return () => clearInterval(id); }, [load]);

  const s = status || {};
  const sig = s.signals || {}; const nd = s.news_desk || {};
  const del = sig.delivery || {};

  return (
    <div>
      <h2 style={S.h2}>Telegram Bots</h2>
      {err && <div style={{ color: C.red, marginBottom: 8 }}>{err}</div>}

      {/* Health row */}
      <div style={S.cards}>
        <BotCard title="Sa-Ra-L Trade Signals" sub="outbound publishing gateway"
                 on={sig.enabled} lines={[
                   `delivered ${del.delivered ?? 0} · pending ${del.pending ?? 0} · failed ${del.failed ?? 0}`,
                   `total publications ${del.total ?? 0}`,
                 ]} />
        <BotCard title="Sa-Ra-L News Desk" sub="inbound intelligence intake"
                 on={nd.enabled} lines={[
                   `inbound analysed today: ${nd.inbound_today ?? 0}`,
                   `never places trades — advisory context only`,
                 ]} />
      </div>
      {!sig.enabled && !nd.enabled && (
        <div style={S.note}>Neither bot is configured. Set <code>notifications.signal_bot</code> and
          <code> notifications.news_desk</code> in <code>settings.local.yaml</code> (or run
          <code> scripts/telegram_setup.py &lt;token&gt; --block signal_bot|news_desk</code>), then restart.</div>
      )}

      {/* Effective strategy context */}
      {ctx?.effective && (
        <div style={S.panel}>
          <div style={S.pTitle}>STRATEGY CONTEXT (from News Desk · advisory, expiring)</div>
          {Object.keys(ctx.effective).filter(k => !['disabled_strategies', 'watch_conditions', 'active_count'].includes(k)).length === 0
            && (ctx.effective.disabled_strategies || []).length === 0
            ? <div style={{ color: C.dim, fontSize: 12.5 }}>No active context adjustments — engine running on its own signals.</div>
            : (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                {Object.entries(ctx.effective).filter(([k]) => !['disabled_strategies', 'watch_conditions', 'active_count'].includes(k)).map(([k, v]) => (
                  <span key={k} style={S.ctxChip} title={v.reason}>{k} = {String(v.value)} <span style={{ color: C.dim }}>· conf {v.confidence}</span></span>
                ))}
                {(ctx.effective.disabled_strategies || []).map(d => <span key={d} style={{ ...S.ctxChip, borderColor: C.red, color: C.red }}>disabled: {d}</span>)}
                {(ctx.effective.watch_conditions || []).map((w, i) => <span key={i} style={S.ctxChip}>watch: {w}</span>)}
              </div>
            )}
        </div>
      )}

      {/* Outbound delivery records */}
      <div style={S.panel}>
        <div style={S.pTitle}>OUTBOUND PUBLICATIONS · delivery state</div>
        {outbound.length === 0 ? <div style={S.empty}>No publications yet today.</div> : (
          <table style={S.table}>
            <thead><tr>{['Type', 'Title', 'State', 'Attempts', 'Error'].map(h => <th key={h} style={S.th}>{h}</th>)}</tr></thead>
            <tbody>
              {outbound.slice().reverse().map(r => (
                <tr key={r.event_id} style={S.tr}>
                  <td style={S.td}>{r.pub_type}</td>
                  <td style={S.td}>{r.title || '—'}</td>
                  <td style={{ ...S.td, color: STATE_COLOR[r.state] || C.dim, fontWeight: 700 }}>{r.state}</td>
                  <td style={S.td}>{r.attempts}/{r.max_attempts}</td>
                  <td style={{ ...S.td, color: C.dim, fontSize: 11 }}>{r.last_error || ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Inbound news impacts */}
      <div style={S.panel}>
        <div style={S.pTitle}>INBOUND NEWS · impact analyses</div>
        {inbound.length === 0 ? <div style={S.empty}>No news submitted yet today. Forward a headline to the News Desk bot.</div> : (
          inbound.map((n, i) => (
            <div key={i} style={S.newsRow}>
              <div style={{ display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap' }}>
                <span style={{ ...S.pill, background: /BULL/.test(n.expected_direction) ? C.green : /BEAR/.test(n.expected_direction) ? C.red : C.amber }}>{n.expected_direction}</span>
                <span style={{ fontWeight: 700, color: C.text }}>{n.classification}</span>
                <span style={{ color: C.dim, fontSize: 11 }}>rel {n.relevance_score} · trust {n.trust_score} · conf {n.confidence} · {n.usable_for_trading ? 'actionable' : 'context-only'}</span>
              </div>
              <div style={{ fontSize: 12.5, color: C.text, marginTop: 3 }}>{n.extracted_text?.slice(0, 200)}</div>
              <div style={{ fontSize: 11.5, color: C.dim, marginTop: 2 }}>{n.analyst_summary}
                {n.risk_flags?.length ? <span style={{ color: C.amber }}>  ⚠ {n.risk_flags.join('; ')}</span> : null}</div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

function BotCard({ title, sub, on, lines }) {
  return (
    <div style={S.card}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <div style={{ fontWeight: 800, color: C.text }}>{title}</div>
          <div style={{ fontSize: 11, color: C.dim }}>{sub}</div>
        </div>
        <span style={{ ...S.dot, background: on ? C.green : C.dim }} title={on ? 'enabled' : 'not configured'} />
      </div>
      {lines.map((l, i) => <div key={i} style={{ fontSize: 12, color: C.dim, marginTop: 6 }}>{l}</div>)}
    </div>
  );
}

const S = {
  h2: { fontSize: 20, marginBottom: 12, color: C.text },
  cards: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 10, marginBottom: 12 },
  card: { background: C.panel, border: `1px solid ${C.border}`, borderRadius: 10, padding: 14, boxShadow: SH.card },
  dot: { width: 11, height: 11, borderRadius: '50%', display: 'inline-block' },
  note: { background: '#fff7ed', border: '1px solid #fed7aa', color: '#b45309', fontSize: 12, borderRadius: 8, padding: '10px 12px', marginBottom: 12, lineHeight: 1.6 },
  panel: { background: C.panel, border: `1px solid ${C.border}`, borderRadius: 10, padding: 14, marginBottom: 12, boxShadow: SH.card },
  pTitle: { fontSize: 13, fontWeight: 700, letterSpacing: 0.6, color: C.cyan, marginBottom: 10 },
  ctxChip: { fontSize: 11.5, fontWeight: 700, color: C.text, background: C.panel2, border: `1px solid ${C.border}`, borderRadius: 9, padding: '3px 9px', cursor: 'help' },
  table: { width: '100%', borderCollapse: 'collapse', fontSize: 12.5 },
  th: { textAlign: 'left', padding: '7px 9px', color: C.dim, borderBottom: `2px solid ${C.border}`, fontWeight: 700 },
  tr: { borderBottom: `1px solid ${C.border}` },
  td: { padding: '7px 9px', color: C.text },
  newsRow: { padding: '8px 0', borderBottom: `1px solid ${C.border}` },
  pill: { fontSize: 9.5, fontWeight: 800, color: '#fff', padding: '1px 7px', borderRadius: 9 },
  empty: { color: C.dim, fontSize: 12.5, padding: '8px 0' },
};
