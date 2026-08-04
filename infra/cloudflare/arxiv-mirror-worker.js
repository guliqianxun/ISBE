/**
 * arxiv PDF mirror on Cloudflare Workers — for deployments whose egress to
 * arxiv.org/Fastly stalls (e.g. CN servers).
 *
 * Deploy: CF Dashboard → Workers & Pages → Create Worker → paste → Deploy,
 * then Settings → Domains & Routes → Add Custom Domain (e.g. arxiv.yourdomain.com;
 * *.workers.dev is usually blocked in CN, a custom domain is required).
 *
 * Optional hardening: set an environment variable SECRET on the worker; the
 * mirror then only answers under /<SECRET>/pdf/... and you configure ISBE with
 *   ARXIV_PDF_BASE_URL=https://arxiv.yourdomain.com/<SECRET>
 * Without SECRET it answers under /pdf/... directly:
 *   ARXIV_PDF_BASE_URL=https://arxiv.yourdomain.com
 *
 * Same-paper requests are cached at the CF edge for 7 days, so arxiv.org sees
 * each PDF at most once per week regardless of retries.
 */

const UPSTREAM = "https://arxiv.org";

export default {
  async fetch(req, env) {
    if (req.method !== "GET" && req.method !== "HEAD") {
      return new Response("method not allowed", { status: 405 });
    }
    let path = new URL(req.url).pathname;

    if (env.SECRET) {
      const prefix = `/${env.SECRET}`;
      if (!path.startsWith(prefix + "/")) return new Response("not found", { status: 404 });
      path = path.slice(prefix.length);
    }

    if (path === "/" || path === "/healthz") {
      return new Response("isbe-arxiv-mirror ok", { status: 200 });
    }
    // Only proxy PDF paths — this is a PDF mirror, not an open proxy.
    if (!/^\/pdf\/[A-Za-z0-9._\/-]+$/.test(path)) {
      return new Response("only /pdf/<arxiv_id>", { status: 403 });
    }

    const upstream = await fetch(UPSTREAM + path, {
      headers: { "User-Agent": "isbe-arxiv-mirror/0.1 (Cloudflare Worker)" },
      redirect: "follow",
      cf: { cacheEverything: true, cacheTtl: 604800 }, // 7d edge cache
    });

    // Pass through, pinning cache headers for the edge.
    const resp = new Response(upstream.body, upstream);
    resp.headers.set("Cache-Control", "public, max-age=604800, immutable");
    return resp;
  },
};
