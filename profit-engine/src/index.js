// PROFIT ENGINE — Cloudflare Worker entry point. Only the default export is allowed here:
// workerd treats every named export of the main module as an entrypoint.
import { handleFetch } from './app.js';
import { runPipeline } from './orchestrator.js';

export default {
  fetch: (request, env, ctx) => handleFetch(request, env, ctx),
  scheduled: (event, env, ctx) => ctx.waitUntil(runPipeline(env, { trigger: 'cron' })),
};
