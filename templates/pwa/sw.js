// Service Worker: アプリシェルのオフラインキャッシュ
// キャッシュ名にgitコミットハッシュ（app_version）を含めることで、
// デプロイごとに古いキャッシュが自動的に破棄される
const CACHE = "sdu-shift-{{ app_version }}";
const SHELL = [
  "/",
  "/offline",
  "/static/manifest.json",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
  "/static/icons/apple-touch-icon.png",
];

self.addEventListener("install", e => {
  e.waitUntil(
    caches.open(CACHE).then(cache =>
      Promise.all(SHELL.map(url => cache.add(url).catch(() => {})))
    )
  );
  self.skipWaiting();
});

self.addEventListener("activate", e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// ページ遷移: ネットワーク優先、失敗時はキャッシュ→オフラインページ
async function handleNavigate(request) {
  try {
    const res = await fetch(request);
    const cache = await caches.open(CACHE);
    cache.put(request, res.clone());
    return res;
  } catch {
    return (await caches.match(request)) || (await caches.match("/offline"));
  }
}

// 静的ファイル: キャッシュ優先、無ければネットワークから取得してキャッシュに保存
async function handleStatic(request) {
  const cached = await caches.match(request);
  if (cached) return cached;
  try {
    const res = await fetch(request);
    const cache = await caches.open(CACHE);
    cache.put(request, res.clone());
    return res;
  } catch {
    return cached;
  }
}

// その他のGET: ネットワーク優先、失敗時はキャッシュ
async function handleOther(request) {
  try {
    return await fetch(request);
  } catch {
    return caches.match(request);
  }
}

self.addEventListener("fetch", e => {
  if (e.request.method !== "GET") return;
  const url = new URL(e.request.url);
  if (e.request.mode === "navigate") {
    e.respondWith(handleNavigate(e.request));
  } else if (url.pathname.startsWith("/static/")) {
    e.respondWith(handleStatic(e.request));
  } else {
    e.respondWith(handleOther(e.request));
  }
});
