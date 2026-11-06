import { test } from 'node:test';
import assert from 'node:assert/strict';
import worker from '../src/index.js';
import { handleFetch } from '../src/app.js';
import { runPipeline } from '../src/orchestrator.js';
import { createD1, fakeFetch, rss, xmlResponse } from './helpers.js';

const FEED = 'https://feeds.example.org/requests.xml';
const recent = new Date(Date.now() - 86400000).toUTCString();
const feedBody = rss([
  { title: 'Looking for a freelancer to automate our invoices in Google Sheets', link: 'https://example.org/p/1?utm_source=rss', description: 'Budget: $300 fixed price', pubDate: recent },
  { title: 'Looking for a freelancer to automate our invoices in Google Sheets', link: 'https://example.org/p/1', description: 'same post again', pubDate: recent },
  { title: 'Earn fast: guaranteed income, small registration fee required', link: 'https://example.org/p/2', pubDate: recent },
  { title: 'Thoughts on the weather in spring', link: 'https://example.org/p/3', pubDate: recent },
]);

function env(extra = {}) {
  return { DB: createD1(), FEEDS: JSON.stringify([FEED]), RUN_TOKEN: 'test-run-token-123', ...extra };
}

test('pipeline: collects, de-duplicates, scores, rejects scams, logs the run; re-run is idempotent', async () => {
  const e = env();
  const f = fakeFetch({ [FEED]: xmlResponse(feedBody) }); // registry sources get 404 → recorded as failed
  const r1 = await runPipeline(e, { fetchImpl: f });
  assert.equal(r1.status, 'partial');
  assert.equal(r1.run.items_fetched, 4);
  assert.equal(r1.run.items_new, 3);
  assert.equal(r1.run.items_duplicate, 1);

  const rows = e.DB._raw.prepare('SELECT title, status, demand_level, revenue_min, risk_level FROM opportunities ORDER BY score DESC').all();
  const byTitle = Object.fromEntries(rows.map((r) => [r.title.slice(0, 12), r]));
  assert.equal(byTitle['Looking for '].demand_level, 'indicated');
  assert.equal(byTitle['Looking for '].revenue_min, 300);
  assert.equal(byTitle['Earn fast: g'].status, 'rejected');
  assert.equal(byTitle['Earn fast: g'].risk_level, 'high');
  assert.equal(byTitle['Thoughts on '].demand_level, 'unknown');
  assert.equal(byTitle['Thoughts on '].revenue_min, null);

  const src = e.DB._raw.prepare("SELECT verification_status, last_success_at FROM sources WHERE id = 'owner-feed-1'").get();
  assert.equal(src.verification_status, 'verified');
  assert.ok(src.last_success_at);
  const reddit = e.DB._raw.prepare("SELECT verification_status FROM sources WHERE id = 'reddit-forhire'").get();
  assert.equal(reddit.verification_status, 'blocked_by_terms');
  assert.ok(!f.calls.some((c) => c.url.includes('reddit.com')), 'restricted source must never be fetched');

  const r2 = await runPipeline(e, { fetchImpl: f });
  assert.equal(r2.run.items_new, 0);
  assert.equal(r2.run.items_duplicate, 4);
  assert.equal(e.DB._raw.prepare('SELECT COUNT(*) n FROM opportunities').get().n, 3);
  assert.equal(e.DB._raw.prepare('SELECT COUNT(*) n FROM runs').get().n, 2);
});

test('pipeline continues when OmniRoute is down', async () => {
  const e = env({ AI_ENABLED: 'true', OMNIROUTE_BASE_URL: 'https://ai.example.org', OMNIROUTE_MODEL: 'auto/free', OMNIROUTE_API_KEY: 'omni-secret-key-xyz' });
  const f = fakeFetch({ [FEED]: xmlResponse(feedBody), 'https://ai.example.org/v1/chat/completions': () => new Response('bad gateway', { status: 502 }) });
  const r = await runPipeline(e, { fetchImpl: f });
  assert.equal(r.run.items_new, 3);
  assert.equal(r.run.ai_calls, 1, 'stops calling a broken AI endpoint after the first failure');
  const ev = e.DB._raw.prepare("SELECT message FROM events WHERE module = 'ai'").all();
  assert.equal(ev.length, 1);
  const all = JSON.stringify(e.DB._raw.prepare('SELECT * FROM events').all());
  assert.ok(!all.includes('omni-secret-key-xyz'), 'API key never reaches the log');
  const aiCall = f.calls.find((c) => c.url.includes('ai.example.org'));
  assert.equal(aiCall.init.headers.authorization, 'Bearer omni-secret-key-xyz');
});

