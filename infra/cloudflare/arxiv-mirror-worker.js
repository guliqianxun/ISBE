/**
 * ISBE scholarly-API gateway on Cloudflare Workers — unified mirror for every
 * external paper source, for deployments whose egress stalls (e.g. CN servers).
 *
 * Routes (GET/HEAD only, prefix → upstream):
 *   /pdf/<id>            → https://arxiv.org/pdf/<id>          (back-compat)
 *   /arxiv/...           → https://arxiv.org/...               (PDFs, abs pages)
 *   /export-arxiv/...    → https://export.arxiv.org/...        (atom metadata API)
 *   /s2/...              → https://api.semanticscholar.org/... (x-api-key forwarded)
 *   /openalex/...        → https://api.openalex.org/...
 *   /openreview/...      → https://api.openreview.net/...
 *   /hf/...              → https://huggingface.co/...          (daily papers API)
 *
 * Deploy: CF Dashboard → Workers & Pages → Create Worker → paste → Deploy,
 * then Settings → Domains & Routes → Add Custom Domain (custom domain is
 * required in CN; *.workers.dev is usually blocked).
 *
 * Optional hardening: set env var SECRET on the worker; all routes then live
 * under /<SECRET>/... and ISBE-side base URLs include the prefix, e.g.
 *   ARXIV_PDF_BASE_URL=https://api.yourdomain.com/<SECRET>/arxiv
 *   ARXIV_API_BASE_URL=https://api.yourdomain.com/<SECRET>/export-arxiv
 *   S2_BASE_URL=https://api.yourdomain.com/<SECRET>/s2
 *
 * Caching: PDFs are immutable → 7d edge cache; API responses → 15min, so
 * repeated collector retries don't hammer upstreams.
 */

const UPSTREAMS = {
  "arxiv": { base: "https://arxiv.org", ttl: 604800 },
  "export-arxiv": { base: "https://export.arxiv.org", ttl: 900 },
  "s2": { base: "https://api.semanticscholar.org", ttl: 900 },
  "openalex": { base: "https://api.openalex.org", ttl: 900 },
  "openreview": { base: "https://api.openreview.net", ttl: 900 },
  "hf": { base: "https://huggingface.co", ttl: 900 },
};

const FORWARD_HEADERS = ["x-api-key", "user-agent", "accept"];

export default {
  async fetch(req, env) {
    if (req.method !== "GET" && req.method !== "HEAD") {
      return new Response("method not allowed", { status: 405 });
    }
    const url = new URL(req.url);
    let path = url.pathname;

    if (env.SECRET) {
      const prefix = `/${env.SECRET}`;
      if (!path.startsWith(prefix + "/")) return new Response("not found", { status: 404 });
      path = path.slice(prefix.length);
    }

    if (path === "/" || path === "/healthz") {
      return new Response("isbe-gateway ok", { status: 200 });
    }

    // Back-compat: bare /pdf/<id> → arxiv PDFs
    if (path.startsWith("/pdf/")) path = "/arxiv" + path;

    const m = path.match(/^\/([a-z-]+)(\/.*)$/);
    const upstream = m && UPSTREAMS[m[1]];
    if (!upstream) {
      return new Response("unknown route; see UPSTREAMS", { status: 403 });
    }

    const headers = new Headers();
    for (const h of FORWARD_HEADERS) {
      const v = req.headers.get(h);
      if (v) headers.set(h, v);
    }
    if (!headers.has("user-agent")) headers.set("user-agent", "isbe-gateway/0.2");

    const resp = await fetch(upstream.base + m[2] + url.search, {
      headers,
      redirect: "follow",
      cf: { cacheEverything: true, cacheTtl: upstream.ttl },
    });
    const out = new Response(resp.body, resp);
    out.headers.set("Cache-Control", `public, max-age=${upstream.ttl}`);
    return out;
  },
};
