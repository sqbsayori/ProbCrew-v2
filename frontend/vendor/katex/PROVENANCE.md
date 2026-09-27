# katex 的本地化原件（`frontend/vendor/katex/`）

★ **这是第三方产物，不是我们写的代码。** 本目录由 `docs/03 §5.1` 判给**架构与集成**，
`docs/06 §11` 的 W5 行把它列为 **N4（零依赖 / 免安装）的实物** —— 公式渲染不能依赖外部 CDN，
因为 `docs/01` N4 与 `docs/04 §7` 校验⑦（N18 验收②）都要求"前端无外部依赖、无 CDN"。

| 项 | 值 |
|---|---|
| 上游 | `https://registry.npmjs.org/katex/-/katex-0.16.22.tgz`（npm 包 `katex`） |
| 版本 | **0.16.22** |
| 拉取日期 | 2026-09-27 |
| 包校验和 | `sha256(katex-0.16.22.tgz) = e9e0d167db3175481cbadaff38e8d90b130f6a3ddb451a47e43c577fd511f365` |
| 许可 | **MIT**（Khan Academy and other contributors）—— 原件见同目录 `LICENSE`；按 `README` 的"引用的开放许可素材按其许可署名"，**许可随原件一并入仓** |
| 体积 | 约 **1.8 MB**（`katex.mjs` 610 KB · `katex.min.css` 23 KB · `fonts/` **60 件** ≈ 1.2 MB） |

## 为什么取这几个文件（而不是整个 `dist/`）

- **`katex.mjs`**：ESM 构建 —— 本仓前端是**免构建 ESM**（ADR-0001），`import` 它即可；
  **不用** `katex.min.js`（UMD / 全局变量那一路），也不引入任何打包器。
- **`katex.min.css` + `fonts/` 全部 60 件**：CSS 里 60 个 `url()` 引用了三种格式的字体回退，
  **只拷一部分会让公式回退到错误字形**。★ 门禁会逐个 `url()` 断言目标文件在本地存在。
- **未取**：`contrib/`（auto-render / mhchem）· `src/` · `types/` · `cli.js` —— 用到时再加，
  加之前先按下面的"更新方式"走一遍。

## 更新方式（一次一条命令）

```bash
curl -sL -o /tmp/katex.tgz https://registry.npmjs.org/katex/-/katex-<版本>.tgz
shasum -a 256 /tmp/katex.tgz                 # 记进上表
tar -xzf /tmp/katex.tgz -C /tmp
cp /tmp/package/dist/katex.mjs /tmp/package/dist/katex.min.css frontend/vendor/katex/
rm -rf frontend/vendor/katex/fonts && cp -R /tmp/package/dist/fonts frontend/vendor/katex/fonts
cp /tmp/package/LICENSE frontend/vendor/katex/LICENSE
bash scripts/verify.sh                       # vendor 完整性一节必须仍然绿
```

★ 本目录**整个**由 `.gitattributes` 的 `frontend/vendor/**  binary` 声明为**二进制**
（不做行尾转换、不做文本合并 —— 第三方产物被"文本合并"即损坏）。

## 谁用它、怎么用（**尚未被任何页面引用**）

挂载属 `docs/03 §7④` 的页面落地步骤（`#/practice` 与 `#/knowledge` 的公式渲染，属 W2 / W4）。
用法（免构建 ESM）：

```js
import katex from '../vendor/katex/katex.mjs'
// ★ 用"写进给定元素"的那条 API（DOM 节点），**不要** renderToString + innerHTML
katex.render('P(A|B)=\\frac{P(AB)}{P(B)}', formulaElement, { throwOnError: false })
```

★ **为什么强调这一句**：`renderToString` 的返回值是 HTML 字符串，落地时很自然会被写成
`element.innerHTML = ...` —— 而那是 **R-E**（门禁红灯的写法）。`katex.render(expr, element)`
是"给定元素 + DOM API"，与 R-E 不冲突。⇒ **页面落地时按这条写**；若确有必要用
`renderToString`，那要先把 R-E 的豁免写成明账（走 ADR），不要顺手放行。
