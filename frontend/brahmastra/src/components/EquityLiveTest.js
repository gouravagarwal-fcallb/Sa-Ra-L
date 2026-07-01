import React, { useState } from 'react';
import { api, C } from '../api';

/**
 * Broker live-test — places ONE real equity order (default: buy 1 Tata Power,
 * NSE, CNC delivery, market) to verify end-to-end order connectivity without
 * running a strategy. Guarded by the same arm → type-exact-phrase flow as live
 * strategies. Nothing fires until you arm AND type the phrase AND press Place.
 */
export default function EquityLiveTest() {
  const [params, setParams] = useState({
    symbol: 'TATAPOWER', exchange: 'NSE', transaction: 'BUY',
    quantity: 1, product: 'CNC', order_type: 'MARKET',
  });
  const [armed, setArmed] = useState(null);   // {confirm_token, required_phrase, warning, ttl}
  const [typed, setTyped] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);       // {ok, text}

  const arm = () => {
    setBusy(true); setMsg(null); setArmed(null); setTyped('');
    api.equityTestArm(params)
      .then(r => setArmed(r))
      .catch(e => setMsg({ ok: false, text: `Cannot arm: ${String(e)}` }))
      .finally(() => setBusy(false));
  };
  const cancel = () => { setArmed(null); setTyped(''); setMsg(null); };
  const place = () => {
    if (!armed) return;
    setBusy(true); setMsg(null);
    api.equityTestConfirm(armed.confirm_token, typed.trim())
      .then(r => { setMsg({ ok: true, text: `✓ Order sent — order_id ${r.order_id}. Verify in your Kite orderbook.` }); setArmed(null); setTyped(''); })
      .catch(e => setMsg({ ok: false, text: `Order failed: ${String(e)}` }))
      .finally(() => setBusy(false));
  };

  const set = (k, v) => setParams(p => ({ ...p, [k]: v }));
  const phraseOk = armed && typed.trim() === armed.required_phrase;

  return (
    <div style={S.card}>
      <div style={S.head}>
        <span style={{ fontWeight: 800, color: C.text }}>🧪 Broker live-test — place 1 real share</span>
        <span style={S.real}>REAL MONEY</span>
      </div>
      <div style={S.desc}>
        Verifies that a real order reaches Zerodha end-to-end, using the smallest possible trade.
        Guarded: you must arm, then type the exact phrase. Needs Kite connected and market open.
      </div>

      {!armed ? (
        <>
          <div style={S.grid}>
            <Field label="Symbol"><input style={S.input} value={params.symbol}
              onChange={e => set('symbol', e.target.value.toUpperCase())} /></Field>
            <Field label="Exchange">
              <select style={S.input} value={params.exchange} onChange={e => set('exchange', e.target.value)}>
                <option>NSE</option><option>BSE</option></select></Field>
            <Field label="Qty (1–5)"><input style={S.input} type="number" min="1" max="5" value={params.quantity}
              onChange={e => set('quantity', Math.max(1, Math.min(5, Number(e.target.value) || 1)))} /></Field>
            <Field label="Product">
              <select style={S.input} value={params.product} onChange={e => set('product', e.target.value)}>
                <option value="CNC">CNC (delivery)</option><option value="MIS">MIS (intraday)</option></select></Field>
            <Field label="Order type">
              <select style={S.input} value={params.order_type} onChange={e => set('order_type', e.target.value)}>
                <option>MARKET</option><option>LIMIT</option></select></Field>
          </div>
          <button style={{ ...S.armBtn, opacity: busy ? 0.6 : 1 }} disabled={busy} onClick={arm}>
            {busy ? 'Arming…' : `Arm: ${params.transaction} ${params.quantity} ${params.symbol}`}
          </button>
        </>
      ) : (
        <div style={S.confirmBox}>
          <div style={{ color: '#b45309', fontWeight: 700, fontSize: 12.5, marginBottom: 8 }}>⚠ {armed.warning}</div>
          <div style={{ fontSize: 12, color: C.dim, marginBottom: 4 }}>
            Type <code style={{ color: C.text, fontWeight: 700 }}>{armed.required_phrase}</code> to confirm
            (expires in {armed.ttl_seconds}s):</div>
          <input style={{ ...S.input, width: '100%', marginBottom: 8 }} autoFocus value={typed}
            placeholder={armed.required_phrase} onChange={e => setTyped(e.target.value)} />
          <div style={{ display: 'flex', gap: 8 }}>
            <button style={{ ...S.placeBtn, opacity: (phraseOk && !busy) ? 1 : 0.5 }}
              disabled={!phraseOk || busy} onClick={place}>
              {busy ? 'Placing…' : '● PLACE REAL ORDER'}</button>
            <button style={S.cancelBtn} disabled={busy} onClick={cancel}>Cancel</button>
          </div>
        </div>
      )}

      {msg && <div style={{ ...S.msg, ...(msg.ok ? S.msgOk : S.msgErr) }} onClick={() => setMsg(null)}>{msg.text}</div>}
    </div>
  );
}

function Field({ label, children }) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      <span style={{ fontSize: 10.5, color: C.dim, fontWeight: 700 }}>{label}</span>
      {children}
    </label>
  );
}

const S = {
  card: { background: C.panel, border: `1px solid #fed7aa`, borderRadius: 10, padding: 14, marginBottom: 14 },
  head: { display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 },
  real: { fontSize: 10, fontWeight: 800, color: '#fff', background: C.red, borderRadius: 8, padding: '2px 8px', letterSpacing: 0.5 },
  desc: { fontSize: 12, color: C.dim, marginBottom: 10, lineHeight: 1.5 },
  grid: { display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 10 },
  input: { fontSize: 13, padding: '5px 8px', borderRadius: 6, border: `1px solid ${C.border}`, background: '#fff', color: C.text, minWidth: 90 },
  armBtn: { background: '#fff7ed', border: `1px solid ${C.amber}`, color: '#b45309', fontWeight: 700, fontSize: 13, padding: '8px 16px', borderRadius: 6, cursor: 'pointer' },
  confirmBox: { background: '#fffbeb', border: '1px solid #fde68a', borderRadius: 8, padding: 12 },
  placeBtn: { background: C.red, border: 'none', color: '#fff', fontWeight: 800, fontSize: 13, padding: '8px 16px', borderRadius: 6, cursor: 'pointer', letterSpacing: 0.3 },
  cancelBtn: { background: '#fff', border: `1px solid ${C.border}`, color: C.dim, fontWeight: 700, fontSize: 13, padding: '8px 16px', borderRadius: 6, cursor: 'pointer' },
  msg: { marginTop: 10, padding: '8px 12px', borderRadius: 6, fontSize: 12.5, fontWeight: 700, cursor: 'pointer' },
  msgOk: { background: '#f0fdf4', color: '#15803d', border: '1px solid #bbf7d0' },
  msgErr: { background: '#fef2f2', color: '#b91c1c', border: '1px solid #fecaca' },
};
