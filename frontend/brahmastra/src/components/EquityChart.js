import React, { useMemo } from 'react';
import {
  AreaChart, Area, XAxis, YAxis, Tooltip,
  ReferenceLine, ResponsiveContainer, CartesianGrid,
} from 'recharts';

export default function EquityChart({ closedTrades, sessionStartPnl = 0 }) {
  const data = useMemo(() => {
    if (!closedTrades || closedTrades.length === 0) return [];
    let cumulative = sessionStartPnl;
    return closedTrades.map((t, i) => {
      cumulative += t.net_pnl ?? 0;
      const ts = t.exit_ts ? new Date(t.exit_ts * 1000).toTimeString().slice(0, 5) : `T${i + 1}`;
      return { ts, pnl: Math.round(cumulative), trade: i + 1 };
    });
  }, [closedTrades, sessionStartPnl]);

  const currentPnl = data.length > 0 ? data[data.length - 1].pnl : 0;
  const isPositive = currentPnl >= 0;
  const strokeColor = isPositive ? '#22c55e' : '#ef4444';
  const gradientId = 'equityGrad';

  if (data.length === 0) {
    return (
      <div style={styles.panel}>
        <div style={styles.title}>EQUITY CURVE</div>
        <div style={styles.empty}>No closed trades yet</div>
      </div>
    );
  }

  const minPnl = Math.min(...data.map(d => d.pnl));
  const maxPnl = Math.max(...data.map(d => d.pnl));
  const padding = Math.max(Math.abs(maxPnl - minPnl) * 0.1, 500);
  const domain = [Math.floor(minPnl - padding), Math.ceil(maxPnl + padding)];

  return (
    <div style={styles.panel}>
      <div style={styles.headerRow}>
        <span style={styles.title}>EQUITY CURVE</span>
        <span style={{ ...styles.pnlBadge, color: strokeColor }}>
          {currentPnl >= 0 ? '+' : ''}₹{currentPnl.toLocaleString()}
        </span>
      </div>
      <ResponsiveContainer width="100%" height={160}>
        <AreaChart data={data} margin={{ top: 8, right: 4, left: 4, bottom: 0 }}>
          <defs>
            <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%"  stopColor={strokeColor} stopOpacity={0.3} />
              <stop offset="95%" stopColor={strokeColor} stopOpacity={0.02} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="2 4" stroke="#1e293b" />
          <XAxis dataKey="ts" tick={{ fontSize: 11, fill: '#475569' }} tickLine={false} axisLine={false} />
          <YAxis domain={domain} tick={{ fontSize: 11, fill: '#475569' }} tickLine={false} axisLine={false}
                 tickFormatter={v => `₹${(v / 1000).toFixed(0)}k`} width={48} />
          <ReferenceLine y={0} stroke="#334155" strokeDasharray="3 3" />
          <Tooltip
            contentStyle={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 6, fontSize: 10 }}
            labelStyle={{ color: '#475569' }}
            formatter={(v) => [`₹${v.toLocaleString()}`, 'P&L']}
          />
          <Area type="monotone" dataKey="pnl"
                stroke={strokeColor} strokeWidth={2}
                fill={`url(#${gradientId})`} dot={false} activeDot={{ r: 3 }} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

const styles = {
  panel: {
    background: '#0f172a', border: '1px solid #1e293b',
    borderRadius: 8, padding: 12,
    display: 'flex', flexDirection: 'column', gap: 8,
  },
  headerRow: { display: 'flex', alignItems: 'center', justifyContent: 'space-between' },
  title: { fontSize: 12, fontWeight: 700, letterSpacing: 2, color: '#475569' },
  pnlBadge: { fontSize: 16, fontWeight: 700 },
  empty: { color: '#475569', fontSize: 13, textAlign: 'center', padding: 40 },
};
