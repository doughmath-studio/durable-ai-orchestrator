import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseFeed, normalizeTedNotices } from '../src/agents/collector.js';
import { canonicalUrl, safeEqual, redact } from '../src/util.js';
import { valuate, parseStatedBudget, WEIGHTS } from '../src/agents/valuator.js';
import { assessRisk } from '../src/agents/risk.js';
import { findBuyer } from '../src/agents/buyer.js';
import { validate } from '../src/agents/validator.js';
import { fetchWithRetry } from '../src/http.js';
import { toCsv, csvCell } from '../src/csv.js';
import { rss } from './helpers.js';

const serviceSource = { id: 'x', category: 'service_request', terms_status: 'ok', verification_status: 'verified' };

test('parseFeed reads RSS 2.0 (CDATA, entities) and Atom 1.0', () => {
  const r = parseFeed(rss([{ title: 'Need a <b>Shopify</b> dev &amp; designer', link: 'https://ex.com/a?x=1&amp;y=2', description: '<p>Budget $400</p>' }]));
  assert.equal(r.kind, 'rss');
  assert.equal(r.items.length, 1);
  assert.equal(r.items[0].title, 'Need a Shopify dev & designer');
  assert.equal(r.items[0].url, 'https://ex.com/a?x=1&y=2');
  assert.equal(r.items[0].description, 'Budget $400');
  assert.ok(r.items[0].published_at);

  const atom = `<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>A</title>
    <entry><title>Looking for a data analyst</title><link rel="alternate" href="https://ex.org/e1"/><updated>2026-11-01T10:00:00Z</updated>
    <author><name>Acme</name></author><summary>Short summary</summary></entry></feed>`;
  const a = parseFeed(atom);
  assert.equal(a.kind, 'atom');
  assert.deepEqual([a.items[0].url, a.items[0].author, a.items[0].published_at], ['https://ex.org/e1', 'Acme', '2026-11-01T10:00:00.000Z']);

  assert.throws(() => parseFeed('<html><body>not a feed</body></html>'), /not an RSS\/Atom feed/);
});

test('TED notices are normalised; missing values stay null', () => {
  const r = normalizeTedNotices({
    totalNoticeCount: 2,
    notices: [
      {
        'publication-number': '123456-2026',
        'notice-title': { eng: 'Poland – IT services – Data platform' },
        'buyer-name': { pol: ['Gmina Test'] },
        'buyer-country': ['POL'],
        'publication-date': '2026-11-04+01:00',
        'classification-cpv': ['72000000', '72000000', '72300000'],
      },
      { 'publication-number': '999-2026' }, // no title → skipped
    ],
  });
  assert.equal(r.items.length, 1);
  const it = r.items[0];
  assert.equal(it.url, 'https://ted.europa.eu/en/notice/-/detail/123456-2026');
  assert.equal(it.buyer, 'Gmina Test');
  assert.equal(it.cpv, '72000000,72300000');
  assert.equal(it.deadline, null);
  assert.equal(it.published_at, '2026-11-03T23:00:00.000Z');
  assert.throws(() => normalizeTedNotices({ error: 'x' }), /no "notices"/);
});

test('canonicalUrl removes tracking params, fragment, www and trailing slash', () => {
  assert.equal(canonicalUrl('https://WWW.Ex.com/job/1/?utm_source=x&b=2&a=1#top'), 'https://ex.com/job/1?a=1&b=2');
  assert.equal(canonicalUrl('https://ex.com/job/1'), canonicalUrl('https://www.ex.com/job/1/?fbclid=abc'));
});

