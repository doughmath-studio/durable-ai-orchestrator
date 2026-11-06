// COLLECTOR — fetches a source and normalises its items into one shape:
// { url, title, description, published_at, author, buyer, buyer_country, deadline, cpv, extra }
import { fetchWithRetry, FetchError } from '../http.js';
import { decodeEntities, stripHtml, toIsoDate, truncate } from '../util.js';

// ---------- RSS / Atom ----------

function unwrapCdata(s) {
  return String(s || '').replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, '$1');
}

function tag(block, name) {
  const re = new RegExp(`<${name}(?:\\s[^>]*)?>([\\s\\S]*?)</${name}>`, 'i');
  const m = re.exec(block);
  return m ? unwrapCdata(m[1]).trim() : '';
}

function atomLink(block) {
  const links = [...block.matchAll(/<link\b([^>]*?)\/?>/gi)].map((m) => m[1]);
  const attr = (a, n) => (new RegExp(`${n}\\s*=\\s*["']([^"']+)["']`, 'i').exec(a) || [])[1];
  const alt = links.find((a) => !attr(a, 'rel') || attr(a, 'rel') === 'alternate');
  return decodeEntities(attr(alt || links[0] || '', 'href') || '');
}

/** Parses RSS 2.0 and Atom 1.0. Returns { kind, title, items }. Throws on non-feed input. */
export function parseFeed(xml, maxItems = 25) {
  const text = String(xml || '');
  const isAtom = /<feed\b[^>]*>/i.test(text) && /<entry\b/i.test(text);
  const isRss = /<rss\b|<rdf:RDF\b|<channel\b/i.test(text);
  if (!isAtom && !isRss) throw new FetchError('response is not an RSS/Atom feed');
  const blocks = isAtom
    ? [...text.matchAll(/<entry\b[^>]*>([\s\S]*?)<\/entry>/gi)].map((m) => m[1])
    : [...text.matchAll(/<item\b[^>]*>([\s\S]*?)<\/item>/gi)].map((m) => m[1]);
  const head = text.split(isAtom ? /<entry\b/i : /<item\b/i)[0];
  const items = [];
  for (const b of blocks.slice(0, maxItems)) {
    const title = stripHtml(tag(b, 'title'));
    const url = isAtom ? atomLink(b) : decodeEntities(tag(b, 'link') || tag(b, 'guid'));
    if (!title || !/^https?:\/\//i.test(url)) continue;
    const description = truncate(stripHtml(isAtom ? tag(b, 'summary') || tag(b, 'content') : tag(b, 'description') || tag(b, 'content:encoded')), 1500);
    const published = isAtom ? tag(b, 'published') || tag(b, 'updated') : tag(b, 'pubDate') || tag(b, 'dc:date');
    const author = stripHtml(isAtom ? tag(tag(b, 'author'), 'name') || tag(b, 'author') : tag(b, 'dc:creator') || tag(b, 'author'));
    items.push({
      url: url.trim(),
      title: truncate(title, 300),
      description,
      published_at: toIsoDate(published),
      author: author || null,
      buyer: null,
      buyer_country: null,
      deadline: null,
      cpv: null,
      extra: {},
    });
  }
  return { kind: isAtom ? 'atom' : 'rss', title: stripHtml(tag(head, 'title')), items };
}

// ---------- TED (EU tenders) ----------

// CPV divisions: 72 IT services, 48 software packages, 79 business services (research, translation,
// marketing), 73 R&D/consulting. These fit a solo operator who sells digital work.
const DEFAULT_TED_CPV = ['72000000', '48000000', '79300000', '79340000', '73000000'];
const TED_FIELDS_FULL = [
  'publication-number',
  'notice-title',
  'buyer-name',
  'buyer-country',
  'publication-date',
  'deadline-receipt-tender-date-lot',
  'classification-cpv',
  'links',
];
const TED_FIELDS_MIN = ['publication-number', 'notice-title', 'publication-date', 'links'];

function yyyymmdd(d) {
  return d.toISOString().slice(0, 10).replace(/-/g, '');
}

export function defaultTedQuery(now = new Date()) {
  const since = new Date(now.getTime() - 3 * 86400000);
  return `classification-cpv IN (${DEFAULT_TED_CPV.join(' ')}) AND publication-date>=${yyyymmdd(since)}`;
}

/** Picks a readable string from TED multilingual values ({eng: "..."} / {ENG: [...]} / "..." / [...]). */
export function pickLang(v) {
  if (v == null) return null;
  if (typeof v === 'string') return v;
  if (Array.isArray(v)) return v.length ? pickLang(v[0]) : null;
  if (typeof v === 'object') {
    for (const k of ['eng', 'ENG', 'en', 'EN', 'pol', 'POL']) if (v[k] != null) return pickLang(v[k]);
    const first = Object.values(v)[0];
    return first == null ? null : pickLang(first);
  }
  return String(v);
}

export function normalizeTedNotices(json, maxItems = 25) {
  const notices = Array.isArray(json?.notices) ? json.notices : null;
  if (!notices) throw new FetchError('TED response has no "notices" array');
  const items = [];
  for (const n of notices.slice(0, maxItems)) {
    const pub = n['publication-number'];
    const title = pickLang(n['notice-title']);
    if (!pub || !title) continue;
    const html = n.links?.html ? pickLang(n.links.html) : null;
    const url = html && /^https:\/\//.test(html) ? html : `https://ted.europa.eu/en/notice/-/detail/${encodeURIComponent(pub)}`;
    const cpv = n['classification-cpv'];
    items.push({
      url,
      title: truncate(stripHtml(title), 300),
      description: null,
      published_at: toIsoDate(pickLang(n['publication-date'])),
      author: null,
      buyer: pickLang(n['buyer-name']),
      buyer_country: pickLang(n['buyer-country']),
      deadline: toIsoDate(pickLang(n['deadline-receipt-tender-date-lot'])),
      cpv: Array.isArray(cpv) ? [...new Set(cpv.map(String))].slice(0, 5).join(',') : cpv ? String(cpv) : null,
      extra: { publication_number: pub },
    });
  }
  return { kind: 'ted_api', title: 'TED', items, total: json.totalNoticeCount ?? null };
}

async function fetchTed(source, cfg, fetchImpl) {
  const query = cfg.tedQuery || defaultTedQuery();
  const call = (fields) =>
    fetchWithRetry(
      source.url,
      {
        method: 'POST',
        headers: { 'content-type': 'application/json', accept: 'application/json', 'user-agent': cfg.userAgent },
        body: JSON.stringify({ query, fields, page: 1, limit: cfg.maxItemsPerSource, scope: 'ACTIVE', paginationMode: 'PAGE_NUMBER' }),
      },
      { attempts: cfg.fetchAttempts, timeoutMs: cfg.fetchTimeoutMs, fetchImpl },
    );
  let res;
  try {
    res = await call(TED_FIELDS_FULL);
  } catch (e) {
    // A 400 usually means a field name is not accepted: retry once with the minimal set.
    if (e instanceof FetchError && e.status === 400) res = await call(TED_FIELDS_MIN);
    else throw e;
  }
  return normalizeTedNotices(await res.json(), cfg.maxItemsPerSource);
}

export async function collect(source, cfg, fetchImpl = globalThis.fetch) {
  if (source.type === 'ted_api') return fetchTed(source, cfg, fetchImpl);
  const res = await fetchWithRetry(
    source.url,
    { headers: { accept: 'application/rss+xml, application/atom+xml, application/xml, text/xml', 'user-agent': cfg.userAgent } },
    { attempts: cfg.fetchAttempts, timeoutMs: cfg.fetchTimeoutMs, fetchImpl },
  );
  const text = await res.text();
  return parseFeed(text.slice(0, 2_000_000), cfg.maxItemsPerSource);
}
