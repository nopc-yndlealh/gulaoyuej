/**
 * feijibei.top — 站点公共模块
 *
 * 抽出自 app.js 的通用能力，供「首页弹窗」与「独立详情页 detail.html」共用：
 *   - 主题切换（日夜自动 / 手动循环）
 *   - HTML 转义与智能分段
 *   - 详情内容按需加载（content-index.json → data/content/{分类}.json）
 *   - 详情渲染与分享栏
 *
 * 挂载到 window.SiteCommon。无第三方依赖，需在 app.js 之前加载。
 */
(function (global) {
  'use strict';

  /* ============================================================
     一、HTML 转义 & 智能分段
     ============================================================ */

  /** 纯字符串实现，避免频繁创建 DOM 节点 */
  const ESCAPE_MAP = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };

  /** @param {string} str @returns {string} */
  function escapeHtml(str) {
    return String(str).replace(/[&<>"']/g, (c) => ESCAPE_MAP[c]);
  }

  /** 智能分段：自动识别文本中的段落边界 @param {string} text @returns {string[]} */
  function splitParagraphs(text) {
    if (!text || !text.trim()) return [];
    // 1. 双换行 = 明确自然段
    if (text.includes('\n\n')) {
      return text
        .split(/\n\n+/)
        .map((p) => p.trim())
        .filter(Boolean);
    }
    // 2. 单换行 = 手动分行
    if (text.includes('\n')) {
      return text
        .split(/\n+/)
        .map((p) => p.trim())
        .filter(Boolean);
    }
    // 3. 无换行：按句号/感叹号/问号切分，每句独立成段
    //    社交内容一句话就是一行，比硬凑更自然
    const sentences = [];
    let buf = '';
    for (let i = 0; i < text.length; i++) {
      const ch = text[i];
      buf += ch;
      if ('。！？!?'.indexOf(ch) !== -1) {
        sentences.push(buf.trim());
        buf = '';
      }
    }
    if (buf.trim()) sentences.push(buf.trim());
    return sentences.length > 0 ? sentences : [text];
  }

  /* ============================================================
     二、主题切换
     早 8 点—晚 8 点用浅色（暖金），其余用暗色（深紫）；
     手动点击则在 light → dark → auto 之间循环。
     ============================================================ */

  const THEME_KEY = 'feijibei-theme';

  function isDaytime() {
    const hour = new Date().getHours();
    return hour >= 8 && hour < 20;
  }

  function getTheme() {
    const saved = global.localStorage.getItem(THEME_KEY);
    if (saved === 'dark' || saved === 'light') return saved;
    return 'auto';
  }

  function getEffectiveTheme() {
    const mode = getTheme();
    if (mode === 'dark') return 'dark';
    if (mode === 'light') return 'light';
    return isDaytime() ? 'light' : 'dark';
  }

  function updateThemeIcons(mode) {
    const icons = document.querySelectorAll('#theme-toggle, #mobile-theme-toggle');
    const effective = getEffectiveTheme();
    icons.forEach((btn) => {
      if (mode === 'dark') {
        btn.textContent = '☀️';
      } else if (mode === 'light') {
        btn.textContent = '🌙';
      } else {
        btn.textContent = effective === 'dark' ? '☀️' : '🌙';
      }
    });
  }

  function applyTheme(mode) {
    const effective = mode === 'auto' ? (isDaytime() ? 'light' : 'dark') : mode;
    document.documentElement.setAttribute('data-theme', effective);
    updateThemeIcons(mode);
  }

  function cycleTheme() {
    const current = getTheme();
    const next = current === 'light' ? 'dark' : current === 'dark' ? 'auto' : 'light';
    global.localStorage.setItem(THEME_KEY, next);
    applyTheme(next);
  }

  /**
   * 初始化主题：立即应用（避免闪烁）+ 绑定按钮 + 每分钟复查日夜。
   * 首页与详情页都调用它。
   */
  function initTheme() {
    applyTheme(getTheme());

    const bind = () => {
      const btns = document.querySelectorAll('#theme-toggle, #mobile-theme-toggle');
      btns.forEach((btn) => {
        if (btn.dataset.themeBound) return;
        btn.dataset.themeBound = '1';
        btn.addEventListener('click', cycleTheme);
      });
    };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bind);
    else bind();

    global.setInterval(() => {
      if (getTheme() === 'auto') applyTheme('auto');
    }, 60000);
  }

  /* ============================================================
     三、详情内容按需加载
     content-index.json（id → 分类 slug）在首次点开详情时才拉取，
     再按分类加载 data/content/{slug}.json，按 slug 缓存。
     ============================================================ */

  /** @type {Record<string, string>} id → 分类 slug */
  let contentIndex = {};
  let contentIndexLoading = null;
  /** @type {Record<string, Record<string, object>>} slug → { id: content } */
  const contentCache = {};

  async function ensureContentIndex() {
    if (contentIndexLoading) return contentIndexLoading;
    if (Object.keys(contentIndex).length > 0) return;
    contentIndexLoading = (async () => {
      try {
        const res = await fetch('./data/content/content-index.json');
        if (res.ok) contentIndex = await res.json();
        else console.warn('content-index 加载失败，详情不可用');
      } catch (e) {
        console.warn('content-index 不可用:', e.message);
      }
    })();
    return contentIndexLoading;
  }

  /** 按需加载某条详情内容 @param {string} id @returns {Promise<object|null>} */
  async function fetchContent(id) {
    await ensureContentIndex();
    const slug = contentIndex[id];
    if (!slug) return null;

    if (contentCache[slug]) return contentCache[slug][id] || null;

    try {
      const res = await fetch(`./data/content/${slug}.json`);
      if (!res.ok) throw new Error(`content/${slug}.json 加载失败`);
      contentCache[slug] = await res.json();
      return contentCache[slug][id] || null;
    } catch (e) {
      console.error('[fetchContent]', e);
      return null;
    }
  }

  /** 从 index.json 的结构化 file_url 里取 id（兼容 './detail.html?id=xxx'） */
  function parseId(urlOrId) {
    if (typeof urlOrId !== 'string') return String(urlOrId || '');
    const m = urlOrId.match(/[?&]id=([^&]+)/);
    return m ? decodeURIComponent(m[1]) : urlOrId;
  }

  /* ============================================================
     四、链接
     ============================================================ */

  /** 站内相对地址，用于新标签页打开详情 @param {string} id @returns {string} */
  function getDetailUrl(id) {
    return './detail.html?id=' + encodeURIComponent(id);
  }

  /** 绝对地址，用于分享（复制链接 / 二维码 / 分享到 QQ） @param {string} id @returns {string} */
  function getShareUrl(id) {
    const dir = global.location.origin + global.location.pathname.replace(/[^/]*$/, '');
    return dir + 'detail.html?id=' + encodeURIComponent(id);
  }

  /* ============================================================
     五、详情渲染
     ============================================================ */

  /** 分享栏 @param {string} id @param {string} title @returns {string} */
  function buildShareBar(id, title) {
    return `
  <div class="share-bar">
    <span class="share-label">分享到</span>
    <button class="share-btn" data-action="copy" data-id="${escapeHtml(id)}" title="复制链接">
      <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
      复制链接
    </button>
    <button class="share-btn" data-action="qr" data-id="${escapeHtml(id)}" title="微信扫码">
      <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><path d="M14 14h7v7h-7z"/></svg>
      微信
    </button>
    <button class="share-btn" data-action="qq" data-id="${escapeHtml(id)}" data-title="${escapeHtml(title || '')}" title="分享到QQ">
      <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2C7 2 4 5 4 8c0 2 1 3 2 4l-1 3 4-2c1 .5 2 .5 3 .5s2 0 3-.5l4 2-1-3c1-1 2-2 2-4 0-3-3-6-8-6z"/></svg>
      QQ
    </button>
    <button class="share-btn" data-action="native" data-id="${escapeHtml(id)}" data-title="${escapeHtml(title || '')}" title="更多">
      <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="5" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="12" cy="19" r="2"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
      更多
    </button>
  </div>`;
  }

  /** 头部（标题 + 标签 + 作者 + 原帖链接） @returns {string} */
  function buildDetailHeader(content, fallbackTitle) {
    const tagLabel =
      content.tag && !/^\d+$/.test(String(content.tag)) ? `#${escapeHtml(String(content.tag))}` : '';
    const authorObj = content.author || {};
    const authorName = typeof authorObj === 'string' ? authorObj : authorObj.name || '';
    const weiboUrl = content.weibo_url || '';

    let html = `<div class="detail-header"><h2 class="detail-title">${escapeHtml(
      content.title || fallbackTitle || ''
    )}</h2>`;
    if (tagLabel || authorName || weiboUrl) {
      html += `<div class="detail-meta">`;
      if (tagLabel) html += `<span class="detail-tag">${tagLabel}</span>`;
      if (authorName) html += `<span class="detail-author">👤 ${escapeHtml(authorName)}</span>`;
      if (weiboUrl)
        html += `<a href="${escapeHtml(weiboUrl)}" target="_blank" rel="noopener" class="detail-weibo-link">🔗 查看原帖</a>`;
      html += `</div>`;
    }
    html += '</div>';
    return html;
  }

  /**
   * 渲染一条详情，返回完整 HTML（头部 + 正文 + 分享栏）与元信息。
   * @param {object} content 详情数据
   * @param {string} id 条目 id
   * @param {string} [title] 兜底标题（卡片标题）
   * @returns {{ html: string, isSocial: boolean, imageCount: number }}
   */
  function renderDetail(content, id, title) {
    const isSocial = content.type === '小红书' || content.type === '微博';
    const postTime = content.time || '';
    const segments = Array.isArray(content.segments) ? content.segments : [];
    const shareBar = buildShareBar(id, content.title || title);
    const head = buildDetailHeader(content, title);

    if (isSocial) {
      // 左图右文
      const images = segments.filter((s) => s.i);
      const totalImg = images.length;
      // 没有图片时不要渲染 .social-gallery —— 它带着 flex:0 0 55% + 背景 + 边框
      // + aspect-ratio，空着也会占掉左边 55% 画一个空框。
      // 微博账号里有大量纯文字帖（古籍考据、品种评论），这个空框看着就像图裂了。
      let html = head + `<div class="social-post${totalImg ? '' : ' text-only'}">`;
      if (totalImg > 0) {
        html += '<div class="social-gallery">';
        html += '<div class="gallery-track" id="gallery-track">';
        images.forEach((seg, idx) => {
          html += `<div class="gallery-slide" data-idx="${idx}">
              <img src="${escapeHtml(seg.i)}" alt="图片 ${idx + 1}" loading="lazy" decoding="async"
                   onload="this.classList.add('loaded')">
            </div>`;
        });
        html += '</div>';
        if (totalImg > 1) {
          html += `<button class="gallery-arrow prev" data-dir="-1" aria-label="上一张">‹</button>`;
          html += `<button class="gallery-arrow next" data-dir="1" aria-label="下一张">›</button>`;
        }
        html += `<div class="gallery-counter">${totalImg > 1 ? '<span id="gallery-idx">1</span> / ' : ''}${totalImg}</div>`;
        html += '</div>';
      }

      html += '<div class="social-content">';
      const authorObj = content.author || {};
      const authorName = typeof authorObj === 'string' ? authorObj : authorObj.name || '';
      if (authorName) html += `<div class="social-author">👤 ${escapeHtml(authorName)}</div>`;
      if (postTime) html += `<div class="social-time">${escapeHtml(postTime)}</div>`;
      segments
        .filter((s) => s.t)
        .forEach((seg) => {
          splitParagraphs(seg.t).forEach((p) => {
            html += `<p class="social-text">${escapeHtml(p)}</p>`;
          });
        });
      html += '</div></div>' + shareBar;
      return { html, isSocial: true, imageCount: totalImg };
    }

    // 图文交错
    let html = head + '<div class="detail-body">';
    segments.forEach((seg) => {
      if (seg.t) {
        html += `<p>${escapeHtml(seg.t)}</p>`;
      } else if (seg.i) {
        html += `<img src="${escapeHtml(seg.i)}" alt="图片" loading="lazy" decoding="async"
                          onload="this.classList.add('loaded')">`;
      }
    });
    html += '</div>' + shareBar;
    return { html, isSocial: false, imageCount: segments.filter((s) => s.i).length };
  }

  /**
   * 内容不存在 / 渲染失败时的占位
   * @param {string} id 条目 id（空串表示没有 id）
   * @param {string} [msg] 提示文案
   * @param {boolean} [showHome] 是否显示「回到图鉴首页」（独立详情页用）
   * @returns {string}
   */
  function renderPlaceholder(id, msg, showHome) {
    return `
    <div class="detail-error">
      <p>${escapeHtml(msg || '暂无详情内容')}</p>
      <p class="detail-error-id">ID: ${escapeHtml(id) || '（缺失）'}</p>
      ${showHome ? '<p class="detail-error-actions"><a href="./index.html">← 回到图鉴首页</a></p>' : ''}
    </div>`;
  }

  /* ============================================================
     导出
     ============================================================ */
  global.SiteCommon = {
    // 基础工具
    escapeHtml,
    splitParagraphs,
    // 主题
    THEME_KEY,
    getTheme,
    getEffectiveTheme,
    applyTheme,
    cycleTheme,
    initTheme,
    // 数据
    ensureContentIndex,
    fetchContent,
    parseId,
    // 链接
    getDetailUrl,
    getShareUrl,
    // 渲染
    buildShareBar,
    renderDetail,
    renderPlaceholder,
  };
})(window);
