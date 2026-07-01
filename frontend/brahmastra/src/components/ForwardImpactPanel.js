import React from 'react';
import { C, SH } from '../api';

/**
 * Forward Impact — the projected likely move over the NEXT ~15–30 minutes.
 * Always on screen. Built from the live Narrator feed (score, direction,
 * bars-to-entry, alert tier) plus the active scenario's confidence/targets and
 * the latest Bollinger context, so the user sees not just "what is" but
 * "what's coming".
 */
const tierColor = (t) => ({
  STRIKE: C.red, ARMED: C.amber, WATCH: C.cyan, CALM: C.dim,
}[(t || '').toUpperCase()] || C.dim);

/**
 * Resolve the MARKET direction (up / down / flat) shown by the panel.
 *
 * `score_dir` is overloaded across the two narrators and MUST NOT be used as a
 * market direction on its own:
 *   - market_feed narrator → score_dir is UP / DOWN / RANGE  (market direction)
 *   - brahmastra narrator  → score_dir is RISING / FALLING / STABLE
 *     (that is the *conviction trajectory* — whether the score is building or
 *     fading — NOT which way price is going). A BEAR setup can be "RISING".
 * Treating RISING/FALLING as up/down was flipping the arrow (a rising-conviction
 * bear read as bullish). The signal that means the same thing in both narrators
 * is the SIGN of `score` (>0 bull, <0 bear), so prefer that, then an explicit
 * directional label, and only then a genuinely directional score_dir token.
 */
function resolveDir(latest, best) {
  const sd = (latest?.score_dir || '').toUpperCase();
  const label = latest?.direction_label || '';
  const score = latest?.score;

  // 1) explicit market-direction label from market_feed ("Strong Up" / "Mild Down" / "Sideways")
  if (/sideways|range|chop/i.test(label)) return { sign: 0, label: label || 'Sideways', src: 'label' };
  if (/up|bull/i.test(label))   return { sign: 1,  label, src: 'label' };
  if (/down|bear/i.test(label)) return { sign: -1, label, src: 'label' };

  // 2) score_dir ONLY when it is an actual market-direction token (never RISING/FALLING/STABLE)
  if (sd === 'RANGE' || sd === 'FLAT') return { sign: 0, label: 'Sideways', src: 'dir' };
  if (sd === 'UP')   return { sign: 1,  label: 'Up',   src: 'dir' };
  if (sd === 'DOWN') return { sign: -1, label: 'Down', src: 'dir' };

  // 3) sign of the signed score (works for the brahmastra narrator, where score_dir
  //    is a conviction trajectory). Small magnitude ⇒ no clean directional edge.
  if (typeof score === 'number') {
    if (Math.abs(score) < 8) return { sign: 0, label: 'Sideways', src: 'score' };
    return score > 0 ? { sign: 1, label: 'Up', src: 'score' } : { sign: -1, label: 'Down', src: 'score' };
  }

  // 4) fall back to the best scenario's hypothesis
  const hyp = (best?.hypothesis || '').toUpperCase();
  if (/bull|up/i.test(hyp)) return { sign: 1, label: 'Up', src: 'scn' };
  if (/bear|down/i.test(hyp)) return { sign: -1, label: 'Down', src: 'scn' };
  return { sign: 0, label: '—', src: 'none' };
}

// Human label for the conviction trajectory (brahmastra score_dir), shown as a
// secondary chip so the operator still sees whether conviction is building.
const trajLabel = (sd) => ({
  RISING: 'conviction building', FALLING: 'conviction fading', STABLE: 'conviction steady',
}[(sd || '').toUpperCase()] || '');

function instImpact(inst, narratorFeed, scenarios, indicators) {
  // narrator feed is oldest-first; the latest projection is the LAST entry.
  const latest = (narratorFeed && narratorFeed.length) ? narratorFeed[narratorFeed.length - 1] : null;
  const scns = scenarios || [];
  const best = scns.slice().sort((a, b) => (b.confidence || 0) - (a.confidence || 0))[0];
  const ind = indicators || {};
  // Bollinger context
  let bbCtx = '';
  if (ind.bb_pct_b != null) {
    if (ind.bb_pct_b > 0.95) bbCtx = 'riding upper band';
    else if (ind.bb_pct_b < 0.05) bbCtx = 'riding lower band';
    else if (ind.bb_squeeze) bbCtx = 'squeeze — expansion likely';
  }
  return { inst, latest, best, bbCtx };
}

