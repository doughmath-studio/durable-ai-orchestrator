// D1 access. Bulk writes go through json_each(?) so one run stays far below the free-plan limit of
// 50 D1 queries per invocation and the 100 bound-parameters-per-query limit.

const OPP_COLUMNS = [
  'id', 'url_hash', 'title_hash', 'title', 'source_id', 'source_url', 'discovered_at', 'published_at', 'category',
  'description', 'buyer', 'buyer_contact_path', 'demand_evidence', 'demand_level', 'monetization', 'revenue_min',
  'revenue_max', 'revenue_currency', 'revenue_basis', 'cost_cash', 'cost_hours', 'cost_basis', 'margin_pct',
  'complexity', 'risks', 'risk_level', 'score', 'confidence', 'score_breakdown', 'product_draft', 'sales_plan',
  'ai_notes', 'next_step', 'status', 'status_reason',
];
export const PUBLIC_COLUMNS = OPP_COLUMNS.filter((c) => c !== 'url_hash' && c !== 'title_hash');

const STALE_RUN_MS = 20 * 60 * 1000;
const CHUNK = 40;

function chunks(arr, n) {
  const out = [];
  for (let i = 0; i < arr.length; i += n) out.push(arr.slice(i, i + n));
  return out;
}

export async function syncSources(db, sources) {
  if (!sources.length) return;
  await db
    .prepare(
      `INSERT INTO sources (id, name, url, type, country, category, terms, terms_status, origin, enabled, verification_status)
       SELECT json_extract(value,'$.id'), json_extract(value,'$.name'), json_extract(value,'$.url'), json_extract(value,'$.type'),
              json_extract(value,'$.country'), json_extract(value,'$.category'), json_extract(value,'$.terms'),
              json_extract(value,'$.terms_status'), json_extract(value,'$.origin'), 1,
              CASE WHEN json_extract(value,'$.terms_status') = 'restricted' THEN 'blocked_by_terms' ELSE 'unverified' END
       FROM json_each(?)
       WHERE true
       ON CONFLICT(id) DO UPDATE SET
         name = excluded.name, type = excluded.type, country = excluded.country, category = excluded.category,
         terms = excluded.terms, terms_status = excluded.terms_status,
         verification_status = CASE
           WHEN excluded.terms_status = 'restricted' THEN 'blocked_by_terms'
           WHEN sources.url <> excluded.url THEN 'unverified'
           ELSE sources.verification_status END,
         url = excluded.url`,
    )
    .bind(JSON.stringify(sources.map((s) => ({ origin: 'registry', ...s }))))
    .run();
}

export async function listSources(db) {
  const { results } = await db.prepare('SELECT * FROM sources ORDER BY id').all();
  return results;
}

export async function saveSourceResults(db, results) {
  if (!results.length) return;
  await db
    .prepare(
      `UPDATE sources SET
         verification_status = (SELECT json_extract(value,'$.verification_status') FROM json_each(?1) WHERE json_extract(value,'$.id') = sources.id),
         last_checked_at     = (SELECT json_extract(value,'$.checked_at') FROM json_each(?1) WHERE json_extract(value,'$.id') = sources.id),
         last_success_at     = COALESCE((SELECT json_extract(value,'$.success_at') FROM json_each(?1) WHERE json_extract(value,'$.id') = sources.id), last_success_at),
         last_error          = (SELECT json_extract(value,'$.error') FROM json_each(?1) WHERE json_extract(value,'$.id') = sources.id),
         consecutive_failures = CASE WHEN (SELECT json_extract(value,'$.error') FROM json_each(?1) WHERE json_extract(value,'$.id') = sources.id) IS NULL
                                     THEN 0 ELSE consecutive_failures + 1 END
       WHERE id IN (SELECT json_extract(value,'$.id') FROM json_each(?1))`,
    )
    .bind(JSON.stringify(results))
    .run();
}

/** Returns the subset of url hashes and title hashes already stored. */
export async function findExisting(db, urlHashes, titleHashes) {
  const known = { url: new Set(), title: new Set() };
  if (!urlHashes.length) return known;
  const { results } = await db
    .prepare(
      `SELECT url_hash, title_hash FROM opportunities
       WHERE url_hash IN (SELECT value FROM json_each(?1)) OR title_hash IN (SELECT value FROM json_each(?2))`,
    )
    .bind(JSON.stringify(urlHashes), JSON.stringify(titleHashes))
    .all();
  for (const r of results) {
    known.url.add(r.url_hash);
    known.title.add(r.title_hash);
  }
  return known;
}

/** Idempotent insert: an existing url_hash is ignored (INSERT OR IGNORE). Returns rows actually inserted. */
export async function insertOpportunities(db, rows) {
  let inserted = 0;
  const select = OPP_COLUMNS.map((c) => `json_extract(value,'$.${c}')`).join(', ');
  for (const part of chunks(rows, CHUNK)) {
    const res = await db
      .prepare(`INSERT OR IGNORE INTO opportunities (${OPP_COLUMNS.join(', ')}) SELECT ${select} FROM json_each(?)`)
      .bind(JSON.stringify(part))
      .run();
    inserted += res.meta?.changes ?? 0;
  }
  return inserted;
}

