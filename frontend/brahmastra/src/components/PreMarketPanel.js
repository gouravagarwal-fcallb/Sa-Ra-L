import React from 'react';

const BIAS_COLOR = {
  STRONG_BULL: '#22c55e',
  BULL:        '#4ade80',
  NEUTRAL:     '#94a3b8',
  BEAR:        '#f87171',
  STRONG_BEAR: '#ef4444',
};

function vixInfo(vix) {
  if (vix == null) return { label: '—',       color: '#475569' };
  if (vix < 15)   return { label: 'CALM',     color: '#22c55e' };
  if (vix < 20)   return { label: 'NORMAL',   color: '#4ade80' };
  if (vix < 25)   return { label: 'ELEVATED', color: '#f59e0b' };
  if (vix < 30)   return { label: 'HIGH',     color: '#fb923c' };
  return              { label: 'EXTREME',  color: '#ef4444' };
}

function directionArrow(direction) {
  if (direction === 'UP')   return '↑';
  if (direction === 'DOWN') return '↓';
  return '→';
}

function timeAgo(unixSeconds) {
  if (!unixSeconds) return '';
  const diff = Math.floor(Date.now() / 1000) - unixSeconds;
  if (diff < 60)   return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function truncate(str, max) {
  if (!str) return '';
  return str.length <= max ? str : str.slice(0, max) + '…';
}

export default function PreMarketPanel({ session, ticks }) {
  const vix       = session?.india_vix;
  const biasLabel = session?.bias_label ?? 'NEUTRAL';
  const biasScore = session?.bias_score ?? 0;
  const biasColor = BIAS_COLOR[biasLabel] ?? '#94a3b8';
  const vi        = vixInfo(vix);
  const phase     = session?.phase ?? 'INIT';
  const mode      = session?.mode  ?? 'OBSERVE';

  // Compute gap from ticks if available
  const niftyPrice = ticks?.NIFTY?.price;

  const scorePct = Math.min(100, Math.abs(biasScore));

  // New fields
  const globalSnapshots = session?.global_snapshots ?? {};
  const news            = session?.news ?? [];
  const scoreBreakdown  = session?.score_breakdown ?? {};
  const pcr             = session?.pcr;
  const maxPain         = session?.max_pain;
  const fiiNetCr        = session?.fii_net_cr;
  const vixTrend        = session?.vix_trend;
  const highRiskEvents  = session?.high_risk_events ?? [];

  const globalKeys = Object.keys(globalSnapshots);
  const breakdownEntries = Object.entries(scoreBreakdown);
  const breakdownTotal   = breakdownEntries.reduce((s, [, v]) => s + Math.abs(v), 0) || 1;
  const newsSlice        = news.slice(0, 6);

  return (
    <div style={styles.panel}>
      <div style={styles.header}>PRE-MARKET BIAS</div>

      <div style={styles.row}>
        {/* Bias tile */}
        <div style={styles.tile}>
          <div style={styles.tileLabel}>BIAS</div>
          <div style={{ ...styles.tileBig, color: biasColor }}>
            {biasLabel.replace('_', ' ')}
          </div>
          <div style={styles.barTrack}>
            <div style={{ ...styles.barFill, width: scorePct + '%', background: biasColor }} />
          </div>
          <div style={{ fontSize: 9, color: biasColor, textAlign: 'center' }}>
            {biasScore !== 0 ? biasScore.toFixed(0) : '—'} / 100
          </div>
        </div>

        {/* VIX tile */}
        <div style={styles.tile}>
          <div style={styles.tileLabel}>INDIA VIX</div>
          <div style={{ ...styles.tileBig, color: vi.color }}>
            {vix != null ? vix.toFixed(2) : '—'}
          </div>
          <div style={{ ...styles.badge, color: vi.color, background: vi.color + '22' }}>
            {vi.label}
          </div>
        </div>

        {/* NIFTY tile */}
        <div style={styles.tile}>
          <div style={styles.tileLabel}>NIFTY</div>
          <div style={{ ...styles.tileBig, color: '#e2e8f0', fontSize: 13 }}>
            {niftyPrice != null ? niftyPrice.toLocaleString('en-IN', { maximumFractionDigits: 0 }) : '—'}
          </div>
          <div style={{ ...styles.badge, color: '#475569', background: '#1e293b' }}>
            {mode}
          </div>
        </div>
      </div>

      {/* Checklist */}
      <div style={styles.checkTitle}>READINESS</div>
      <div style={styles.checkGrid}>
        <CheckItem label="Pre-market loaded"  ok={biasScore !== 0} />
        <CheckItem label="India VIX fetched"  ok={vix != null} />
        <CheckItem label="VIX below danger"   ok={vix != null && vix < 25} warn={vix != null && vix >= 25} />
        <CheckItem label="Session entered"    ok={phase === 'ACTIVE' || phase === 'TRADING'} />
        <CheckItem label="Bias available"     ok={biasLabel !== 'NEUTRAL'} />
      </div>

      {/* ── High Risk Events banner ─────────────────────────────────── */}
      {highRiskEvents.length > 0 && (
        <div style={styles.riskBanner}>
          <span style={styles.riskIcon}>⚠</span>
          <span style={styles.riskText}>
            HIGH-RISK EVENTS: {highRiskEvents.join(' · ')}
          </span>
        </div>
      )}

      {/* ── Global Indicators ───────────────────────────────────────── */}
      {globalKeys.length > 0 && (
        <div>
          <div style={styles.sectionHeader}>GLOBAL INDICATORS</div>
          <div style={styles.globalGrid}>
            {globalKeys.map(key => {
              const snap = globalSnapshots[key];
              const hasError   = snap.error != null || snap.change_pct == null;
              const changePct  = snap.change_pct;
              const dir        = snap.direction ?? 'FLAT';
              const arrow      = directionArrow(dir);
              const chgColor   = hasError
                ? '#475569'
                : dir === 'UP'   ? '#22c55e'
                : dir === 'DOWN' ? '#ef4444'
                : '#94a3b8';
              const shortName  = snap.name ?? key;

              return (
                <div key={key} style={styles.globalItem}>
                  <div style={styles.globalName}>{shortName}</div>
                  <div style={{ color: chgColor, fontSize: 11, fontWeight: 700, fontFamily: '"Courier New", monospace' }}>
                    {hasError
                      ? '—'
                      : `${arrow} ${changePct >= 0 ? '+' : ''}${changePct.toFixed(2)}%`}
                  </div>
                  {snap.price != null && !hasError && (
                    <div style={styles.globalPrice}>
                      {snap.price.toLocaleString('en-IN', { maximumFractionDigits: 2 })}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* ── India Context ────────────────────────────────────────────── */}
      {(pcr != null || maxPain != null || fiiNetCr != null || vixTrend != null) && (
        <div>
          <div style={styles.sectionHeader}>INDIA CONTEXT</div>
          <div style={styles.contextRow}>
            <ContextItem label="PCR"       value={pcr       != null ? pcr.toFixed(2)       : '—'} />
            <ContextItem label="MAX PAIN"  value={maxPain   != null ? maxPain.toLocaleString('en-IN') : '—'} />
            <ContextItem label="FII NET"   value={fiiNetCr  != null ? `₹${fiiNetCr.toFixed(0)} Cr` : '—'}
                         color={fiiNetCr != null ? (fiiNetCr >= 0 ? '#22c55e' : '#ef4444') : undefined} />
            <ContextItem label="VIX TREND" value={vixTrend  ?? '—'}
                         color={vixTrend === 'FALLING' ? '#22c55e' : vixTrend === 'RISING' ? '#ef4444' : '#94a3b8'} />
          </div>
        </div>
      )}

      {/* ── Score Breakdown ──────────────────────────────────────────── */}
      {breakdownEntries.length > 0 && (
        <div>
          <div style={styles.sectionHeader}>SCORE BREAKDOWN</div>
          <div style={styles.chipsRow}>
            {breakdownEntries.map(([factor, val]) => {
              const pct     = (Math.abs(val) / breakdownTotal) * 100;
              const chipClr = val > 0 ? '#22c55e' : val < 0 ? '#ef4444' : '#475569';
              return (
                <div key={factor} style={{ ...styles.chip, borderColor: chipClr + '55' }}>
                  <div style={{ ...styles.chipLabel }}>{factor.replace(/_/g, ' ')}</div>
                  <div style={{ ...styles.chipVal, color: chipClr }}>
                    {val > 0 ? '+' : ''}{val}
                  </div>
                  <div style={styles.chipBarTrack}>
                    <div style={{ ...styles.chipBarFill, width: pct + '%', background: chipClr }} />
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* ── News ─────────────────────────────────────────────────────── */}
      {newsSlice.length > 0 && (
        <div>
          <div style={styles.sectionHeader}>NEWS</div>
          <div style={styles.newsCol}>
            {newsSlice.map((item, i) => (
              <div key={i} style={styles.newsItem}>
                <div style={styles.newsTitle}>{truncate(item.title, 80)}</div>
                <div style={styles.newsMeta}>
                  <span style={styles.newsPublisher}>{item.publisher}</span>
                  {item.time && (
                    <span style={styles.newsTime}>{timeAgo(item.time)}</span>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function CheckItem({ label, ok, warn }) {
  const icon  = ok ? '✓' : warn ? '⚠' : '○';
  const color = ok ? '#22c55e' : warn ? '#f59e0b' : '#334155';
  return (
    <div style={styles.checkItem}>
      <span style={{ ...styles.checkIcon, color }}>{icon}</span>
      <span style={{ fontSize: 10, color: ok || warn ? '#94a3b8' : '#334155' }}>{label}</span>
    </div>
  );
}

function ContextItem({ label, value, color }) {
  return (
    <div style={styles.contextItem}>
      <div style={styles.contextLabel}>{label}</div>
      <div style={{ ...styles.contextValue, color: color ?? '#e2e8f0' }}>{value}</div>
    </div>
  );
}

const styles = {
  panel: {
    background: '#0f172a', border: '1px solid #1e293b',
    borderRadius: 8, padding: 12,
    display: 'flex', flexDirection: 'column', gap: 10,
    fontFamily: '"Courier New", monospace',
  },
  header: { fontSize: 10, fontWeight: 700, letterSpacing: 2, color: '#475569' },
  row: { display: 'flex', gap: 8 },
  tile: {
    flex: 1, background: '#1e293b', borderRadius: 6, padding: '8px 10px',
    display: 'flex', flexDirection: 'column', gap: 4, alignItems: 'center',
    border: '1px solid #334155',
  },
  tileLabel: { fontSize: 8, color: '#475569', letterSpacing: 2 },
  tileBig:   { fontSize: 15, fontWeight: 700, letterSpacing: 1 },
  barTrack: {
    width: '80%', height: 4, background: '#0f172a',
    borderRadius: 99, overflow: 'hidden',
  },
  barFill:  { height: 4, borderRadius: 99, transition: 'width 0.5s' },
  badge: {
    fontSize: 9, fontWeight: 700, letterSpacing: 1,
    padding: '2px 8px', borderRadius: 99,
  },
  checkTitle: {
    fontSize: 9, fontWeight: 700, letterSpacing: 2, color: '#334155',
  },
  checkGrid: { display: 'flex', flexDirection: 'column', gap: 3 },
  checkItem: { display: 'flex', alignItems: 'center', gap: 8 },
  checkIcon: { fontSize: 10, width: 14, textAlign: 'center', fontWeight: 700, flexShrink: 0 },

  // ── shared section header ────────────────────────────────────────
  sectionHeader: {
    fontSize: 9, fontWeight: 700, letterSpacing: 2, color: '#475569',
    marginBottom: 5,
  },

  // ── high-risk events banner ──────────────────────────────────────
  riskBanner: {
    display: 'flex', alignItems: 'center', gap: 8,
    background: '#ef444418', border: '1px solid #ef444455',
    borderRadius: 5, padding: '5px 10px',
  },
  riskIcon: { fontSize: 12, color: '#ef4444' },
  riskText: { fontSize: 10, color: '#ef4444', fontWeight: 700, letterSpacing: 1 },

  // ── global indicators ────────────────────────────────────────────
  globalGrid: {
    display: 'flex', flexWrap: 'wrap', gap: 5,
  },
  globalItem: {
    background: '#1e293b', border: '1px solid #334155',
    borderRadius: 5, padding: '5px 8px',
    minWidth: 90, flex: '1 1 90px',
    display: 'flex', flexDirection: 'column', gap: 2,
  },
  globalName: {
    fontSize: 8, color: '#64748b', letterSpacing: 1, fontWeight: 700,
  },
  globalPrice: {
    fontSize: 9, color: '#475569',
  },

  // ── india context ────────────────────────────────────────────────
  contextRow: {
    display: 'flex', gap: 6, flexWrap: 'wrap',
  },
  contextItem: {
    background: '#1e293b', border: '1px solid #334155',
    borderRadius: 5, padding: '5px 10px',
    flex: '1 1 80px',
    display: 'flex', flexDirection: 'column', gap: 2, alignItems: 'center',
  },
  contextLabel: {
    fontSize: 8, color: '#64748b', letterSpacing: 1, fontWeight: 700,
  },
  contextValue: {
    fontSize: 12, fontWeight: 700, color: '#e2e8f0',
  },

  // ── score breakdown chips ────────────────────────────────────────
  chipsRow: {
    display: 'flex', flexWrap: 'wrap', gap: 5,
  },
  chip: {
    background: '#1e293b', border: '1px solid #334155',
    borderRadius: 5, padding: '4px 8px',
    minWidth: 80, flex: '1 1 80px',
    display: 'flex', flexDirection: 'column', gap: 2,
  },
  chipLabel: {
    fontSize: 8, color: '#64748b', letterSpacing: 1, fontWeight: 700,
    textTransform: 'uppercase',
  },
  chipVal: {
    fontSize: 11, fontWeight: 700,
  },
  chipBarTrack: {
    width: '100%', height: 3, background: '#0f172a',
    borderRadius: 99, overflow: 'hidden', marginTop: 2,
  },
  chipBarFill: {
    height: 3, borderRadius: 99, transition: 'width 0.5s',
  },

  // ── news ─────────────────────────────────────────────────────────
  newsCol: {
    display: 'flex', flexDirection: 'column', gap: 5,
  },
  newsItem: {
    background: '#1e293b', border: '1px solid #334155',
    borderRadius: 5, padding: '5px 8px',
  },
  newsTitle: {
    fontSize: 10, color: '#e2e8f0', lineHeight: 1.4,
  },
  newsMeta: {
    display: 'flex', gap: 8, marginTop: 2,
  },
  newsPublisher: {
    fontSize: 9, color: '#3b82f6', fontWeight: 700,
  },
  newsTime: {
    fontSize: 9, color: '#475569',
  },
};
