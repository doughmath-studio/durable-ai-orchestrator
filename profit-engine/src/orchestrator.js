// ORCHESTRATOR — one run: SCOUT → COLLECTOR → RISK CONTROL (dedup) → VALIDATOR → BUYER FINDER →
// VALUATOR → PRODUCTIZER → SALES PLANNER → (optional AI) → D1. Used by the cron and by POST /run.
import { loadConfig, secretsOf } from './config.js';
import { SOURCE_REGISTRY, ownerFeedSources, canCollect } from './sources.js';
import { collect } from './agents/collector.js';
import { validate } from './agents/validator.js';
import { findBuyer } from './agents/buyer.js';
import { assessRisk } from './agents/risk.js';
import { valuate } from './agents/valuator.js';
import { productize, planSale, nextStep } from './agents/productizer.js';
import { enrichWithAI } from './ai.js';
import { notifyCritical } from './notify.js';
import * as store from './db.js';
import { canonicalUrl, titleFingerprint, sha256Hex, redact, truncate } from './util.js';

/** SCOUT: which sources to check in this run (never restricted ones; least recently checked first). */
export function pickSources(sources, max) {
  return sources
    .filter((s) => s.enabled && s.verification_status !== 'blocked_by_terms' && s.terms_status !== 'restricted')
    .sort((a, b) => String(a.last_checked_at || '').localeCompare(String(b.last_checked_at || '')))
    .slice(0, max);
}

