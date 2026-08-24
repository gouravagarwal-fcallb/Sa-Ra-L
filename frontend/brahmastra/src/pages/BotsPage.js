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
  const [showHelp, setShowHelp] = useState(false);

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

      {/* Beginner help — how the News Desk works and how to use it, in plain terms */}
      <div style={S.help}>
        <div style={S.helpHead} onClick={() => setShowHelp(v => !v)}>
          <span style={{ fontWeight: 800, color: C.text }}>🟢 New here? How to use the News Desk bot</span>
          <span style={{ color: C.blue, fontWeight: 700 }}>{showHelp ? '▲ hide' : '▼ show steps'}</span>
        </div>
        {showHelp && (
          <div style={S.helpBody}>
            <p style={S.hp}><b>What it does (in one line):</b> you forward a news headline to the bot on
              Telegram, and it replies with the likely market impact — direction, how relevant it is, and
              whether it's strong enough to act on. <b>It never places a trade.</b> It only adds advisory
              context; you and the strategies stay in control.</p>

            <div style={S.hStep}><span style={S.hNum}>1</span><div>
              <b>One-time setup (≈2 min).</b> In Telegram, search <code>@BotFather</code> → send
              <code> /newbot</code> → follow the prompts → it gives you a <b>token</b> (looks like
              <code> 123456:AAE…</code>). Put that token under <code>notifications.news_desk</code> in
              <code> settings.local.yaml</code> (or run <code>scripts/telegram_setup.py &lt;token&gt; --block news_desk</code>),
              then restart the app. The green dot above turns on when it's connected.</div></div>

            <div style={S.hStep}><span style={S.hNum}>2</span><div>
              <b>Start a chat with your bot.</b> Open the bot in Telegram and press <b>Start</b> once (this
              lets it message you back). You only do this the first time.</div></div>

            <div style={S.hStep}><span style={S.hNum}>3</span><div>
              <b>Send it news, any day.</b> Forward a headline, paste a sentence, or type what you heard
              (e.g. <i>"RBI holds repo rate, cuts CRR by 50bps"</i>). Within a few seconds it replies with an
              <b> impact card</b>, and the same card also appears below under
              <i> "Inbound news · impact analyses"</i>.</div></div>

            <div style={S.hStep}><span style={S.hNum}>4</span><div>
              <b>Read the reply.</b> <b>Direction</b> = likely market push (Bullish/Bearish/Neutral).
              <b> Relevance</b> = how much it matters to Nifty/Sensex right now. <b>Trust</b> = how credible
              the source/claim looks. <b>Confidence</b> = overall certainty. <b>Actionable</b> means it's
              strong enough to lean on; <b>context-only</b> means "note it, don't act yet".</div></div>

            <p style={{ ...S.hp, color: C.dim }}>Tip: there are two separate bots. This <b>News Desk</b> is
              <i> inbound</i> (you → bot, for analysis). The <b>Trade Signals</b> bot is <i>outbound</i>
              (platform → your channel, publishing trade calls). They use different tokens and never mix.</p>
          </div>
        )}
      </div>

      {s.warning && <div style={{ ...S.note, background: '#fef2f2', borderColor: '#fecaca', color: '#b91c1c' }}>⚠ {s.warning}</div>}

      {/* Health row */}
      <div style={S.cards}>
        <BotCard title="Sa-Ra-L Trade Signals" sub="outbound publishing gateway"
                 on={sig.enabled} hint={sig.config?.hint}
                 lines={[
                   `bot ${sig.config?.token ?? '—'} → chat ${sig.config?.chat_id ?? '—'}`,
                   `delivered ${del.delivered ?? 0} · pending ${del.pending ?? 0} · failed ${del.failed ?? 0}`,
                   sig.config?.conflict ? `⚠ ${sig.config.conflict}` : `total publications ${del.total ?? 0}`,
                 ]} />
        <BotCard title="Sa-Ra-L News Desk" sub="inbound intelligence intake"
                 on={nd.enabled && nd.config?.polling !== false && !nd.config?.last_error}
                 hint={nd.config?.hint}
                 lines={[
                   `bot ${nd.config?.token ?? '—'} → chat ${nd.config?.chat_id ?? '—'}`,
                   nd.config?.last_error ? `⚠ ${nd.config.last_error}`
                     : (nd.config?.polling === false ? '⚠ not polling (thread down) — restart'
                        : `polling · inbound analysed today: ${nd.inbound_today ?? 0}`),
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

function BotCard({ title, sub, on, lines, hint }) {
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
      {!on && hint && <div style={S.hint}>⚠ {hint}</div>}
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
  help: { background: '#f0fdf4', border: '1px solid #bbf7d0', borderRadius: 10, padding: '10px 14px', marginBottom: 12 },
  helpHead: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', cursor: 'pointer' },
  helpBody: { marginTop: 10, lineHeight: 1.65 },
  hp: { fontSize: 12.5, color: C.text, margin: '0 0 10px' },
  hStep: { display: 'flex', gap: 10, alignItems: 'flex-start', marginBottom: 9, fontSize: 12.5, color: C.text },
  hNum: { flexShrink: 0, width: 20, height: 20, borderRadius: '50%', background: C.green, color: '#fff', fontWeight: 800, fontSize: 12, display: 'flex', alignItems: 'center', justifyContent: 'center' },
  hint: { marginTop: 8, fontSize: 11.5, color: '#b45309', background: '#fff7ed', border: '1px solid #fed7aa', borderRadius: 6, padding: '6px 8px', lineHeight: 1.5 },
};
