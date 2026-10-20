// shouldidive-relay: fetches upstream files that refuse GitHub Actions
// egress IPs, for the data-refresh workflows.
//
// scisland.org (the Navy's San Clemente Island schedule) answers 403 to GitHub
// runners but 200 to the same request from a home connection or a Worker.
// pipeline/fetch_sci_closures.py tries the site directly first and only falls
// back here on refusal.
//
// Not an open proxy: every route maps to ONE fixed upstream URL. Anything
// else is a 404.

const ROUTES = {
  "/scisland/data.js": {
    url: "https://www.scisland.org/assets/js/data.js",
    referer: "https://www.scisland.org/schedule.html",
    type: "application/javascript; charset=utf-8",
  },
};

const UA =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
  "(KHTML, like Gecko) Chrome/126.0 Safari/537.36";

export default {
  async fetch(request) {
    if (request.method !== "GET") {
      return new Response("method not allowed", { status: 405 });
    }
    const route = ROUTES[new URL(request.url).pathname];
    if (!route) {
      return new Response("shouldidive-relay: unknown route", { status: 404 });
    }
    const upstream = await fetch(route.url, {
      headers: { "User-Agent": UA, Referer: route.referer },
      // Edge-cache 10 min: the refresh runs twice a day, so this only
      // absorbs retries and keeps us polite to the source.
      cf: { cacheTtl: 600, cacheEverything: true },
    });
    return new Response(upstream.body, {
      status: upstream.status,
      headers: {
        "Content-Type": route.type,
        "Cache-Control": "no-store",
        "X-Upstream-Status": String(upstream.status),
      },
    });
  },
};
