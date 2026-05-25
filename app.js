/* ==================== 主逻辑 ==================== */
(function() {
  'use strict';

let allData = null;      // 树状分类数据 { tree, flatGroups, totalItems }
let contentIndex = null; // ID → 分类映射（content-index.json）
let contentCache = {};   // 已加载的分类内容缓存 { '丹陶': {...}, '奥斯汀': {...}, ... }
let activeCatIdx = -1;   // -1 = 全部，格式: "group-child" 如 "月季-丹陶" 或 "多肉"

/* 加载数据 */
async function loadData() {
  try {
    const [indexRes, ciRes] = await Promise.all([
      fetch('./data/index.json'),
      fetch('./data/content/content-index.json')
    ]);
    if (!indexRes.ok) throw new Error('data/index.json 未找到');
    if (!ciRes.ok) throw new Error('data/content/content-index.json 未找到');

    const rawData = await indexRes.json();
    contentIndex = await ciRes.json();
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

/* 按需加载分类内容（带缓存） */
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
const TREE_CONFIG = [
  {
    name: '月季',
    children: [
      '丹陶', '吉洛', '奥斯汀', '戴尔巴德', '玫昂', '科尔德斯',
      '古老月季', '育种相关', '特性合集', '种植分享',
      '竞赛,评选以及月季园实测', '其他育种家'
    ]
  },
  { name: '多肉', children: [
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
    '瓦松属'
  ] }
];

/* 数据转换：扁平数组 → 树状分类结构 */
function transformData(rawData) {
  // 先按原始 cat 分组（支持 "多肉/石莲花属" 层级格式）
  const flatGroups = {};
  rawData.forEach(item => {
    const catName = item.cat || '未分类';
    if (!flatGroups[catName]) flatGroups[catName] = [];
    flatGroups[catName].push({
      title: item.title,
      thumb: item.images && item.images.length > 0 ? item.images[0] : '',
      file_url: `./detail.html?id=${item.id}`
    });
  });

  // 构建树
  const tree = [];
  let totalItems = 0;

  TREE_CONFIG.forEach(group => {
    const node = { name: group.name, children: [], itemCount: 0 };

    if (group.children.length > 0) {
      // 有显式 children 列表（月季或带子分类的多肉）
      group.children.forEach(childName => {
        // 多肉：cat 键为 "多肉/石莲花属" 格式
        const catKey = (group.name === '多肉') ? (group.name + '/' + childName) : childName;
        const items = flatGroups[catKey] || [];
        node.children.push({ name: childName, items: items, isLeaf: true });
        node.itemCount += items.length;
        totalItems += items.length;
      });
    } else {
      // 无显式 children（多肉）：从 flatGroups 中自动发现子分类
      // 匹配 "多肉/xxx" 格式的 key
      const prefix = group.name + '/';
      const subCats = {};
      const directItems = [];

      Object.keys(flatGroups).forEach(catName => {
        if (catName.startsWith(prefix)) {
          const subName = catName.slice(prefix.length);
          subCats[subName] = flatGroups[catName];
        } else if (catName === group.name) {
          directItems.push(...flatGroups[catName]);
        }
      });

      // 有子分类时按子分类展示
      if (Object.keys(subCats).length > 0) {
        Object.keys(subCats).sort().forEach(subName => {
          const items = subCats[subName];
          node.children.push({ name: subName, items: items, isLeaf: true });
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

  return { tree, flatGroups, totalItems };
}

/* 初始化 */
function init() {
  document.getElementById('loading').classList.add('hidden');
  renderSidebar();
  renderMobileNav();
  renderGrid(-1);
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
  evoLink.href = '景天科演化图谱.html';
  evoLink.className = 'cat-item evo-link';
  evoLink.innerHTML = '<span class="label">🧬 景天科演化图谱 2019</span>';
  container.appendChild(evoLink);
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
  allData.tree.forEach(group => {
    group.children.forEach((child, cIdx) => {
      const chip = document.createElement('div');
      chip.className = 'cat-chip';
      chip.dataset.idx = `group-${allData.tree.indexOf(group)}-${cIdx}`;
      chip.textContent = `${child.name} (${child.items.length})`;
      scrollContainer.appendChild(chip);
    });
  });

  // 演化图谱入口（移动端）
  const evoChip = document.createElement('a');
  evoChip.href = '景天科演化图谱.html';
  evoChip.className = 'cat-chip evo-chip';
  evoChip.textContent = '🧬 演化图谱 2019';
  scrollContainer.appendChild(evoChip);
}

/* 根据树节点索引获取要展示的条目 */
function getItemsByIndex(catIdx) {
  if (catIdx === -1) {
    // 全部：合并所有叶子
    let result = [];
    allData.tree.forEach(g => g.children.forEach(c => result.push(...c.items)));
    return result;
  }
  // 父组节点（如 group-0 = 月季）
  if (/^group-(\d+)$/.test(catIdx)) {
    const gIdx = parseInt(catIdx.match(/^group-(\d+)$/)[1]);
    const group = allData.tree[gIdx];
    if (group) { let result = []; group.children.forEach(c => result.push(...c.items)); return result; }
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

/* 渲染右侧卡片网格 */
function renderGrid(catIdx, keyword = '') {
  const grid = document.getElementById('grid');
  let items = getItemsByIndex(catIdx);
  if (keyword) {
    const kw = keyword.toLowerCase();
    items = items.filter(it => it.title.toLowerCase().includes(kw));
  }

  // 更新标题
  let titleText = '全部植物';
  if (/^group-(\d+)-(\d+)$/.test(catIdx)) {
    const m = catIdx.match(/^group-(\d+)-(\d+)$/);
    titleText = allData.tree[parseInt(m[1])].children[parseInt(m[2])].name;
  } else if (/^group-(\d+)$/.test(catIdx)) {
    titleText = allData.tree[parseInt(catIdx.match(/^group-(\d+)$/)[1])].name;
  }
  document.getElementById('section-title').textContent = titleText;
  document.getElementById('total-count').textContent = `${items.length} 个`;

  if (!items.length) {
    grid.innerHTML = '<div class="empty">暂无匹配结果</div>';
    return;
  }
  grid.innerHTML = items.map(it => {
    const safeTitle = escapeHtml(it.title);
    const safeUrl = escapeHtml(it.file_url || '');
    const thumbHtml = it.thumb
      ? `<img class="thumb" src="${escapeHtml(it.thumb)}" alt="${safeTitle} 缩略图" loading="lazy"
             onerror="this.style.display='none';this.nextElementSibling.style.display='flex';">
         <div class="no-thumb" style="display:none;">无图</div>`
      : `<div class="no-thumb">无图</div>`;
    return `<div class="card" data-url="${safeUrl}" data-title="${safeTitle}" role="button" tabindex="0" aria-label="查看详情：${safeTitle}">
      ${thumbHtml}
      <div class="card-title">${safeTitle}</div>
    </div>`;
  }).join('');
}

/* 设置激活分类 */
function setActiveCategory(idx) {
  activeCatIdx = idx;
  // 桌面端 sidebar
  document.querySelectorAll('#cat-list .cat-item').forEach(el => {
    el.classList.toggle('active', el.dataset.idx === idx);
  });
  // 移动端 chips
  document.querySelectorAll('#mobile-cat-scroll .cat-chip').forEach(el => {
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
  el.addEventListener('input', e => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      renderGrid(activeCatIdx, e.target.value.trim());
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
  document.getElementById('cat-list').addEventListener('click', e => {
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
      renderGrid(item.dataset.idx, document.getElementById('search-input').value.trim());
      document.getElementById('content').scrollTo(0, 0);
      return;
    }

    const idx = item.dataset.idx;
    setActiveCategory(idx);
    renderGrid(idx, document.getElementById('search-input').value.trim());
    document.getElementById('content').scrollTo(0, 0);
  });

  // 移动端分类点击
  document.getElementById('mobile-cat-scroll').addEventListener('click', e => {
    const chip = e.target.closest('.cat-chip');
    if (!chip) return;
    const idx = chip.dataset.idx;
    setActiveCategory(idx);
    const keyword = document.getElementById('mobile-search-input').value.trim();
    renderGrid(idx, keyword);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  });

  // 搜索（桌面端 + 移动端共用防抖逻辑）
  debounceSearch('search-input', 'content');
  debounceSearch('mobile-search-input', 'window');

  // 卡片点击 → 弹窗
  document.getElementById('grid').addEventListener('click', e => {
    const card = e.target.closest('.card');
    if (!card) return;
    openModal(card.dataset.url, card.dataset.title);
  });

  // 关闭弹窗
  document.getElementById('modal-close').addEventListener('click', closeModal);
  document.getElementById('modal-overlay').addEventListener('click', e => {
    if (e.target === document.getElementById('modal-overlay')) closeModal();
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') {
      closeModal();
      document.getElementById('lightbox').classList.remove('active');
    }
  });
}

/* 打开弹窗 — 内联渲染详情（按需加载内容） */
async function openModal(url, title) {
  const id = url.replace('./detail.html?id=', '');
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
      let html = `<h2 class="detail-title">${escapeHtml(content.title || title)}</h2><div class="detail-body">`;
      content.segments.forEach(seg => {
        if (seg.t) {
          html += `<p>${escapeHtml(seg.t)}</p>`;
        } else if (seg.i) {
          html += `<img src="${escapeHtml(seg.i)}" alt="图片" loading="lazy">`;
        }
      });
      html += '</div>';
      detailEl.innerHTML = html;
      // 绑定图片点击灯箱事件
      detailEl.querySelectorAll('.detail-body img').forEach(img => {
        img.addEventListener('click', () => showLightbox(img.src));
        img.style.cursor = 'pointer';
      });
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
  setTimeout(() => { detailEl.innerHTML = ''; }, 300);
  document.body.style.overflow = '';
}

/* 图片灯箱 */
function showLightbox(src) {
  const lb = document.getElementById('lightbox');
  lb.innerHTML = `<img src="${src}" alt="预览">`;
  lb.classList.add('active');
}
document.addEventListener('DOMContentLoaded', () => {
  const lb = document.getElementById('lightbox');
  lb.addEventListener('click', () => lb.classList.remove('active'));
});

/* HTML 转义 */
function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

/* 启动 */
loadData();

/* 关闭 IIFE，避免全局变量污染 */
})();
