/* ============================================================
   花卉周报 — 独立区块渲染器
   完全自包含：fetch ./data/daily.json → 渲染卡片网格
   - 不依赖 app.js，不碰主图鉴渲染链路
   - 封面走 images.weserv.nl 代理（防防盗链）
   - 整卡为 <a> 直跳原帖（外链模式，不转存）
   - 加载失败静默隐藏，主站零影响
   ============================================================ */
(function () {
  'use strict';

  var DAILY_JSON = './data/daily.json';

  var PLATFORM_LABEL = {
    bilibili: 'B站',
    xiaohongshu: '小红书',
    douyin: '抖音',
  };
  var PLATFORM_CLASS = {
    bilibili: 'pf-bili',
    xiaohongshu: 'pf-xhs',
    douyin: 'pf-dy',
  };

  function escapeHtml(str) {
    return String(str == null ? '' : str).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  /* 封面走 weserv 代理；https 源加 ssl=1 让 weserv 走 https 抓取 */
  function proxiedCover(url) {
    if (!url) return '';
    var clean = url.replace(/^https?:\/\//, '');
    return (
      'https://images.weserv.nl/?url=' +
      encodeURIComponent(clean) +
      '&w=400&h=300&fit=cover&output=jpg&ssl=1'
    );
  }

  function fmtLikes(n) {
    n = Number(n) || 0;
    if (n >= 10000) return (n / 10000).toFixed(1).replace(/\.0$/, '') + '万';
    return String(n);
  }

  function fmtDate(s) {
    if (!s) return '';
    return s.length >= 10 ? s.slice(0, 10) : s;
  }

  function render(issue) {
    var section = document.getElementById('daily-report');
    var grid = document.getElementById('daily-grid');
    var issueEl = document.getElementById('daily-issue');
    var foot = document.getElementById('daily-foot');
    if (!section || !grid) return;

    var items = Array.isArray(issue.items) ? issue.items : [];
    if (!items.length) {
      section.hidden = true; // 无内容则完全隐藏
      return;
    }
    section.hidden = false;

    if (issueEl) issueEl.textContent = issue.issue ? issue.issue + ' 期' : '';

    grid.innerHTML = items
      .map(function (it) {
        var safeTitle = escapeHtml(it.title || '');
        var cover = proxiedCover(it.cover);
        var pf = it.platform || '';
        var pfLabel = PLATFORM_LABEL[pf] || pf || '未知';
        var pfClass = PLATFORM_CLASS[pf] || '';
        var author = escapeHtml(it.author || '');
        var likes = fmtLikes(it.likes);
        var date = fmtDate(it.published_at);
        var url = escapeHtml(it.url || '#');
        var thumbHtml = cover
          ? '<img class="dcard-thumb" src="' + cover + '" alt="' + safeTitle + '" loading="lazy" decoding="async" ' +
            "onerror=\"this.style.display='none';this.nextElementSibling.style.display='flex';\" " +
            "onload=\"this.classList.add('loaded')\">" +
            '<div class="dcard-noimg" style="display:none;">无图</div>'
          : '<div class="dcard-noimg">无图</div>';
        return (
          '<a class="dcard ' + pfClass + '" href="' + url + '" target="_blank" rel="noopener noreferrer" ' +
          'title="' + safeTitle + '">' +
          '<span class="dcard-pf">' + escapeHtml(pfLabel) + '</span>' +
          thumbHtml +
          '<div class="dcard-body">' +
          '<div class="dcard-title">' + safeTitle + '</div>' +
          '<div class="dcard-meta">' +
          '<span class="dcard-author">' + author + '</span>' +
          '<span class="dcard-likes">♥ ' + likes + '</span>' +
          '</div>' +
          (date ? '<div class="dcard-date">' + date + '</div>' : '') +
          '</div>' +
          '</a>'
        );
      })
      .join('');

    if (foot) {
      var counts = issue.source_counts || {};
      var parts = [];
      Object.keys(counts).forEach(function (k) {
        parts.push((PLATFORM_LABEL[k] || k) + ' ' + counts[k]);
      });
      var stat = '共 ' + items.length + ' 条' + (parts.length ? ' · ' + parts.join(' · ') : '');
      foot.innerHTML = '<span class="daily-stat">' + escapeHtml(stat) + '</span>';
    }
  }

  async function load() {
    try {
      var res = await fetch(DAILY_JSON, { cache: 'no-cache' });
      if (!res.ok) return; // 静默隐藏
      var issue = await res.json();
      render(issue);
    } catch (e) {
      // 静默失败，主站零影响
      if (e && e.message) console.warn('花卉周报加载失败:', e.message);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', load);
  } else {
    load();
  }
})();
