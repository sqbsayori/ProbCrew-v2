# `frontend/components/` —— 复用 UI（域四直接用，不要改）

## 这一版有什么

`index.js` 是**唯一入口**（ESM，免构建，直接 `import`）：

| 工厂 | 作用 | 关键参数 |
|---|---|---|
| `card()` | 卡片容器 | `{ title, actions, body, footer }` |
| `table()` | 表格；**`rows` 为空时自动渲染空态** | `{ columns, rows, empty, caption }`，`columns[].render(row)` 可自定义单元格 |
| `emptyState()` | 空态 | `{ title, hint }` |
| `skeleton()` | 加载骨架 | `{ lines }` |
| `notice()` | 提示条（也用于 **F17/F18 的显式降级话术**） | `{ message, tone }`，`tone ∈ info/success/warn/danger` |
| `chart()` | ★ **占位**：渲染实现属后续工作包 | `{ title }` |

`COMPONENTS` 导出这份清单本身（便于检索"这一版有什么"）。

## 怎么用（域四搭"用法与第一页"时）

```js
import { card, table, notice } from '../components/index.js'

main.append(
  notice({ message: '检索增强暂不可用，已降级为关键词匹配', tone: 'warn' }),
  card({
    title: '题目列表',
    body: table({
      columns: [
        { key: 'stem', label: '题干' },
        { key: 'kc', label: '知识点' },
        {
          key: 'difficulty',
          label: '难度',
          render: (row) => `${row.difficulty}/5`,
        },
      ],
      // ★ 假数据就用 contracts/*.schema.json 的 examples（docs/04 §4）
      rows: [{ stem: '贝叶斯公式的适用条件？', kc: 'kc_bayes', difficulty: 3 }],
      empty: '还没有题目',
    }),
  }),
)
```

## 两条不要踩的线

1. **不要改这里的实现** —— `frontend/components/**` 的写作用域是**架构与集成**（`docs/05 §3`）。
   需要新组件或改接口 ⇒ 找本域（走 `docs/05 §3` 规则 1 的记账）。
2. **不要在这里或你的页面里硬编码色值** —— 颜色/圆角/阴影只能来自 `styles/tokens.css`（**R-D**）；
   页面自有样式写进你自己的 `features/<x>/styles.css`，共用骨架已在 `styles/pages.css` 里。

## 组装纪律

- 只用 `core/dom.js` 的 `el` / `text` 组装，**不要 `innerHTML`**（**R-E**：XSS 的唯一防线）。
- `components/` 不得 `import features/`；`features/` 之间也不得互相 import（只能经 `core/bus.js`）。
