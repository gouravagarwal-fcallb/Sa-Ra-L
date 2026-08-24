import React, { useEffect, useState, useCallback } from 'react';
import { api, C, SH } from '../api';
import PreflightPanel from '../components/PreflightPanel';

/**
 * Pre-Market Analysis (global basis) — the day's most important read.
 * Global markets (SGX Nifty, S&P, Dow, Nikkei, Hang Seng, Crude, Gold, USD/INR)
 * + India internals (VIX, PCR, Max Pain, FII) rolled into one tradeable bias,
 * with the full score breakdown, risk events and news.
 */
const dirColor = (d) => ({ UP: C.green, DOWN: C.red, FLAT: C.dim }[d] || C.dim);
const arrow = (d) => ({ UP: '▲', DOWN: '▼', FLAT: '■' }[d] || '·');

function biasColor(label = '') {
  const u = label.toUpperCase();
  if (u.includes('STRONGLY_BULL')) return '#0f8a3c';
  if (u.includes('BULL')) return C.green;
  if (u.includes('STRONGLY_BEAR')) return '#b91c1c';
  if (u.includes('BEAR')) return C.red;
  return C.dim;
}

// Hover explanations: what each internal means and how to read it. Shown on
// mouse-over (point 6/7 of the UI review) — plain "what + how to analyse".
const INTERNAL_TIPS = {
  'India VIX': "India VIX — the market's expected 30-day volatility (the 'fear gauge'). Rising VIX = more fear / bigger expected swings; falling VIX = calm. Read: <13 complacent, 13–18 normal, 18–25 elevated, >25 high stress (favour hedges, smaller size).",
  'PCR (Put/Call)': 'Put/Call Ratio = total PE open interest ÷ total CE OI. Read: >1.3 heavy put-writing → bullish support building; <0.7 heavy call-writing → bearish/resistance; ~0.9–1.1 neutral. Extreme readings often mark reversals.',
  'Max Pain': 'Max Pain — the strike where option buyers lose the most (writers pay the least). Price tends to gravitate toward it into expiry. Use as a magnet/target level, not a forecast; compare to spot for pull direction.',
  'FII net (₹ cr)': 'FII net — net ₹ crore Foreign Institutional Investors bought (+) or sold (−) in the cash segment. Sustained selling pressures the index, buying supports it. (Shows the previous session figure — FII data is end-of-day.)',
};
// Score-factor explanations for the breakdown panel (point 4/7).
const SCORE_TIPS = {
  'dow': 'Dow Jones overnight close — US lead. Up → +points (risk-on), down → −points.',
  'sp500': 'S&P 500 overnight move — broad US risk appetite feeding into Indian open.',
  'nasdaq': 'Nasdaq overnight move — tech/growth risk appetite.',
  'gift nifty': 'GIFT Nifty (ex-SGX) — the most direct overnight pointer to NIFTY open. Premium/discount to prev close drives the points.',
  'sgx nifty': 'GIFT/SGX Nifty — direct overnight pointer to NIFTY open.',
  'nikkei': 'Nikkei (Japan) — Asian session tone.',
  'hang seng': 'Hang Seng (HK) — China/Asia risk tone.',
  'vix': 'India VIX level/trend — high or rising VIX subtracts (fear), low/falling adds (calm).',
  'sensex': 'Sensex prior-session trend feeding momentum.',
  'crude': 'Crude oil — higher crude is a headwind for India (import bill), so it usually subtracts.',
  'usdinr': 'USD/INR — a weaker rupee (USDINR up) is risk-off for equities, so it usually subtracts.',
  'gold': 'Gold — safe-haven bid up can signal risk-off.',
  'pcr': 'Put/Call ratio contribution — bullish put-writing adds, bearish call-writing subtracts.',
};
const scoreTip = (k) => SCORE_TIPS[k.toLowerCase().replace(/_/g, ' ')] ||
  'Contribution of this factor to the total bias score. Positive = bullish pull, negative = bearish pull; magnitude = how strong.';

