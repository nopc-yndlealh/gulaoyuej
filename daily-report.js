/* 花卉周报前端：读取 ./data/daily.json，渲染卡片网格（纯外链聚合）。
   点击卡片弹层展示详情，弹层内“前往原帖”跳转到 B站/小红书原帖。
   设计跟随站点 CSS 变量（暖金白昼 / 深紫黑夜）。
   失败（无 daily.json / 网络错误）则静默隐藏整个区块，不影响主页。 */
(function () {
  'use strict';

  var DATA_URL = './data/daily.json';
  var section = document.getElementById('daily-report');
  if (!section) return;
  var grid = document.getElementById('daily-grid');
  var issueEl = document.getElementById('daily-issue');
  var footEl = document.getElementById('daily-foot');
  var modal = document.getElementById('daily-modal');
  var modalContent = document.getElementById('daily-modal-content');
  var modalClose = document.getElementById('daily-modal-close');

  var PLAT = {
    bilibili:    { name: 'B站',   cls: 'bili' },
    xiaohongshu: { name: '小红书', cls: 'xhs' },
    guonongbang: { name: '果农邦', cls: 'gnb' },
    tahuaxing:   { name: '踏花行', cls: 'thx' }
  };

  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  // 封面图：原实现走 images.weserv.nl 代理做 https 转换 + 限宽，但该服务在国内被墙，
  // 导致周报卡片封面全部加载失败、退化成纯文字块（看起来像“堆叠”）。
  // 改为直接使用平台 CDN 直链，并把 http 升级为 https——B站(i*.hdslb.com) /
  // 小红书(xhscdn.com) 的 CDN 均支持 https 且国内可达，https 站点也不会被混合内容拦截。
  function proxied(src, w) {
    if (!src) return '';
    return String(src).trim().replace(/^http:\/\//i, 'https://');
  }

  function fmtDate(iso) {
    if (!iso) return '';
    var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
    if (m) return m[1] + '.' + m[2] + '.' + m[3];
    var d = new Date(iso);
    if (isNaN(d.getTime())) return '';
    var p = function (n) { return String(n).padStart(2, '0'); };
    return d.getFullYear() + '.' + p(d.getMonth() + 1) + '.' + p(d.getDate());
  }

  function fmtLikes(n) {
    n = Number(n) || 0;
    if (n >= 10000) return (n / 10000).toFixed(1).replace(/\.0$/, '') + 'w';
    return String(n);
  }

  function platOf(p) { return PLAT[p] || { name: p || '未知', cls: 'def' }; }

  function coverHtml(it, w) {
    var src = it.cover ? proxied(it.cover, w) : '';
    if (!src) return '<div class="no-thumb">🌿</div>';
    return '<img class="thumb" src="' + escapeHtml(src) + '" alt="" loading="lazy" ' +
           'onload="this.classList.add(\'loaded\')" ' +
           'onerror="this.closest(\'.daily-cover\').classList.add(\'no-cover\')">';
  }

  function cardHtml(it) {
    var p = platOf(it.platform);
    var author = it.author ? escapeHtml(it.author) : '未知作者';
    var likes = it.likes ? '<span class="daily-likes">♥ ' + fmtLikes(it.likes) + '</span>' : '';
    var date = fmtDate(it.published_at);
    var dateHtml = date ? '<span class="daily-date">· ' + date + '</span>' : '';
    return '' +
      '<div class="daily-card" role="button" tabindex="0" data-id="' + escapeHtml(it.id) + '">' +
        '<div class="daily-cover">' +
          '<span class="daily-badge daily-badge--' + p.cls + '">' + p.name + '</span>' +
          coverHtml(it, 480) +
        '</div>' +
        '<div class="daily-body">' +
          '<h3 class="daily-title">' + escapeHtml(it.title) + '</h3>' +
          (it.summary
            ? '<p class="daily-summary">' + escapeHtml(it.summary) + '</p>'
            : '') +
          '<div class="daily-meta">' + likes +
            '<span class="daily-author">' + author + '</span>' + dateHtml +
          '</div>' +
        '</div>' +
      '</div>';
  }

  function modalHtml(it) {
    var p = platOf(it.platform);
    var src = it.cover ? proxied(it.cover, 1000) : '';
    var cover = src
      ? '<img class="daily-modal-cover" src="' + escapeHtml(src) + '" alt="" ' +
        'onload="this.classList.add(\'loaded\')" ' +
        'onerror="this.classList.add(\'no-thumb\');this.classList.remove(\'loaded\')">'
      : '<div class="daily-modal-cover no-thumb">🌿</div>';
    var authorUrl = it.author_url ? ' href="' + escapeHtml(it.author_url) + '" target="_blank" rel="noopener"' : '';
    var author = it.author
      ? '<a class="daily-modal-author"' + authorUrl + '>' + escapeHtml(it.author) + '</a>'
      : '未知作者';
    var meta = [];
    if (it.likes) meta.push('♥ ' + fmtLikes(it.likes) + ' 赞');
    var date = fmtDate(it.published_at);
    if (date) meta.push(date);
    meta.push(p.name);
    var summary = it.summary ? '<p class="daily-modal-summary">' + escapeHtml(it.summary) + '</p>' : '';
    var go = it.url
      ? '<a class="daily-modal-go" href="' + escapeHtml(it.url) + '" target="_blank" rel="noopener">前往原帖 →</a>'
      : '';
    return '' +
      '<div class="daily-modal-cover-wrap">' + cover + '</div>' +
      '<div class="daily-modal-info">' +
        '<span class="daily-badge daily-badge--' + p.cls + '">' + p.name + '</span>' +
        '<h3 class="daily-modal-title">' + escapeHtml(it.title) + '</h3>' +
        '<div class="daily-modal-meta">' + meta.map(escapeHtml).join(' · ') + '</div>' +
        '<div class="daily-modal-author-line">作者：' + author + '</div>' +
        summary + go +
      '</div>';
  }

  // 弹层开关
  var lastFocus = null;
  function openModal(it) {
    if (!modal) return;
    lastFocus = document.activeElement;
    modalContent.innerHTML = modalHtml(it);
    modal.hidden = false;
    document.body.style.overflow = 'hidden';
    if (modalClose) modalClose.focus();
  }
  function closeModal() {
    if (!modal) return;
    modal.hidden = true;
    modalContent.innerHTML = '';
    document.body.style.overflow = '';
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }

  // 事件委托：卡片点击 / 键盘
  grid.addEventListener('click', function (e) {
    var card = e.target.closest('.daily-card');
    if (!card) return;
    var it = window.__dailyItems && window.__dailyItems[card.getAttribute('data-id')];
    if (it) openModal(it);
  });
  grid.addEventListener('keydown', function (e) {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    var card = e.target.closest('.daily-card');
    if (!card) return;
    e.preventDefault();
    var it = window.__dailyItems && window.__dailyItems[card.getAttribute('data-id')];
    if (it) openModal(it);
  });
  if (modalClose) modalClose.addEventListener('click', closeModal);
  if (modal) {
    modal.addEventListener('click', function (e) {
      if (e.target === modal) closeModal();
    });
  }
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && modal && !modal.hidden) closeModal();
  });

  function render(data) {
    var items = Array.isArray(data.items) ? data.items : [];
    if (!items.length) return; // 静默：无内容则不显示区块
    var byId = {};
    items.forEach(function (it) { byId[it.id] = it; });
    window.__dailyItems = byId;

    grid.innerHTML = items.map(cardHtml).join('');
    if (issueEl) issueEl.textContent = data.issue ? '· ' + data.issue : '';

    if (footEl) {
      var counts = data.source_counts || {};
      var parts = [('本期 ' + items.length + ' 条')];
      Object.keys(counts).forEach(function (k) {
        parts.push(platOf(k).name + ' ' + counts[k]);
      });
      parts.push('白名单博主精选 · 外链聚合');
      footEl.textContent = parts.join(' · ');
    }

    // 显示/隐藏由 app.js 的 setActiveCategory 统一控制：只在“全部植物”首页展示
    // 这里不主动设置 section.hidden，避免覆盖分类切换状态
  }

  function load() {
    fetch(DATA_URL, { cache: 'no-cache' })
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(render)
      .catch(function (err) {
        // 失败静默：隐藏区块，不影响主页其它功能
        console.warn('[花卉周报] 加载失败，已隐藏区块:', err.message);
        section.hidden = true;
      });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', load);
  } else {
    load();
  }
})();
