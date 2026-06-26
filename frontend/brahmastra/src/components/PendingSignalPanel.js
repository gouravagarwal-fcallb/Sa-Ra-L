import React, { useState, useEffect } from 'react';

function useCountdown(expiresAt) {
  const [secondsLeft, setSecondsLeft] = useState(null);

  useEffect(() => {
    if (!expiresAt) { setSecondsLeft(null); return; }

    function calc() {
      const now = Date.now();
      const exp = new Date(expiresAt).getTime();
      const diff = Math.max(0, Math.round((exp - now) / 1000));
      setSecondsLeft(diff);
    }

    calc();
    const iv = setInterval(calc, 1000);
    return () => clearInterval(iv);
  }, [expiresAt]);

  return secondsLeft;
}

function SignalCard({ instrument, signal, onApprove, onReject }) {
  const [confirming, setConfirming] = useState(null); // 'approve' | 'reject' | null
  const [result, setResult]         = useState(null); // 'approved' | 'rejected' | null
  const [loading, setLoading]       = useState(false);

  const secondsLeft = useCountdown(signal.expires_at);

  const isUrgent = secondsLeft != null && secondsLeft < 180; // < 3 min
  const hypoColor = signal.hypothesis === 'BULL' ? '#22c55e' : '#ef4444';
  const hypoArrow = signal.hypothesis === 'BULL' ? '▲' : '▼';

  async function handleConfirm(action) {
    setLoading(true);
    try {
      const url = action === 'approve' ? `/api/approve/${instrument}` : `/api/reject/${instrument}`;
      const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' } });
      if (res.ok) {
        setResult(action === 'approve' ? 'approved' : 'rejected');
        if (action === 'approve') {
          onApprove && onApprove(instrument);
        } else {
          onReject && onReject(instrument);
        }
      } else {
        setResult('error');
      }
    } catch (e) {
      setResult('error');
    }
    setLoading(false);
    setConfirming(null);
  }

  if (result === 'approved') {
    return (
      <div style={styles.card}>
        <div style={{ ...styles.resultMsg, color: '#22c55e' }}>
          ✓ Approved — {instrument} {signal.hypothesis}
        </div>
      </div>
    );
  }

  const timeDisplay = secondsLeft != null
    ? `${Math.floor(secondsLeft / 60)}m ${secondsLeft % 60}s`
    : (signal.minutes_left != null ? `${signal.minutes_left}m` : '—');

  const conf = Math.max(0, Math.min(100, signal.confidence ?? 0));

  return (
    <div style={{ ...styles.card, boxShadow: '0 0 12px #f59e0b44' }}>
      {/* Card header row */}
      <div style={styles.cardTop}>
        <span style={styles.instrBadge}>{instrument}</span>
        <span style={{ ...styles.hypoBadge, color: hypoColor, background: hypoColor + '22' }}>
          {hypoArrow} {signal.hypothesis}
        </span>
        <span style={styles.lotsLabel}>
          {signal.lots != null ? `${signal.lots} lot${signal.lots !== 1 ? 's' : ''}` : ''}
        </span>
        <span style={{
          ...styles.timerBadge,
          color:      isUrgent ? '#ef4444' : '#f59e0b',
          background: isUrgent ? '#ef444422' : '#f59e0b22',
          border:     `1px solid ${isUrgent ? '#ef444466' : '#f59e0b66'}`,
        }}>
          ⏱ {timeDisplay}
        </span>
      </div>

      {/* Price levels row */}
      <div style={styles.levelsRow}>
        <PriceLevel label="ENTRY"  val={signal.entry_price}  color="#e2e8f0" />
        <PriceLevel label="SL"     val={signal.sl_price}     color="#ef4444" />
        <PriceLevel label="T1"     val={signal.target1}      color="#22c55e" />
      </div>

      {/* Confidence bar */}
      <div style={styles.confRow}>
        <span style={styles.confLabel}>CONF</span>
        <div style={styles.barTrack}>
          <div style={{
            ...styles.barFill,
            width: conf + '%',
            background: conf >= 80 ? '#22c55e' : conf >= 65 ? '#f59e0b' : '#3b82f6',
          }} />
        </div>
        <span style={styles.confPct}>{conf.toFixed(1)}%</span>
      </div>

      {/* Armed/Expires times */}
      {(signal.armed_at || signal.expires_at) && (
        <div style={styles.timesRow}>
          {signal.armed_at  && <span style={styles.timeLabel}>Armed: {signal.armed_at}</span>}
          {signal.expires_at && <span style={styles.timeLabel}>Expires: {signal.expires_at}</span>}
        </div>
      )}

      {/* Confirmation prompt */}
      {confirming && (
        <div style={styles.confirmBox}>
          <span style={styles.confirmText}>
            {confirming === 'approve' ? 'Approve' : 'Reject'} {instrument} {signal.hypothesis}?
          </span>
          <button
            style={styles.btnYes}
            onClick={() => handleConfirm(confirming)}
            disabled={loading}
          >
            {loading ? '...' : 'YES'}
          </button>
          <button style={styles.btnNo} onClick={() => setConfirming(null)} disabled={loading}>
            NO
          </button>
        </div>
      )}

      {/* Action buttons */}
      {!confirming && result !== 'approved' && result !== 'rejected' && (
        <div style={styles.actionRow}>
          <button style={styles.btnApprove} onClick={() => setConfirming('approve')}>
            ✓ APPROVE
          </button>
          <button style={styles.btnReject} onClick={() => setConfirming('reject')}>
            ✕ REJECT
          </button>
        </div>
      )}

      {result === 'error' && (
        <div style={{ fontSize: 12, color: '#ef4444', marginTop: 4 }}>
          Request failed. Try again.
        </div>
      )}
    </div>
  );
}

