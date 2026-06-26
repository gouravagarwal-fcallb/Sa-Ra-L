import React, { useState } from 'react';

const TIER_COLOR = {
  QUIET:  '#64748b',
  WATCH:  '#3b82f6',
  ALERT:  '#f59e0b',
  SIGNAL: '#22c55e',
};

const TIER_BG = {
  QUIET:  '#64748b22',
  WATCH:  '#3b82f622',
  ALERT:  '#f59e0b22',
  SIGNAL: '#22c55e22',
};

function scoreArrow(dir) {
  if (dir === 'RISING')  return '↑';
  if (dir === 'FALLING') return '↓';
  return '→';
}

export default function NarratorPanel({ narrator }) {
  const [activeTab, setActiveTab] = useState('NIFTY');
  const [expandedIdx, setExpandedIdx] = useState(null);

  const entries = (narrator && narrator[activeTab]) ? narrator[activeTab] : [];
  const hasSignal = entries.some(e => e.alert_tier === 'SIGNAL');

  const panelBorder = hasSignal
    ? '1px solid #22c55e88'
    : '1px solid #1e293b';
  const panelShadow = hasSignal
    ? '0 0 16px #22c55e33'
    : undefined;

  const instruments = ['NIFTY', 'SENSEX'];

  return (
    <div style={{ ...styles.panel, border: panelBorder, boxShadow: panelShadow }}>
      <div style={styles.headerRow}>
        <span style={styles.header}>MARKET NARRATOR</span>
        <div style={styles.tabs}>
          {instruments.map(inst => (
            <button
              key={inst}
              style={{
                ...styles.tab,
                ...(activeTab === inst ? styles.tabActive : {}),
              }}
              onClick={() => { setActiveTab(inst); setExpandedIdx(null); }}
            >
              {inst}
            </button>
          ))}
        </div>
      </div>

      {entries.length === 0 ? (
        <div style={styles.empty}>Waiting for first bar...</div>
      ) : (
        <div style={styles.entries}>
          {entries.slice(0, 5).map((entry, i) => {
            const tierColor = TIER_COLOR[entry.alert_tier] || '#64748b';
            const tierBg    = TIER_BG[entry.alert_tier]    || '#64748b22';
            const isExpanded = expandedIdx === i;
            const scoreVal  = entry.score != null ? entry.score.toFixed(1) : '—';
            const arrow     = scoreArrow(entry.score_dir);
            const scoreColor = entry.score > 0 ? '#22c55e' : entry.score < 0 ? '#ef4444' : '#94a3b8';

            return (
              <div
                key={i}
                style={{ ...styles.entryRow, ...(isExpanded ? styles.entryRowExpanded : {}) }}
                onClick={() => setExpandedIdx(isExpanded ? null : i)}
              >
                <div style={styles.entryTop}>
                  {/* Timestamp chip */}
                  <span style={styles.tsChip}>{entry.timestamp || '—'}</span>

                  {/* Tier badge */}
                  <span style={{
                    ...styles.tierBadge,
                    color: tierColor,
                    background: tierBg,
                  }}>
                    {entry.alert_tier || 'QUIET'}
                  </span>

                  {/* Score */}
                  <span style={{ ...styles.score, color: scoreColor }}>
                    {entry.score > 0 ? '+' : ''}{scoreVal}{arrow}
                  </span>

                  {/* Headline */}
                  <span style={styles.headline}>{entry.headline || '—'}</span>

                  {/* Expand indicator */}
                  {entry.detail && (
                    <span style={styles.expandIcon}>{isExpanded ? '▲' : '▼'}</span>
                  )}
                </div>

                {/* Expanded detail */}
                {isExpanded && entry.detail && (
                  <pre style={styles.detail}>{entry.detail}</pre>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

const styles = {
  panel: {
    background: '#0f172a',
    borderRadius: 8,
    padding: 12,
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
    transition: 'box-shadow 0.3s, border-color 0.3s',
  },
  headerRow: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 8,
  },
  header: {
    fontSize: 12, fontWeight: 700, letterSpacing: 2, color: '#475569',
  },
  tabs: { display: 'flex', gap: 4 },
  tab: {
    background: 'transparent',
    border: '1px solid #1e293b',
    color: '#475569',
    fontSize: 11, fontWeight: 700, letterSpacing: 1,
    padding: '2px 10px', borderRadius: 3, cursor: 'pointer',
  },
  tabActive: {
    borderColor: '#475569',
    color: '#94a3b8',
  },
  empty: {
    color: '#334155', fontSize: 12, textAlign: 'center',
    padding: '20px 0',
    fontFamily: '"Courier New", monospace',
  },
  entries: {
    display: 'flex', flexDirection: 'column', gap: 4,
  },
  entryRow: {
    background: '#1e293b',
    borderRadius: 5,
    padding: '7px 12px',
    cursor: 'pointer',
    border: '1px solid #334155',
    transition: 'background 0.15s',
  },
  entryRowExpanded: {
    background: '#162032',
    border: '1px solid #1e3a5f',
  },
  entryTop: {
    display: 'flex',
    alignItems: 'center',
    gap: 7,
    flexWrap: 'nowrap',
    overflow: 'hidden',
  },
  tsChip: {
    fontSize: 11, fontWeight: 700,
    background: '#0f172a', color: '#64748b',
    padding: '1px 7px', borderRadius: 3,
    flexShrink: 0,
    letterSpacing: 0.5,
  },
  tierBadge: {
    fontSize: 11, fontWeight: 700, letterSpacing: 1,
    padding: '2px 8px', borderRadius: 99,
    flexShrink: 0,
  },
  score: {
    fontSize: 12, fontWeight: 700,
    flexShrink: 0,
    letterSpacing: 0.5,
    fontFamily: '"Courier New", monospace',
  },
  headline: {
    fontSize: 12, color: '#94a3b8',
    flex: 1,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
    fontFamily: '"Courier New", monospace',
  },
  expandIcon: {
    fontSize: 11, color: '#475569', flexShrink: 0,
  },
  detail: {
    marginTop: 8,
    fontSize: 11,
    color: '#64748b',
    fontFamily: '"Courier New", monospace',
    whiteSpace: 'pre-wrap',
    wordBreak: 'break-word',
    background: '#0a0e1a',
    borderRadius: 4,
    padding: '8px 10px',
    border: '1px solid #1e293b',
    lineHeight: 1.6,
  },
};
