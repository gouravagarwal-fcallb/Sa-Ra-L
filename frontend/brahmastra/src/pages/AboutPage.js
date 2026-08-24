import React from 'react';
import { C, SH } from '../api';

/** About Sa-Ra-L — who we are, vision, mission, values. Mirrors ABOUT.md. */
export default function AboutPage() {
  return (
    <div style={S.wrap}>
      <div style={S.hero}>
        <div style={S.title}>Sa-Ra-L</div>
        <div style={S.tag}>Disciplined, evidence-driven algorithmic trading for Indian markets.</div>
        <div style={S.tag2}>Complexity in the engine. Simplicity in the cockpit.</div>
      </div>

      <Section title="Who we are">
        Sa-Ra-L is a real-money algorithmic options-trading platform for the Indian markets —
        NIFTY, SENSEX and USD/INR — built and run with a Chartered Accountant's discipline rather
        than a gambler's instinct. Not a single "magic" algorithm, but a <b>portfolio of independent
        strategies</b>, each with its own logic, risk limits and track record, all under <b>one
        unified control system</b>. The name <i>Sa-Ra-L</i> (<b>saral</b> — to make the genuinely
        complex simple to operate) is the philosophy: serious machinery behind a calm, honest cockpit,
        with strategies named after the <i>astras</i> of Indian lore — precision instruments, used sparingly.
      </Section>

      <Section title="Our Vision">
        To grow disciplined capital into lasting wealth — and to prove that a careful individual,
        armed with evidence and good engineering, can trade with institutional rigour and complete
        transparency. The measurable goal: turn a <b>₹50 lakh</b> base into <b>₹5 crore</b> over a
        3–5 year horizon — through a diversified book of small, repeatable edges, compounded patiently
        and protected fiercely.
      </Section>

      <Section title="Our Mission">
        <ol style={S.ol}>
          <li><b>Prove before deploying.</b> No strategy touches real money until backtest, readiness and paper validation pass.</li>
          <li><b>Protect capital first.</b> A 1:4 rule keeps ≤20% deployed; daily loss caps and auto square-off on every strategy.</li>
          <li><b>Make real-money actions deliberate.</b> Going live takes an armed token + a typed confirmation, with a global STOP.</li>
          <li><b>Stay honest about what we know.</b> Modelled is labelled modelled; a loser is called a loser and kept on paper.</li>
          <li><b>Keep one source of truth.</b> Every strategy, backtest and check in one dashboard.</li>
        </ol>
      </Section>

      <Section title="What we want to do next">
        Earn each strategy its capital · model real costs (brokerage, STT, slippage) · deepen the
        edges (real-volume underlyings, confirmed high-conviction setups) · compound with patience.
      </Section>

      <div style={S.values}>
        {[['Discipline', 'Rules over impulses.'],
          ['Evidence', 'Capital follows proof.'],
          ['Transparency', 'Every number sourced.'],
          ['Risk-first', 'Survival before returns.'],
          ['Simplicity', 'Hard problems, made operable.']].map(([k, v]) => (
          <div key={k} style={S.valueCard}>
            <div style={S.valueK}>{k}</div>
            <div style={S.valueV}>{v}</div>
          </div>
        ))}
      </div>

      <div style={S.fine}>
        Sa-Ra-L is a personal, owner-operated trading system. It is not investment advice and not a
        managed product. All capital at risk is the operator's own, deployed under the rules above.
      </div>
    </div>
  );
}

function Section({ title, children }) {
  return (
    <div style={S.section}>
      <div style={S.h}>{title}</div>
      <div style={S.body}>{children}</div>
    </div>
  );
}

const S = {
  wrap: { maxWidth: 900, margin: '0 auto' },
  hero: { background: C.panel, border: `1px solid ${C.border}`, borderRadius: 12, boxShadow: SH.card, padding: '26px 24px', marginBottom: 18 },
  title: { fontSize: 30, fontWeight: 800, color: C.text, letterSpacing: 1 },
  tag: { fontSize: 15, color: C.blue, fontWeight: 600, marginTop: 8 },
  tag2: { fontSize: 13, color: C.dim, marginTop: 2, fontStyle: 'italic' },
  section: { background: C.panel, border: `1px solid ${C.border}`, borderRadius: 12, boxShadow: SH.card, padding: '16px 20px', marginBottom: 14 },
  h: { fontSize: 16, fontWeight: 800, color: C.text, marginBottom: 8 },
  body: { fontSize: 13.5, color: C.text, lineHeight: 1.65 },
  ol: { margin: 0, paddingLeft: 20, lineHeight: 1.7 },
  values: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(150px,1fr))', gap: 10, margin: '4px 0 14px' },
  valueCard: { background: C.bg, border: `1px solid ${C.border}`, borderRadius: 9, padding: '12px 14px' },
  valueK: { fontSize: 13, fontWeight: 800, color: C.blue },
  valueV: { fontSize: 12, color: C.dim, marginTop: 3 },
  fine: { fontSize: 11, color: C.dim, fontStyle: 'italic', lineHeight: 1.5, padding: '0 4px 20px' },
};
