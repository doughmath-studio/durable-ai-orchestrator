// Server-rendered HTML for iPhone Safari. No JavaScript, no external assets.
import { escapeHtml as h } from './util.js';

const CSS = `
:root{--bg:#fff;--fg:#111;--mut:#666;--line:#e3e3e3;--card:#f7f7f8;--ok:#0a7a3d;--warn:#a15c00;--bad:#b3261e;--acc:#1f5fbf}
@media (prefers-color-scheme:dark){:root{--bg:#111;--fg:#eee;--mut:#9a9a9a;--line:#2b2b2b;--card:#1a1a1c;--ok:#4cc38a;--warn:#f0a548;--bad:#ff8a80;--acc:#7aa7ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.45 -apple-system,system-ui,sans-serif}
main{max-width:760px;margin:0 auto;padding:16px}h1{font-size:22px;margin:4px 0 2px}h2{font-size:17px;margin:22px 0 8px}
.mut{color:var(--mut);font-size:14px}.grid{display:grid;grid-template-columns:repeat(2,1fr);gap:8px}
.k{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px}.k b{display:block;font-size:24px}
.note{border-left:3px solid var(--warn);padding:6px 10px;background:var(--card);font-size:14px;margin:12px 0}
ol,ul{padding-left:20px}li{margin:6px 0;overflow-wrap:anywhere}a{color:var(--acc)}
.tag{display:inline-block;font-size:12px;border:1px solid var(--line);border-radius:6px;padding:0 6px;margin-right:4px}
.ok{color:var(--ok)}.bad{color:var(--bad)}.warn{color:var(--warn)}
form{display:flex;gap:8px;margin-top:8px}input{flex:1;font-size:16px;padding:10px;border:1px solid var(--line);border-radius:10px;background:var(--bg);color:var(--fg)}
button{font-size:16px;padding:10px 14px;border:0;border-radius:10px;background:var(--acc);color:#fff}
pre{white-space:pre-wrap;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px;font-size:13px;overflow-wrap:anywhere}
table{border-collapse:collapse;width:100%;font-size:14px}td{border-bottom:1px solid var(--line);padding:6px 4px;vertical-align:top}
`;

