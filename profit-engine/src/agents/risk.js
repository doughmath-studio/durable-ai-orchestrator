// RISK CONTROL — flags fraud patterns, illegal or unethical work, personal-data issues and
// unrealistic promises. A `high` risk item is rejected and never productised.

const HIGH = [
  [/\b(upfront|registration|application|training)\s+fee\b/i, 'asks the worker to pay a fee (common scam pattern)'],
  [/\bguaranteed\s+(income|profit|returns?)\b/i, 'promises guaranteed income'],
  [/\b(crypto|forex|binary\s+options?)\s+(investment|trading|signals?)\b/i, 'investment/trading scheme'],
  [/\b(fake|paid)\s+reviews?\b|\breview\s+manipulation\b/i, 'fake reviews'],
  [/\b(write|do)\s+(my|the)\s+(essay|thesis|homework|exam)\b/i, 'academic dishonesty'],
  [/\bbypass\w*\s+(captcha|login|paywall|authentication)\b|\bcredential\s+stuffing\b/i, 'circumventing access controls'],
  [/\b(scrape|harvest)\w*\s+(emails?|phone\s+numbers?|personal\s+data)\b/i, 'harvesting personal data'],
  [/\b(gambling|casino|betting)\b/i, 'gambling'],
  [/\b(adult|onlyfans|escort)\b/i, 'adult content'],
  [/\bmoney\s+mule\b|\breceive\s+(and|&)\s+forward\s+(payments?|packages?)\b/i, 'money/package forwarding (mule pattern)'],
  [/\b(whatsapp|telegram)\s+only\b/i, 'off-platform contact only (scam signal)'],
  [/\bmass\s+(dm|messaging|emailing)\b|\bspam\b/i, 'spam'],
];

const MEDIUM = [
  [/\b(gdpr|personal\s+data|email\s+list|lead\s+list|contact\s+database)\b/i, 'involves personal data: GDPR lawful basis needed'],
  [/\b(unpaid|for\s+exposure|equity\s+only|revenue\s+share\s+only)\b/i, 'unpaid or speculative compensation'],
  [/\b(us\s+citizens?\s+only|security\s+clearance|must\s+be\s+(located|based)\s+in)\b/i, 'eligibility restriction'],
  [/\b(medical|legal|financial)\s+advice\b/i, 'regulated advice'],
];

export function assessRisk(item, source) {
  const text = `${item.title} ${item.description || ''}`;
  const risks = [];
  let level = 'low';
  for (const [re, label] of HIGH) if (re.test(text)) risks.push(label);
  if (risks.length) level = 'high';
  for (const [re, label] of MEDIUM) {
    if (re.test(text)) {
      risks.push(label);
      if (level === 'low') level = 'medium';
    }
  }
  if (source.category === 'public_tender') {
    risks.push('tender rules: eligibility, deadlines, possible deposit/insurance; read the full documentation');
    if (level === 'low') level = 'medium';
  }
  if (source.category === 'grant') risks.push('grant eligibility (country/entity type) must be checked');
  if (source.terms_status === 'attribution_required') risks.push('reuse requires attribution to the source');
  return { risk_level: level, risks };
}
