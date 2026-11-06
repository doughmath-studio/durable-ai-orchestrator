// Small shared helpers. No external dependencies: runs in Cloudflare Workers and in Node 22+.

const TRACKING_PARAMS = /^(utm_|fbclid$|gclid$|mc_cid$|mc_eid$|ref$|ref_src$|igshid$)/i;

/** Canonical form of a URL for de-duplication: lowercase host, no fragment, no tracking params, no trailing slash. */
export function canonicalUrl(raw) {
  let u;
  try {
    u = new URL(String(raw).trim());
  } catch {
    return String(raw || '').trim().toLowerCase();
  }
  u.hash = '';
  u.hostname = u.hostname.toLowerCase().replace(/^www\./, '');
  const keep = [...u.searchParams.entries()].filter(([k]) => !TRACKING_PARAMS.test(k));
  keep.sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
  u.search = '';
  for (const [k, v] of keep) u.searchParams.append(k, v);
  if (u.pathname.length > 1) u.pathname = u.pathname.replace(/\/+$/, '');
  return u.toString();
}

/** Normalised title used to catch the same item re-posted under another URL. */
export function titleFingerprint(title) {
  return String(title || '')
    .toLowerCase()
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/[^\p{L}\p{N}]+/gu, ' ')
    .trim();
}

export async function sha256Hex(text) {
  const data = new TextEncoder().encode(text);
  const digest = await crypto.subtle.digest('SHA-256', data);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

export function nowIso(clock = Date) {
  return new clock().toISOString();
}

/** Parse a date string into ISO-8601, or null when it is missing or invalid (never "today" as a guess). */
export function toIsoDate(value) {
  if (!value) return null;
  const s = String(value).trim();
  // TED style: 2026-11-04+01:00 or 20261104
  const compact = /^(\d{4})(\d{2})(\d{2})$/.exec(s);
  const candidate = compact ? `${compact[1]}-${compact[2]}-${compact[3]}T00:00:00Z` : s.replace(/^(\d{4}-\d{2}-\d{2})([+-]\d{2}:\d{2})$/, '$1T00:00:00$2');
  const t = Date.parse(candidate);
  return Number.isFinite(t) ? new Date(t).toISOString() : null;
}

export function escapeHtml(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/** Strip HTML tags and collapse whitespace. */
export function stripHtml(s) {
  return decodeEntities(String(s || '').replace(/<[^>]*>/g, ' '))
    .replace(/\s+/g, ' ')
    .trim();
}

const NAMED = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ' };
export function decodeEntities(s) {
  return String(s || '').replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (m, e) => {
    if (e[0] === '#') {
      const code = e[1] === 'x' || e[1] === 'X' ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10);
      return Number.isFinite(code) && code > 0 && code < 0x110000 ? String.fromCodePoint(code) : m;
    }
    return NAMED[e.toLowerCase()] ?? m;
  });
}

/** Constant-time string comparison for tokens. */
export function safeEqual(a, b) {
  const x = new TextEncoder().encode(String(a ?? ''));
  const y = new TextEncoder().encode(String(b ?? ''));
  let diff = x.length ^ y.length;
  const n = Math.max(x.length, y.length);
  for (let i = 0; i < n; i++) diff |= (x[i] ?? 0) ^ (y[i] ?? 0);
  return diff === 0;
}

export function truncate(s, n) {
  const str = String(s ?? '');
  return str.length > n ? str.slice(0, n - 1) + '…' : str;
}

/** Remove anything that looks like a secret before it reaches logs or the database. */
export function redact(text, secrets = []) {
  let out = String(text ?? '');
  for (const s of secrets) if (s && s.length >= 6) out = out.split(s).join('[REDACTED]');
  return out.replace(/(bearer\s+)[A-Za-z0-9._\-]{8,}/gi, '$1[REDACTED]');
}

export function clampInt(value, min, max, fallback) {
  const n = Number.parseInt(value, 10);
  if (!Number.isFinite(n)) return fallback;
  return Math.min(max, Math.max(min, n));
}
