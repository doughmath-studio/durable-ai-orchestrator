// Source registry. Every entry starts as `unverified`: the Worker checks it on its first run
// (reachable + parses as a feed/API response) and records the result in D1 (`sources` table).
//
// terms_status meaning:
//   ok                   - public-sector open data or explicit permission to reuse
//   attribution_required - reuse allowed with a visible link back to the source
//   needs_review         - terms unclear: the Worker checks reachability but does NOT collect items
//   restricted           - terms forbid automated/commercial use: never fetched (kept for transparency)
//
// Nothing here was fetched from the build container (its network is closed). The URLs come from the
// providers' public documentation as known at build time; the first production run is the real check.

export const SOURCE_REGISTRY = [
  {
    id: 'ted-eu-it-services',
    name: 'TED — EU public tenders: IT, software, data & business services',
    url: 'https://api.ted.europa.eu/v3/notices/search',
    type: 'ted_api',
    country: 'EU',
    category: 'public_tender',
    terms:
      'TED data is EU open data; reuse incl. commercial is permitted under Commission Decision 2011/833/EU with source acknowledgement. Anonymous search API, no key.',
    terms_status: 'attribution_required',
  },
  {
    id: 'grants-gov-new',
    name: 'Grants.gov — new federal funding opportunities (RSS)',
    url: 'https://www.grants.gov/rss/GG_NewOppByCategory.xml',
    type: 'rss',
    country: 'US',
    category: 'grant',
    terms: 'US federal government information, public domain (17 U.S.C. §105). Many grants require US entities: check eligibility.',
    terms_status: 'ok',
  },
  {
    id: 'hn-freelance-requests',
    name: 'Hacker News (via hnrss.org) — posts asking for freelancers / tools',
    url: 'https://hnrss.org/newest?q=%22looking+for+a%22+OR+%22seeking+freelancer%22+OR+%22need+someone%22&points=1',
    type: 'rss',
    country: 'GLOBAL',
    category: 'service_request',
    terms: 'hnrss.org is a free third-party RSS mirror of public HN posts; link back to the original post.',
    terms_status: 'attribution_required',
  },
  {
    id: 'remoteok-jobs',
    name: 'Remote OK — remote contract and job posts (RSS)',
    url: 'https://remoteok.com/remote-jobs.rss',
    type: 'rss',
    country: 'GLOBAL',
    category: 'service_request',
    terms: 'Remote OK asks for a link back to the job and mention of Remote OK as source when the feed is reused.',
    terms_status: 'attribution_required',
  },
  {
    id: 'weworkremotely',
    name: 'We Work Remotely — remote jobs (RSS)',
    url: 'https://weworkremotely.com/remote-jobs.rss',
    type: 'rss',
    country: 'GLOBAL',
    category: 'service_request',
    terms: 'Public RSS feed; reuse terms not confirmed. Collected only after the owner reviews the terms.',
    terms_status: 'needs_review',
  },
  {
    id: 'reddit-forhire',
    name: 'Reddit r/forhire',
    url: 'https://www.reddit.com/r/forhire/new/.rss',
    type: 'rss',
    country: 'GLOBAL',
    category: 'service_request',
    terms: 'Reddit Data API terms restrict commercial use of content without an agreement. Not fetched.',
    terms_status: 'restricted',
  },
];

/** Feeds supplied by the owner in the FEEDS variable. The owner is responsible for their terms. */
export function ownerFeedSources(feeds) {
  return feeds.map((url, i) => ({
    id: `owner-feed-${i + 1}`,
    name: `Owner feed #${i + 1}`,
    url,
    type: 'feed',
    country: null,
    category: 'owner_feed',
    terms: 'Added by the owner via FEEDS; owner confirmed the terms allow this use.',
    terms_status: 'ok',
    origin: 'owner_feeds',
  }));
}

export function canCollect(source) {
  return source.terms_status === 'ok' || source.terms_status === 'attribution_required';
}