const TIER_META = {
  BEST:        { color: '#0f8a3c', label: 'BEST FIT' },
  SUITED:      { color: '#2563eb', label: 'SUITED' },
  ARMED:       { color: '#d97706', label: 'ARMED / WATCHING' },
  NEUTRAL:     { color: '#5b6b82', label: 'NEUTRAL' },
  LESS_SUITED: { color: '#b45309', label: 'LESS SUITED' },
  OFF:         { color: '#94a3b8', label: 'NOT TODAY' },
};

export default function PreMarketPage({ onOpen }) {
  const [data, setData] = useState(null);
  const [err, setErr]   = useState(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback((force) => {
    setLoading(true);
    api.premarket(force)
      .then(d => { setData(d); setErr(null); })
      .catch(e => setErr(String(e)))
      .finally(() => setLoading(false));
  }, []);
  useEffect(() => { load(false); }, [load]);

  // Live option-chain internals — used to fill PCR/Max Pain in the frozen panel when
  // the morning snapshot couldn't reach the chain (so it's not "—" in one place only).
  const [liveInt, setLiveInt] = useState(null);
  useEffect(() => {
    let alive = true;
    const f = () => api.marketInternals().then(d => { if (alive) setLiveInt(d); }).catch(() => {});
    f(); const id = setInterval(f, 30000);
    return () => { alive = false; clearInterval(id); };
  }, []);

  const b = data?.briefing || {};
  const de = data?.direction_engine || {};
  const concl = data?.conclusion || {};
  const score = b.bias_score;
  const pct = score == null ? 50 : Math.max(0, Math.min(100, (score + 100) / 2));  // -100..100 → 0..100
  const cDir = (concl.direction === 'BULLISH') ? C.green : (concl.direction === 'BEARISH') ? C.red : C.dim;

  return (
    <div>
      <div style={S.headRow}>
        <h2 style={S.h2}>Pre-Market Analysis <span style={{ color: C.dim, fontWeight: 400, fontSize: 14 }}>· global basis · {data?.date || ''}</span></h2>
        <button style={S.refresh} disabled={loading} onClick={() => load(true)}>
          {loading ? 'Fetching…' : '↻ Re-snapshot'}
        </button>
      </div>

      {/* Pre-open GO / NO-GO one-glance */}
      <PreflightPanel />

      <div style={S.frozenNote}>
        ❄ The sections below are the <b>frozen overnight→open read</b>
        {data?.generated_at ? <> · snapshot {fmtStamp(data.generated_at)}</> : null}.
        It does not move intraday — “↻ Re-snapshot” recomputes it. The live market is shown
        separately in the <span style={{ color: C.cyan, fontWeight: 700 }}>cyan Live Market panel</span> below.
      </div>
      {err && <div style={{ color: C.red, marginBottom: 10 }}>{err}</div>}

      {/* Conclusion & score — the actionable verdict for today's initial trades */}
      {concl.available ? (
        <div style={{ ...S.conclusion, borderLeft: `6px solid ${cDir}` }}>
          <div style={S.conclTop}>
            <span style={S.conclEyebrow}>PRE-MARKET CONCLUSION</span>
            <span style={{ ...S.convPill, background: cDir }}>{concl.conviction} CONVICTION</span>
          </div>
          <div style={{ fontSize: 22, fontWeight: 800, color: cDir, marginTop: 4 }}>
            {concl.direction} <span style={{ color: C.dim, fontWeight: 600, fontSize: 16 }}>· score {concl.score > 0 ? '+' : ''}{concl.score}/100</span>
          </div>
          <div style={{ fontSize: 15, fontWeight: 700, color: C.text, marginTop: 8 }}>➜ {concl.action}</div>
          <div style={{ fontSize: 13, color: C.dim, marginTop: 4 }}>{concl.posture}</div>
          {concl.rationale?.length > 0 && (
            <ul style={S.bullets}>{concl.rationale.map((r, i) => <li key={i}>{r}</li>)}</ul>
          )}
          {concl.cautions?.length > 0 && (
            <div style={S.cautions}>
              {concl.cautions.map((c, i) => <div key={i} style={S.cautionRow}>⚠ {c}</div>)}
            </div>
          )}

          {/* Which strategies fit this scenario (advisory; all keep running) */}
          {concl.strategy_fit?.items?.length > 0 && (
            <div style={S.fitWrap}>
              <div style={S.fitTitle}>STRATEGIES THAT FIT THIS SCENARIO</div>
              <div style={S.fitNote}>ℹ {concl.strategy_fit.note}</div>
              <div style={S.fitGrid}>
                {concl.strategy_fit.items.map(it => {
                  const m = TIER_META[it.tier] || TIER_META.NEUTRAL;
                  return (
                    <div key={it.name} style={{ ...S.fitRow, borderLeft: `4px solid ${m.color}` }}>
                      <div style={S.fitRowTop}>
                        <span style={S.fitName} onClick={() => onOpen && onOpen(it.name)} title="Open strategy">{it.name}</span>
                        <span style={{ ...S.fitTier, color: m.color, borderColor: m.color }}>{m.label}</span>
                      </div>
                      <div style={S.fitReason}>{it.reason}</div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      ) : (
        <div style={{ ...S.conclusion, borderLeft: `6px solid ${C.dim}` }}>
          <span style={S.conclEyebrow}>PRE-MARKET CONCLUSION</span>
          <div style={{ color: C.dim, marginTop: 6 }}>{concl.reason || 'Awaiting data — hit Refresh during pre-market hours.'}</div>
        </div>
      )}

      {/* LIVE market — the separate, intraday-updating companion (distinct cyan) */}
      <LiveMarketCard />

      {/* Headline bias */}
      <div style={S.card}>
        <div style={S.cardTitle}>TODAY'S BIAS</div>
        {b.available ? (
          <>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 14 }}>
              <span style={{ fontSize: 26, fontWeight: 800, color: biasColor(b.bias_label) }}>{(b.bias_label || '—').replace(/_/g, ' ')}</span>
              <span style={{ fontSize: 16, color: C.dim }}>score <b style={{ color: biasColor(b.bias_label) }}>{score > 0 ? '+' : ''}{score}</b> / 100</span>
            </div>
            <div style={S.gaugeTrack}>
              <div style={{ ...S.gaugeFill, left: `${Math.min(pct, 50)}%`, width: `${Math.abs(pct - 50)}%`, background: biasColor(b.bias_label) }} />
              <div style={S.gaugeMid} />
            </div>
            <div style={S.gaugeLabels}><span>Bearish −100</span><span>Neutral 0</span><span>Bullish +100</span></div>
          </>
        ) : <div style={{ color: C.dim }}>{b.reason || 'unavailable (needs market data)'}</div>}
        {de.available && (
          <div style={{ marginTop: 10, fontSize: 12, color: C.dim }}>
            Direction engine (Dow/Gift/VIX/Sensex): <b style={{ color: biasColor(de.direction) }}>{de.direction}</b> · score {de.score} — {de.reason}
          </div>
        )}
      </div>

      {/* Global markets */}
      <div style={S.card}>
        <div style={S.cardTitle}>GLOBAL MARKETS</div>
        {b.available && b.global_markets?.length ? (
          <table style={S.table}>
            <thead><tr>{['Market', 'Symbol', 'Price', 'Change %', 'Signal'].map(h => <th key={h} style={S.th}>{h}</th>)}</tr></thead>
            <tbody>
              {b.global_markets.map(m => (
                <tr key={m.symbol} style={S.tr}>
                  <td style={S.tdName}>{m.name}</td>
                  <td style={S.tdDim}>{m.symbol}</td>
                  <td style={S.td}>{m.price != null ? Number(m.price).toLocaleString('en-IN', { maximumFractionDigits: 2 }) : '—'}</td>
                  <td style={{ ...S.td, color: dirColor(m.direction), fontWeight: 700 }}>
                    {m.change_pct != null ? `${m.change_pct > 0 ? '+' : ''}${m.change_pct}%` : '—'}
                  </td>
                  <td style={{ ...S.td, color: dirColor(m.direction), fontWeight: 700 }}>{arrow(m.direction)} {m.direction}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <div style={{ color: C.dim }}>{b.reason || 'no global data'}</div>}
        {b.available && b.global_markets?.length ? <GlobalNet markets={b.global_markets} /> : null}
      </div>

      <div style={S.twoCol}>
        {/* India internals */}
        <div style={{ ...S.card, flex: 1 }}>
          <div style={S.cardTitle}>INDIA INTERNALS <span style={S.hint}>· hover any label for meaning</span></div>
          <Metric label="India VIX" tip={INTERNAL_TIPS['India VIX']} value={b.india_vix != null ? Number(b.india_vix).toFixed(2) : '—'} note={b.vix_trend} />
          <Metric label="PCR (Put/Call)" tip={INTERNAL_TIPS['PCR (Put/Call)']}
                  value={b.pcr != null ? b.pcr : (liveInt?.pcr != null ? liveInt.pcr : '—')}
                  note={b.pcr != null ? b.pcr_label : (liveInt?.pcr != null ? 'live' : null)}
                  noteColor={b.pcr != null ? biasColor(b.pcr_label) : C.cyan} />
          <Metric label="Max Pain" tip={INTERNAL_TIPS['Max Pain']}
                  value={b.max_pain != null ? b.max_pain : (liveInt?.max_pain != null ? liveInt.max_pain : '—')}
                  note={b.max_pain == null && liveInt?.max_pain != null ? 'live' : null} noteColor={C.cyan} />
          <Metric label="FII net (₹ cr)" tip={INTERNAL_TIPS['FII net (₹ cr)']}
                  value={b.fii_net_cr != null ? `${b.fii_net_cr >= 0 ? '+' : ''}₹${Math.round(b.fii_net_cr).toLocaleString('en-IN')} Cr` : '—'}
                  valueColor={b.fii_net_cr == null ? C.text : b.fii_net_cr >= 0 ? C.green : C.red} />
          {(b.pcr == null || b.max_pain == null) && (
            <div style={S.internalNote}>
              ℹ PCR / Max Pain read live from the NSE option chain (Kite fallback). A "—" means the
              chain wasn't reachable at fetch time — it populates after ~09:20 once OI builds, and
              needs Kite connected. Hit ↻ Refresh after the open. FII net is previous-session EOD data.
            </div>
          )}
        </div>

        {/* Score breakdown */}
        <div style={{ ...S.card, flex: 1 }}>
          <div style={S.cardTitle}>SCORE BREAKDOWN (each factor's points) <span style={S.hint}>· hover for meaning · click a factor for the math</span></div>
          <ScoreBreakdown breakdown={b.score_breakdown} derivation={b.score_derivation || {}} />
        </div>
      </div>

      {/* Risk events + news */}
      <div style={S.twoCol}>
        <div style={{ ...S.card, flex: 1 }}>
          <div style={S.cardTitle}>HIGH-RISK EVENTS TODAY</div>
          {b.high_risk_events?.length
            ? b.high_risk_events.map((e, i) => <div key={i} style={S.eventRow}>⚠ {e}</div>)
            : <div style={{ color: C.dim }}>None flagged.</div>}
        </div>
        <MarketNews headlines={b.news} />
      </div>
    </div>
  );
}

function fmtStamp(iso) {
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' });
  } catch { return ''; }
}

/** LIVE market — the intraday-updating companion to the frozen pre-market read
 *  (UI review point 3). Distinct cyan styling so it's never confused with the
 *  frozen snapshot above. Polls quotes every 5s and option-chain internals every 30s. */
function LiveMarketCard() {
  const [m, setM] = useState(null);
  const [intern, setIntern] = useState(null);
  const [updated, setUpdated] = useState('');
  useEffect(() => {
    let alive = true;
    const tick = () => api.marketSummary().then(d => { if (alive) { setM(d); setUpdated(new Date().toLocaleTimeString('en-IN')); } }).catch(() => {});
    const tickI = () => api.marketInternals().then(d => { if (alive) setIntern(d); }).catch(() => {});
    tick(); tickI();
    const a = setInterval(tick, 5000); const c = setInterval(tickI, 30000);
    return () => { alive = false; clearInterval(a); clearInterval(c); };
  }, []);

  const Q = ({ label, q }) => {
    if (!q || q.ltp == null) return <div style={S.liveQ}><span style={S.liveQLabel}>{label}</span><span style={{ color: C.dim }}>—</span></div>;
    const up = (q.change || 0) >= 0;
    const col = (q.change || 0) === 0 ? C.dim : up ? C.green : C.red;
    return (
      <div style={S.liveQ}>
        <span style={S.liveQLabel}>{label}</span>
        <span style={{ fontSize: 18, fontWeight: 800, color: C.text }}>{q.ltp.toLocaleString('en-IN', { maximumFractionDigits: 2 })}</span>
        <span style={{ color: col, fontWeight: 700, fontSize: 12 }}>
          {up ? '▲' : '▼'} {Math.abs(q.change || 0).toLocaleString('en-IN', { maximumFractionDigits: 2 })}
          {q.change_pct != null ? ` (${up ? '+' : ''}${q.change_pct}%)` : ''}</span>
      </div>
    );
  };
  const vixUp = (m?.vix_change || 0) >= 0;

  return (
    <div style={S.liveCard}>
      <div style={S.liveHead}>
        <span style={S.liveDot} />
        <span style={S.liveTitle}>LIVE MARKET — now</span>
        <span style={{ marginLeft: 'auto', fontSize: 10.5, color: C.cyan }}>
          {m?.source === 'kite' ? 'live · Kite' : m?.source === 'yfinance' ? 'delayed · Yahoo' : 'no feed'}
          {updated ? ` · ${updated}` : ''}</span>
      </div>
      <div style={S.liveGrid}>
        <Q label="NIFTY" q={m?.nifty} />
        <Q label="SENSEX" q={m?.sensex} />
        <div style={S.liveQ}>
          <span style={S.liveQLabel}>INDIA VIX</span>
          <span style={{ fontSize: 18, fontWeight: 800, color: m?.vix == null ? C.dim : (vixUp ? C.green : C.red) }}>
            {m?.vix != null ? Number(m.vix).toLocaleString('en-IN', { maximumFractionDigits: 2 }) : '—'}</span>
          {m?.vix_change != null && <span style={{ fontSize: 12, fontWeight: 700, color: vixUp ? C.green : C.red }}>{vixUp ? '▲' : '▼'} {Math.abs(m.vix_change)}</span>}
        </div>
        <div style={S.liveQ}>
          <span style={S.liveQLabel}>PCR (live)</span>
          <span style={{ fontSize: 18, fontWeight: 800, color: C.text }}>{intern?.pcr != null ? intern.pcr : '—'}</span>
          <span style={{ fontSize: 11, color: C.dim }}>{intern?.source === 'kite' ? 'Kite OI' : 'awaiting chain'}</span>
        </div>
        <div style={S.liveQ}>
          <span style={S.liveQLabel}>MAX PAIN (live)</span>
          <span style={{ fontSize: 18, fontWeight: 800, color: C.text }}>{intern?.max_pain != null ? intern.max_pain : '—'}</span>
        </div>
      </div>
      <div style={{ fontSize: 10.5, color: C.dim, marginTop: 8 }}>
        Updates live during market hours (quotes 5s, option-chain 30s) — compare against the frozen pre-market read above.
      </div>
    </div>
  );
}

/** Score Breakdown with click-to-expand derivation (UI review points 4 & 7). */
function ScoreBreakdown({ breakdown, derivation }) {
  const [open, setOpen] = useState(null);
  if (!breakdown || !Object.keys(breakdown).length) return <div style={{ color: C.dim }}>—</div>;
  return (
    <table style={S.table}>
      <tbody>
        {Object.entries(breakdown).map(([k, v]) => {
          const d = derivation[k];
          const isOpen = open === k;
          return (
            <React.Fragment key={k}>
              <tr style={{ ...S.tr, cursor: 'pointer', background: isOpen ? C.panel2 : 'transparent' }}
                  onClick={() => setOpen(isOpen ? null : k)}>
                <td style={{ ...S.tdName, ...S.tipCell }} title={scoreTip(k)}>
                  <span style={{ color: C.dim, marginRight: 6 }}>{isOpen ? '▾' : '▸'}</span>{k.replace(/_/g, ' ')}
                </td>
                <td style={{ ...S.td, textAlign: 'right', fontWeight: 700, color: v > 0 ? C.green : v < 0 ? C.red : C.dim }}>
                  {v > 0 ? '+' : ''}{v}
                </td>
              </tr>
              {isOpen && (
                <tr>
                  <td colSpan={2} style={S.derivCell}>
                    <div><b>Input:</b> {d?.input || 'n/a'}</div>
                    <div style={{ marginTop: 3 }}><b>Rule:</b> {d?.rule || scoreTip(k)}</div>
                    <div style={{ marginTop: 3, color: v > 0 ? C.green : v < 0 ? C.red : C.dim, fontWeight: 700 }}>
                      → contributed {v > 0 ? '+' : ''}{v} to the bias score.</div>
                  </td>
                </tr>
              )}
            </React.Fragment>
          );
        })}
      </tbody>
    </table>
  );
}

/** Market News — news-desk (Bot 1) impact reads merged with headline feed (point 9). */
function MarketNews({ headlines }) {
  const [desk, setDesk] = useState({ items: [], enabled: false });
  useEffect(() => {
    let alive = true;
    const load = () => api.news(15).then(d => { if (alive) setDesk(d); }).catch(() => {});
    load(); const id = setInterval(load, 20000);
    return () => { alive = false; clearInterval(id); };
  }, []);
  const sentCol = (s) => (s || '').includes('BULL') ? C.green : (s || '').includes('BEAR') ? C.red : C.amber;
  const hasAny = (desk.items?.length || 0) > 0 || (headlines?.length || 0) > 0;
  return (
    <div style={{ ...S.card, flex: 1 }}>
      <div style={S.cardTitle}>MARKET NEWS <span style={S.hint}>· News-Desk impact reads + headlines</span></div>
      {desk.items?.length > 0 && desk.items.map((n, i) => (
        <div key={`d${i}`} style={S.deskRow}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'baseline' }}>
            <span style={{ ...S.deskPill, background: sentCol(n.sentiment) }}>{n.sentiment}</span>
            <span style={{ fontSize: 10.5, color: C.dim }}>{n.ts}</span>
          </div>
          <div style={{ fontSize: 12.5, color: C.text, marginTop: 3 }}>{n.text}</div>
          {n.summary && <div style={{ fontSize: 11.5, color: C.dim, marginTop: 2 }}>{n.summary}</div>}
        </div>
      ))}
      {headlines?.length > 0 && headlines.map((n, i) => (
        <div key={`h${i}`} style={S.newsRow}>{n.title || n.headline || String(n)}</div>
      ))}
      {!hasAny && (
        <div style={{ color: C.dim, fontSize: 12.5 }}>
          No headlines yet.{desk.enabled
            ? ' Forward any market news to the News-Desk Telegram bot — its impact read appears here.'
            : ' (News-Desk bot not configured — set notifications.news_desk in settings.local.yaml to enable inbound news analysis.)'}
        </div>
      )}
    </div>
  );
}

/** Net read of the mixed global signals — counts UP vs DOWN and states the verdict
 *  in one line (point 5: "what is the net conclusion of such mixed signals"). */
function GlobalNet({ markets }) {
  const up = markets.filter(m => m.direction === 'UP').length;
  const down = markets.filter(m => m.direction === 'DOWN').length;
  const flat = markets.length - up - down;
  const net = up - down;
  const verdict = net >= 2 ? { t: 'RISK-ON', c: C.green }
    : net <= -2 ? { t: 'RISK-OFF', c: C.red }
    : { t: 'MIXED / NEUTRAL', c: C.amber };
  return (
    <div style={{ ...S.globalNet, borderLeft: `4px solid ${verdict.c}` }}>
      <span style={{ fontWeight: 800, color: verdict.c, fontSize: 13 }}>GLOBAL CUES NET: {verdict.t}</span>
      <span style={{ color: C.dim, fontSize: 12.5, marginLeft: 10 }}>
        {up} up · {down} down{flat ? ` · ${flat} flat` : ''} —{' '}
        {verdict.t === 'RISK-ON' ? 'overnight lead supports a positive / buy-on-dip open.'
          : verdict.t === 'RISK-OFF' ? 'overnight lead is negative — favour caution / sell-on-rise.'
          : 'signals cancel out — let the open + India internals decide direction.'}
      </span>
    </div>
  );
}

function Metric({ label, value, note, noteColor, valueColor, tip }) {
  return (
    <div style={S.metric}>
      <span style={{ color: C.dim, fontSize: 13, ...(tip ? S.tipCell : {}) }} title={tip || undefined}>{label}</span>
      <span>
        <b style={{ fontSize: 16, color: valueColor || C.text }}>{value}</b>
        {note ? <span style={{ marginLeft: 8, fontSize: 11, fontWeight: 700, color: noteColor || C.dim }}>{note}</span> : null}
      </span>
    </div>
  );
}

const S = {
  headRow: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 },
  h2: { fontSize: 20, margin: 0, color: C.text },
  refresh: { background: C.blue, border: 'none', color: '#fff', fontWeight: 700, fontSize: 13, padding: '7px 16px', borderRadius: 6, cursor: 'pointer' },
  card: { background: C.panel, border: `1px solid ${C.border}`, borderRadius: 10, padding: 16, marginBottom: 12, boxShadow: SH.card },
  conclusion: { background: C.panel, border: `1px solid ${C.border}`, borderRadius: 10, padding: '16px 20px', marginBottom: 12, boxShadow: SH.raised },
  conclTop: { display: 'flex', alignItems: 'center', gap: 12 },
  conclEyebrow: { fontSize: 12, fontWeight: 800, letterSpacing: 1, color: C.dim },
  convPill: { fontSize: 10, fontWeight: 800, color: '#fff', padding: '2px 9px', borderRadius: 10, letterSpacing: 0.4 },
  bullets: { margin: '10px 0 0 0', paddingLeft: 18, color: C.text, fontSize: 12.5, lineHeight: 1.6 },
  cautions: { marginTop: 10, background: '#fff7ed', border: `1px solid #fed7aa`, borderRadius: 8, padding: '8px 12px' },
  cautionRow: { color: '#b45309', fontSize: 12.5, fontWeight: 600, padding: '2px 0' },
  fitWrap: { marginTop: 16, borderTop: `1px solid ${C.border}`, paddingTop: 12 },
  fitTitle: { fontSize: 12, fontWeight: 800, letterSpacing: 0.8, color: C.text, marginBottom: 4 },
  fitNote: { fontSize: 11.5, color: C.dim, fontStyle: 'italic', marginBottom: 10 },
  fitGrid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 8 },
  fitRow: { background: C.panel2, borderRadius: 6, padding: '8px 12px' },
  fitRowTop: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 },
  fitName: { fontWeight: 700, fontSize: 13, color: C.blue, textDecoration: 'underline', textUnderlineOffset: 2, cursor: 'pointer' },
  fitTier: { fontSize: 9.5, fontWeight: 800, letterSpacing: 0.4, padding: '1px 7px', borderRadius: 9, border: '1px solid' },
  fitReason: { fontSize: 11.5, color: C.text, marginTop: 3 },
  cardTitle: { fontSize: 13, fontWeight: 700, letterSpacing: 0.6, color: C.cyan, marginBottom: 12 },
  hint: { fontWeight: 400, fontSize: 10.5, color: C.dim, letterSpacing: 0 },
  tipCell: { cursor: 'help', borderBottom: `1px dotted ${C.dim}`, display: 'inline-block', width: 'fit-content' },
  globalNet: { marginTop: 12, padding: '9px 12px', background: C.panel2, borderRadius: 6 },
  internalNote: { marginTop: 10, fontSize: 11, color: C.dim, lineHeight: 1.5, background: C.panel2, borderRadius: 6, padding: '8px 10px' },
  frozenNote: { fontSize: 11.5, color: C.dim, lineHeight: 1.5, background: '#f1f5fb', border: `1px solid ${C.border}`, borderRadius: 8, padding: '8px 12px', marginBottom: 12 },
  derivCell: { padding: '8px 12px 10px 26px', background: C.panel2, fontSize: 12, color: C.text, borderBottom: `1px solid ${C.border}`, lineHeight: 1.5 },
  // Live market card — distinct cyan identity vs the frozen pre-market slate.
  liveCard: { background: '#ecfeff', border: `1px solid #a5e8f5`, borderLeft: `5px solid ${C.cyan}`, borderRadius: 10, padding: 16, marginBottom: 12, boxShadow: SH.card },
  liveHead: { display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 },
  liveTitle: { fontSize: 13, fontWeight: 800, letterSpacing: 0.6, color: C.cyan },
  liveDot: { width: 9, height: 9, borderRadius: '50%', background: '#dc2626', boxShadow: '0 0 0 3px rgba(220,38,38,0.18)', display: 'inline-block' },
  liveGrid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 12 },
  liveQ: { display: 'flex', flexDirection: 'column', gap: 2, background: '#ffffff', border: `1px solid #cdeef6`, borderRadius: 8, padding: '8px 12px' },
  liveQLabel: { fontSize: 10.5, fontWeight: 700, letterSpacing: 0.4, color: C.dim },
  deskRow: { padding: '8px 0', borderBottom: `1px solid ${C.border}` },
  deskPill: { fontSize: 9.5, fontWeight: 800, color: '#fff', padding: '1px 7px', borderRadius: 9, letterSpacing: 0.3 },
  gaugeTrack: { position: 'relative', height: 12, background: '#eef2f8', borderRadius: 6, marginTop: 14, border: `1px solid ${C.border}` },
  gaugeFill: { position: 'absolute', top: 0, height: '100%', borderRadius: 6, opacity: 0.85 },
  gaugeMid: { position: 'absolute', left: '50%', top: -3, bottom: -3, width: 2, background: C.dim },
  gaugeLabels: { display: 'flex', justifyContent: 'space-between', fontSize: 10, color: C.dim, marginTop: 6 },
  twoCol: { display: 'flex', gap: 12, flexWrap: 'wrap' },
  table: { width: '100%', borderCollapse: 'collapse', fontSize: 13 },
  th: { textAlign: 'left', padding: '8px 10px', color: C.dim, borderBottom: `2px solid ${C.border}`, fontWeight: 700 },
  tr: { borderBottom: `1px solid ${C.border}` },
  td: { padding: '8px 10px', color: C.text },
  tdName: { padding: '8px 10px', color: C.text, fontWeight: 700 },
  tdDim: { padding: '8px 10px', color: C.dim, fontSize: 12 },
  metric: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '8px 0', borderBottom: `1px solid ${C.border}` },
  eventRow: { color: C.amber, fontSize: 13, padding: '4px 0', fontWeight: 600 },
  newsRow: { color: C.text, fontSize: 12.5, padding: '5px 0', borderBottom: `1px solid ${C.border}` },
};
