// PROFIT ENGINE — HTTP API. The Worker entry point (src/index.js) must export only the default handler.
import { loadConfig } from './config.js';
import { runPipeline } from './orchestrator.js';
import * as store from './db.js';
import { toCsv } from './csv.js';
import { renderDashboard, renderOpportunity } from './dashboard.js';
import { safeEqual, clampInt } from './util.js';

export const VERSION = '0.1.0';
const SECURITY_HEADERS = {
  'x-content-type-options': 'nosniff',
  'referrer-policy': 'no-referrer',
  'x-frame-options': 'DENY',
  'content-security-policy': "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'",
  'cache-control': 'no-store',
};

const json = (data, status = 200, extra = {}) =>
  new Response(JSON.stringify(data, null, 2), { status, headers: { 'content-type': 'application/json; charset=utf-8', ...SECURITY_HEADERS, ...extra } });
const html = (body, status = 200) => new Response(body, { status, headers: { 'content-type': 'text/html; charset=utf-8', ...SECURITY_HEADERS } });

function bearer(request) {
  const m = /^Bearer\s+(.+)$/i.exec(request.headers.get('authorization') || '');
  return m ? m[1].trim() : '';
}

function cookie(request, name) {
  const m = new RegExp(`(?:^|;\\s*)${name}=([^;]+)`).exec(request.headers.get('cookie') || '');
  return m ? decodeURIComponent(m[1]) : '';
}

/** Read access: open unless READ_TOKEN is set. Accepts Bearer, cookie, or ?key= once (then sets a cookie). */
function readAccess(request, url, cfg) {
  if (!cfg.readToken) return { ok: true };
  const given = bearer(request) || cookie(request, 'pe_key') || url.searchParams.get('key') || '';
  if (!safeEqual(given, cfg.readToken)) return { ok: false };
  if (url.searchParams.has('key')) {
    url.searchParams.delete('key');
    return {
      ok: true,
      redirect: new Response(null, {
        status: 303,
        headers: { location: url.pathname + url.search, 'set-cookie': `pe_key=${encodeURIComponent(given)}; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=2592000`, ...SECURITY_HEADERS },
      }),
    };
  }
  return { ok: true };
}

async function handleRun(request, env, ctx, cfg, deps) {
  if (!cfg.runToken) return json({ error: 'RUN_TOKEN secret is not configured; manual runs are disabled' }, 503);
  let given = bearer(request);
  const isForm = (request.headers.get('content-type') || '').includes('application/x-www-form-urlencoded');
  if (!given && isForm) given = String((await request.formData()).get('token') || '');
  if (!safeEqual(given, cfg.runToken)) return json({ error: 'unauthorized' }, 401, { 'www-authenticate': 'Bearer' });
  const result = await runPipeline(env, { trigger: 'manual', fetchImpl: deps.fetchImpl });
  if (isForm) return html(`<meta http-equiv="refresh" content="0;url=/?ran=${encodeURIComponent(result.status)}">`, 200);
  return json(result, result.status === 'locked' ? 409 : 200);
}

export async function handleFetch(request, env, ctx, deps = {}) {
  const cfg = loadConfig(env);
  const url = new URL(request.url);
  const path = url.pathname.replace(/\/+$/, '') || '/';
  const method = request.method.toUpperCase();

  try {
    if (path === '/health' && method === 'GET') {
      let db = 'ok';
      try {
        await env.DB.prepare('SELECT 1 AS one').first();
      } catch (e) {
        db = 'error';
      }
      return json({ ok: db === 'ok', version: VERSION, time: new Date().toISOString(), db, ai_enabled: cfg.ai.enabled, owner_feeds: cfg.feeds.length }, db === 'ok' ? 200 : 503);
    }

    if (path === '/run') {
      if (method !== 'POST') return json({ error: 'use POST' }, 405, { allow: 'POST' });
      return await handleRun(request, env, ctx, cfg, deps);
    }

    if (method !== 'GET') return json({ error: 'method not allowed' }, 405);
    const access = readAccess(request, url, cfg);
    if (!access.ok) return json({ error: 'unauthorized: open the page once with ?key=READ_TOKEN' }, 401);
    if (access.redirect) return access.redirect;

    if (path === '/') {
      const [stats, top, errors, runs] = await Promise.all([
        store.stats(env.DB),
        store.listOpportunities(env.DB, { page: 1, limit: 10, status: null }).then((r) => r.items.filter((o) => o.status !== 'rejected')),
        store.recentErrors(env.DB, 8),
        store.recentRuns(env.DB, 1),
      ]);
      const ran = url.searchParams.get('ran');
      return html(renderDashboard({ stats, top, errors, runs, aiEnabled: cfg.ai.enabled, flash: ran ? `Manual run finished: ${ran}` : '' }));
    }

    const filters = {
      status: url.searchParams.get('status') || null,
      minScore: url.searchParams.has('min_score') ? Number(url.searchParams.get('min_score')) : null,
    };
    if (filters.minScore != null && !Number.isFinite(filters.minScore)) filters.minScore = null;

    if (path === '/opportunities') {
      const page = clampInt(url.searchParams.get('page'), 1, 100000, 1);
      const limit = clampInt(url.searchParams.get('limit'), 1, 100, 20);
      return json(await store.listOpportunities(env.DB, { page, limit, ...filters }));
    }

    if (path === '/opportunities.csv') {
      const { items } = await store.listOpportunities(env.DB, { page: 1, limit: 1000, ...filters });
      return new Response(toCsv(items), {
        headers: { 'content-type': 'text/csv; charset=utf-8', 'content-disposition': 'attachment; filename="opportunities.csv"', ...SECURITY_HEADERS },
      });
    }

    const m = /^\/opportunities\/([a-f0-9]{8,64})$/.exec(path);
    if (m) {
      const o = await store.getOpportunity(env.DB, m[1]);
      if (!o) return json({ error: 'not found' }, 404);
      return url.searchParams.get('view') === 'html' ? html(renderOpportunity(o)) : json(o);
    }

    if (path === '/sources') return json({ sources: await store.listSources(env.DB) });
    if (path === '/runs') return json({ runs: await store.recentRuns(env.DB, 20) });

    return json({ error: 'not found' }, 404);
  } catch (e) {
    // Do not leak internals; details go to the events table on runs.
    console.error('request failed', e?.message);
    return json({ error: 'internal error' }, 500);
  }
}
