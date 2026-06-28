import React, { useState, useEffect, useRef } from 'react';

const CATEGORIES = ['ALL', 'TICK', 'BAR', 'ANALYSE', 'TRADE', 'SCENARIO', 'SYSTEM', 'ERROR'];

const CAT_COLOR = {
  TICK:     '#475569',
  BAR:      '#64748b',
  ANALYSE:  '#3b82f6',
  TRADE:    '#22c55e',
  SCENARIO: '#f59e0b',
  SYSTEM:   '#94a3b8',
  ERROR:    '#ef4444',
  INFO:     '#64748b',
};

export default function LogStream({ logs }) {
  const [filter, setFilter] = useState('ALL');
  const [paused, setPaused] = useState(false);
  const bottomRef = useRef(null);
  const containerRef = useRef(null);

  const entries = logs || [];
  const filtered = filter === 'ALL'
    ? entries
    : entries.filter(l => (l.category || '').toUpperCase() === filter);

  useEffect(() => {
    if (!paused && bottomRef.current) {
      bottomRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [filtered.length, paused]);

  function handleScroll() {
    const el = containerRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    setPaused(!atBottom);
  }

  return (
    <div style={styles.panel}>
      <div style={styles.headerRow}>
        <span style={styles.title}>LOG STREAM</span>
        <div style={styles.filters}>
          {CATEGORIES.map(c => (
            <button key={c}
                    style={{ ...styles.chip, ...(filter === c ? styles.chipActive : {}) }}
                    onClick={() => setFilter(c)}>
              {c}
            </button>
          ))}
        </div>
        {paused && (
          <button style={styles.resumeBtn} onClick={() => setPaused(false)}>
            ▼ RESUME
          </button>
        )}
      </div>

      <div ref={containerRef} style={styles.logBox} onScroll={handleScroll}>
        {filtered.length === 0
          ? <div style={styles.empty}>No log entries</div>
          : filtered.slice(-500).map((l, i) => <LogLine key={i} entry={l} />)
        }
        <div ref={bottomRef} />
      </div>
    </div>
  );
}

function LogLine({ entry }) {
  const cat   = (entry.category || 'INFO').toUpperCase();
  const color = CAT_COLOR[cat] ?? '#64748b';
  const ts    = entry.ts || '--:--:--';

  return (
    <div style={styles.line}>
      <span style={styles.ts}>{ts}</span>
      <span style={{ ...styles.cat, color }}>{cat.padEnd(8)}</span>
      <span style={styles.msg}>{entry.message || ''}</span>
    </div>
  );
}

const styles = {
  panel: {
    background: '#ffffff', border: '1px solid #dde5ef',
    borderRadius: 10, padding: 14,
    display: 'flex', flexDirection: 'column', gap: 8,
    minHeight: 200, boxShadow: '0 1px 3px rgba(15,23,42,0.08)',
  },
  headerRow: {
    display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap',
  },
  title: { fontSize: 13, fontWeight: 700, letterSpacing: 1, color: '#1f2a3a' },
  filters: { display: 'flex', gap: 4, flexWrap: 'wrap', flex: 1 },
  chip: {
    background: '#f4f7fc', border: '1px solid #dde5ef',
    color: '#5b6b82', fontSize: 11, fontWeight: 700,
    padding: '3px 9px', borderRadius: 4, cursor: 'pointer', letterSpacing: 0.5,
  },
  chipActive: { background: '#e8f1fb', borderColor: '#2563eb', color: '#2563eb' },
  resumeBtn: {
    background: '#fef3e2', border: '1px solid #d97706',
    color: '#d97706', fontSize: 11, fontWeight: 700,
    padding: '3px 9px', borderRadius: 4, cursor: 'pointer', letterSpacing: 0.5,
  },
  logBox: {
    overflowY: 'auto', maxHeight: 380,
    background: '#f8fafc', border: '1px solid #eef2f8', borderRadius: 6, padding: '6px 8px',
    fontFamily: '"Courier New", monospace',
  },
  line: {
    display: 'flex', gap: 8, padding: '3px 0',
    borderBottom: '1px solid #eef2f8',
  },
  ts:  { fontSize: 12, color: '#5b6b82', flexShrink: 0 },
  cat: { fontSize: 12, fontWeight: 700, flexShrink: 0, width: 80 },
  msg: { fontSize: 12.5, color: '#1f2a3a', wordBreak: 'break-all' },
  empty: { color: '#5b6b82', fontSize: 13, textAlign: 'center', padding: 16 },
};
