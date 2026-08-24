import React, { useEffect, useState, useCallback } from 'react';
import { api, C, SH } from '../api';
import EquityLiveTest from '../components/EquityLiveTest';

/** The "properly checked" board: backtest / backfill / ticks / config per strategy. */
const cell = (v) => {
  const col = v === true ? C.green : v === false ? C.red : C.dim;
  const txt = v === true ? '✓' : v === false ? '✗' : '–';
  return <span style={{ color: col, fontWeight: 700, fontSize: 14 }}>{txt}</span>;
};
const OVERALL = { READY: C.green, PARTIAL: C.amber, NOT_READY: C.red, PLANNED: C.dim, UNKNOWN: C.dim };

export default function ReadinessPage({ onOpen }) {
  const [rows, setRows] = useState([]);
  const load = useCallback(() => { api.strategies().then(setRows).catch(() => {}); }, []);
  useEffect(() => { load(); const id = setInterval(load, 8000); return () => clearInterval(id); }, [load]);

  return (
    <div>
      <h2 style={S.h2}>Readiness — is each strategy properly checked?</h2>
      <EquityLiveTest />
      <div style={S.legend}>
        <span style={{ color: C.green }}>✓ ok</span>
        <span style={{ color: C.red }}>✗ failing</span>
        <span style={{ color: C.dim }}>– n/a</span>
      </div>
      <div style={S.tableWrap}>
        <table style={S.table}>
          <thead><tr>
            {['Strategy', 'Status', 'Backtest', 'Backfill', 'Live ticks', 'Config', 'Overall'].map(h =>
              <th key={h} style={S.th}>{h}</th>)}
          </tr></thead>
          <tbody>
            {rows.map(s => {
              const r = s.readiness || {};
              return (
                <tr key={s.name} style={S.tr}>
                  <td style={S.tdName} onClick={() => onOpen && onOpen(s.name)} title="Open strategy">
                    <span style={S.link}>{s.name}</span>
                  </td>
                  <td style={S.td}>{s.status}</td>
                  <td style={S.tdC}>{cell(r.backtest_ok)}</td>
                  <td style={S.tdC}>{cell(r.backfill_ok)}</td>
                  <td style={S.tdC}>{cell(r.ticks_ok)}</td>
                  <td style={S.tdC}>{cell(r.config_audit_ok)}</td>
                  <td style={S.td}><span style={{ color: OVERALL[r.overall] || C.dim, fontWeight: 700 }}>{r.overall || '—'}</span></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

const S = {
  h2: { fontSize: 20, marginBottom: 8, color: C.text },
  legend: { display: 'flex', gap: 18, fontSize: 12.5, marginBottom: 14, fontWeight: 600 },
  tableWrap: { overflowX: 'auto', background: C.panel, border: `1px solid ${C.border}`, borderRadius: 10, boxShadow: SH.card },
  table: { width: '100%', borderCollapse: 'collapse', fontSize: 13 },
  th: { textAlign: 'left', padding: '11px 14px', color: C.dim, borderBottom: `2px solid ${C.border}`, fontWeight: 700 },
  tr: { borderBottom: `1px solid ${C.border}` },
  td: { padding: '9px 14px', color: C.text },
  tdName: { padding: '9px 14px', color: C.text, fontWeight: 700, cursor: 'pointer' },
  link: { color: C.blue, textDecoration: 'underline', textUnderlineOffset: 2 },
  tdC: { padding: '9px 14px', textAlign: 'center' },
};
