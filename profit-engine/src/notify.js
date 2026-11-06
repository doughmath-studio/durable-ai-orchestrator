// Critical-only notifications. Off unless TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID secrets are set (free).
// Only the system's own failure messages are sent — never messages to clients.
import { fetchWithRetry } from './http.js';
import { redact, truncate } from './util.js';
import { secretsOf } from './config.js';

export async function notifyCritical(cfg, text, fetchImpl = globalThis.fetch) {
  const { telegramToken, telegramChatId } = cfg.notify;
  if (!telegramToken || !telegramChatId) return false;
  await fetchWithRetry(
    `https://api.telegram.org/bot${telegramToken}/sendMessage`,
    {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ chat_id: telegramChatId, text: truncate(`PROFIT ENGINE — critical\n${redact(text, secretsOf(cfg))}`, 3500) }),
    },
    { attempts: 2, timeoutMs: 8000, fetchImpl },
  );
  return true;
}
