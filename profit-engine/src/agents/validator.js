// VALIDATOR — checks the source, the date and the basic integrity of an item.
// Returns { ok, reasons[] }. Failing items are stored as `rejected` so the reason is visible.

export function validate(item, source, { now = new Date(), maxItemAgeDays = 45 } = {}) {
  const reasons = [];
  if (!item.title || item.title.length < 8) reasons.push('title missing or too short');
  if (!/^https:\/\//i.test(item.url)) reasons.push('source link is not https');
  if (source.verification_status !== 'verified') reasons.push(`source not verified (${source.verification_status})`);
  if (!['ok', 'attribution_required'].includes(source.terms_status)) reasons.push(`source terms: ${source.terms_status}`);
  if (item.published_at) {
    const ageDays = (now.getTime() - Date.parse(item.published_at)) / 86400000;
    if (ageDays > maxItemAgeDays) reasons.push(`older than ${maxItemAgeDays} days`);
    if (ageDays < -2) reasons.push('publication date is in the future');
  }
  if (item.deadline && Date.parse(item.deadline) < now.getTime()) reasons.push('deadline has passed');
  return { ok: reasons.length === 0, reasons };
}
