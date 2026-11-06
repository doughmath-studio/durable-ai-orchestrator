// VALUATOR — estimates revenue, costs, margin and a transparent 0–100 score.
//
// Rules:
//  * Revenue is filled ONLY from an amount stated in the source text. Otherwise it is NULL ("unknown").
//  * Cash cost defaults to 0 PLN because the MVP uses only free tools; time cost is a labelled heuristic.
//  * Each score component is either a number in [0,1] or `null` (unknown). Unknown components add 0 points
//    and lower `confidence`; they are never treated as confirmed.
//
// score      = Σ weight_i × value_i        over known components (max 100)
// confidence = Σ weight_i (known) / 100    (share of the score that rests on known data)

export const WEIGHTS = { demand: 30, monetization: 20, value: 20, feasibility: 15, freshness: 10, risk: 5 };

// Approximate PLN per unit, used ONLY to compare stated budgets with the hourly target.
// Override with FX_PLN='{"EUR":4.3,...}'. Not used for any displayed amount.
const DEFAULT_FX_PLN = { PLN: 1, EUR: 4.3, USD: 4.0, GBP: 5.1 };
const TARGET_PLN_PER_HOUR = 100; // the owner's target effective rate; value score = rate / target, capped at 1
const HOURS_BY_COMPLEXITY = { low: 4, medium: 12, high: 40 };

const MONETIZATION = {
  public_tender: {
    clarity: 0.5,
    text: 'Bid directly if the scope fits a solo provider, or offer subcontracting to bidders; secondary: a paid tender-alert digest for this CPV niche.',
  },
  grant: { clarity: 0.4, text: 'Paid grant-application writing for eligible organisations; secondary: curated grant digest.' },
  service_request: { clarity: 0.8, text: 'Deliver the requested work for a fixed price agreed in writing before starting.' },
  owner_feed: { clarity: null, text: 'Not classified: owner feed — review manually.' },
};

const CURRENCY_RE =
  /(?:(?<sym>[$€£])\s?(?<a1>\d[\d,.\s]{0,9}\d|\d)(?:\s?[kK])?(?:\s?[-–]\s?[$€£]?\s?(?<b1>\d[\d,.\s]{0,9}\d|\d))?|(?<a2>\d[\d,.\s]{0,9}\d|\d)\s?(?<code>PLN|zł|EUR|USD|GBP)\b)(?<hr>\s?(?:\/|per\s)\s?(?:h|hr|hour)\b)?/i;

function num(s) {
  if (!s) return null;
  const n = Number(String(s).replace(/[\s,]/g, '').replace(/\.(?=\d{3}\b)/g, ''));
  return Number.isFinite(n) && n > 0 ? n : null;
}

/** Extract a budget stated in the text. Returns null if none. */
export function parseStatedBudget(text) {
  const m = CURRENCY_RE.exec(String(text || ''));
  if (!m) return null;
  const g = m.groups;
  const symMap = { $: 'USD', '€': 'EUR', '£': 'GBP' };
  const currency = g.sym ? symMap[g.sym] : g.code.toLowerCase() === 'zł' ? 'PLN' : g.code.toUpperCase();
  let a = num(g.a1 || g.a2);
  if (a && /[kK]/.test(m[0]) && a < 1000) a *= 1000;
  const b = num(g.b1);
  if (!a) return null;
  return { min: Math.min(a, b || a), max: Math.max(a, b || a), currency, hourly: Boolean(g.hr), raw: m[0].trim() };
}

export function estimateComplexity(item, category) {
  const text = `${item.title} ${item.description || ''}`.toLowerCase();
  if (category === 'public_tender') return 'high';
  if (/\b(platform|saas|mobile app|marketplace|full[- ]stack|migration|erp|integration)\b/.test(text)) return 'high';
  if (/\b(script|landing page|logo|spreadsheet|data entry|scrap(e|ing) public|automation|zapier|make\.com|bot|report)\b/.test(text)) return 'low';
  return 'medium';
}

export function valuate(item, source, buyerInfo, riskInfo, { now = new Date(), maxItemAgeDays = 45, fx = DEFAULT_FX_PLN } = {}) {
  const category = source.category in MONETIZATION ? source.category : 'owner_feed';
  const mon = MONETIZATION[category];
  const complexity = estimateComplexity(item, category);
  const hours = HOURS_BY_COMPLEXITY[complexity];
  const budget = parseStatedBudget(`${item.title} ${item.description || ''}`);

  let revenue = { min: null, max: null, currency: null, basis: 'unknown: no amount stated in the source' };
  if (budget) {
    revenue = budget.hourly
      ? { min: budget.min * hours, max: budget.max * hours, currency: budget.currency, basis: `stated hourly rate "${budget.raw}" × estimated ${hours} h (estimate)` }
      : { min: budget.min, max: budget.max, currency: budget.currency, basis: `stated in source: "${budget.raw}"` };
  }
  const cost = { cash: 0, hours, basis: `cash 0 PLN (free tools only); time ${hours} h = heuristic for "${complexity}" complexity, not measured` };
  const marginPct = revenue.min != null ? Math.round(((revenue.min - cost.cash) / revenue.min) * 100) : null;

  // ---- components ----
  const demand = { confirmed: 1, indicated: 0.5, unknown: null }[buyerInfo.demand_level];
  let value = null;
  let effectiveRatePln = null;
  if (revenue.min != null && fx[revenue.currency]) {
    effectiveRatePln = Math.round((revenue.min * fx[revenue.currency]) / hours);
    value = Math.min(1, effectiveRatePln / TARGET_PLN_PER_HOUR);
  }
  const feasibility = { low: 1, medium: 0.6, high: 0.25 }[complexity];
  let freshness = null;
  if (item.published_at) {
    const age = Math.max(0, (now.getTime() - Date.parse(item.published_at)) / 86400000);
    freshness = Math.max(0, 1 - age / maxItemAgeDays);
  }
  const risk = { low: 1, medium: 0.5, high: 0 }[riskInfo.risk_level];
  const components = { demand, monetization: mon.clarity, value, feasibility, freshness, risk };

  let score = 0;
  let knownWeight = 0;
  const breakdown = {};
  for (const [k, w] of Object.entries(WEIGHTS)) {
    const v = components[k];
    if (v == null) {
      breakdown[k] = { weight: w, value: null, points: 0, note: 'unknown' };
    } else {
      const pts = Math.round(w * v * 10) / 10;
      breakdown[k] = { weight: w, value: Math.round(v * 100) / 100, points: pts };
      score += pts;
      knownWeight += w;
    }
  }
  breakdown.effective_rate_pln_per_hour = effectiveRatePln;
  breakdown.formula = 'score = Σ weight×value over known components; confidence = known weight / 100';

  return {
    monetization: mon.text,
    complexity,
    revenue,
    cost,
    margin_pct: marginPct,
    score: Math.round(score * 10) / 10,
    confidence: Math.round(knownWeight) / 100,
    breakdown,
  };
}
