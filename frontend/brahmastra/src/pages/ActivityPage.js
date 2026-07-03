import React, { useEffect, useState, useCallback, useRef } from 'react';
import { api, C, SH } from '../api';

/** One window for EVERYTHING: every log line, signal and trade-call across all
 *  strategies, tagged by strategy, live. Answers "show me all activity in one place". */
const CATS = ['ALL', 'TRADE', 'ANALYSIS', 'SIGNAL', 'CTRL', 'ERROR'];
const CAT_COLOR = { TRADE: C.green, ANALYSIS: C.blue, SIGNAL: C.purple, ERROR: C.red, CTRL: C.amber, SYSTEM: C.dim };

export default function ActivityPage({ onOpen }) {
  const [data, setData] = useState({ lines: [], trade_calls: [], running: 0, total_lines: 0 });
  const [cat, setCat] = useState('ALL');
  const [callsOnly, setCallsOnly] = useState(false);
  const [err, setErr] = useState(null);
  const [following, setFollowing] = useState(true);   // false once the user scrolls up
  const boxRef = useRef(null);
  const followRef = useRef(true);                      // read inside the data effect w/o re-subscribing

  const load = useCallback(() => {
    api.activity(cat).then(d => { setData(d); setErr(null); }).catch(e => setErr(String(e)));
  }, [cat]);
  useEffect(() => { load(); const id = setInterval(load, 4000); return () => clearInterval(id); }, [load]);

  // Auto-scroll to "now" ONLY while the user is parked at the bottom. If they've
  // scrolled up to inspect a past time, hold that position across the 4s refresh.
  useEffect(() => {
    if (boxRef.current && followRef.current) {
      boxRef.current.scrollTop = boxRef.current.scrollHeight;
    }
  }, [data]);

  function onScroll() {
    const el = boxRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
    followRef.current = atBottom;
    if (atBottom !== following) setFollowing(atBottom);
  }

  function jumpToNow() {
    const el = boxRef.current;
    if (el) el.scrollTop = el.scrollHeight;
    followRef.current = true;
    setFollowing(true);
  }

  const rows = callsOnly ? (data.trade_calls || []) : (data.lines || []);

  return (
    <div>
      <div style={S.head}>
        <h2 style={S.h2}>Activity — all strategies, one window</h2>
        <div style={S.meta}>
          <b style={{ color: C.green }}>{data.running}</b> running · {data.total_lines} log lines today
          {(data.trade_calls || []).length === 0 &&
            <span style={{ color: C.amber, marginLeft: 8 }}>· no trade calls yet today</span>}
        </div>
      </div>
      {err && <div style={{ color: C.red, marginBottom: 8 }}>{err}</div>}

      <div style={S.filters}>
        {CATS.map(c => (
          <button key={c} onClick={() => { setCat(c); setCallsOnly(false); }}
                  style={{ ...S.chip, ...(cat === c && !callsOnly ? S.chipOn : {}) }}>{c}</button>
        ))}
        <button onClick={() => setCallsOnly(v => !v)}
                style={{ ...S.chip, ...(callsOnly ? S.chipOn : {}), marginLeft: 'auto', borderColor: C.green }}>
          ▶ Trade calls only
        </button>
      </div>

      <div style={{ position: 'relative' }}>
      {!following && (
        <button onClick={jumpToNow} style={S.jump}>
          ▼ Scroll paused — Jump to now
        </button>
      )}
      <div ref={boxRef} style={S.stream} onScroll={onScroll}>
        {rows.length === 0
          ? <div style={S.empty}>No activity yet. Start strategies (Strategies → Paper) and their analysis appears here live.</div>
          : rows.map((l, i) => {
            const col = CAT_COLOR[(l.category || '').toUpperCase()] || C.dim;
            return (
              <div key={i} style={S.line}>
                <span style={S.ts}>{l.ts}</span>
                <span style={S.strat} onClick={() => onOpen && onOpen(l.strategy)} title="open strategy">{l.strategy}</span>
                <span style={{ ...S.cat, color: col }}>{(l.category || 'INFO').toUpperCase()}</span>
                <span style={S.msg}>{l.message}</span>
              </div>
            );
          })}
      </div>
      </div>
      <div style={{ color: C.dim, fontSize: 11, marginTop: 8 }}>
        Live feed (4s). "Trade calls only" filters to TRADE / SIGNAL / ANALYSIS lines — the
        signals each strategy generates. A quiet day with few calls is normal; the analysis
        lines show each strategy is watching and choosing not to force a trade.
      </div>
    </div>
  );
}

const S = {
  head: { display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', flexWrap: 'wrap', gap: 8 },
  h2: { fontSize: 20, marginBottom: 6, color: C.text },
  meta: { fontSize: 12, color: C.dim },
  filters: { display: 'flex', gap: 5, flexWrap: 'wrap', margin: '10px 0' },
  chip: { background: C.panel, border: `1px solid ${C.border}`, color: C.dim, fontSize: 12, fontWeight: 700, padding: '4px 11px', borderRadius: 14, cursor: 'pointer' },
  chipOn: { background: C.blue, color: '#fff', borderColor: C.blue },
  stream: { background: C.panel, border: `1px solid ${C.border}`, borderRadius: 10, boxShadow: SH.card, padding: 10, height: '60vh', overflowY: 'auto', fontFamily: 'monospace', fontSize: 12.5 },
  line: { display: 'flex', gap: 10, padding: '3px 4px', borderBottom: `1px solid ${C.bg}`, alignItems: 'baseline' },
  ts: { color: C.dim, width: 64, flexShrink: 0 },
  strat: { color: C.blue, width: 150, flexShrink: 0, fontWeight: 700, cursor: 'pointer', textDecoration: 'underline', textUnderlineOffset: 2, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' },
  cat: { width: 70, flexShrink: 0, fontWeight: 700 },
  msg: { color: C.text, flex: 1, whiteSpace: 'pre-wrap', wordBreak: 'break-word' },
  empty: { color: C.dim, textAlign: 'center', padding: 40 },
  jump: {
    position: 'absolute', top: 8, right: 16, zIndex: 5,
    background: C.amber, color: '#1a1205', border: 'none',
    fontSize: 12, fontWeight: 800, padding: '5px 12px', borderRadius: 14,
    cursor: 'pointer', boxShadow: SH.card,
  },
};
