import React, { useState } from 'react';
import { api, C } from '../api';

/**
 * Two-step real-money guard: ARM (server issues a one-time token + the exact
 * phrase) then CONFIRM (user types the phrase). Nothing goes live in one click.
 */
export default function LiveGuardModal({ strategy, onClose, onLive }) {
  const [step, setStep]   = useState('arming');   // arming | confirm | done | error
  const [info, setInfo]   = useState(null);
  const [typed, setTyped] = useState('');
  const [err, setErr]     = useState(null);

  const arm = () => {
    setStep('arming'); setErr(null);
    api.armLive(strategy)
      .then(d => { setInfo(d); setStep('confirm'); })
      .catch(e => { setErr(String(e)); setStep('error'); });
  };

  React.useEffect(arm, []);   // eslint-disable-line

  const confirm = () => {
    api.confirmLive(strategy, info.confirm_token, typed)
      .then(() => { setStep('done'); onLive && onLive(); })
      .catch(e => setErr(String(e)));
  };

  return (
    <div style={S.overlay} onClick={onClose}>
      <div style={S.modal} onClick={e => e.stopPropagation()}>
        <div style={S.head}>⚠ GO LIVE — {strategy}</div>
        {step === 'error' && <div style={S.err}>{err}</div>}
        {step === 'confirm' && info && (
          <>
            <div style={S.warn}>{info.warning}</div>
            <div style={S.cap}>Capital cap: <b>Rs.{Number(info.capital_cap_rs).toLocaleString('en-IN')}</b> · token expires in {info.ttl_seconds}s</div>
            <div style={S.label}>Type exactly: <code style={S.code}>{info.required_phrase}</code></div>
            <input autoFocus value={typed} onChange={e => setTyped(e.target.value)}
                   placeholder={info.required_phrase} style={S.input} />
            {err && <div style={S.err}>{err}</div>}
            <div style={S.btns}>
              <button style={S.cancel} onClick={onClose}>Cancel</button>
              <button style={{ ...S.go, opacity: typed === info.required_phrase ? 1 : 0.4 }}
                      disabled={typed !== info.required_phrase} onClick={confirm}>
                PLACE REAL ORDERS
              </button>
            </div>
          </>
        )}
        {step === 'arming' && <div style={S.body}>Arming…</div>}
        {step === 'done' && <div style={{ ...S.body, color: C.green }}>LIVE — real orders enabled. ✓</div>}
        {(step === 'done' || step === 'error') && (
          <div style={S.btns}><button style={S.cancel} onClick={onClose}>Close</button></div>
        )}
      </div>
    </div>
  );
}

const S = {
  overlay: { position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.75)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000 },
  modal: { width: 460, background: C.panel, border: `2px solid ${C.red}`, borderRadius: 10, padding: 20 },
  head: { fontSize: 16, fontWeight: 800, color: C.red, marginBottom: 12 },
  warn: { color: C.text, fontSize: 12, marginBottom: 8, lineHeight: 1.5 },
  cap: { color: C.amber, fontSize: 11, marginBottom: 10 },
  label: { color: C.dim, fontSize: 12, marginBottom: 6 },
  code: { color: C.cyan, background: C.panel2, padding: '2px 6px', borderRadius: 3 },
  input: { width: '100%', boxSizing: 'border-box', background: C.panel2, border: `1px solid ${C.border}`, color: C.text, padding: '8px 10px', borderRadius: 4, fontFamily: 'monospace', fontSize: 13 },
  err: { color: C.red, fontSize: 11, marginTop: 8 },
  body: { color: C.text, fontSize: 13, padding: '10px 0' },
  btns: { display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 14 },
  cancel: { background: 'transparent', border: `1px solid ${C.border}`, color: C.dim, padding: '7px 14px', borderRadius: 4, cursor: 'pointer' },
  go: { background: C.red, border: 'none', color: '#fff', fontWeight: 700, padding: '7px 14px', borderRadius: 4, cursor: 'pointer' },
};