test('valuator: unknown revenue stays null and lowers confidence; stated budget is used', () => {
  const now = new Date('2026-11-06T00:00:00Z');
  const item = { title: 'Looking for a developer to build a scraper script', description: 'Details inside', published_at: '2026-11-05T00:00:00Z' };
  const buyer = findBuyer(item, serviceSource);
  const risk = assessRisk(item, serviceSource);
  const v = valuate(item, serviceSource, buyer, risk, { now });
  assert.equal(v.revenue.min, null);
  assert.match(v.revenue.basis, /^unknown/);
  assert.equal(v.margin_pct, null);
  assert.equal(v.breakdown.value.value, null);
  assert.equal(v.confidence, (100 - WEIGHTS.value) / 100);

  const item2 = { ...item, description: 'Budget: $400 fixed price' };
  const v2 = valuate(item2, serviceSource, findBuyer(item2, serviceSource), assessRisk(item2, serviceSource), { now });
  assert.deepEqual([v2.revenue.min, v2.revenue.currency], [400, 'USD']);
  assert.match(v2.revenue.basis, /stated in source/);
  assert.equal(v2.confidence, 1);
  assert.ok(v2.score > v.score);
  // score is exactly the sum of component points
  const sum = Object.keys(WEIGHTS).reduce((s, k) => s + v2.breakdown[k].points, 0);
  assert.equal(Math.round(sum * 10) / 10, v2.score);

  assert.deepEqual(parseStatedBudget('pay 50 USD per hour'), { min: 50, max: 50, currency: 'USD', hourly: true, raw: '50 USD per hour' });
  assert.equal(parseStatedBudget('no money mentioned'), null);
});

test('risk control rejects scams; validator rejects stale items and unverified sources', () => {
  const scam = { title: 'Easy job, guaranteed income! Small registration fee, telegram only', description: '' };
  assert.equal(assessRisk(scam, serviceSource).risk_level, 'high');
  assert.equal(assessRisk({ title: 'Need a logo for my bakery', description: '' }, serviceSource).risk_level, 'low');

  const now = new Date('2026-11-06T00:00:00Z');
  const old = { title: 'Need a logo for my bakery', url: 'https://ex.com/1', published_at: '2026-01-01T00:00:00Z' };
  assert.equal(validate(old, serviceSource, { now }).ok, false);
  assert.equal(validate({ ...old, published_at: '2026-11-05T00:00:00Z' }, serviceSource, { now }).ok, true);
  assert.equal(validate({ ...old, published_at: null }, { ...serviceSource, verification_status: 'unverified' }, { now }).ok, false);
});

test('fetchWithRetry retries transient errors only', async () => {
  let n = 0;
  const flaky = async () => (++n < 3 ? new Response('busy', { status: 503 }) : new Response('ok'));
  const res = await fetchWithRetry('https://x', {}, { attempts: 3, fetchImpl: flaky, sleepImpl: async () => {} });
  assert.equal(await res.text(), 'ok');
  assert.equal(n, 3);

  let m = 0;
  const notFound = async () => (m++, new Response('nope', { status: 404 }));
  await assert.rejects(fetchWithRetry('https://x', {}, { attempts: 3, fetchImpl: notFound, sleepImpl: async () => {} }), /HTTP 404/);
  assert.equal(m, 1);

  let k = 0;
  const down = async () => (k++, Promise.reject(new TypeError('connection refused')));
  await assert.rejects(fetchWithRetry('https://x', {}, { attempts: 2, fetchImpl: down, sleepImpl: async () => {} }), /network error/);
  assert.equal(k, 2);
});

test('CSV escapes quotes/newlines and neutralises formulas; tokens compare safely; secrets redacted', () => {
  assert.equal(csvCell('=HYPERLINK("x")'), `"'=HYPERLINK(""x"")"`);
  assert.equal(csvCell('a,b'), '"a,b"');
  assert.equal(csvCell(null), '');
  const csv = toCsv([{ id: '1', title: 'line1\nline2' }], ['id', 'title']);
  assert.equal(csv, 'id,title\r\n1,"line1\nline2"\r\n');
  assert.equal(safeEqual('abc', 'abc'), true);
  assert.equal(safeEqual('abc', 'abd'), false);
  assert.equal(safeEqual('abc', 'abcd'), false);
  assert.equal(redact('key sk-very-secret-123 and Bearer abcdefghijk', ['sk-very-secret-123']), 'key [REDACTED] and Bearer [REDACTED]');
});
