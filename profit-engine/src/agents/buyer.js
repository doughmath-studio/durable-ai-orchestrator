// BUYER FINDER — who would pay, what they need and what evidence of demand exists.
// demand_level:
//   confirmed - the buyer published an explicit, current request to buy (tender, grant call, job/contract post)
//   indicated - text signals a need ("looking for", "need help with"), but no explicit purchase
//   unknown   - no evidence; never upgraded by guessing

const REQUEST_PATTERNS = [
  /\b(looking\s+for|seeking|need(?:ed|s)?|hiring|wanted|searching\s+for)\s+(a|an|someone|freelancers?|developers?|designers?|consultants?|help)\b/i,
  /\b(szukam|poszukujemy|potrzebuj[eę]|zlec[eę])\b/i,
  /\[(hiring|task)\]/i,
];
const CONTRACT_PATTERNS = /\b(contract|freelance|part[-\s]?time|project[-\s]based|one[-\s]off|gig|budget|fixed\s+price)\b/i;

export function findBuyer(item, source) {
  const text = `${item.title} ${item.description || ''}`;
  if (source.category === 'public_tender') {
    return {
      buyer: item.buyer || 'Public contracting authority (name not in the API response)',
      buyer_contact_path: 'Tender documents on TED (link) → contact point named in the notice; submit via the e-procurement platform named there',
      demand_level: 'confirmed',
      demand_evidence: `Published public tender${item.buyer ? ` by ${item.buyer}` : ''}${item.buyer_country ? ` (${item.buyer_country})` : ''}${
        item.deadline ? `, deadline ${item.deadline.slice(0, 10)}` : ''
      }${item.cpv ? `, CPV ${item.cpv}` : ''}.`,
    };
  }
  if (source.category === 'grant') {
    return {
      buyer: item.author || 'Funding agency named in the notice',
      buyer_contact_path: 'Agency contact listed in the funding notice (link)',
      demand_level: 'confirmed',
      demand_evidence: 'Open funding call published by a public agency.',
    };
  }
  const match = REQUEST_PATTERNS.map((re) => re.exec(text)).find(Boolean);
  const isJobBoard = source.id === 'remoteok-jobs' || source.id === 'weworkremotely';
  if (isJobBoard && CONTRACT_PATTERNS.test(text)) {
    return {
      buyer: item.author || 'Company that posted the listing',
      buyer_contact_path: 'Apply through the original listing (link)',
      demand_level: 'confirmed',
      demand_evidence: 'Active contract/freelance listing on a job board.',
    };
  }
  if (match || isJobBoard) {
    return {
      buyer: item.author || 'Author of the post',
      buyer_contact_path: 'Reply on the original post/listing (link); do not move to private channels before a scope is agreed',
      demand_level: 'indicated',
      demand_evidence: match ? `Text says: "${match[0]}"` : 'Job listing (employment, not necessarily a contract).',
    };
  }
  return { buyer: null, buyer_contact_path: null, demand_level: 'unknown', demand_evidence: null };
}
