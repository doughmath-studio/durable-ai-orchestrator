// Reads configuration from the Worker environment. Every paid or external feature is OFF by default.
import { clampInt } from './util.js';

export function loadConfig(env = {}) {
  let feeds = [];
  if (env.FEEDS) {
    try {
      const parsed = JSON.parse(env.FEEDS);
      if (Array.isArray(parsed)) feeds = parsed.filter((u) => typeof u === 'string' && /^https:\/\//i.test(u));
    } catch {
      feeds = [];
    }
  }
  const aiEnabled =
    String(env.AI_ENABLED || 'false').toLowerCase() === 'true' && Boolean(env.OMNIROUTE_BASE_URL) && Boolean(env.OMNIROUTE_MODEL);
  return {
    feeds,
    // Free plan: 50 external subrequests per invocation. Keep a wide margin.
    maxSourcesPerRun: clampInt(env.MAX_SOURCES_PER_RUN, 1, 20, 8),
    maxItemsPerSource: clampInt(env.MAX_ITEMS_PER_SOURCE, 1, 100, 25),
    fetchTimeoutMs: clampInt(env.FETCH_TIMEOUT_MS, 1000, 30000, 10000),
    fetchAttempts: clampInt(env.FETCH_ATTEMPTS, 1, 4, 3),
    maxItemAgeDays: clampInt(env.MAX_ITEM_AGE_DAYS, 1, 365, 45),
    readyScoreThreshold: clampInt(env.READY_SCORE_THRESHOLD, 0, 100, 55),
    tedQuery: env.TED_QUERY || '',
    ai: {
      enabled: aiEnabled,
      baseUrl: (env.OMNIROUTE_BASE_URL || '').replace(/\/+$/, ''),
      apiKey: env.OMNIROUTE_API_KEY || '',
      model: env.OMNIROUTE_MODEL || '',
      maxCalls: clampInt(env.AI_MAX_CALLS_PER_RUN, 0, 10, 3),
      timeoutMs: clampInt(env.AI_TIMEOUT_MS, 2000, 30000, 20000),
    },
    notify: {
      telegramToken: env.TELEGRAM_BOT_TOKEN || '',
      telegramChatId: env.TELEGRAM_CHAT_ID || '',
    },
    runToken: env.RUN_TOKEN || '',
    readToken: env.READ_TOKEN || '',
    userAgent: 'ProfitEngine/0.1 (+opportunity research; contact via repository owner)',
  };
}

export function secretsOf(config) {
  return [config.runToken, config.readToken, config.ai.apiKey, config.notify.telegramToken].filter(Boolean);
}
