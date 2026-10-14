/**
 * feijibei.top — Service Worker
 *
 * 缓存策略：
 *   - 导航请求 / HTML：Network-First，离线回退缓存（保证发版立刻可见）
 *   - 带 ?v= 的静态资源：Cache-First（版本号变了 URL 就变，天然失效）
 *   - 数据 JSON（/data/*）：Network-First，离线回退缓存
 *   - 跨域资源（img.feijibei.top 图片、B站缩略图、二维码接口）：完全不接管
 *
 * __BUILD__ 由 tools/publish.py 在发布时替换为构建号；
 * 构建号变化 → CACHE 名变化 → activate 阶段自动清理全部旧缓存。
 *
 * ⚠️ 历史问题：本文件在此前版本中只有常量和数组、没有任何事件监听器，
 *    注册后不产生任何缓存行为（等于没装），却让人误以为有离线能力。
 *    现已补全实现。
 */

const BUILD = '__BUILD__';
const CACHE = `feijibei-${BUILD}`;

/* 只预热体积小的外壳资源；大体积数据 JSON（2.4MB 搜索索引等）
   不放进 precache，避免首次访问被 SW 拖慢。 */
const PRECACHE = [
  './',
  './index.html',
  './manifest.json',
  './icon-192.svg',
  './icon-512.svg',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE)
      .then((cache) => cache.addAll(PRECACHE))
      .catch(() => { /* 预缓存失败不应阻塞安装 */ })
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);

  /* 跨域（R2 图片 / B站缩略图 / 二维码接口）一律放行，交给浏览器与 CDN */
  if (url.origin !== self.location.origin) return;

  /* 数据 JSON：Network-First，离线回退缓存 */
  if (url.pathname.includes('/data/')) {
    event.respondWith(networkFirst(req));
    return;
  }

  /* 带版本号的静态资源：Cache-First */
  if (url.searchParams.has('v')) {
    event.respondWith(cacheFirst(req));
    return;
  }

  /* HTML 导航：Network-First，保证发版立刻可见 */
  if (req.mode === 'navigate' || url.pathname === '/' || url.pathname.endsWith('.html')) {
    event.respondWith(networkFirst(req));
  }
});

async function networkFirst(req) {
  const cache = await caches.open(CACHE);
  try {
    const res = await fetch(req);
    if (res && res.ok) cache.put(req, res.clone());
    return res;
  } catch (err) {
    const hit = await cache.match(req, { ignoreSearch: true });
    if (hit) return hit;
    throw err;
  }
}

async function cacheFirst(req) {
  const cache = await caches.open(CACHE);
  const hit = await cache.match(req);
  if (hit) return hit;
  const res = await fetch(req);
  if (res && res.ok) cache.put(req, res.clone());
  return res;
}
