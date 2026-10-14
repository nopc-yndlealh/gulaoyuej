/**
 * feijibei.top — Service Worker
 *
 * 策略：**同源请求一律 Network-First**，缓存只作为离线兜底。
 *   - 在线：永远拿服务端最新版本，不存在「改了看不到」
 *   - 离线：回退到最近一次成功缓存的副本
 *   - 跨域（img.feijibei.top 图片、B站缩略图、二维码接口）：完全不接管
 *
 * ⚠️ 为什么不再用 Cache-First 缓存带 ?v= 的静态资源（这是踩过的坑）：
 *   上一版按「带 ?v= 版本号的资源走 Cache-First」实现。但 index.html 里的
 *   `?v=20261014-7f18328` 是靠手工维护的，改代码时没人会记得同步改它 ——
 *   于是 URL 不变、SW 命中旧缓存，**改完的 app.js 永远加载不出来**，
 *   而且完全没有任何报错，页面只是看起来「没变化」。
 *   我自己的分类树改动就这样被冻住了一轮。
 *   Network-First 让新鲜度不再依赖任何人记得改版本号。
 *
 * 20261014-7f18328 由 tools/build.py --stamp 注入构建号，未注入时退回 'v3'。
 * 因为已经是 Network-First，缓存名只影响离线副本的新旧，不影响线上正确性。
 */
const BUILD = '20261014-7f18328';
const IS_STAMPED = !BUILD.startsWith('__');
const CACHE = `feijibei-${IS_STAMPED ? BUILD : 'v3'}`;

/* 只预热外壳，不碰大体积数据（避免首次访问被 SW 拖慢） */
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

  /* 跨域资源放行，交给浏览器与 CDN */
  if (url.origin !== self.location.origin) return;

  /* 其余同源请求（HTML / JS / CSS / data JSON）一律 Network-First */
  event.respondWith(networkFirst(req));
});

async function networkFirst(req) {
  const cache = await caches.open(CACHE);
  try {
    /* cache: 'no-cache' 绕开 HTTP 缓存，确保拿到真正的当前版本 */
    const res = await fetch(req, { cache: 'no-cache' });
    if (res && res.ok) cache.put(req, res.clone());
    return res;
  } catch (err) {
    const hit = await cache.match(req, { ignoreSearch: true });
    if (hit) return hit;
    throw err;
  }
}