export default function ForwardImpactPanel({ narrator, scenarios, indicators, instruments }) {
  const list = instruments && instruments.length ? instruments
    : Object.keys(narrator || {});
  const rows = (list.length ? list : ['NIFTY']).map(inst =>
    instImpact(inst, (narrator || {})[inst], (scenarios || {})[inst], (indicators || {})[inst]));

  return (
    <div style={S.panel}>
      <div style={S.title}>FORWARD IMPACT · next 15–30 min</div>
      <div style={S.body}>
        {rows.map(({ inst, latest, best, bbCtx }) => {
          const rd = resolveDir(latest, best);
          const up = rd.sign > 0;
          const down = rd.sign < 0;
          const dirColor = up ? C.green : down ? C.red : C.dim;
          const traj = trajLabel(latest?.score_dir);
          return (
            <div key={inst} style={S.row}>
              <div style={S.instCol}>
                <div style={{ fontWeight: 700 }}>{inst}</div>
                {latest && (
                  <span style={{ ...S.tier, background: tierColor(latest.alert_tier) }}>
                    {latest.alert_tier || '—'}
                  </span>
                )}
              </div>
              <div style={{ flex: 1 }}>
                <div style={{ color: dirColor, fontWeight: 700, fontSize: 13, display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                  <span>{up ? '▲' : down ? '▼' : '•'} {latest?.direction_label || rd.label}</span>
                  {traj && (
                    <span style={S.chipDim} title="Conviction trajectory (whether the score is building or fading) — separate from which way price is heading.">
                      {traj}
                    </span>
                  )}
                  {latest?.confidence != null && (
                    <span style={{ ...S.chip, borderColor: dirColor, color: dirColor }}
                          title="Confidence (0–100): timeframe alignment × signal strength, penalised for high/extreme volatility and chop.">
                      conf {latest.confidence}/100{latest.conviction ? ` · ${latest.conviction}` : ''}
                    </span>
                  )}
                  {latest?.structure && (
                    <span style={S.chipDim} title="Structural context inferred from Bollinger band position + width change across timeframes.">
                      {latest.structure}
                    </span>
                  )}
                  {latest?.volatility && (
                    <span style={{ ...S.chipDim, color: /Extreme|Elevated/.test(latest.volatility) ? C.amber : C.dim }}
                          title="Volatility regime from Bollinger band width (≈ expected swing size).">
                      vol: {latest.volatility}
                    </span>
                  )}
                  {latest?.score != null && !latest?.confidence && <span style={{ color: C.dim, fontWeight: 400 }}>score {latest.score}</span>}
                </div>
                <div style={{ color: C.text, fontSize: 11, marginTop: 3 }}>
                  {latest?.reason || latest?.headline || (best ? `${best.hypothesis} scenario @ ${Math.round(best.confidence || 0)}% conf` : 'awaiting signal…')}
                </div>
                {latest?.levels && latest.levels !== '—' && (
                  <div style={{ color: C.dim, fontSize: 10.5, marginTop: 2 }}>
                    key band {latest.levels}
                    {best?.t1 != null && `  ·  target ${best.t1}  ·  SL ${best.sl ?? '—'}`}
                    {bbCtx && <span style={{ color: C.amber }}>  · BB: {bbCtx}</span>}
                  </div>
                )}
                {latest?.risk && (
                  <div style={{ color: C.amber, fontSize: 10.5, marginTop: 2 }}>⚠ {latest.risk}</div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

const S = {
  panel: { background: C.panel, border: `1px solid ${C.border}`, borderRadius: 10, padding: 12, marginBottom: 12, boxShadow: SH.card },
  title: { fontSize: 13, fontWeight: 700, letterSpacing: 0.6, color: C.purple, marginBottom: 10 },
  body: { display: 'flex', flexDirection: 'column', gap: 7 },
  row: { display: 'flex', gap: 10, padding: 10, background: C.panel2, border: `1px solid ${C.border}`, borderRadius: 8 },
  instCol: { width: 74, display: 'flex', flexDirection: 'column', gap: 4, alignItems: 'flex-start' },
  tier: { fontSize: 10, fontWeight: 700, color: '#fff', padding: '2px 7px', borderRadius: 4 },
  chip: { fontSize: 9.5, fontWeight: 800, letterSpacing: 0.3, padding: '1px 7px', borderRadius: 9, border: '1px solid', cursor: 'help' },
  chipDim: { fontSize: 9.5, fontWeight: 700, color: C.dim, background: C.panel2, padding: '1px 7px', borderRadius: 9, cursor: 'help' },
};
