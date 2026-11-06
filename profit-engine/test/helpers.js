// Test helpers: a D1-compatible wrapper over node:sqlite (real SQLite, real schema) and a fake fetch.
import { DatabaseSync } from 'node:sqlite';
import { readFileSync } from 'node:fs';

const SCHEMA = readFileSync(new URL('../migrations/0001_init.sql', import.meta.url), 'utf8');

export function createD1() {
  const db = new DatabaseSync(':memory:');
  db.exec(SCHEMA);
  const statement = (sql, args = []) => ({
    bind: (...a) => statement(sql, a),
    async run() {
      const r = db.prepare(sql).run(...args);
      return { success: true, meta: { changes: Number(r.changes) } };
    },
    async all() {
      return { success: true, results: db.prepare(sql).all(...args).map((r) => ({ ...r })) };
    },
    async first() {
      const r = db.prepare(sql).get(...args);
      return r ? { ...r } : null;
    },
  });
  return { prepare: (sql) => statement(sql), _raw: db };
}

/** routes: { 'https://host/path': () => Response | Promise<Response> | throws } ; unknown URLs → 404 */
export function fakeFetch(routes) {
  const calls = [];
  const fn = async (url, init = {}) => {
    calls.push({ url: String(url), init });
    const handler = routes[String(url)];
    if (!handler) return new Response('not found', { status: 404 });
    return handler(init, calls.filter((c) => c.url === String(url)).length);
  };
  fn.calls = calls;
  return fn;
}

export const rss = (items) => `<?xml version="1.0"?><rss version="2.0"><channel><title>Test feed</title>
${items
  .map(
    (i) => `<item><title><![CDATA[${i.title}]]></title><link>${i.link}</link><description>${i.description || ''}</description>
<pubDate>${i.pubDate || new Date().toUTCString()}</pubDate></item>`,
  )
  .join('\n')}</channel></rss>`;

export const xmlResponse = (body) => () => new Response(body, { status: 200, headers: { 'content-type': 'application/rss+xml' } });
