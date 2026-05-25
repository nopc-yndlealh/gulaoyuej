/**
 * feijibei.top — Service Worker
 * 策略：
 *   - 静态资源（./index.html, ./style.css, ./app.js, icon-*.svg）：Cache-First
 *   - 数据 JSON（./data/*.json）：Network-First，失败回退缓存
 *   - CDN 图片（r2.dev）：Network-Only（跨域，不缓存）
 */
const CACHE_NAME = 'feijibei-v1.5';
const PRECACHE = [
  './index.html',
  './style.css',
  './app.js',
  './manifest.json',
  './icon-192.svg',
  './icon-512.svg',
  './data/index.json',
  './data/content/content-index.json',
  './data/search-index.json',
];

/* ========== Install：预缓存关键文件 ========== */
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(PRECACHE))
  );
  self.skipWaiting();
});

/* ========== Activate：清理旧缓存 ========== */
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  );
});

/* ========== Fetch：按资源类型选择策略 ========== */
self.addEventListener('fetch', event => {
  const { request } = event;
  const url = new URL(request.url);

  // 跳过跨域 CDN 图片（r2.dev / cloudflare 等）
  if (url.origin !== self.location.origin) return;

  // 静态资源：Cache-First
  if (isStaticAsset(url.pathname)) {
    event.respondWith(cacheFirst(request));
    return;
  }

  // 数据 JSON：Network-First
  if (url.pathname.endsWith('.json') && url.pathname.startsWith('/data/')) {
    event.respondWith(networkFirst(request));
    return;
  }

  // 其余：Network-First（页面导航等）
  event.respondWith(networkFirst(request));
});

/* ---------- 策略实现 ---------- */
async function cacheFirst(request) {
  const cached = await caches.match(request);
  if (cached) return cached;
  try {
    const res = await fetch(request);
    if (res.ok) {
      const clone = res.clone();
      caches.open(CACHE_NAME).then(c => c.put(request, clone));
    }
    return res;
  } catch {
    return new Response('离线状态，无法加载', { status: 503 });
  }
}

async function networkFirst(request) {
  try {
    const res = await fetch(request);
    if (res.ok) {
      const clone = res.clone();
      caches.open(CACHE_NAME).then(c => c.put(request, clone));
    }
    return res;
  } catch {
    const cached = await caches.match(request);
    return cached || new Response('{"error":"离线，暂无缓存数据"}', {
      status: 503,
      headers: { 'Content-Type': 'application/json' }
    });
  }
}

function isStaticAsset(pathname) {
  return ['.html', '.css', '.js', '.svg', '.png', '.ico'].some(ext =>
    pathname.endsWith(ext)
  );
}
