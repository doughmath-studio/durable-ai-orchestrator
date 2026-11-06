-- PROFIT ENGINE — D1 schema v1.
-- Unknown values are stored as NULL (never as 0). Amounts are in the currency named next to them.

CREATE TABLE IF NOT EXISTS sources (
  id                  TEXT PRIMARY KEY,
  name                TEXT NOT NULL,
  url                 TEXT NOT NULL,
  type                TEXT NOT NULL CHECK (type IN ('rss', 'atom', 'feed', 'ted_api')),
  country             TEXT,
  category            TEXT NOT NULL,
  terms               TEXT NOT NULL,             -- human-readable summary of the terms of use
  terms_status        TEXT NOT NULL CHECK (terms_status IN ('ok', 'attribution_required', 'needs_review', 'restricted')),
  origin              TEXT NOT NULL DEFAULT 'registry' CHECK (origin IN ('registry', 'owner_feeds')),
  enabled             INTEGER NOT NULL DEFAULT 1,
  verification_status TEXT NOT NULL DEFAULT 'unverified'
                      CHECK (verification_status IN ('unverified', 'verified', 'failed', 'blocked_by_terms', 'needs_terms_review')),
  last_checked_at     TEXT,
  last_success_at     TEXT,
  last_error          TEXT,
  consecutive_failures INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS opportunities (
  id                  TEXT PRIMARY KEY,          -- sha-256 of the canonical URL (first 24 hex chars)
  url_hash            TEXT NOT NULL UNIQUE,
  title_hash          TEXT NOT NULL,
  title               TEXT NOT NULL,
  source_id           TEXT NOT NULL,
  source_url          TEXT NOT NULL,
  discovered_at       TEXT NOT NULL,
  published_at        TEXT,
  category            TEXT NOT NULL,
  description         TEXT,
  buyer               TEXT,
  buyer_contact_path  TEXT,
  demand_evidence     TEXT,
  demand_level        TEXT NOT NULL CHECK (demand_level IN ('confirmed', 'indicated', 'unknown')),
  monetization        TEXT,
  revenue_min         REAL,
  revenue_max         REAL,
  revenue_currency    TEXT,
  revenue_basis       TEXT NOT NULL,
  cost_cash           REAL,
  cost_hours          REAL,
  cost_basis          TEXT NOT NULL,
  margin_pct          REAL,
  complexity          TEXT CHECK (complexity IN ('low', 'medium', 'high')),
  risks               TEXT NOT NULL DEFAULT '[]', -- JSON array of strings
  risk_level          TEXT NOT NULL CHECK (risk_level IN ('low', 'medium', 'high')),
  score               REAL NOT NULL,
  confidence          REAL NOT NULL,
  score_breakdown     TEXT NOT NULL,              -- JSON, see src/agents/valuator.js
  product_draft       TEXT,
  sales_plan          TEXT,
  ai_notes            TEXT,
  next_step           TEXT NOT NULL,
  status              TEXT NOT NULL CHECK (status IN ('new', 'validated', 'rejected', 'ready_for_commercial_check')),
  status_reason       TEXT
);

CREATE INDEX IF NOT EXISTS idx_opp_score ON opportunities (score DESC);
CREATE INDEX IF NOT EXISTS idx_opp_status ON opportunities (status);
CREATE INDEX IF NOT EXISTS idx_opp_title_hash ON opportunities (title_hash);

CREATE TABLE IF NOT EXISTS runs (
  id              TEXT PRIMARY KEY,
  trigger         TEXT NOT NULL CHECK (trigger IN ('cron', 'manual')),
  started_at      TEXT NOT NULL,
  finished_at     TEXT,
  status          TEXT NOT NULL CHECK (status IN ('running', 'ok', 'partial', 'failed')),
  sources_checked INTEGER NOT NULL DEFAULT 0,
  items_fetched   INTEGER NOT NULL DEFAULT 0,
  items_new       INTEGER NOT NULL DEFAULT 0,
  items_duplicate INTEGER NOT NULL DEFAULT 0,
  errors_count    INTEGER NOT NULL DEFAULT 0,
  ai_calls        INTEGER NOT NULL DEFAULT 0,
  summary         TEXT
);

CREATE INDEX IF NOT EXISTS idx_runs_started ON runs (started_at DESC);

CREATE TABLE IF NOT EXISTS events (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id     TEXT,
  at         TEXT NOT NULL,
  level      TEXT NOT NULL CHECK (level IN ('info', 'warn', 'error', 'critical')),
  module     TEXT NOT NULL,
  message    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_at ON events (at DESC);
