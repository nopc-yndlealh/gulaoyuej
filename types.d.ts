/**
 * feijibei.top — 类型定义
 * 所有数据结构的 TypeScript interface 定义
 */

// ── index.json 条目 ──
export interface IndexEntry {
  id: string;
  title: string;
  cat: string;
  type: string;
  images: string[];
  tag: string;
  author: string;
}

// ── content segment（图文段） ──
export interface ContentSegment {
  i?: string; // 图片 URL
  t?: string; // 文本内容
}

// ── content/{cat}.json 中的单条内容 ──
export interface ContentEntry {
  title: string;
  type: string; // '月季' | '微博' | '小红书' | '知识图谱' 等
  segments: ContentSegment[];
  author: AuthorInfo | string;
  time: string;
  tag: string;
  weibo_url?: string;
}

// ── 作者信息 ──
export interface AuthorInfo {
  name: string;
  [key: string]: unknown;
}

// ── search-index.json 条目 ──
export interface SearchEntry {
  title: string;
  cat: string;
  text: string;
  thumb: string;
}

// ── 树状分类节点 ──
export interface TreeNode {
  name: string;
  children: TreeChild[];
  itemCount: number;
}

export interface TreeChild {
  name: string;
  items: GridItem[];
  isLeaf: boolean;
}

// ── 卡片网格条目（transformData 产物） ──
export interface GridItem {
  id: string;
  title: string;
  thumb: string;
  file_url: string;
  tag: string;
  author: string;
  cat?: string; // 搜索模式下有值
}

// ── 转换后的完整数据结构 ──
export interface TransformedData {
  tree: TreeNode[];
  flatGroups: Record<string, GridItem[]>;
  totalItems: number;
  raw: IndexEntry[];
}

// ── 分类配置 ──
export interface TreeConfigGroup {
  name: string;
  children: string[];
}

// ── 搜索结果 ──
export interface SearchResult {
  items: GridItem[];
  isSearch: boolean;
}

// ── 分类器快捷键映射 ──
export type ClassifyKeys = Record<string, string>;
