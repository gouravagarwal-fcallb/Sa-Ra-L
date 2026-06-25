import React, { useState } from 'react';

const MODE_COLOR = {
  LIVE:          '#ef4444',
  PAPER:         '#3b82f6',
  OBSERVE:       '#64748b',
  ARMED_CONFIRM: '#f59e0b',
  PAUSED:        '#94a3b8',
};

export default function ControlPanel({ session }) {
  const [confirming, setConfirming] = useState(null);
  const [feedback, setFeedback]     = useState(null);

  const mode  = session?.mode  || 'OBSERVE';
  const phase = session?.phase || 'INIT';

  async function send(cmd, extra = {}) {
    try {
      const res  = await fetch('/api/control', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ command: cmd, ...extra }),
      });
      const data = await res.json();
      setFeedback({ cmd, ok: res.ok, msg: data.message || data.detail || '' });
    } catch (e) {
      setFeedback({ cmd, ok: false, msg: e.message });
    }
    setConfirming(null);
  }

  const modeColor = MODE_COLOR[mode] ?? '#94a3b8';
  const isPaused  = phase === 'PAUSED';
  const isStopped = phase === 'STOPPED';

  return (
    <div style={styles.panel}>
      <div style={styles.header}>CONTROL PANEL</div>

      <div style={styles.modeRow}>
        <span style={styles.modeLabel}>MODE</span>
        <span style={{
          ...styles.modeBadge,
          background:  modeColor + '22',
          color:       modeColor,
          boxShadow:   `0 0 8px ${modeColor}44`,
        }}>
          {mode}
        </span>
        <span style={styles.phase}>{phase}</span>
      </div>

      {/* Confirmation prompt */}
      {confirming && (
        <div style={styles.confirmBox}>
          <span style={styles.confirmText}>
            Execute <strong>{confirming}</strong>?
          </span>
          <button style={styles.btnYes} onClick={() => send(confirming)}>YES</button>
          <button style={styles.btnNo}  onClick={() => setConfirming(null)}>NO</button>
        </div>
      )}

      <div style={styles.btnRow}>
        <CtrlBtn label="■ STOP"
                 color="#ef4444"
                 disabled={isStopped}
                 onClick={() => setConfirming('stop')} />
        <CtrlBtn label="⏸ PAUSE"
                 color="#f59e0b"
                 disabled={isPaused || isStopped}
                 onClick={() => setConfirming('pause')} />
        <CtrlBtn label="▶ RESUME"
                 color="#22c55e"
                 disabled={!isPaused}
                 onClick={() => send('resume')} />
        {mode === 'ARMED_CONFIRM' && (
          <CtrlBtn label="✓ CONFIRM"
                   color="#3b82f6"
                   onClick={() => send('confirm')} />
        )}
      </div>

      {feedback && (
        <div style={{ ...styles.feedback, color: feedback.ok ? '#22c55e' : '#ef4444' }}>
          {feedback.cmd}: {feedback.msg || (feedback.ok ? 'OK' : 'Failed')}
        </div>
      )}
    </div>
  );
}

function CtrlBtn({ label, color, onClick, disabled }) {
  return (
    <button
      style={{
        ...styles.btn,
        borderColor: disabled ? '#1e293b' : color + '88',
        color:       disabled ? '#334155' : color,
        cursor:      disabled ? 'not-allowed' : 'pointer',
      }}
      onClick={disabled ? undefined : onClick}
    >
      {label}
    </button>
  );
}

const styles = {
  panel: {
    background: '#0f172a', border: '1px solid #1e293b',
    borderRadius: 8, padding: 12,
    display: 'flex', flexDirection: 'column', gap: 8,
  },
  header: { fontSize: 10, fontWeight: 700, letterSpacing: 2, color: '#475569' },
  modeRow: { display: 'flex', alignItems: 'center', gap: 8 },
  modeLabel: { fontSize: 9, color: '#475569', letterSpacing: 1 },
  modeBadge: {
    fontSize: 10, fontWeight: 700, letterSpacing: 1,
    padding: '2px 10px', borderRadius: 99,
    transition: 'all 0.3s',
  },
  phase: { fontSize: 9, color: '#475569', marginLeft: 'auto' },
  confirmBox: {
    background: '#1e293b', borderRadius: 6, padding: '8px 12px',
    display: 'flex', alignItems: 'center', gap: 10,
    border: '1px solid #f59e0b44',
  },
  confirmText: { fontSize: 11, color: '#e2e8f0', flex: 1 },
  btnYes: {
    background: '#22c55e22', border: '1px solid #22c55e88', color: '#22c55e',
    fontSize: 10, fontWeight: 700, padding: '3px 12px', borderRadius: 4, cursor: 'pointer',
  },
  btnNo: {
    background: '#ef444422', border: '1px solid #ef444488', color: '#ef4444',
    fontSize: 10, fontWeight: 700, padding: '3px 12px', borderRadius: 4, cursor: 'pointer',
  },
  btnRow: { display: 'flex', gap: 6, flexWrap: 'wrap' },
  btn: {
    background: 'transparent', border: '1px solid',
    fontSize: 10, fontWeight: 700, letterSpacing: 1,
    padding: '4px 14px', borderRadius: 4,
    transition: 'opacity 0.2s',
  },
  feedback: { fontSize: 9, letterSpacing: 0.5, marginTop: 2 },
};