/** Starts a run unless another one is in progress (lock). Returns false if locked. */
export async function startRun(db, run, now = new Date()) {
  const staleBefore = new Date(now.getTime() - STALE_RUN_MS).toISOString();
  await db
    .prepare(`UPDATE runs SET status = 'failed', finished_at = ?1, summary = 'abandoned: exceeded time limit' WHERE status = 'running' AND started_at <= ?2`)
    .bind(now.toISOString(), staleBefore)
    .run();
  const res = await db
    .prepare(
      `INSERT INTO runs (id, trigger, started_at, status)
       SELECT ?1, ?2, ?3, 'running' WHERE NOT EXISTS (SELECT 1 FROM runs WHERE status = 'running')`,
    )
    .bind(run.id, run.trigger, run.started_at)
    .run();
  return (res.meta?.changes ?? 0) === 1;
}

export async function finishRun(db, run) {
  await db
    .prepare(
      `UPDATE runs SET finished_at = ?2, status = ?3, sources_checked = ?4, items_fetched = ?5, items_new = ?6,
         items_duplicate = ?7, errors_count = ?8, ai_calls = ?9, summary = ?10 WHERE id = ?1`,
    )
    .bind(run.id, run.finished_at, run.status, run.sources_checked, run.items_fetched, run.items_new, run.items_duplicate, run.errors_count, run.ai_calls, run.summary)
    .run();
}

export async function logEvents(db, events) {
  if (!events.length) return;
  await db
    .prepare(
      `INSERT INTO events (run_id, at, level, module, message)
       SELECT json_extract(value,'$.run_id'), json_extract(value,'$.at'), json_extract(value,'$.level'),
              json_extract(value,'$.module'), json_extract(value,'$.message') FROM json_each(?)`,
    )
    .bind(JSON.stringify(events))
    .run();
}

export async function recentRuns(db, limit = 5) {
  const { results } = await db.prepare('SELECT * FROM runs ORDER BY started_at DESC LIMIT ?').bind(limit).all();
  return results;
}

export async function listOpportunities(db, { page = 1, limit = 20, status = null, minScore = null } = {}) {
  const where = [];
  const args = [];
  if (status) {
    where.push(`status = ?${args.length + 1}`);
    args.push(status);
  }
  if (minScore != null) {
    where.push(`score >= ?${args.length + 1}`);
    args.push(minScore);
  }
  const w = where.length ? `WHERE ${where.join(' AND ')}` : '';
  const total = (await db.prepare(`SELECT COUNT(*) AS n FROM opportunities ${w}`).bind(...args).first())?.n ?? 0;
  const { results } = await db
    .prepare(`SELECT ${PUBLIC_COLUMNS.join(', ')} FROM opportunities ${w} ORDER BY score DESC, discovered_at DESC LIMIT ?${args.length + 1} OFFSET ?${args.length + 2}`)
    .bind(...args, limit, (page - 1) * limit)
    .all();
  return { total, page, limit, pages: Math.max(1, Math.ceil(total / limit)), items: results.map(decodeRow) };
}

export async function getOpportunity(db, id) {
  const row = await db.prepare(`SELECT ${PUBLIC_COLUMNS.join(', ')} FROM opportunities WHERE id = ?`).bind(id).first();
  return row ? decodeRow(row) : null;
}

export async function stats(db) {
  const row = await db
    .prepare(
      `SELECT COUNT(*) AS found,
              SUM(CASE WHEN status IN ('validated','ready_for_commercial_check') THEN 1 ELSE 0 END) AS validated,
              SUM(CASE WHEN demand_level = 'confirmed' AND status <> 'rejected' THEN 1 ELSE 0 END) AS demand_confirmed,
              SUM(CASE WHEN status = 'ready_for_commercial_check' THEN 1 ELSE 0 END) AS ready,
              SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) AS rejected
       FROM opportunities`,
    )
    .first();
  const n = (v) => Number(v ?? 0);
  return { found: n(row?.found), validated: n(row?.validated), demand_confirmed: n(row?.demand_confirmed), ready: n(row?.ready), rejected: n(row?.rejected) };
}

export async function recentErrors(db, limit = 10) {
  const { results } = await db
    .prepare(`SELECT at, level, module, message FROM events WHERE level IN ('error','critical') ORDER BY id DESC LIMIT ?`)
    .bind(limit)
    .all();
  return results;
}

function decodeRow(r) {
  const out = { ...r };
  for (const k of ['risks', 'score_breakdown', 'ai_notes']) {
    if (typeof out[k] === 'string') {
      try {
        out[k] = JSON.parse(out[k]);
      } catch {
        /* keep string */
      }
    }
  }
  return out;
}
