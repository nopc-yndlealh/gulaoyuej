# 数据迁移报告

- 迁移来源：`data/index.json` + `data/content/content-index.json` + `data/content/*.json`
- 产出：`content/posts/{id}.json` × 4043
- 分类权威：`index.json` 的 `cat`（贴吧 637 条已按主题重分类，比 content-index 的旧口径新）

## 修复的问题

1. **261 条孤儿内容**：有正文、没进 `index.json`，读者完全看不到 → 已迁入并标记 `status: draft`
2. **1 条空壳列表项**：`index.json` 有、正文缺失，点开是空白 → 见下方清单
3. **3 条悬空映射**：`content-index` 指向不存在的正文 → 已丢弃该映射
4. **1007 条分类口径分歧**（占 25.8%）：已统一到 `cat`，来源信息保留在 `source` 字段

## 悬空映射（有列表项、无正文，需要人工补或删）

- `sedum_evo_2019` — 墨西哥景天科演化树 · 知识图谱（cat=多肉）

## 仅在遗留 content.json 里的条目

- `succ_0848`
- `succ_0928`
- `tieba_4465214318`
- `tieba_4609025392`
- `tieba_4621621631`
- `tieba_4688412953`
- `tieba_4850064270`
- `tieba_4873645832`
- `tieba_4900935268`
- `tieba_4921751828`
- `tieba_5021480388`
- `tieba_5059140801`
- `tieba_5062221744`
- `tieba_5072118365`
- `tieba_5082442470`
- `tieba_5082687998`
- `tieba_5085642781`
- `tieba_5132699567`
- `tieba_5168372846`
- `tieba_5282330443`
- `tieba_5474491329`
- `tieba_5480112385`
- `tieba_5638720047`
- `tieba_5698964974`
- `tieba_5820756929`
- `tieba_5861730921`
- `tieba_5935783125`
- `tieba_5956735749`
- `tieba_5956935488`
- `tieba_5961549552`
- `tieba_5982792264`
- `tieba_6062195218`
- `tieba_6200884434`
- `tieba_6381293407`
- `tieba_6423256395`
- `tieba_6512128738`
- `tieba_6515057055`
- `tieba_6842099887`
- `tieba_6944471843`
- `tieba_7036703242`
- `tieba_7049264690`
- `tieba_7076293776`
- `tieba_7082558212`
- `tieba_7127044512`
- `tieba_7178290451`
- `tieba_7244295266`
- `tieba_7313723530`
- `tieba_7330143630`
- `tieba_7337207006`
- `tieba_7546165307`

## 分类口径分歧明细（前 60 条）

- `index.cat=云小123456` ←→ `content-index 分块=贴吧`：486 条
- `index.cat=(不在 index.json 里)` ←→ `content-index 分块=微博`：262 条
- `index.cat=多肉/综合日常` ←→ `content-index 分块=多肉-小红书`：108 条
- `index.cat=养花日常` ←→ `content-index 分块=贴吧`：89 条
- `index.cat=其他育种家` ←→ `content-index 分块=贴吧`：25 条
- `index.cat=古老月季` ←→ `content-index 分块=贴吧`：22 条
- `index.cat=种植分享` ←→ `content-index 分块=贴吧`：13 条
- `index.cat=古代园艺` ←→ `content-index 分块=贴吧`：2 条

## 下一步

- `status: draft` 的 261 条需你过一遍：确认是垃圾就删，是内容就改 `status` 为 `published`
- `cat="微博"` 的条目是待重分类队列（你已有 weibo-classifier 流程）
- 确认无误后，把 `data/` 加进 `.gitignore`，由构建脚本在 CF Pages 构建时现场生成