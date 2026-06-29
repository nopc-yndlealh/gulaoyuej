/* ==================== 主逻辑 ==================== */

/** @typedef {import('./types').IndexEntry} IndexEntry */
/** @typedef {import('./types').ContentSegment} ContentSegment */
/** @typedef {import('./types').ContentEntry} ContentEntry */
/** @typedef {import('./types').AuthorInfo} AuthorInfo */
/** @typedef {import('./types').SearchEntry} SearchEntry */
/** @typedef {import('./types').TreeNode} TreeNode */
/** @typedef {import('./types').TreeChild} TreeChild */
/** @typedef {import('./types').GridItem} GridItem */
/** @typedef {import('./types').TransformedData} TransformedData */
/** @typedef {import('./types').TreeConfigGroup} TreeConfigGroup */
/** @typedef {import('./types').SearchResult} SearchResult */
/** @typedef {import('./types').ClassifyKeys} ClassifyKeys */

(function () {
  'use strict';

  /* ===== 主题切换 ===== */
  const THEME_KEY = 'feijibei-theme';

  function isDaytime() {
    const hour = new Date().getHours();
    return hour >= 8 && hour < 20;
  }

  function getTheme() {
    const saved = localStorage.getItem(THEME_KEY);
    if (saved === 'dark' || saved === 'light') return saved;
    return 'auto'; // 早8-晚8白天, 其余黑夜
  }

  function getEffectiveTheme() {
    const mode = getTheme();
    if (mode === 'dark') return 'dark';
    if (mode === 'light') return 'light';
    return isDaytime() ? 'light' : 'dark';
  }

  function applyTheme(mode) {
    const html = document.documentElement;
    const effective = mode === 'auto' ? (isDaytime() ? 'light' : 'dark') : mode;
    html.setAttribute('data-theme', effective);
    updateThemeIcons(mode);
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

  function cycleTheme() {
    const current = getTheme();
    const next = current === 'light' ? 'dark' : current === 'dark' ? 'auto' : 'light';
    localStorage.setItem(THEME_KEY, next);
    applyTheme(next);
  }

  // 初始化主题
  applyTheme(getTheme());

  // 绑定按钮
  document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('theme-toggle').addEventListener('click', cycleTheme);
    document.getElementById('mobile-theme-toggle').addEventListener('click', cycleTheme);
  });

  // 每分钟检查时间切换（仅在 auto 模式下响应）
  setInterval(() => {
    if (getTheme() === 'auto') applyTheme('auto');
  }, 60000);

  /* ===== 数据变量 ===== */
  /** @type {Record<string, string>} id → 分类 slug */
  let contentIndex = {};
  /** @type {Record<string, Record<string, ContentEntry>>} slug → {id: content} */
  const contentCache = {};
  /** @type {TransformedData | null} */
  let allData = null;
  /** @type {number|string} 当前选中的分类索引 */
  let activeCatIdx = -1;
  /** @type {Record<string, SearchEntry>} id → {title, cat, text, thumb} */
  let searchIndex = {};
  /** @type {string[]} 首页推荐 ID 列表 */
  let featuredIds = [];
  /** @type {IndexEntry[] | null} 原始索引数据（内部引用） */
  let _indexData = null;
  /** @type {number} 当前轮播图片总数 */
  let galleryTotal = 0;
  /** @type {number} 当前轮播图片索引 */
  let galleryIdx = 1;

  /* ===== 分页配置 ===== */
  let pageSize = 30; // 每页卡片数（动态计算）
  let currentPage = 1; // 当前页码（从1开始）
  let currentKeyword = ''; // 当前搜索关键词

  /* 根据容器宽度动态计算每页数量，确保最后一行永远填满 */
  /** @returns {number} */
  function calculatePageSize() {
    const grid = document.getElementById('grid');
    if (!grid) return 30;

    const width = grid.clientWidth;
    const vw = window.innerWidth;
    let minCardWidth, gap, targetRows;

    if (vw <= 420) {
      // 手机固定2列
      return Math.max(2, 2 * 4);
    } else if (vw <= 768) {
      minCardWidth = 140;
      gap = 12;
      targetRows = 4;
    } else {
      minCardWidth = 170;
      gap = 16;
      targetRows = 4;
    }

    const perRow = Math.max(1, Math.floor((width + gap) / (minCardWidth + gap)));
    return perRow * targetRows;
  }

  /* 加载数据 */
  async function loadData() {
    try {
      const indexRes = await fetch('./data/index.json');
      if (!indexRes.ok) throw new Error('data/index.json 未找到');

      /** @type {IndexEntry[]} */
      const rawData = await indexRes.json();
      _indexData = rawData;

      // 加载 content-index（小，~160KB，且详情页依赖它查 slug）
      try {
        const ciRes = await fetch('./data/content/content-index.json');
        if (ciRes.ok) contentIndex = await ciRes.json();
      } catch (e) {
        console.warn('content-index 加载失败:', e.message);
      }

      // search-index.json（~2.5MB）→ 延迟到首次搜索时按需加载
      // content-index 依然在启动时加载，因为 getContent() 依赖它

      // 加载首页推荐列表
      try {
        const fRes = await fetch('./data/featured.json');
        if (fRes.ok) featuredIds = await fRes.json();
      } catch (e) {
        console.warn('推荐列表不可用:', e.message);
      }

      allData = transformData(rawData);
      init();
    } catch (e) {
      document.getElementById('loading').innerHTML =
        `<p style="color:#c0392b;font-size:16px;">数据加载失败：${e.message}</p>
       <p style="margin-top:8px;font-size:13px;color:#888;">
         请确认 data/index.json 和 data/content/ 文件存在且格式正确。
       </p>`;
    }
  }

  /* 按需加载搜索索引（只加载一次） */
  /** @returns {Promise<void>} */
  let searchIndexLoading = null;
  async function ensureSearchIndex() {
    if (searchIndexLoading) return searchIndexLoading;
    if (Object.keys(searchIndex).length > 0) return;
    searchIndexLoading = (async () => {
      try {
        const res = await fetch('./data/search-index.json');
        if (res.ok) {
          searchIndex = await res.json();
          console.log(`🔍 搜索索引已加载（${Object.keys(searchIndex).length} 条）`);
        } else {
          console.warn('搜索索引加载失败，仅支持标题搜索');
        }
      } catch (e) {
        console.warn('搜索索引不可用:', e.message);
      }
    })();
    return searchIndexLoading;
  }

  /* 按需加载分类内容（带缓存） */
  /** @param {string} id @returns {Promise<ContentEntry | null>} */
  async function getContent(id) {
    const slug = contentIndex[id];
    if (!slug) return null;

    // 缓存命中
    if (contentCache[slug]) {
      return contentCache[slug][id] || null;
    }

    // 加载分类文件
    try {
      const res = await fetch(`./data/content/${slug}.json`);
      if (!res.ok) throw new Error(`content/${slug}.json 加载失败`);
      contentCache[slug] = await res.json();
      return contentCache[slug][id] || null;
    } catch (e) {
      console.error('[getContent]', e);
      return null;
    }
  }

  /* 树状分类配置 */
  /** @type {TreeConfigGroup[]} */
  const TREE_CONFIG = [
    {
      name: '月季',
      children: [
        '丹陶',
        '吉洛',
        '奥斯汀',
        '戴尔巴德',
        '玫昂',
        '科尔德斯',
        '古老月季',
        '育种相关',
        '特性合集',
        '种植分享',
        '竞赛,评选以及月季园实测',
        '其他育种家',
        '云小123456',
      ],
    },
    {
      name: '多肉',
      children: [
        '石莲花属',
        '莲花掌属',
        '风车草属',
        '青锁龙属',
        '景天属',
        '仙女杯属',
        'Lenophyllum',
        '美丽莲属',
        '厚叶草属',
        'Prometheum',
        '瓦松属',
        '综合日常',
      ],
    },
    { name: '古代园艺', children: [] },
    { name: '养花日常', children: [] },
  ];

  /* 数据转换：扁平数组 → 树状分类结构 */
  /** @param {IndexEntry[]} rawData @returns {TransformedData} */
  function transformData(rawData) {
    // 先按原始 cat 分组（支持 "多肉/石莲花属" 层级格式）
    /** @type {Record<string, GridItem[]>} */
    const flatGroups = {};
    rawData.forEach((item) => {
      const catName = item.cat || '未分类';
      if (!flatGroups[catName]) flatGroups[catName] = [];
      flatGroups[catName].push({
        id: item.id,
        title: item.title,
        thumb: item.thumb || (item.images && item.images.length > 0 ? item.images[0] : ''),
        file_url: `./detail.html?id=${item.id}`,
        tag: item.tag || '',
        author: item.author || '',
      });
    });

    // 构建树
    const tree = [];
    let totalItems = 0;

    TREE_CONFIG.forEach((group) => {
      const node = { name: group.name, children: [], itemCount: 0 };

      if (group.children.length > 0) {
        // 有显式 children 列表（月季或带子分类的多肉）
        group.children.forEach((childName) => {
          // 多肉：cat 键为 "多肉/石莲花属" 格式
          const catKey = group.name === '多肉' ? `${group.name}/${childName}` : childName;
          const items = flatGroups[catKey] || [];
          node.children.push({ name: childName, items, isLeaf: true });
          node.itemCount += items.length;
          totalItems += items.length;
        });
      } else {
        // 无显式 children（多肉）：从 flatGroups 中自动发现子分类
        // 匹配 "多肉/xxx" 格式的 key
        const prefix = `${group.name}/`;
        const subCats = {};
        const directItems = [];

        Object.keys(flatGroups).forEach((catName) => {
          if (catName.startsWith(prefix)) {
            const subName = catName.slice(prefix.length);
            subCats[subName] = flatGroups[catName];
          } else if (catName === group.name) {
            directItems.push(...flatGroups[catName]);
          }
        });

        // 有子分类时按子分类展示
        if (Object.keys(subCats).length > 0) {
          Object.keys(subCats)
            .sort()
            .forEach((subName) => {
              const items = subCats[subName];
              node.children.push({ name: subName, items, isLeaf: true });
              node.itemCount += items.length;
              totalItems += items.length;
            });
        }

        // 直接属于多肉（无子分类）的条目
        if (directItems.length > 0) {
          node.children.push({ name: '其他', items: directItems, isLeaf: true });
          node.itemCount += directItems.length;
          totalItems += directItems.length;
        }
      }

      tree.push(node);
    });

    return { tree, flatGroups, totalItems, raw: rawData };
  }

  /* 初始化 */
  async function init() {
    document.getElementById('loading').classList.add('hidden');
    renderSidebar();
    renderMobileNav();
    if (featuredIds.length > 0) {
      renderFeatured();
    } else {
      await renderGrid(-1);
    }
    bindEvents();
  }

  /* 渲染桌面端左侧树状分类 */
  function renderSidebar() {
    const container = document.getElementById('cat-list');
    let html = `<div class="cat-item active" data-idx="-1">
    <span class="label">全部植物</span>
    <span class="count">${allData.totalItems}</span>
  </div>`;

    allData.tree.forEach((group, gIdx) => {
      // 父节点（月季 / 多肉）
      const groupId = `group-${gIdx}`;
      html += `<div class="tree-group" data-group="${groupId}">
      <div class="cat-item parent-item expanded" data-idx="${groupId}">
        <span class="tree-arrow">▼</span>
        <span class="label">${escapeHtml(group.name)}</span>
        <span class="count">${group.itemCount}</span>
      </div>
      <div class="tree-children">`;

      group.children.forEach((child, cIdx) => {
        const leafId = `${groupId}-${cIdx}`;
        html += `<div class="cat-item leaf-item" data-idx="${leafId}">
        <span class="tree-indent"></span>
        <span class="label">${escapeHtml(child.name)}</span>
        <span class="count">${child.items.length}</span>
      </div>`;
      });

      html += `</div></div>`;
    });

    container.innerHTML = html;

    // 演化图谱入口
    const evoLink = document.createElement('a');
    evoLink.href = './sedum-evo-2019.html';
    evoLink.target = '_blank'; // 新标签页打开，避免当前页路由错乱
    evoLink.className = 'cat-item evo-link';
    evoLink.innerHTML = '<span class="label">🧬 景天科演化图谱 2019</span>';
    evoLink.addEventListener('click', (e) => e.stopPropagation()); // 阻止 sidebar click handler 拦截
    container.appendChild(evoLink);

    // 园艺导航入口
    const navLink = document.createElement('a');
    navLink.href = './nav.html';
    navLink.className = 'cat-item evo-link';
    navLink.innerHTML = '<span class="label">🧭 园艺导航</span>';
    navLink.addEventListener('click', (e) => e.stopPropagation());
    container.appendChild(navLink);

    // 花友推荐入口
    const authorLink = document.createElement('a');
    authorLink.href = './authors.html';
    authorLink.className = 'cat-item evo-link';
    authorLink.innerHTML = '<span class="label">🎬 花友推荐</span>';
    authorLink.addEventListener('click', (e) => e.stopPropagation());
    container.appendChild(authorLink);
  }

  /* 渲染移动端横向分类条（扁平展示，带分组前缀） */
  function renderMobileNav() {
    const scrollContainer = document.getElementById('mobile-cat-scroll');

    // 全部
    const allChip = document.createElement('div');
    allChip.className = 'cat-chip active';
    allChip.dataset.idx = '-1';
    allChip.textContent = `全部 (${allData.totalItems})`;
    scrollContainer.appendChild(allChip);

    // 各叶子分类
    allData.tree.forEach((group, gIdx) => {
      group.children.forEach((child, cIdx) => {
        const chip = document.createElement('div');
        chip.className = 'cat-chip';
        chip.dataset.idx = `group-${gIdx}-${cIdx}`;
        chip.textContent = `${child.name} (${child.items.length})`;
        scrollContainer.appendChild(chip);
      });
    });

    // 演化图谱入口（移动端）
    const evoChip = document.createElement('a');
    evoChip.href = './sedum-evo-2019.html';
    evoChip.target = '_blank';
    evoChip.className = 'cat-chip evo-chip';
    evoChip.textContent = '🧬 演化图谱 2019';
    evoChip.addEventListener('click', (e) => e.stopPropagation());
    scrollContainer.appendChild(evoChip);

    // 园艺导航入口（移动端）
    const navChip = document.createElement('a');
    navChip.href = './nav.html';
    navChip.className = 'cat-chip evo-chip';
    navChip.textContent = '🧭 园艺导航';
    navChip.addEventListener('click', (e) => e.stopPropagation());
    scrollContainer.appendChild(navChip);

    // 花友推荐入口（移动端）
    const authorChip = document.createElement('a');
    authorChip.href = './authors.html';
    authorChip.className = 'cat-chip evo-chip';
    authorChip.textContent = '🎬 花友推荐';
    authorChip.addEventListener('click', (e) => e.stopPropagation());
    scrollContainer.appendChild(authorChip);
  }

  /* 根据树节点索引获取要展示的条目 */
  /** @param {number|string} catIdx @returns {GridItem[]} */
  function getItemsByIndex(catIdx) {
    if (catIdx === -1) {
      // 全部：合并所有叶子
      const result = [];
      allData.tree.forEach((g) => g.children.forEach((c) => result.push(...c.items)));
      return result;
    }
    if (typeof catIdx === 'number') catIdx = String(catIdx);
    // 父组节点（如 group-0 = 月季）
    if (/^group-(\d+)$/.test(catIdx)) {
      const gIdx = parseInt(catIdx.match(/^group-(\d+)$/)[1]);
      const group = allData.tree[gIdx];
      if (group) {
        const result = [];
        group.children.forEach((c) => result.push(...c.items));
        return result;
      }
    }
    // 叶子节点（如 group-0-0 = 丹陶）
    const leafMatch = catIdx.match(/^group-(\d+)-(\d+)$/);
    if (leafMatch) {
      const gIdx = parseInt(leafMatch[1]);
      const cIdx = parseInt(leafMatch[2]);
      const group = allData.tree[gIdx];
      if (group && group.children[cIdx]) return group.children[cIdx].items;
    }
    return [];
  }

  /* 首页推荐 */
  function renderFeatured() {
    // 优先从 searchIndex 查找，缺失项从原始 _indexData 回退
    const idMap = { ...searchIndex };
    if (_indexData) {
      _indexData.forEach((entry) => {
        if (!idMap[entry.id]) {
          idMap[entry.id] = {
            title: entry.title,
            cat: entry.cat || '',
            text: '',
            thumb: entry.thumb || (entry.images && entry.images[0]) || '',
          };
        }
      });
    }
    const items = featuredIds
      .map((id) => (idMap[id] ? { id, ...idMap[id], tag: '', author: '' } : null))
      .filter(Boolean);

    document.getElementById('section-title').textContent = '精选推荐';
    document.getElementById('total-count').textContent = `${items.length} 篇`;
    activeCatIdx = -1;
    setActiveCategory('-1');

    const grid = document.getElementById('grid');
    document.getElementById('pagination').innerHTML = '';
    if (!items.length) {
      grid.innerHTML = '<div class="empty">暂无推荐内容</div>';
      return;
    }

    grid.innerHTML = items
      .map((it) => {
        const safeTitle = escapeHtml(it.title);
        const safeCat = escapeHtml(it.cat);
        const safeId = escapeHtml(it.id);
        const thumbHtml = it.thumb
          ? `<img class="thumb" src="${escapeHtml(it.thumb)}" alt="${safeTitle}" loading="lazy" decoding="async"
             onerror="this.style.display='none';this.nextElementSibling.style.display='flex';"
             onload="this.classList.add('loaded')">
         <div class="no-thumb" style="display:none;">无图</div>`
          : `<div class="no-thumb">无图</div>`;
        const tagBadge =
          it.tag && !/^\d+$/.test(String(it.tag))
            ? `<span class="card-tag">#${escapeHtml(it.tag)}</span>`
            : '';
        return `<div class="card" data-id="${escapeHtml(it.id)}" data-title="${safeTitle}" role="button" tabindex="0" aria-label="查看详情：${safeTitle}">
      <span class="card-cat">${safeCat}</span>
      ${tagBadge}
      ${thumbHtml}
      <div class="card-title">${safeTitle}</div>
    </div>`;
      })
      .join('');
  }

  /* 搜索/过滤条目 — 返回要展示的条目列表及是否为搜索模式 */
  /** @param {number|string} catIdx @param {string} keyword @returns {Promise<SearchResult>} */
  async function searchItems(catIdx, keyword) {
    if (keyword) {
      // 首次搜索时按需加载 search-index.json
      await ensureSearchIndex();

      if (Object.keys(searchIndex).length > 0) {
        // 全文搜索模式：跨分类匹配标题+正文
        const kw = keyword.toLowerCase();
        const results = [];
        for (const [id, entry] of Object.entries(searchIndex)) {
          if (entry.title.toLowerCase().includes(kw) || entry.text.toLowerCase().includes(kw)) {
            results.push({ id, title: entry.title, cat: entry.cat, thumb: entry.thumb, file_url: '', tag: '', author: '' });
          }
        }
        // 按分类分组排序，同分类内按标题排
        results.sort((a, b) => a.cat.localeCompare(b.cat, 'zh') || a.title.localeCompare(b.title, 'zh'));
        return { items: results, isSearch: true };
      }
      // 搜索索引未加载成功 → 只匹配当前分类下的标题
      let items = getItemsByIndex(catIdx);
      const kw = keyword.toLowerCase();
      items = items.filter((it) => it.title.toLowerCase().includes(kw));
      return { items, isSearch: true };
    }

    let items = getItemsByIndex(catIdx);
    return { items, isSearch: false };
  }

  /* 更新区域标题和计数 */
  /** @param {number|string} catIdx @param {string} keyword @param {boolean} isSearch @param {number} totalCount */
  function updateSectionHeader(catIdx, keyword, isSearch, totalCount) {
    let titleText = '全部植物';
    if (isSearch) {
      titleText = `搜索: ${keyword}`;
    } else {
      if (typeof catIdx === 'number') catIdx = String(catIdx);
      if (/^group-(\d+)-(\d+)$/.test(catIdx)) {
        const m = catIdx.match(/^group-(\d+)-(\d+)$/);
        titleText = allData.tree[parseInt(m[1])].children[parseInt(m[2])].name;
      } else if (/^group-(\d+)$/.test(catIdx)) {
        titleText = allData.tree[parseInt(catIdx.match(/^group-(\d+)$/)[1])].name;
      }
    }
    document.getElementById('section-title').textContent = titleText;
    document.getElementById('total-count').textContent = `${totalCount} 个`;
  }

  /* 渲染卡片 HTML */
  /** @param {GridItem[]} pageItems @param {boolean} isSearch @returns {string} */
  function renderCards(pageItems, isSearch) {
    return pageItems
      .map((it) => {
        const safeTitle = escapeHtml(it.title);
        const safeUrl = escapeHtml(it.file_url || '');
        const safeCat = escapeHtml(it.cat || '');
        const safeId = escapeHtml(it.id || '');
        const thumbHtml = it.thumb
          ? `<img class="thumb" src="${escapeHtml(it.thumb)}" alt="${safeTitle} 缩略图" loading="lazy" decoding="async"
               onerror="this.style.display='none';this.nextElementSibling.style.display='flex';"
               onload="this.classList.add('loaded')">
           <div class="no-thumb" style="display:none;">无图</div>`
          : `<div class="no-thumb">无图</div>`;
        const catBadge = isSearch ? `<span class="card-cat">${safeCat}</span>` : '';
        const tagBadge =
          it.tag && !/^\d+$/.test(String(it.tag))
            ? `<span class="card-tag">#${escapeHtml(it.tag)}</span>`
            : '';
        return `<div class="card" data-id="${safeId}" data-url="${safeUrl}" data-title="${safeTitle}" role="button" tabindex="0" aria-label="查看详情：${safeTitle}">
      ${catBadge}
      ${tagBadge}
      ${thumbHtml}
      <div class="card-title">${safeTitle}</div>
    </div>`;
      })
      .join('');
  }

  /* 渲染右侧卡片网格 — 协调搜索/标题/分页/渲染 */
  /** @param {number|string} catIdx @param {string} [keyword] */
  async function renderGrid(catIdx, keyword = '') {
    const grid = document.getElementById('grid');
    currentKeyword = keyword;

    // 1. 搜索/过滤
    const { items, isSearch } = await searchItems(catIdx, keyword);

    // 2. 更新标题
    updateSectionHeader(catIdx, keyword, isSearch, items.length);

    // 3. 分页计算
    pageSize = calculatePageSize();

    if (!items.length) {
      grid.innerHTML = '<div class="empty">暂无匹配结果</div>';
      document.getElementById('pagination').innerHTML = '';
      return;
    }

    const totalPages = Math.ceil(items.length / pageSize);
    currentPage = Math.max(1, Math.min(currentPage, totalPages));
    const start = (currentPage - 1) * pageSize;
    const pageItems = items.slice(start, start + pageSize);

    // 4. 渲染卡片
    grid.innerHTML = renderCards(pageItems, isSearch);

    renderPagination(
      currentPage,
      totalPages,
      items.length,
      start + 1,
      Math.min(start + pageSize, items.length)
    );
  }

  /* 渲染分页控件 */
  /** @param {number} page @param {number} totalPages @param {number} total @param {number} from @param {number} to */
  function renderPagination(page, totalPages, total, from, to) {
    const container = document.getElementById('pagination');
    if (totalPages <= 1) {
      container.innerHTML = `<div class="page-info">共 ${total} 个</div>`;
      return;
    }

    let html = '<div class="page-inner">';

    // 上一页
    html += `<button class="page-btn" data-page="${page - 1}" ${page <= 1 ? 'disabled' : ''}>‹</button>`;

    // 页码按钮
    const maxVisible = 5;
    let startPage = Math.max(1, page - Math.floor(maxVisible / 2));
    const endPage = Math.min(totalPages, startPage + maxVisible - 1);
    if (endPage - startPage < maxVisible - 1) {
      startPage = Math.max(1, endPage - maxVisible + 1);
    }

    if (startPage > 1) {
      html += `<button class="page-btn" data-page="1">1</button>`;
      if (startPage > 2) html += `<span class="page-ellipsis">…</span>`;
    }

    for (let i = startPage; i <= endPage; i++) {
      html += `<button class="page-btn${i === page ? ' active' : ''}" data-page="${i}">${i}</button>`;
    }

    if (endPage < totalPages) {
      if (endPage < totalPages - 1) html += `<span class="page-ellipsis">…</span>`;
      html += `<button class="page-btn" data-page="${totalPages}">${totalPages}</button>`;
    }

    // 下一页
    html += `<button class="page-btn" data-page="${page + 1}" ${page >= totalPages ? 'disabled' : ''}>›</button>`;

    html += '</div>';
    html += `<div class="page-info">${from}-${to} / 共 ${total} 个</div>`;

    container.innerHTML = html;
  }

  /* 切换分页 */
  /** @param {number} page */
  async function goToPage(page) {
    currentPage = page;
    await renderGrid(activeCatIdx, currentKeyword);
    const content = document.getElementById('content');
    if (content) content.scrollTo({ top: 0, behavior: 'smooth' });
    else window.scrollTo({ top: 0, behavior: 'smooth' });
  }
  // goToPage 通过事件委托调用，无需暴露到 window

  /* 设置激活分类 */
  function setActiveCategory(idx) {
    activeCatIdx = idx;
    // 桌面端 sidebar
    document.querySelectorAll('#cat-list .cat-item').forEach((el) => {
      el.classList.toggle('active', el.dataset.idx === idx);
    });
    // 移动端 chips
    document.querySelectorAll('#mobile-cat-scroll .cat-chip').forEach((el) => {
      el.classList.toggle('active', el.dataset.idx === idx);
    });
    // 移动端滚动到激活项
    const activeChip = document.querySelector('#mobile-cat-scroll .cat-chip.active');
    if (activeChip) {
      activeChip.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
    }
  }

  /* 搜索防抖公共函数 */
  function debounceSearch(inputId, scrollTarget) {
    let timer;
    const el = document.getElementById(inputId);
    if (!el) return;
    el.addEventListener('input', (e) => {
      clearTimeout(timer);
      timer = setTimeout(async () => {
        currentPage = 1;
        await renderGrid(activeCatIdx, e.target.value.trim());
        if (scrollTarget === 'content') {
          document.getElementById('content').scrollTo(0, 0);
        } else {
          window.scrollTo({ top: 0, behavior: 'smooth' });
        }
      }, 250);
    });
  }

  /* 绑定事件 */
  function bindEvents() {
    // 桌面端分类点击
    document.getElementById('cat-list').addEventListener('click', async (e) => {
      const item = e.target.closest('.cat-item');
      if (!item) return;

      // 父节点：折叠/展开切换
      if (item.classList.contains('parent-item')) {
        item.classList.toggle('expanded');
        const groupEl = item.closest('.tree-group');
        if (groupEl) {
          const children = groupEl.querySelector('.tree-children');
          if (children) {
            const isOpen = item.classList.contains('expanded');
            children.style.display = isOpen ? '' : 'none';
            item.querySelector('.tree-arrow').textContent = isOpen ? '▼' : '▶';
          }
        }
        // 点击父节点也显示该组全部内容
        setActiveCategory(item.dataset.idx);
        currentPage = 1;
        await renderGrid(item.dataset.idx, document.getElementById('search-input').value.trim());
        document.getElementById('content').scrollTo(0, 0);
        return;
      }

      const idx = item.dataset.idx;
      setActiveCategory(idx);
      currentPage = 1;
      if (idx === '-1' && featuredIds.length > 0) {
        renderFeatured();
      } else {
        await renderGrid(idx, document.getElementById('search-input').value.trim());
      }
      document.getElementById('content').scrollTo(0, 0);
    });

    // 移动端分类点击
    document.getElementById('mobile-cat-scroll').addEventListener('click', async (e) => {
      const chip = e.target.closest('.cat-chip');
      if (!chip) return;
      const idx = chip.dataset.idx;
      setActiveCategory(idx);
      currentPage = 1;
      if (idx === '-1' && featuredIds.length > 0) {
        renderFeatured();
      } else {
        const keyword = document.getElementById('mobile-search-input').value.trim();
        await renderGrid(idx, keyword);
      }
      window.scrollTo({ top: 0, behavior: 'smooth' });
    });

    // 搜索（桌面端 + 移动端共用防抖逻辑）
    debounceSearch('search-input', 'content');
    debounceSearch('mobile-search-input', 'window');

    // 搜索框聚焦时预加载 search-index（让搜索体验更流畅）
    ['search-input', 'mobile-search-input'].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.addEventListener('focus', () => ensureSearchIndex(), { once: true });
    });

    // 卡片点击 → 弹窗（支持 data-id 直接打开, 或 data-url 兼容旧格式）
    document.getElementById('grid').addEventListener('click', (e) => {
      // 分类模式下不打开详情
      if (classifyMode) return;
      const card = e.target.closest('.card');
      if (!card) return;
      const id = card.dataset.id || card.dataset.url;
      // ── 特殊页面：必须在同步上下文中打开，否则弹窗拦截器会阻止 ──
      const SPECIAL_PAGES = { sedum_evo_2019: './sedum-evo-2019.html' };
      if (SPECIAL_PAGES[id]) {
        window.open(SPECIAL_PAGES[id], '_blank');
        return;
      }
      openModal(id, card.dataset.title);
    });

    // 关闭弹窗
    document.getElementById('modal-close').addEventListener('click', closeModal);
    document.getElementById('modal-overlay').addEventListener('click', (e) => {
      if (e.target === document.getElementById('modal-overlay')) closeModal();
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        closeModal();
        document.getElementById('lightbox').classList.remove('active');
        document.getElementById('qr-overlay').classList.remove('active');
      }
    });

    // ── 事件委托：分页按钮 ──
    document.getElementById('pagination').addEventListener('click', (e) => {
      const btn = e.target.closest('.page-btn');
      if (!btn || btn.disabled) return;
      const page = parseInt(btn.dataset.page, 10);
      if (!isNaN(page)) goToPage(page);
    });

    // ── 事件委托：弹窗内轮播箭头 + 分享按钮 ──
    document.getElementById('detail-view').addEventListener('click', (e) => {
      // 轮播箭头
      const arrow = e.target.closest('.gallery-arrow');
      if (arrow) {
        slideGallery(parseInt(arrow.dataset.dir, 10) || 1);
        return;
      }
      // 分享按钮
      const shareBtn = e.target.closest('.share-btn');
      if (shareBtn) {
        const action = shareBtn.dataset.action;
        const sid = shareBtn.dataset.id || '';
        const stitle = shareBtn.dataset.title || '';
        if (action === 'copy') copyLink(sid);
        else if (action === 'qr') showQrPopup(sid);
        else if (action === 'qq') shareToQQ(sid, stitle);
        else if (action === 'native') nativeShare(sid, stitle);
      }
    });

    // ── 事件委托：分类工具栏按钮 ──
    document.addEventListener('click', (e) => {
      if (!classifyMode) return;
      const exportBtn = e.target.closest('.classify-export-btn');
      if (exportBtn) {
        exportClassifyResult();
        return;
      }
      const clearBtn = e.target.closest('.classify-clear-btn');
      if (clearBtn) {
        clearClassifyData();
      }
    });

    // ── 分类器模式切换按钮 ──
    const classifyToggle = document.getElementById('classify-toggle');
    if (classifyToggle) classifyToggle.addEventListener('click', toggleClassifyMode);

    // ── QR 二维码关闭 ──
    const qrOverlay = document.getElementById('qr-overlay');
    if (qrOverlay) {
      qrOverlay.addEventListener('click', (e) => {
        if (e.target === qrOverlay || e.target.id === 'qr-close') closeQrPopup();
      });
    }
  }

  /* 打开弹窗 — 内联渲染详情（按需加载内容） */
  /** @param {string} urlOrId @param {string} title */
  async function openModal(urlOrId, title) {
    const id =
      typeof urlOrId === 'string' && urlOrId.startsWith('./detail.html')
        ? urlOrId.replace('./detail.html?id=', '')
        : urlOrId;

    document.getElementById('modal-title').textContent = title;

    const detailEl = document.getElementById('detail-view');

    // 先显示加载指示
    detailEl.innerHTML = `<div class="detail-loading"><p>加载中...</p></div>`;
    detailEl.classList.add('active');
    document.getElementById('modal-overlay').classList.add('open');
    document.body.style.overflow = 'hidden';

    try {
      const content = await getContent(id);

      if (content && Array.isArray(content.segments)) {
        const isSocial = content.type === '小红书' || content.type === '微博';
        const postTime = content.time || '';

        const tagLabel =
          content.tag && !/^\d+$/.test(String(content.tag)) ? `#${escapeHtml(String(content.tag))}` : '';
        const authorObj = content.author || {};
        const authorName = typeof authorObj === 'string' ? authorObj : authorObj.name || '';
        const weiboUrl = content.weibo_url || '';

        let html;
        if (isSocial) {
          // 左图右文布局
          const images = content.segments.filter((s) => s.i);
          const totalImg = images.length;
          html = `<div class="detail-header"><h2 class="detail-title">${escapeHtml(content.title || title)}</h2>`;
          if (tagLabel || authorName) {
            html += `<div class="detail-meta">${tagLabel ? `<span class="detail-tag">${tagLabel}</span>` : ''}${authorName ? `<span class="detail-author">👤 ${escapeHtml(authorName)}</span>` : ''}${weiboUrl ? `<a href="${weiboUrl}" target="_blank" class="detail-weibo-link">🔗 查看原帖</a>` : ''}</div>`;
          }
          html += '</div><div class="social-post">';
          // 左侧图片轮播
          html += '<div class="social-gallery">';
          if (totalImg > 0) {
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
            // inject current index
          }
          html += '</div>';
          // 右侧正文
          html += '<div class="social-content">';
          if (authorName) html += `<div class="social-author">👤 ${escapeHtml(authorName)}</div>`;
          if (postTime) html += `<div class="social-time">${escapeHtml(postTime)}</div>`;
          content.segments
            .filter((s) => s.t)
            .forEach((seg) => {
              const paragraphs = splitParagraphs(seg.t);
              paragraphs.forEach((p) => {
                html += `<p class="social-text">${escapeHtml(p)}</p>`;
              });
            });
          html += '</div></div>';

          html += buildShareBar(id, content.title || title);
          detailEl.innerHTML = html;
          // 绑定图片点击灯箱
          detailEl.querySelectorAll('.gallery-slide img').forEach((img) => {
            img.addEventListener('click', () => showLightbox(img.src));
            img.style.cursor = 'pointer';
          });
          // 初始化轮播状态
          galleryTotal = totalImg;
          galleryIdx = 1;
        } else {
          // 原有布局：文字图片交错
          html = `<div class="detail-header"><h2 class="detail-title">${escapeHtml(content.title || title)}</h2>`;
          if (tagLabel || authorName) {
            html += `<div class="detail-meta">${tagLabel ? `<span class="detail-tag">${tagLabel}</span>` : ''}${authorName ? `<span class="detail-author">👤 ${escapeHtml(authorName)}</span>` : ''}${weiboUrl ? `<a href="${weiboUrl}" target="_blank" class="detail-weibo-link">🔗 查看原帖</a>` : ''}</div>`;
          }
          html += '</div><div class="detail-body">';
          content.segments.forEach((seg) => {
            if (seg.t) {
              html += `<p>${escapeHtml(seg.t)}</p>`;
            } else if (seg.i) {
              html += `<img src="${escapeHtml(seg.i)}" alt="图片" loading="lazy" decoding="async"
                          onload="this.classList.add('loaded')">`;
            }
          });
          html += '</div>';
          html += buildShareBar(id, content.title || title);
          detailEl.innerHTML = html;
          // 绑定图片点击灯箱事件
          detailEl.querySelectorAll('.detail-body img').forEach((img) => {
            img.addEventListener('click', () => showLightbox(img.src));
            img.style.cursor = 'pointer';
          });
        }
      } else {
        detailEl.innerHTML = `
        <div class="detail-error">
          <p>暂无详情内容</p>
          <p style="font-size:12px;color:#bbb;margin-top:8px;">ID: ${escapeHtml(id)}</p>
        </div>`;
      }
    } catch (err) {
      console.error('[openModal] 渲染详情失败:', err);
      detailEl.innerHTML = `
      <div class="detail-error">
        <p>内容渲染出错</p>
        <p style="font-size:12px;color:#bbb;margin-top:8px;">请稍后重试</p>
      </div>`;
    }
  }

  /* 关闭弹窗 */
  function closeModal() {
    document.getElementById('modal-overlay').classList.remove('open');
    const detailEl = document.getElementById('detail-view');
    detailEl.classList.remove('active');
    // 延迟清空内容，让关闭动画更平滑
    setTimeout(() => {
      detailEl.innerHTML = '';
    }, 300);
    document.body.style.overflow = '';
  }

  /* 图片灯箱 */
  /** @param {string} src */
  function showLightbox(src) {
    const lb = document.getElementById('lightbox');
    lb.innerHTML = `<img src="${src}" alt="预览" loading="lazy" decoding="async"
                     onload="this.classList.add('loaded')">`;
    lb.classList.add('active');
  }
  document.addEventListener('DOMContentLoaded', () => {
    const lb = document.getElementById('lightbox');
    lb.addEventListener('click', () => lb.classList.remove('active'));
  });

  /* 图片轮播切换 */
  /** @param {number} dir */
  function slideGallery(dir) {
    const track = document.getElementById('gallery-track');
    if (!track || !galleryTotal) return;
    galleryIdx = Math.max(1, Math.min(galleryTotal, galleryIdx + dir));
    track.style.transform = `translateX(-${(galleryIdx - 1) * 100}%)`;
    const counter = document.getElementById('gallery-idx');
    if (counter) counter.textContent = String(galleryIdx);
  }
  // slideGallery 通过事件委托调用，无需暴露到 window

  /* HTML 转义（纯字符串实现，避免频繁创建 DOM 节点） */
  /** @param {string} str @returns {string} */
  const ESCAPE_MAP = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  function escapeHtml(str) {
    return String(str).replace(/[&<>"']/g, (c) => ESCAPE_MAP[c]);
  }

  /* 智能分段：自动识别文本中的段落边界 */
  /** @param {string} text @returns {string[]} */
  function splitParagraphs(text) {
    if (!text || !text.trim()) return [];
    // 1. 双换行 = 明确自然段
    if (text.includes('\n\n')) {
      return text
        .split(/\n\n+/)
        .map((p) => {
          return p.trim();
        })
        .filter(Boolean);
    }
    // 2. 单换行 = 手动分行
    if (text.includes('\n')) {
      return text
        .split(/\n+/)
        .map((p) => {
          return p.trim();
        })
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

  /* ===== 分享功能 ===== */

  /** @param {string} id @param {string} title @returns {string} */
  function buildShareBar(id, title) {
    const url = encodeURIComponent(`${location.origin + location.pathname}?share=${id}`);
    const text = encodeURIComponent(title || '园艺图鉴');
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

  /** @param {string} id @returns {string} */
  function getShareUrl(id) {
    return `${location.origin + location.pathname.replace(/\/?$/, '/')}detail.html?id=${encodeURIComponent(id)}`;
  }

  /** @param {string} id */
  function copyLink(id) {
    const url = getShareUrl(id);
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(url).then(() => showToast('链接已复制，去微信/小红书粘贴吧'));
    } else {
      const ta = document.createElement('textarea');
      ta.value = url;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
      showToast('链接已复制，去微信/小红书粘贴吧');
    }
  }

  /** @param {string} id @param {string} title */
  function shareToQQ(id, title) {
    const url = getShareUrl(id);
    const qqUrl = `https://connect.qq.com/widget/shareqq/index.html?url=${encodeURIComponent(
      url
    )}&title=${encodeURIComponent(title || '园艺图鉴')}&summary=${encodeURIComponent(
      '来自园艺图鉴的精彩内容'
    )}&pics=`;
    window.open(qqUrl, '_blank', 'width=600,height=500');
  }

  /** @param {string} id */
  function showQrPopup(id) {
    const url = getShareUrl(id);
    const qrImg = document.getElementById('qr-img');
    qrImg.src = `https://api.qrserver.com/v1/create-qr-code/?size=200x200&data=${encodeURIComponent(url)}`;
    document.getElementById('qr-overlay').classList.add('active');
  }

  function closeQrPopup() {
    document.getElementById('qr-overlay').classList.remove('active');
  }

  /** @param {string} id @param {string} title */
  function nativeShare(id, title) {
    const url = getShareUrl(id);
    if (navigator.share) {
      navigator.share({ title: title || '园艺图鉴', text: title || '园艺图鉴', url }).catch(() => {});
    } else {
      copyLink(id);
    }
  }

  /** @param {string} msg */
  function showToast(msg) {
    const t = document.getElementById('toast');
    t.textContent = msg;
    t.classList.add('show');
    setTimeout(() => t.classList.remove('show'), 2200);
  }

  /* 启动 */
  loadData();

  /* 窗口大小变化时重新计算每页数量并重绘 */
  let resizeTimer;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(async () => {
      const newSize = calculatePageSize();
      if (newSize !== pageSize) {
        pageSize = newSize;
        await renderGrid(activeCatIdx, currentKeyword);
      }
    }, 250);
  });

  /* ===== 分类器模式 ===== */
  const CLASSIFY_MODE_KEY = 'feijibei-classify-mode';
  const CLASSIFY_MAP_KEY = 'feijibei-classify-map';
  const CLASSIFY_CFG_KEY = 'feijibei-classify-config';
  const CLASSIFY_DEL_KEY = 'feijibei-classify-deletions';

  // 可自定义的快捷键配置（localStorage 持久化）
  /** @type {ClassifyKeys} */
  const DEFAULT_KEYS = {
    q: '玫昂',
    w: '育种相关',
    e: '',
    r: '',
    d: '',
  };
  const DEFAULT_COLORS = ['#e74c3c', '#9b59b6', '#27ae60', '#e67e22', '#3498db'];

  /** @type {ClassifyKeys} */
  let classifyKeys = {};
  try {
    classifyKeys = JSON.parse(localStorage.getItem(CLASSIFY_CFG_KEY) || '{}');
  } catch {
    /* JSON 解析失败则使用默认值 */
  }
  if (!Object.keys(classifyKeys).length) classifyKeys = Object.assign({}, DEFAULT_KEYS);

  function saveClassifyKeys() {
    localStorage.setItem(CLASSIFY_CFG_KEY, JSON.stringify(classifyKeys));
  }

  /** @type {boolean} */
  let classifyMode = false;
  /** @type {string|null} */
  let classifySelectedId = null;
  /** @type {Record<string, string>} */
  let classifyMap = {};
  /** @type {string[]} */
  let classifyDeletions = [];

  try {
    classifyMap = JSON.parse(localStorage.getItem(CLASSIFY_MAP_KEY) || '{}');
  } catch {
    classifyMap = {};
  }
  try {
    classifyDeletions = JSON.parse(localStorage.getItem(CLASSIFY_DEL_KEY) || '[]');
  } catch {
    classifyDeletions = [];
  }

  function toggleClassifyMode() {
    classifyMode = !classifyMode;
    if (classifyMode) {
      enterClassifyMode();
      document.getElementById('classify-toggle').style.display = '';
    } else {
      exitClassifyMode();
      document.getElementById('classify-toggle').style.display = 'none';
    }
  }
  // toggleClassifyMode 通过事件委托调用，无需暴露到 window

  // Ctrl+Shift+C 快捷键切换分类模式
  document.addEventListener('keydown', (e) => {
    if (e.ctrlKey && e.shiftKey && e.key === 'C') {
      e.preventDefault();
      toggleClassifyMode();
    }
  });

  function enterClassifyMode() {
    document.body.classList.add('classify-mode');
    renderClassifyToolbar();
    applyClassifyLabels();
    showToast('分类模式 — 填入类别后按快捷键');
  }

  function exitClassifyMode() {
    document.body.classList.remove('classify-mode');
    classifySelectedId = null;
    removeClassifyToolbar();
    clearClassifyLabels();
    showToast('分类模式已关闭');
  }

  function renderClassifyToolbar() {
    const sectionHeader = document.querySelector('.section-header');
    if (!sectionHeader || document.getElementById('classify-toolbar')) return;

    const tb = document.createElement('div');
    tb.id = 'classify-toolbar';
    tb.className = 'classify-toolbar';

    const keyOrder = ['q', 'w', 'e', 'r', 'd'];
    let html = '<span class="classify-toolbar-label">快捷键：</span>';

    keyOrder.forEach((key) => {
      const name = classifyKeys[key] || '';
      html += `<span class="classify-key-hint">
      <kbd>${key.toUpperCase()}</kbd>
      <input type="text" class="classify-key-input" data-key="${key}"
             value="${escapeHtml(name)}" placeholder="分类名"
             title="输入分类名，留空则禁用此键">
    </span>`;
    });

    html += `<span class="classify-key-hint classify-delete-hint">
    <kbd>X</kbd>待删除
  </span>`;
    html += `<span class="classify-key-hint classify-undo">
    <kbd>Z</kbd>撤销
  </span>`;
    html += `<button class="classify-export-btn" data-action="export">导出</button>`;
    html += `<button class="classify-clear-btn" data-action="clear">清空</button>`;

    tb.innerHTML = html;
    sectionHeader.appendChild(tb);

    // 绑定输入事件
    tb.querySelectorAll('.classify-key-input').forEach((input) => {
      input.addEventListener('input', (e) => {
        const key = e.target.dataset.key;
        classifyKeys[key] = e.target.value.trim();
        saveClassifyKeys();
      });
      // 阻止输入时触发快捷键
      input.addEventListener('keydown', (e) => e.stopPropagation());
    });
  }

  function removeClassifyToolbar() {
    const tb = document.getElementById('classify-toolbar');
    if (tb) tb.remove();
  }

  function applyClassifyLabels() {
    document.querySelectorAll('.card').forEach((card) => {
      const id = card.dataset.id;
      if (!id) return;
      // 已分类
      if (classifyMap[id]) {
        addClassifyBadge(card, classifyMap[id]);
      }
      // 待删除
      if (classifyDeletions.includes(id)) {
        addDeleteBadge(card);
      }
    });
  }

  // 事件委托：分类模式下点击卡片选中
  document.addEventListener('click', (e) => {
    if (!classifyMode) return;
    const card = e.target.closest('.card');
    if (!card) return;
    e.stopPropagation();
    const id = card.dataset.id;
    if (!id) return;
    document
      .querySelectorAll('.card.classify-selected')
      .forEach((c) => c.classList.remove('classify-selected'));
    card.classList.add('classify-selected');
    classifySelectedId = id;
  });

  function clearClassifyLabels() {
    document.querySelectorAll('.classify-badge').forEach((b) => b.remove());
    document.querySelectorAll('.classify-delete-badge').forEach((b) => b.remove());
    document.querySelectorAll('.card.classify-selected,.card.classify-marked').forEach((c) => {
      c.classList.remove('classify-selected', 'classify-marked');
    });
  }

  function classifyByKey(key) {
    if (!classifyMode || !classifySelectedId) {
      if (classifyMode) showToast('请先点击一张卡片');
      return;
    }
    const catName = classifyKeys[key];
    if (!catName) {
      showToast('请先在工具栏填入该键对应的分类名');
      return;
    }

    // 从待删除列表移除（重新分类 = 不删了）
    const delIdx = classifyDeletions.indexOf(classifySelectedId);
    if (delIdx >= 0) {
      classifyDeletions.splice(delIdx, 1);
      localStorage.setItem(CLASSIFY_DEL_KEY, JSON.stringify(classifyDeletions));
    }

    classifyMap[classifySelectedId] = catName;

    const card = document.querySelector(`.card[data-id="${CSS.escape(classifySelectedId)}"]`);
    if (card) {
      card.querySelector('.classify-delete-badge')?.remove();
      card.classList.remove('classify-marked');
      card.querySelector('.classify-badge')?.remove();
      addClassifyBadge(card, catName);
    }

    autoSelectNext(classifySelectedId);
    localStorage.setItem(CLASSIFY_MAP_KEY, JSON.stringify(classifyMap));
  }
  // classifyByKey 通过键盘事件调用，无需暴露到 window

  function toggleDeleteMark() {
    if (!classifyMode || !classifySelectedId) {
      if (classifyMode) showToast('请先点击一张卡片');
      return;
    }
    const id = classifySelectedId;
    const idx = classifyDeletions.indexOf(id);
    const card = document.querySelector(`.card[data-id="${CSS.escape(id)}"]`);

    if (idx >= 0) {
      // 取消删除标记
      classifyDeletions.splice(idx, 1);
      if (card) {
        card.querySelector('.classify-delete-badge')?.remove();
        card.classList.remove('classify-marked');
      }
      showToast('已取消删除标记');
    } else {
      // 标记删除
      classifyDeletions.push(id);
      if (card) {
        addDeleteBadge(card);
        card.classList.add('classify-marked');
      }
      autoSelectNext(id);
      showToast('已标记为待删除');
    }

    localStorage.setItem(CLASSIFY_DEL_KEY, JSON.stringify(classifyDeletions));
  }
  // toggleDeleteMark 通过键盘事件调用，无需暴露到 window

  function classifyUndo() {
    if (!classifyMode || !classifySelectedId) return;
    delete classifyMap[classifySelectedId];
    const card = document.querySelector(`.card[data-id="${CSS.escape(classifySelectedId)}"]`);
    if (card) {
      card.querySelector('.classify-badge')?.remove();
    }
    localStorage.setItem(CLASSIFY_MAP_KEY, JSON.stringify(classifyMap));
    showToast('已撤销');
  }
  // classifyUndo 通过键盘事件调用，无需暴露到 window

  function addClassifyBadge(card, catName) {
    if (card.querySelector('.classify-badge')) return;
    const colors = ['#e74c3c', '#9b59b6', '#27ae60', '#e67e22', '#3498db', '#1abc9c', '#f39c12', '#2980b9'];
    const keyOrder = ['q', 'w', 'e', 'r', 'd'];
    const idx = keyOrder.findIndex((k) => classifyKeys[k] === catName);
    const color = colors[idx] || '#666';

    const badge = document.createElement('span');
    badge.className = 'classify-badge';
    badge.textContent = catName;
    badge.style.background = color;
    card.appendChild(badge);
  }

  function addDeleteBadge(card) {
    if (card.querySelector('.classify-delete-badge')) return;
    const badge = document.createElement('span');
    badge.className = 'classify-delete-badge';
    badge.textContent = '🗑 待删除';
    card.appendChild(badge);
  }

  function autoSelectNext(currentId) {
    const cards = Array.from(document.querySelectorAll('.card'));
    const idx = cards.findIndex((c) => c.dataset.id === currentId);
    if (idx >= 0 && idx + 1 < cards.length) {
      const nextCard = cards[idx + 1];
      document
        .querySelectorAll('.card.classify-selected')
        .forEach((c) => c.classList.remove('classify-selected'));
      nextCard.classList.add('classify-selected');
      classifySelectedId = nextCard.dataset.id;
      nextCard.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  }

  function exportClassifyResult() {
    const classifyCount = Object.keys(classifyMap).length;
    const delCount = classifyDeletions.length;
    if (classifyCount === 0 && delCount === 0) {
      showToast('还没有分类或删除数据');
      return;
    }

    // 按 cat 分组统计
    const stats = {};
    for (const [id, cat] of Object.entries(classifyMap)) {
      if (!stats[cat]) stats[cat] = 0;
      stats[cat]++;
    }

    const result = {
      _meta: {
        description: '分类结果 — patches 用于更新 index.json cat，_deletions 是需要删除的条目',
        total_classified: classifyCount,
        total_deletions: delCount,
        stats,
      },
      patches: Object.assign({}, classifyMap),
      _deletions: classifyDeletions.slice(),
    };

    const blob = new Blob([JSON.stringify(result, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `classify-result-${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    URL.revokeObjectURL(url);

    let msg = `已导出 ${classifyCount} 条分类`;
    if (delCount > 0) msg += ` + ${delCount} 条待删除`;
    showToast(msg);
  }
  // exportClassifyResult 通过事件委托调用，无需暴露到 window

  function clearClassifyData() {
    if (Object.keys(classifyMap).length === 0 && classifyDeletions.length === 0) {
      showToast('没有数据需要清空');
      return;
    }
    classifyMap = {};
    classifyDeletions = [];
    classifySelectedId = null;
    localStorage.removeItem(CLASSIFY_MAP_KEY);
    localStorage.removeItem(CLASSIFY_DEL_KEY);
    document.querySelectorAll('.classify-badge,.classify-delete-badge').forEach((b) => b.remove());
    document.querySelectorAll('.card.classify-selected,.card.classify-marked').forEach((c) => {
      c.classList.remove('classify-selected', 'classify-marked');
    });
    showToast('全部数据已清空');
  }
  // clearClassifyData 通过事件委托调用，无需暴露到 window

  // 全局键盘监听
  document.addEventListener('keydown', (e) => {
    if (!classifyMode) return;
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;

    const key = e.key.toLowerCase();
    if (classifyKeys[key]) {
      e.preventDefault();
      classifyByKey(key);
    } else if (key === 'x') {
      e.preventDefault();
      toggleDeleteMark();
    } else if (key === 'z') {
      e.preventDefault();
      classifyUndo();
    }
  });

  // MutationObserver：翻页后自动打标注
  let _classifyObserver = null;
  function startClassifyObserver() {
    if (_classifyObserver) return;
    const grid = document.getElementById('grid');
    if (!grid) return;
    _classifyObserver = new MutationObserver(() => {
      if (!classifyMode) return;
      applyClassifyLabels();
    });
    _classifyObserver.observe(grid, { childList: true });
  }
  document.addEventListener('DOMContentLoaded', startClassifyObserver);
  if (document.readyState !== 'loading') startClassifyObserver();

  /* 注册 Service Worker（PWA 离线支持） */
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('./sw.js').catch((err) => console.warn('SW 注册失败:', err.message));
    });
  }

  /* 关闭 IIFE，避免全局变量污染 */
})();
