// PRODUCTIZER — turns a validated opportunity into a concrete sellable deliverable (draft).
// SALES PLANNER — prepares a personal offer and a step-by-step sales plan (draft, never sent).
import { truncate } from '../util.js';

function priceLine(val) {
  const r = val.revenue;
  if (r.min == null) return 'Price: unknown — set it after the buyer confirms scope (no amount stated in the source).';
  const range = r.min === r.max ? `${r.min} ${r.currency}` : `${r.min}–${r.max} ${r.currency}`;
  return `Price anchor: ${range} (${r.basis}).`;
}

export function productize(item, source, buyerInfo, val) {
  const t = truncate(item.title, 120);
  let product;
  if (source.category === 'public_tender') {
    product = [
      `Deliverable A (direct): response to tender "${t}" — only if the scope is something one person can deliver; read the full tender documents first.`,
      `Deliverable B (asset): "Tender Alert — ${item.cpv ? `CPV ${item.cpv.split(',')[0]}` : 'IT & data services'}${item.buyer_country ? `, ${item.buyer_country}` : ''}": weekly PDF/email digest of matching TED tenders with deadlines and buyer names, sold to SMEs who bid in this niche. Source: TED (attribution required).`,
    ];
  } else if (source.category === 'grant') {
    product = [
      `Deliverable: grant-application package for "${t}" — eligibility check, narrative draft, budget table template.`,
      'Asset: monthly digest of similar open calls for one sector.',
    ];
  } else {
    product = [`Deliverable: fixed-scope service answering the request "${t}" — written scope, 1 revision round, hand-over notes.`];
  }
  product.push(priceLine(val));
  product.push(`Cash cost: ${val.cost.cash} PLN. Time: ~${val.cost.hours} h (heuristic).`);
  return product.join('\n');
}

export function planSale(item, source, buyerInfo, val, risk) {
  const steps = [];
  steps.push(`1. Open the source and read it fully: ${item.url}`);
  if (risk.risks.length) steps.push(`2. Check risks: ${risk.risks.join('; ')}.`);
  else steps.push('2. No automatic risk flags; still confirm the request is genuine.');
  steps.push(`3. Contact path: ${buyerInfo.buyer_contact_path || 'unknown — find the official contact on the source page'}.`);
  steps.push('4. Send the draft below ONLY after you (the owner) approve and edit it. The system never sends it.');
  steps.push('5. Agree scope and price in writing; take a deposit or use the platform escrow before starting.');
  steps.push('6. Record the outcome (won/lost/no reply) so the scoring can be calibrated on real sales.');

  const greeting = buyerInfo.buyer && !/^(Author|Company|Public|Funding)/.test(buyerInfo.buyer) ? `Hello ${buyerInfo.buyer},` : 'Hello,';
  const draft =
    source.category === 'public_tender'
      ? `${greeting}\n\nI saw your notice "${truncate(item.title, 100)}" on TED. I would like to ask whether subcontracting or a partial lot is possible for [specific part of the scope]. I can deliver [deliverable] by [date].\n\nBest regards,\n[Your name]`
      : `${greeting}\n\nI saw your post "${truncate(item.title, 100)}". I can do this as a fixed-scope job: [1–2 sentences on approach], delivered in [N] days. ${
          val.revenue.min != null ? `Your stated budget (${val.revenue.min} ${val.revenue.currency}) works for this scope.` : 'Could you share your budget and deadline?'
        }\n\nExample of similar work: [link]\n\n[Your name]`;

  return `${steps.join('\n')}\n\n--- DRAFT MESSAGE (NOT SENT — needs owner approval) ---\n${draft}`;
}

export function nextStep(status, buyerInfo) {
  if (status === 'rejected') return 'None — rejected (see reason).';
  if (status === 'ready_for_commercial_check') return 'Owner: open the link, verify the request, then approve or edit the draft offer.';
  if (buyerInfo.demand_level !== 'confirmed') return 'Find evidence of a paying buyer before investing time.';
  return 'Review manually; score below the ready threshold.';
}
