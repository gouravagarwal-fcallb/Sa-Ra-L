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
  const ts    = entry.ts ? new Date(entry.ts * 1000).toTimeString().slice(0, 8) : '--:--:--';

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
    background: '#0f172a', border: '1px solid #1e293b',
    borderRadius: 8, padding: 12,
    display: 'flex', flexDirection: 'column', gap: 8,
    minHeight: 200,
  },
  headerRow: {
    display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap',
  },
  title: { fontSize: 10, fontWeight: 700, letterSpacing: 2, color: '#475569' },
  filters: { display: 'flex', gap: 4, flexWrap: 'wrap', flex: 1 },
  chip: {
    background: 'transparent', border: '1px solid #334155',
    color: '#475569', fontSize: 8, fontWeight: 700,
    padding: '1px 6px', borderRadius: 3, cursor: 'pointer', letterSpacing: 1,
  },
  chipActive: { background: '#1e293b', borderColor: '#475569', color: '#94a3b8' },
  resumeBtn: {
    background: '#f59e0b22', border: '1px solid #f59e0b',
    color: '#f59e0b', fontSize: 8, fontWeight: 700,
    padding: '2px 8px', borderRadius: 3, cursor: 'pointer', letterSpacing: 1,
  },
  logBox: {
    overflowY: 'auto', maxHeight: 300,
    background: '#020617', borderRadius: 4, padding: '6px 8px',
    fontFamily: '"Courier New", monospace',
  },
  line: {
    display: 'flex', gap: 8, padding: '1px 0',
    borderBottom: '1px solid #0f172a',
  },
  ts:  { fontSize: 9, color: '#334155', flexShrink: 0 },
  cat: { fontSize: 9, fontWeight: 700, flexShrink: 0, width: 70 },
  msg: { fontSize: 9, color: '#94a3b8', wordBreak: 'break-all' },
  empty: { color: '#334155', fontSize: 10, textAlign: 'center', padding: 16 },
};
