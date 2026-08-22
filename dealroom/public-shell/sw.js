// v4 purges anything an earlier worker cached under the rule this file already
// stated but did not fully apply — see isLiveData below.
const SHELL_CACHE = "dealroom-shell-v4";
const SHELL = [
  "/offline.html",
  "/manifest.webmanifest",
  "/icons/dealroom-192.png",
  "/icons/dealroom-512.png",
];
const DATA_PATHS = new Set(["/pipeline/changes", "/mcp"]);

// EVERY LIVE SURFACE, not just the two that existed when this file was written.
// The exact-path set missed every /api/ route: a GET to /api/system-work/session
// or /api/room/turns fell through to the shell-asset branch, which caches the
// response AND serves the cached copy when the network fails. That is the exact
// thing the DATA_PATHS comment below forbids — a Model Room showing an hour-old
// roster, an hour-old cursor and a healthy pulse while the wire is unreachable
// would be worse than showing nothing, because it reads as live.
function isLiveData(pathname) {
  return DATA_PATHS.has(pathname) || pathname.startsWith("/api/");
}

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL_CACHE).then((cache) => cache.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== SHELL_CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

async function liveData(request) {
  try {
    return await fetch(request);
  } catch {
    return new Response(JSON.stringify({
      error: "offline",
      state: "reconnecting",
      live: false,
      message: "Deal data is unavailable until the connection returns.",
    }), {
      status: 503,
      headers: { "content-type": "application/json", "cache-control": "no-store" },
    });
  }
}

async function navigation(request) {
  try {
    const response = await fetch(request);
    if (response.ok) {
      const cache = await caches.open(SHELL_CACHE);
      await cache.put(request, response.clone());
    }
    return response;
  } catch {
    return (await caches.match(request)) || caches.match("/offline.html");
  }
}

async function shellAsset(request) {
  try {
    const response = await fetch(request);
    if (response.ok) {
      const cache = await caches.open(SHELL_CACHE);
      await cache.put(request, response.clone());
    }
    return response;
  } catch {
    return (await caches.match(request)) || new Response("Offline", { status: 503 });
  }
}

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin) return;
  if (isLiveData(url.pathname)) {
    // Deliberately no Cache API fallback: stale deal data must never look live.
    event.respondWith(liveData(event.request));
  } else if (event.request.method !== "GET") {
    return;
  } else if (event.request.mode === "navigate") {
    event.respondWith(navigation(event.request));
  } else {
    event.respondWith(shellAsset(event.request));
  }
});