function PriceLevel({ label, val, color }) {
  if (val == null) return null;
  return (
    <div style={styles.levelItem}>
      <span style={styles.levelLabel}>{label}</span>
      <span style={{ ...styles.levelVal, color }}>
        {val.toLocaleString('en-IN', { maximumFractionDigits: 0 })}
      </span>
    </div>
  );
}

export default function PendingSignalPanel({ pendingSignals, executionMode }) {
  const signals = pendingSignals || {};
  const instruments = Object.keys(signals);

  // Only render in HUMAN_WATCH mode with at least one pending signal
  if (executionMode !== 'human_watch' || instruments.length === 0) {
    return null;
  }

  return (
    <div style={styles.panel}>
      <div style={styles.headerRow}>
        <span style={styles.header}>PENDING SIGNALS</span>
        <span style={styles.countBadge}>{instruments.length}</span>
        <span style={styles.watchBadge}>● HUMAN WATCH</span>
      </div>
      <div style={styles.cards}>
        {instruments.map(inst => (
          <SignalCard
            key={inst}
            instrument={inst}
            signal={signals[inst]}
          />
        ))}
      </div>
    </div>
  );
}

const styles = {
  panel: {
    background: '#0f172a',
    border: '1px solid #f59e0b88',
    borderRadius: 8,
    padding: 12,
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
    boxShadow: '0 0 20px #f59e0b22',
  },
  headerRow: {
    display: 'flex', alignItems: 'center', gap: 8,
  },
  header: {
    fontSize: 12, fontWeight: 700, letterSpacing: 2, color: '#f59e0b',
  },
  countBadge: {
    background: '#f59e0b22', color: '#f59e0b',
    borderRadius: 99, padding: '1px 8px', fontSize: 11, fontWeight: 700,
  },
  watchBadge: {
    marginLeft: 'auto',
    fontSize: 11, fontWeight: 700, letterSpacing: 1,
    color: '#f59e0b',
  },
  cards: {
    display: 'flex', flexDirection: 'column', gap: 8,
  },
  card: {
    background: '#1e293b',
    border: '1px solid #f59e0b44',
    borderRadius: 6,
    padding: 12,
    display: 'flex', flexDirection: 'column', gap: 7,
  },
  cardTop: {
    display: 'flex', alignItems: 'center', gap: 7, flexWrap: 'wrap',
  },
  instrBadge: {
    fontSize: 13, fontWeight: 700, color: '#e2e8f0',
    background: '#0f172a', padding: '2px 10px', borderRadius: 4,
    letterSpacing: 1,
  },
  hypoBadge: {
    fontSize: 12, fontWeight: 700, letterSpacing: 1,
    padding: '2px 10px', borderRadius: 99,
  },
  lotsLabel: {
    fontSize: 11, color: '#64748b', letterSpacing: 1,
  },
  timerBadge: {
    marginLeft: 'auto',
    fontSize: 11, fontWeight: 700, letterSpacing: 0.5,
    padding: '2px 10px', borderRadius: 99,
  },
  levelsRow: {
    display: 'flex', gap: 14, flexWrap: 'wrap',
  },
  levelItem: {
    display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 1,
  },
  levelLabel: {
    fontSize: 10, color: '#475569', letterSpacing: 1,
  },
  levelVal: {
    fontSize: 13, fontWeight: 700, fontFamily: '"Courier New", monospace',
  },
  confRow: {
    display: 'flex', alignItems: 'center', gap: 6,
  },
  confLabel: {
    fontSize: 10, color: '#475569', letterSpacing: 1, width: 34, flexShrink: 0,
  },
  barTrack: {
    flex: 1, height: 5, background: '#0f172a',
    borderRadius: 99, overflow: 'hidden',
  },
  barFill: {
    height: 5, borderRadius: 99,
    transition: 'width 0.4s ease',
  },
  confPct: {
    fontSize: 11, fontWeight: 700, color: '#94a3b8', width: 40, textAlign: 'right',
  },
  timesRow: {
    display: 'flex', gap: 12, flexWrap: 'wrap',
  },
  timeLabel: {
    fontSize: 11, color: '#475569', letterSpacing: 0.5,
  },
  confirmBox: {
    background: '#0f172a', borderRadius: 5, padding: '7px 10px',
    display: 'flex', alignItems: 'center', gap: 8,
    border: '1px solid #f59e0b44',
  },
  confirmText: {
    fontSize: 13, color: '#e2e8f0', flex: 1,
  },
  btnYes: {
    background: '#22c55e22', border: '1px solid #22c55e88', color: '#22c55e',
    fontSize: 12, fontWeight: 700, padding: '4px 14px', borderRadius: 4, cursor: 'pointer',
  },
  btnNo: {
    background: '#ef444422', border: '1px solid #ef444488', color: '#ef4444',
    fontSize: 12, fontWeight: 700, padding: '4px 14px', borderRadius: 4, cursor: 'pointer',
  },
  actionRow: {
    display: 'flex', gap: 8,
  },
  btnApprove: {
    flex: 1,
    background: '#22c55e22', border: '1px solid #22c55e88', color: '#22c55e',
    fontSize: 12, fontWeight: 700, letterSpacing: 1,
    padding: '7px 0', borderRadius: 4, cursor: 'pointer',
  },
  btnReject: {
    flex: 1,
    background: '#ef444422', border: '1px solid #ef444488', color: '#ef4444',
    fontSize: 12, fontWeight: 700, letterSpacing: 1,
    padding: '7px 0', borderRadius: 4, cursor: 'pointer',
  },
  resultMsg: {
    fontSize: 13, fontWeight: 700, textAlign: 'center', padding: '8px 0',
  },
};
