// Optional AI enrichment through OmniRoute (self-hosted, OpenAI-compatible).
// Confirmed from OmniRoute docs/reference/API_REFERENCE.md (v3.8.x): POST {base}/v1/chat/completions,
// header "Authorization: Bearer <OmniRoute API key>", model as "provider/model" or a combo name.
// OmniRoute runs as your own server (default http://localhost:20128). A Cloudflare Worker can only use it
// if it is reachable over public HTTPS. When it is off or fails, the pipeline continues without AI.
import { fetchWithRetry } from './http.js';
import { truncate } from './util.js';

export async function enrichWithAI(item, ai, fetchImpl = globalThis.fetch) {
  if (!ai.enabled) return null;
  const url = /\/v1$/.test(ai.baseUrl) ? `${ai.baseUrl}/chat/completions` : `${ai.baseUrl}/v1/chat/completions`;
  const headers = { 'content-type': 'application/json' };
  if (ai.apiKey) headers.authorization = `Bearer ${ai.apiKey}`;
  const prompt = [
    'You analyse a business opportunity for a solo operator with zero budget.',
    'Reply with JSON only: {"buyer_need": string, "offer_angle": string, "extra_risks": string[], "is_genuine_request": "yes"|"no"|"unclear"}.',
    'Do not invent prices, budgets or facts that are not in the text.',
    `Title: ${truncate(item.title, 300)}`,
    `Text: ${truncate(item.description || '', 1500)}`,
  ].join('\n');
  const res = await fetchWithRetry(
    url,
    {
      method: 'POST',
      headers,
      body: JSON.stringify({ model: ai.model, messages: [{ role: 'user', content: prompt }], temperature: 0.2, max_tokens: 400 }),
    },
    { attempts: 2, timeoutMs: ai.timeoutMs, fetchImpl },
  );
  const json = await res.json();
  const content = json?.choices?.[0]?.message?.content;
  if (typeof content !== 'string' || !content.trim()) throw new Error('AI response has no message content');
  const m = /\{[\s\S]*\}/.exec(content);
  try {
    const parsed = JSON.parse(m ? m[0] : content);
    return JSON.stringify(parsed).slice(0, 2000);
  } catch {
    return JSON.stringify({ raw: truncate(content, 1500) });
  }
}
