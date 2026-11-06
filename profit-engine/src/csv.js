// CSV export (RFC 4180) with protection against spreadsheet formula injection.

export const CSV_COLUMNS = [
  'id', 'status', 'score', 'confidence', 'title', 'category', 'source_id', 'source_url', 'published_at', 'discovered_at',
  'buyer', 'demand_level', 'demand_evidence', 'monetization', 'revenue_min', 'revenue_max', 'revenue_currency',
  'revenue_basis', 'cost_cash', 'cost_hours', 'margin_pct', 'complexity', 'risk_level', 'risks', 'next_step',
];

export function csvCell(v) {
  if (v == null) return '';
  let s = typeof v === 'object' ? JSON.stringify(v) : String(v);
  if (/^[=+\-@\t\r]/.test(s)) s = `'${s}`; // Excel/Sheets would otherwise evaluate it
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export function toCsv(rows, columns = CSV_COLUMNS) {
  const lines = [columns.join(',')];
  for (const r of rows) lines.push(columns.map((c) => csvCell(r[c])).join(','));
  return lines.join('\r\n') + '\r\n';
}
