import React, { useEffect, useState, useCallback } from 'react';
import { api, C, SH } from '../api';

/**
 * Intraday Equity Scanner — ranks liquid NSE stocks by today's intraday setup
 * (Opening-Range Breakout + VWAP + volume + RSI + % move). Every value is computed
 * from real intraday bars; when there's no live data it shows an honest empty-state
 * (never fabricated prices/signals).
 */
const fmt = (v, d = 2) => v == null ? '—'
  : (typeof v === 'number' ? v.toLocaleString('en-IN', { maximumFractionDigits: d }) : v);

function BiasPill({ bias, score }) {
  const c = bias === 'LONG' ? C.green : bias === 'SHORT' ? C.red : C.dim;
  return <span style={{ ...S.pill, background: c + '18', color: c, border: `1px solid ${c}44` }}>
    {bias} {score > 0 ? `+${score}` : score}
  </span>;
}
function Tag({ text, tone }) {
  const c = tone === 'up' ? C.green : tone === 'down' ? C.red : C.dim;
  return <span style={{ ...S.tag, color: c, borderColor: c + '55' }}>{text}</span>;
}

export default function EquityScannerPage() {
  const [data, setData] = useState(null);
  const [lastGood, setLastGood] = useState(null);   // most recent scan that had rows
  const [err, setErr]   = useState(null);
  const [loading, setLoading] = useState(false);
  const [fu, setFu]     = useState(null);
  const [fuLoading, setFuLoading] = useState(false);
  const [rankBy, setRankBy] = useState('score');   // 'score' (conviction) | 'shocker'

  const load = useCallback(() => {
    setLoading(true); setErr(null);
    api.equityWatchlist(20)
      .then(d => {
        setData(d);
        if (d.status === 'ok' && (d.watchlist || []).length) setLastGood(d);
        setLoading(false);
      })
      .catch(e => { setErr(String(e)); setLoading(false); });
  }, []);
  useEffect(() => { load(); }, [load]);

  const loadFollowup = useCallback(() => {
    setFuLoading(true);
    api.equityFollowup()
      .then(d => { setFu(d); setFuLoading(false); })
      .catch(e => { setFu({ status: 'error', note: String(e), picks: [] }); setFuLoading(false); });
  }, []);
  useEffect(() => { loadFollowup(); }, [loadFollowup]);

  // When a fresh rescan comes back empty (e.g. too early in the session, before
  // the opening range has formed), don't wipe the screen — keep showing the last
  // scan that had data, clearly flagged as stale, so it never just vanishes.
  const liveEmpty = data && data.status !== 'ok';
  const showStale = liveEmpty && lastGood;
  const shown = data && data.status === 'ok' ? data : (showStale ? lastGood : data);
  const wl = (shown && shown.watchlist) || [];
  // Client-side re-rank (the scan already returns the full universe): biggest
  // volume shocker first, or the default conviction order.
  const displayed = rankBy === 'shocker'
    ? [...wl].sort((a, b) => (b.vol_ratio || 0) - (a.vol_ratio || 0))
    : wl;
  const orbTone = (o) => o === 'BREAKOUT_UP' ? 'up' : o === 'BREAKDOWN' ? 'down' : 'flat';

  return (
    <div>
      <div style={S.head}>
        <h2 style={S.h2}>Intraday Equity Scanner</h2>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <select value={rankBy} onChange={e => setRankBy(e.target.value)}
                  title="How to rank the scan"
                  style={{ padding: '6px 10px', borderRadius: 8, border: `1px solid ${C.border}`,
                           background: C.panel, color: C.text, fontWeight: 600, fontSize: 13 }}>
            <option value="score">Rank: Conviction</option>
            <option value="shocker">Rank: 🔥 Volume shocker</option>
          </select>
          <button style={S.refresh} onClick={load} disabled={loading}>
            {loading ? '⏳ Scanning…' : '↻ Rescan'}
          </button>
        </div>
      </div>
      <p style={S.sub}>
        Ranks liquid NSE stocks by today's intraday momentum — Opening-Range Breakout,
        VWAP position, volume surge, RSI and % move. Signals are computed live from
        intraday bars; strongest conviction first.
      </p>

      {err && <div style={S.err}>Couldn't reach the scanner ({err}).</div>}

      {liveEmpty && !showStale && (
        <div style={S.empty}>
          <b>No live scan yet.</b> {data.note || 'No intraday data available.'}
          <div style={S.emptySub}>
            Scanned {data.scanned ?? 0} symbols · {data.data_source || 'yfinance'} ·
            signals need ~30 min of bars to form (rescan after ~9:45 IST).
          </div>
        </div>
      )}

      {showStale && (
        <div style={S.stale}>
          <b>Latest rescan had no fresh bars yet</b> — too early in the session, or the
          feed is briefly quiet. Showing your last good scan from{' '}
          <b>{lastGood.generated_at ? `${lastGood.generated_at.slice(11, 16)} IST` : 'earlier'}</b>.
          Rescan again after ~9:45 IST once the opening range has formed.
        </div>
      )}

      {wl.length > 0 && (
        <>
          <div style={S.meta}>
            {shown.returned} of {shown.scanned} scanned · source {shown.data_source} ·
            {shown.generated_at ? ` ${shown.generated_at.slice(11, 16)} IST` : ''}
            {shown.shockers ? ` · 🔥 ${shown.shockers} volume shocker${shown.shockers > 1 ? 's' : ''}` : ''}
            {showStale ? ' · (last good scan)' : ''}
          </div>
          <div style={S.tableWrap}>
            <table style={S.table}>
              <thead>
                <tr>
                  {['#', 'Symbol', 'Bias', 'LTP', '% Chg', 'Opening Range', 'VWAP', 'Vol×', 'RSI', 'Read']
                    .map(h => <th key={h} style={S.th}>{h}</th>)}
                </tr>
              </thead>
              <tbody>
                {displayed.map((r, i) => (
                  <tr key={r.symbol} style={S.tr}>
                    <td style={S.tdDim}>{i + 1}</td>
                    <td style={S.tdSym}>{r.symbol}</td>
                    <td style={S.td}><BiasPill bias={r.bias} score={r.score} /></td>
                    <td style={S.td}>{fmt(r.ltp)}</td>
                    <td style={{ ...S.td, color: r.pct_change >= 0 ? C.green : C.red, fontWeight: 700 }}>
                      {r.pct_change >= 0 ? '+' : ''}{fmt(r.pct_change)}%
                    </td>
                    <td style={S.td}><Tag text={r.orb.replace('_', ' ')} tone={orbTone(r.orb)} /></td>
                    <td style={S.td}><Tag text={r.vwap_pos} tone={r.vwap_pos === 'ABOVE' ? 'up' : 'down'} /></td>
                    <td style={{ ...S.td, fontWeight: r.vol_ratio >= 1.5 ? 700 : 400,
                                 color: r.vol_shocker ? C.red : r.vol_ratio >= 1.5 ? C.amber : C.text }}
                        title={r.vol_shocker ? `Volume shocker — ${r.vol_tier} (${fmt(r.vol_ratio, 1)}× its average)` : ''}>
                      {r.vol_shocker ? '🔥 ' : ''}{fmt(r.vol_ratio, 1)}×</td>
                    <td style={S.td}>{fmt(r.rsi, 0)}</td>
                    <td style={S.tdRead}>{r.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      <FollowupPanel fu={fu} loading={fuLoading} reload={loadFollowup} />

      <p style={S.foot}>
        Scanner only — it surfaces setups for your eye; it does not place orders. Bias is a
        composite conviction score (−100 short … +100 long) from the five signals above.
        Data is intraday (delayed feeds shift the numbers); treat it as a shortlist, not a trigger.
      </p>
    </div>
  );
}

/**
 * "Yesterday's picks — how did they do?" Grades a saved day's intraday scan
 * against the NEXT trading day's real close (momentum carry-over). Honest
 * empty-states when there's no snapshot or the next day hasn't closed yet.
 */
function FollowupPanel({ fu, loading, reload }) {
  const rows = (fu && fu.picks) || [];
  const okColor = (v) => v == null ? C.dim : v >= 50 ? C.green : v >= 40 ? C.amber : C.red;
  return (
    <div style={S.fuWrap}>
      <div style={S.head}>
        <h3 style={S.h3}>Next-day follow-up — how did a day's picks actually do?</h3>
        <button style={S.refresh} onClick={reload} disabled={loading}>
          {loading ? '⏳ Grading…' : '↻ Re-grade'}
        </button>
      </div>
      <p style={S.sub}>
        Each live scan is saved automatically. This grades a saved day's picks against the
        <b> next trading day's real close</b> — LONG calls "hit" if the stock rose, SHORT calls
        if it fell. It measures momentum <i>carry-over</i> (follow-through into the next day),
        which is a different edge than same-day intraday.
      </p>

      {fu && fu.status !== 'ok' && (
        <div style={S.empty}>
          <b>{fu.status === 'pending' ? 'Waiting on the next session.'
             : fu.status === 'no_snapshots' ? 'No saved scans yet.'
             : fu.status === 'error' ? "Couldn't grade." : 'Nothing to grade yet.'}</b>
          {' '}{fu.note}
        </div>
      )}

      {fu && fu.status === 'ok' && fu.verdict && (
        <div style={{ ...S.verdict,
          background: fu.verdict === 'CARRY_OVER' ? '#ecfdf5' : fu.verdict === 'REVERSAL' ? '#fef2f2' : '#f8fafc',
          borderColor: fu.verdict === 'CARRY_OVER' ? '#a7f3d0' : fu.verdict === 'REVERSAL' ? '#fecaca' : C.border }}>
          <b style={{ color: fu.verdict === 'CARRY_OVER' ? C.green : fu.verdict === 'REVERSAL' ? C.red : C.dim }}>
            {fu.verdict === 'CARRY_OVER' ? '↗ CARRY-OVER' : fu.verdict === 'REVERSAL' ? '↩ REVERSAL' : '↔ MIXED'}
          </b>{' — '}{fu.verdict_note}
        </div>
      )}

      {fu && fu.status === 'ok' && (
        <>
          <div style={S.fuStats}>
            <div style={S.stat}>
              <div style={S.statLbl}>Graded (of picks)</div>
              <div style={S.statVal}>{fu.directional} directional</div>
            </div>
            <div style={S.stat}>
              <div style={S.statLbl}>Hit rate (next day)</div>
              <div style={{ ...S.statVal, color: okColor(fu.hit_rate_pct) }}>
                {fu.hit_rate_pct == null ? '—' : `${fu.hit_rate_pct}%`}
              </div>
            </div>
            <div style={S.stat}>
              <div style={S.statLbl}>Avg return (call direction)</div>
              <div style={{ ...S.statVal, color: (fu.avg_dir_return_pct || 0) >= 0 ? C.green : C.red }}>
                {fu.avg_dir_return_pct == null ? '—'
                  : `${fu.avg_dir_return_pct >= 0 ? '+' : ''}${fu.avg_dir_return_pct}%`}
              </div>
            </div>
            <div style={S.stat}>
              <div style={S.statLbl}>Snapshot graded</div>
              <div style={S.statVal}>{fu.snapshot_date}</div>
            </div>
          </div>
          <div style={S.tableWrap}>
            <table style={S.table}>
              <thead>
                <tr>{['Symbol', 'Called', 'Entry', 'Next close', 'Move', 'In-direction', 'Result']
                  .map(h => <th key={h} style={S.th}>{h}</th>)}</tr>
              </thead>
              <tbody>
                {rows.map(r => (
                  <tr key={r.symbol} style={S.tr}>
                    <td style={S.tdSym}>{r.symbol}</td>
                    <td style={S.td}><Tag text={r.bias}
                      tone={r.bias === 'LONG' ? 'up' : r.bias === 'SHORT' ? 'down' : 'flat'} /></td>
                    <td style={S.td}>{fmt(r.entry)}</td>
                    <td style={S.td}>{fmt(r.next_close)}</td>
                    <td style={{ ...S.td, color: r.raw_change_pct >= 0 ? C.green : C.red }}>
                      {r.raw_change_pct >= 0 ? '+' : ''}{fmt(r.raw_change_pct)}%
                    </td>
                    <td style={{ ...S.td, fontWeight: 700, color: r.dir_return_pct >= 0 ? C.green : C.red }}>
                      {r.dir_return_pct >= 0 ? '+' : ''}{fmt(r.dir_return_pct)}%
                    </td>
                    <td style={S.td}>
                      {r.bias === 'NEUTRAL' ? <span style={{ color: C.dim }}>—</span>
                        : r.hit ? <span style={{ color: C.green, fontWeight: 700 }}>✓ hit</span>
                        : <span style={{ color: C.red, fontWeight: 700 }}>✗ miss</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p style={S.fuFoot}>
            "In-direction" return is signed to the call (positive = the call was right). Prices are
            real yfinance closes. A single day is a tiny sample — read the trend across many days,
            not one, before trusting it.
          </p>
        </>
      )}
    </div>
  );
}

const S = {
  head: { display: 'flex', alignItems: 'center', justifyContent: 'space-between' },
  h2: { fontSize: 22, fontWeight: 800, color: C.text, margin: '4px 0' },
  sub: { color: C.dim, fontSize: 13.5, lineHeight: 1.5, maxWidth: 900, margin: '0 0 14px' },
  refresh: { background: C.panel, border: `1px solid ${C.border}`, color: C.blue, fontWeight: 600, fontSize: 13, padding: '6px 12px', borderRadius: 6, cursor: 'pointer' },
  meta: { color: C.dim, fontSize: 12, marginBottom: 8 },
  tableWrap: { overflowX: 'auto', background: C.panel, border: `1px solid ${C.border}`, borderRadius: 10, boxShadow: SH.card },
  table: { width: '100%', borderCollapse: 'collapse', minWidth: 860 },
  th: { textAlign: 'left', fontSize: 11.5, color: C.dim, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.3, padding: '10px 12px', borderBottom: `1px solid ${C.border}`, whiteSpace: 'nowrap' },
  tr: { borderBottom: `1px solid ${C.border}` },
  td: { fontSize: 13.5, color: C.text, padding: '9px 12px', whiteSpace: 'nowrap' },
  tdDim: { fontSize: 12, color: C.dim, padding: '9px 12px' },
  tdSym: { fontSize: 13.5, fontWeight: 700, color: C.text, padding: '9px 12px' },
  tdRead: { fontSize: 12, color: C.dim, padding: '9px 12px' },
  pill: { fontSize: 11.5, fontWeight: 700, padding: '2px 9px', borderRadius: 999, whiteSpace: 'nowrap' },
  tag: { fontSize: 11.5, fontWeight: 600, padding: '2px 8px', borderRadius: 6, border: '1px solid', whiteSpace: 'nowrap' },
  empty: { background: C.panel, border: `1px solid ${C.border}`, borderRadius: 10, padding: 18, color: C.text, fontSize: 14, boxShadow: SH.card },
  emptySub: { color: C.dim, fontSize: 12.5, marginTop: 6 },
  err: { color: C.red, background: '#fef2f2', border: '1px solid #fecaca', borderRadius: 8, padding: 14, marginBottom: 12 },
  stale: { color: C.text, background: '#fffbeb', border: '1px solid #fde68a', borderRadius: 8, padding: 12, marginBottom: 12, fontSize: 13 },
  foot: { color: C.dim, fontSize: 12.5, lineHeight: 1.5, maxWidth: 920, marginTop: 14 },
  fuWrap: { marginTop: 26, paddingTop: 20, borderTop: `2px solid ${C.border}` },
  h3: { fontSize: 18, fontWeight: 800, color: C.text, margin: '4px 0' },
  fuStats: { display: 'flex', flexWrap: 'wrap', gap: 12, margin: '4px 0 14px' },
  stat: { background: C.panel, border: `1px solid ${C.border}`, borderRadius: 10, padding: '10px 16px', boxShadow: SH.card, minWidth: 150 },
  statLbl: { fontSize: 11, color: C.dim, fontWeight: 600, textTransform: 'uppercase', letterSpacing: 0.3 },
  statVal: { fontSize: 18, fontWeight: 800, color: C.text, marginTop: 3 },
  fuFoot: { color: C.dim, fontSize: 12, lineHeight: 1.5, maxWidth: 920, marginTop: 10 },
  verdict: { border: '1px solid', borderRadius: 8, padding: '10px 14px', margin: '2px 0 14px', fontSize: 13, lineHeight: 1.5, color: C.text },
};