export async function runPipeline(env, { trigger = 'manual', fetchImpl = globalThis.fetch, now = () => new Date() } = {}) {
  const cfg = loadConfig(env);
  const secrets = secretsOf(cfg);
  const db = env.DB;
  const t0 = now();
  const run = {
    id: crypto.randomUUID(),
    trigger,
    started_at: t0.toISOString(),
    sources_checked: 0,
    items_fetched: 0,
    items_new: 0,
    items_duplicate: 0,
    errors_count: 0,
    ai_calls: 0,
  };
  const events = [];
  const log = (level, module, message) => {
    events.push({ run_id: run.id, at: now().toISOString(), level, module, message: truncate(redact(message, secrets), 1000) });
    if (level === 'error' || level === 'critical') run.errors_count++;
  };

  if (!(await store.startRun(db, run, t0))) return { status: 'locked', message: 'another run is in progress' };

  try {
    // ---- SCOUT ----
    await store.syncSources(db, [...SOURCE_REGISTRY, ...ownerFeedSources(cfg.feeds)]);
    const picked = pickSources(await store.listSources(db), cfg.maxSourcesPerRun);
    if (!picked.length) log('info', 'scout', 'no sources available (FEEDS empty and registry sources all blocked)');

    // ---- COLLECTOR (parallel, each bounded by timeout + retries) ----
    const settled = await Promise.allSettled(picked.map((s) => collect(s, cfg, fetchImpl)));
    const sourceResults = [];
    const candidates = [];
    picked.forEach((source, i) => {
      run.sources_checked++;
      const r = settled[i];
      const checkedAt = now().toISOString();
      if (r.status === 'rejected') {
        const msg = `${source.id}: ${r.reason?.message || r.reason}${r.reason?.body ? ` — ${r.reason.body.slice(0, 200)}` : ''}`;
        log('error', 'collector', msg);
        sourceResults.push({ id: source.id, verification_status: 'failed', checked_at: checkedAt, success_at: null, error: truncate(redact(msg, secrets), 500) });
        return;
      }
      const collectable = canCollect(source);
      const status = collectable ? 'verified' : 'needs_terms_review';
      sourceResults.push({ id: source.id, verification_status: status, checked_at: checkedAt, success_at: checkedAt, error: null });
      if (!collectable) {
        log('warn', 'validator', `${source.id}: reachable, but terms need owner review — items not collected`);
        return;
      }
      const verifiedSource = { ...source, verification_status: status };
      for (const item of r.value.items) candidates.push({ item, source: verifiedSource });
      run.items_fetched += r.value.items.length;
    });

    // ---- RISK CONTROL: de-duplication (within the batch and against D1) ----
    for (const c of candidates) {
      c.url_hash = (await sha256Hex(canonicalUrl(c.item.url))).slice(0, 24);
      c.title_hash = (await sha256Hex(titleFingerprint(`${c.item.title}|${c.item.buyer || ''}`))).slice(0, 24);
    }
    const known = await store.findExisting(db, candidates.map((c) => c.url_hash), candidates.map((c) => c.title_hash));
    const seenUrl = new Set(known.url);
    const seenTitle = new Set(known.title);
    const fresh = [];
    for (const c of candidates) {
      if (seenUrl.has(c.url_hash) || seenTitle.has(c.title_hash)) {
        run.items_duplicate++;
        continue;
      }
      seenUrl.add(c.url_hash);
      seenTitle.add(c.title_hash);
      fresh.push(c);
    }

    // ---- VALIDATOR → BUYER FINDER → RISK → VALUATOR → PRODUCTIZER → SALES PLANNER ----
    const nowDate = now();
    const rows = fresh.map(({ item, source, url_hash, title_hash }) => {
      const v = validate(item, source, { now: nowDate, maxItemAgeDays: cfg.maxItemAgeDays });
      const buyer = findBuyer(item, source);
      const risk = assessRisk(item, source);
      const val = valuate(item, source, buyer, risk, { now: nowDate, maxItemAgeDays: cfg.maxItemAgeDays });
      let status;
      let reason;
      if (!v.ok) [status, reason] = ['rejected', v.reasons.join('; ')];
      else if (risk.risk_level === 'high') [status, reason] = ['rejected', `high risk: ${risk.risks.join('; ')}`];
      else if (buyer.demand_level === 'confirmed' && val.score >= cfg.readyScoreThreshold)
        [status, reason] = ['ready_for_commercial_check', `confirmed demand and score ≥ ${cfg.readyScoreThreshold}`];
      else [status, reason] = ['validated', buyer.demand_level !== 'confirmed' ? 'demand not confirmed' : `score below ${cfg.readyScoreThreshold}`];
      const keep = status !== 'rejected';
      return {
        id: url_hash,
        url_hash,
        title_hash,
        title: item.title,
        source_id: source.id,
        source_url: item.url,
        discovered_at: nowDate.toISOString(),
        published_at: item.published_at,
        category: source.category,
        description: item.description,
        buyer: buyer.buyer,
        buyer_contact_path: buyer.buyer_contact_path,
        demand_evidence: buyer.demand_evidence,
        demand_level: buyer.demand_level,
        monetization: val.monetization,
        revenue_min: val.revenue.min,
        revenue_max: val.revenue.max,
        revenue_currency: val.revenue.currency,
        revenue_basis: val.revenue.basis,
        cost_cash: val.cost.cash,
        cost_hours: val.cost.hours,
        cost_basis: val.cost.basis,
        margin_pct: val.margin_pct,
        complexity: val.complexity,
        risks: JSON.stringify(risk.risks),
        risk_level: risk.risk_level,
        score: val.score,
        confidence: val.confidence,
        score_breakdown: JSON.stringify(val.breakdown),
        product_draft: keep ? productize(item, source, buyer, val) : null,
        sales_plan: keep ? planSale(item, source, buyer, val, risk) : null,
        ai_notes: null,
        next_step: nextStep(status, buyer),
        status,
        status_reason: reason,
        _item: item,
      };
    });

    // ---- optional AI enrichment of the best few (never changes numbers or status) ----
    if (cfg.ai.enabled && cfg.ai.maxCalls > 0) {
      const top = rows.filter((r) => r.status !== 'rejected').sort((a, b) => b.score - a.score).slice(0, cfg.ai.maxCalls);
      for (const r of top) {
        run.ai_calls++;
        try {
          r.ai_notes = await enrichWithAI(r._item, cfg.ai, fetchImpl);
        } catch (e) {
          log('warn', 'ai', `OmniRoute unavailable, continuing without AI: ${e.message}`);
          break; // do not hammer a broken endpoint
        }
      }
    }
    for (const r of rows) delete r._item;

    run.items_new = await store.insertOpportunities(db, rows);
    await store.saveSourceResults(db, sourceResults);

    const failed = sourceResults.filter((s) => s.verification_status === 'failed').length;
    run.status = failed === 0 ? 'ok' : failed === sourceResults.length ? 'failed' : 'partial';
    run.summary = `sources ${run.sources_checked} (failed ${failed}), fetched ${run.items_fetched}, new ${run.items_new}, duplicates ${run.items_duplicate}`;
    if (run.status === 'failed' && run.sources_checked > 0) log('critical', 'orchestrator', `all sources failed: ${run.summary}`);
    log('info', 'orchestrator', run.summary);
  } catch (e) {
    run.status = 'failed';
    run.summary = `crash: ${e?.message || e}`;
    log('critical', 'orchestrator', `run crashed: ${e?.stack || e}`);
  }

  run.finished_at = now().toISOString();
  await store.finishRun(db, run).catch(() => {});
  await store.logEvents(db, events).catch(() => {});
  const critical = events.filter((e) => e.level === 'critical');
  if (critical.length) await notifyCritical(cfg, critical.map((e) => e.message).join('\n'), fetchImpl).catch(() => {});
  return { status: run.status, run };
}