function page(title, body) {
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>${h(title)}</title><style>${CSS}</style></head><body><main>${body}</main></body></html>`;
}

const DISCLAIMER =
  '<div class="note">Amounts are <b>potential</b> values stated in sources or estimates — not earned money. Revenue counts only after a confirmed, paid sale.</div>';

function statusTag(s) {
  const cls = s === 'ready_for_commercial_check' ? 'ok' : s === 'rejected' ? 'bad' : '';
  return `<span class="tag ${cls}">${h(s)}</span>`;
}

function money(o) {
  if (o.revenue_min == null) return '<span class="mut">value: unknown</span>';
  const r = o.revenue_min === o.revenue_max ? o.revenue_min : `${o.revenue_min}–${o.revenue_max}`;
  return `<span class="mut">potential: ${h(r)} ${h(o.revenue_currency)}</span>`;
}

export function renderDashboard({ stats, top, errors, runs, aiEnabled, flash }) {
  const last = runs[0];
  const lastLine = last
    ? `Last run: ${h(last.started_at)} (${h(last.trigger)}) — <b class="${last.status === 'ok' ? 'ok' : last.status === 'failed' ? 'bad' : 'warn'}">${h(last.status)}</b>. ${h(last.summary || '')}`
    : 'No runs yet.';
  const topHtml = top.length
    ? `<ol>${top
        .map(
          (o) =>
            `<li><a href="/opportunities/${h(o.id)}?view=html">${h(o.title)}</a><br>${statusTag(o.status)}<span class="tag">score ${h(o.score)}</span><span class="tag">conf ${h(
              Math.round(o.confidence * 100),
            )}%</span><span class="tag">${h(o.demand_level)}</span> ${money(o)}</li>`,
        )
        .join('')}</ol>`
    : '<p class="mut">Nothing yet. Run the engine once sources are verified.</p>';
  const errHtml = errors.length
    ? `<ul>${errors.map((e) => `<li><span class="${e.level === 'critical' ? 'bad' : 'warn'}">${h(e.level)}</span> <span class="mut">${h(e.at)} · ${h(e.module)}</span><br>${h(e.message)}</li>`).join('')}</ul>`
    : '<p class="mut">No errors.</p>';
  return page(
    'Profit Engine',
    `<h1>Profit Engine</h1><div class="mut">${lastLine}<br>AI enrichment: ${aiEnabled ? 'on' : 'off'}</div>
${flash ? `<div class="note">${h(flash)}</div>` : ''}
<div class="grid" style="margin-top:12px">
<div class="k"><b>${stats.found}</b>found</div><div class="k"><b>${stats.validated}</b>passed validation</div>
<div class="k"><b>${stats.demand_confirmed}</b>confirmed demand</div><div class="k"><b>${stats.ready}</b>ready for commercial check</div></div>
${DISCLAIMER}
<h2>Top 10</h2>${topHtml}
<h2>Recent errors</h2>${errHtml}
<h2>Manual run</h2><form method="post" action="/run"><input type="password" name="token" placeholder="RUN_TOKEN" autocomplete="current-password" required><button>Run now</button></form>
<p class="mut"><a href="/opportunities">JSON</a> · <a href="/opportunities.csv">CSV</a> · <a href="/sources">sources</a> · <a href="/runs">runs</a> · <a href="/health">health</a></p>`,
  );
}

export function renderOpportunity(o) {
  const rows = [
    ['Status', `${statusTag(o.status)} ${h(o.status_reason || '')}`],
    ['Score', `${h(o.score)} / 100 · confidence ${h(Math.round(o.confidence * 100))}%`],
    ['Source', `<a href="${h(o.source_url)}" rel="noopener noreferrer">${h(o.source_id)}</a>`],
    ['Published', h(o.published_at || 'unknown')],
    ['Buyer', h(o.buyer || 'unknown')],
    ['Contact path', h(o.buyer_contact_path || 'unknown')],
    ['Demand', `${h(o.demand_level)} — ${h(o.demand_evidence || 'no evidence')}`],
    ['Monetization', h(o.monetization)],
    ['Potential revenue', `${o.revenue_min == null ? 'unknown' : `${h(o.revenue_min)}–${h(o.revenue_max)} ${h(o.revenue_currency)}`} <span class="mut">(${h(o.revenue_basis)})</span>`],
    ['Costs', `${h(o.cost_cash)} PLN cash, ~${h(o.cost_hours)} h <span class="mut">(${h(o.cost_basis)})</span>`],
    ['Cash margin', o.margin_pct == null ? 'unknown' : `${h(o.margin_pct)}%`],
    ['Complexity', h(o.complexity)],
    ['Risk', `${h(o.risk_level)} — ${h((o.risks || []).join('; ') || 'none flagged')}`],
    ['Next step', h(o.next_step)],
  ];
  const breakdown = o.score_breakdown ? `<pre>${h(JSON.stringify(o.score_breakdown, null, 2))}</pre>` : '';
  return page(
    o.title,
    `<p><a href="/">← dashboard</a></p><h1>${h(o.title)}</h1>${DISCLAIMER}<table>${rows.map(([k, v]) => `<tr><td class="mut">${k}</td><td>${v}</td></tr>`).join('')}</table>
${o.description ? `<h2>Description</h2><p>${h(o.description)}</p>` : ''}
${o.product_draft ? `<h2>Product draft</h2><pre>${h(o.product_draft)}</pre>` : ''}
${o.sales_plan ? `<h2>Sales plan</h2><pre>${h(o.sales_plan)}</pre>` : ''}
${o.ai_notes ? `<h2>AI notes</h2><pre>${h(typeof o.ai_notes === 'string' ? o.ai_notes : JSON.stringify(o.ai_notes, null, 2))}</pre>` : ''}
<h2>Score breakdown</h2>${breakdown}`,
  );
}
