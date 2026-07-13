/**
 * feijibei.top — Service Worker
 * 策略：
 *   - 静态资源（./index.html, ./style.css, ./app.js, icon-*.svg）：Cache-First
 *   - 数据 JSON（./data/*.json）：Network-First，失败回退缓存
 *   - CDN 图片（r2.dev）：Network-Only（跨域，不缓存）
 */
const CACHE_NAME = 'feijibei-v2.9';
const PRECACHE = [
  './index.html',
  './style.css?v=20260714',
  './app.js?v=20260714',
  './manifest.json',
  './icon-192.svg',
  './icon-512.svg',
  './data/index.json',
  './data/content/content-index.json',
  './data/search-index.json',
  './data/nav-links.json',
  './data/authors.json',
];