test('POST /run requires the token; concurrent run is locked', async () => {
  const e = env();
  const deps = { fetchImpl: fakeFetch({ [FEED]: xmlResponse(feedBody) }) };
  const req = (headers = {}, body) => new Request('https://w.example/run', { method: 'POST', headers, body });

  assert.equal((await handleFetch(req(), e, {}, deps)).status, 401);
  assert.equal((await handleFetch(req({ authorization: 'Bearer wrong' }), e, {}, deps)).status, 401);
  assert.equal((await handleFetch(new Request('https://w.example/run'), e, {}, deps)).status, 405);
  const ok = await handleFetch(req({ authorization: 'Bearer test-run-token-123' }), e, {}, deps);
  assert.equal(ok.status, 200);
  assert.equal((await ok.json()).run.items_new, 3);

  const form = await handleFetch(req({ 'content-type': 'application/x-www-form-urlencoded' }, 'token=test-run-token-123'), e, {}, deps);
  assert.equal(form.status, 200);

  const noToken = await handleFetch(req({ authorization: 'Bearer x' }), { ...e, RUN_TOKEN: '' }, {}, deps);
  assert.equal(noToken.status, 503, 'fails closed when RUN_TOKEN is not configured');

  e.DB._raw.prepare("INSERT INTO runs (id, trigger, started_at, status) VALUES ('busy','cron',?, 'running')").run(new Date().toISOString());
  const locked = await handleFetch(req({ authorization: 'Bearer test-run-token-123' }), e, {}, deps);
  assert.equal(locked.status, 409);
});

test('GET endpoints: health, pagination, item, CSV, dashboard, read token', async () => {
  const e = env();
  await runPipeline(e, { fetchImpl: fakeFetch({ [FEED]: xmlResponse(feedBody) }) });
  const get = (path, headers = {}, en = e) => handleFetch(new Request(`https://w.example${path}`, { headers }), en, {});

  const health = await (await get('/health')).json();
  assert.equal(health.ok, true);
  assert.equal(health.ai_enabled, false);

  const p1 = await (await get('/opportunities?limit=2&page=1')).json();
  assert.deepEqual([p1.total, p1.pages, p1.items.length], [3, 2, 2]);
  assert.ok(p1.items[0].score >= p1.items[1].score);
  assert.ok(!('url_hash' in p1.items[0]));
  const p2 = await (await get('/opportunities?limit=2&page=2')).json();
  assert.equal(p2.items.length, 1);
  const rejected = await (await get('/opportunities?status=rejected')).json();
  assert.equal(rejected.total, 1);

  const one = await get(`/opportunities/${p1.items[0].id}`);
  assert.equal(one.status, 200);
  assert.equal((await one.json()).id, p1.items[0].id);
  assert.equal((await get('/opportunities/deadbeefdeadbeef')).status, 404);
  const page = await get(`/opportunities/${p1.items[0].id}?view=html`);
  assert.match(await page.text(), /DRAFT MESSAGE \(NOT SENT/);

  const csv = await get('/opportunities.csv');
  assert.match(csv.headers.get('content-type'), /text\/csv/);
  assert.equal((await csv.text()).trim().split('\r\n').length, 4);

  const dash = await (await get('/')).text();
  assert.match(dash, /found/);
  assert.match(dash, /not earned money/);

  const locked = { ...e, READ_TOKEN: 'read-token-456789' };
  assert.equal((await get('/opportunities', {}, locked)).status, 401);
  assert.equal((await get('/opportunities', { authorization: 'Bearer read-token-456789' }, locked)).status, 200);
  const viaKey = await get('/?key=read-token-456789', {}, locked);
  assert.equal(viaKey.status, 303);
  assert.match(viaKey.headers.get('set-cookie'), /HttpOnly; Secure/);
  assert.equal((await get('/health', {}, locked)).status, 200, '/health stays public');
});

test('scheduled handler runs the pipeline via waitUntil', async () => {
  const e = env({ FEEDS: '[]' });
  const original = globalThis.fetch;
  globalThis.fetch = fakeFetch({});
  try {
    const pending = [];
    await worker.scheduled({ cron: '0 */6 * * *' }, e, { waitUntil: (p) => pending.push(p) });
    await Promise.all(pending);
  } finally {
    globalThis.fetch = original;
  }
  const run = e.DB._raw.prepare('SELECT trigger, status FROM runs').get();
  assert.equal(run.trigger, 'cron');
  assert.equal(run.status, 'failed'); // every registry source answered 404 in this offline test
  const crit = e.DB._raw.prepare("SELECT COUNT(*) n FROM events WHERE level = 'critical'").get();
  assert.equal(crit.n, 1);
});
