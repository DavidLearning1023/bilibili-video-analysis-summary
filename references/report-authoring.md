# 可视化报告 · 写法速查

> **这份文件是给「写正文片段的人」看的，不是给设计者看的。**
> 样式表在 `assets/template/report.css`（**不要改，也不要另写 CSS**）。
> 你只写 `<div class="page">` 里面的内容，用下面这些现成 class。

---

## 一、工作流

```
1. 把正文片段写到临时文件 body.html（从 <header class="masthead"> 开始）
2. 跑：
   python scripts/render_report.py --body body.html \
       --title "20261016-《视频标题》分析总结" \
       --out "D:/目标目录/20261016-《视频标题》分析总结.html" --pdf
3. 脚本自动套上 shell.html + report.css，输出单文件 HTML 与 PDF
```

**片段里不要出现**：`<!DOCTYPE>`、`<html>`、`<head>`、`<style>`、`<body>`、`<div class="page">`。脚本会自动剥掉（并告警）。

---

## 二、整体骨架（固定顺序，任何视频类型都不许乱）

```html
<header class="masthead"> … </header>          <!-- 报头 -->
<section class="bleed"> <dl class="verdict"> … </dl> </section>  <!-- 定性卡 -->
<h2><span class="no">01</span><span>视频概览与定性<span class="sub">…</span></span></h2>
<h2><span class="no">02</span><span>视频核心总结<span class="sub">…</span></span></h2>
<h2><span class="no">03</span><span>评论区概览与总结<span class="sub">…</span></span></h2>
<h2><span class="no">04</span><span>评论区核心总结<span class="sub">…</span></span></h2>
<h2><span class="no">05</span><span>关键参数与对照时间点<span class="sub">…</span></span></h2>
<h2><span class="no">06</span><span>内容纠偏<span class="sub">…</span></span></h2>
<h2><span class="no">07</span><span>延伸发散<span class="sub">…</span></span></h2>
<footer> … </footer>
```

**各部分装什么，看 `references/video-types.md`**（按视频类型调整细节，骨架不变）。

---

## 三、class 速查

### 报头
```html
<header class="masthead">
  <p class="kicker">视频数据分析报告</p>              <!-- 栏目标签，可换 -->
  <h1>标题</h1>
  <p class="standfirst">导语：结论前置，2–4 行，<b>关键判断加粗</b></p>
  <p class="meta"><span>BV号</span><span>UP 主 <b>xxx</b></span> … </p>
</header>
```
`.standfirst` 是**唯一**允许出现"大号导语"的地方，别在正文里重复。

### 定性卡（一屏判断）
```html
<section class="bleed">
  <dl class="verdict">
    <div class="vcell"><dt>字段名</dt><dd>内容</dd></div>
    <div class="vcell vcell-full"><dt>字段名</dt><dd>跨两列的内容</dd></div>
  </dl>
</section>
```
- 标准字段池：视频类型 / 主题 / 最核心主张 / 说得对不对 / 价值在哪 / 视频本身价值 / 评论区价值
- **按类型增减**（见 `video-types.md`）
- **⚠️ 一页最多 6 格（3 行 × 2 列），`vcell-full` 额外占一整行。** 字段池有 7 项不等于都要用——**先数格子再写**，超过 6 个就合并（例：「能解决什么问题」+「成熟度」合成一格，「价值在哪」并进「一句话判断」）。7 格必跨页，实测改 4 版也压不回去，属于纯浪费
- 星级评分用方块指示器，**不要用 emoji**：
```html
<span class="rate"><i class="on"></i><i class="on"></i><i></i><i></i><i></i><em>2 / 5 · 说明</em></span>
```

### 章节标题
```html
<h2><span class="no">01</span><span>章标题<span class="sub">一句话副标题</span></span></h2>
<h3>1.1 节标题</h3>
<h4>小节标题</h4>
```
- `h2` 自动带顶部粗线，`.no` 自动换行显示序号
- **每个标题都要有意义**（"说明""特点"是废标题）

### 来源标记（三源区分，**必用**）
```html
<span class="src src-v">视频说</span>
<span class="src src-c">评论说</span>
<span class="src src-m">我推断</span>
```
- `src-v` 墨底白字（最重）· `src-c` 灰底白字 · `src-m` 描边
- 单色梯度，**全文只用这一个体系**，不要引入第二种颜色区分来源

### 判定标签
```html
<span class="verd verd-no">被推翻</span>        <!-- 赭红实心：最重 -->
<span class="verd verd-tight">要收紧</span>      <!-- 赭红描边 -->
<span class="verd verd-ok">基本可信</span>       <!-- 灰色描边 -->
```

### 时间戳（**必须是链接**）
```html
<a class="ts" href="https://www.bilibili.com/video/BVxxxx/?t=581" target="_blank" rel="noopener">09:41</a>
```
秒数取整。`?t=` 后面是**秒数**不是 `09:41`。

### 表格
```html
<div class="tw">
<table>
  <thead><tr><th style="width:26%">列名</th><th>列名</th></tr></thead>
  <tbody>
    <tr><td>…</td><td class="n">1,234</td></tr>   <!-- class="n" 用等宽数字 -->
  </tbody>
</table>
</div>
```
**表头一律用 `<th style="width:…">` 定宽**，否则列宽会随内容乱跳。

### 图表

**横向条形对比（比值类，最常用）**
```html
<figure>
  <div class="bars">
    <div class="bar"><span class="k">标签</span>
      <span class="track"><span class="fill fill-a" style="width:43.6%"></span></span>
      <span class="v">43.6%</span></div>
  </div>
  <figcaption>怎么读 + 口径</figcaption>
</figure>
```
`fill-a` 赭红（要强调的）· `fill-b` 中灰 · `fill-c` 浅灰

**小倍数图（多组 × 多指标，分布类）**
```html
<figure class="bleed tall">      <!-- tall = 允许跨页，高图必加 -->
  <div class="small-multiples">
    <div class="sm">
      <div class="sm-name">A 类别名</div>
      <div class="sm-row"><span>指标1</span><span class="t"><span class="fill fill-b" style="display:block;width:29.1%"></span></span><span class="v">29.1%</span></div>
      <div class="sm-row"><span>指标2</span>…</div>
      <div class="sm-type hot">一句话结论</div>   <!-- 加 hot 变赭红加粗 -->
    </div>
    …
  </div>
  <figcaption>怎么读 + 口径</figcaption>
</figure>
```
- **少于 4 个类别时不要用小倍数图**，直接用条形对比
- 图注必须写**「怎么读」+「口径」**两件事

**图注规则（硬性）**：`figcaption` 与图表同宽（样式表已保证），不要自己加 `style`。

### 引言块（长引文）
```html
<div class="pull">
  引文原文，<b>可以加粗关键句</b>。
  <cite>@用户名 · 数字 赞 · 补充说明</cite>
</div>
```
- 用于**值得单独站出来的长评论或关键原话**
- **不要连续用两个**，中间必须有正文

### 折叠（长清单）
```html
<details>
  <summary><span class="tag">标签</span>摘要行</summary>
  <div class="body"><p>内容</p></div>
</details>
```
- **≥4 条并列**才折叠；默认**全部不展开**
- 打印时自动展开（样式表已处理），所以 PDF 不会丢内容

### 提示条（局限声明 / 重要提醒）
```html
<div class="caveat"><b>标题句。</b>正文，<b>关键处用赭红</b>。</div>
```

### 指标定义段
```html
<p class="metrics"><b>指标名</b>＝算法（在问什么）<br><b>指标名</b>＝算法（在问什么）</p>
```

### 页脚
```html
<footer>
  <p><b>一句话最终建议。</b></p>
  <p>生成日期 · 数据源 · 引文性质说明<br><b>本报告不含任何读者个人信息，可自由分享。</b></p>
</footer>
```

---

## 四、排版红线（违反了要重做）

| 红线 | 说明 |
|---|---|
| **禁 emoji** | 包括引用里的 👍 ✅ ❌，一律换成文字（「7,199 赞」「被推翻」） |
| **禁渐变 / 禁投影 / 禁毛玻璃 / 大圆角** | 全站已无，别在片段里加 inline style 破例 |
| **只用一种强调色** | 赭红 `#A63D2F`。需要区分层级时用**灰阶**或**字重**，不要引入第二种色相 |
| **禁纯黑 #000** | 正文墨黑 `#1F2227`，样式表已定 |
| **禁深色底反白排正文** | 只有 `.src-v` / `.verd-no` 这类小标签允许 |
| **不要写 `<style>` / inline `style` 调版式** | 唯一例外：`fill` 的 `width`、图片宽度 |
| **不要用 `#` 三级以上标题** | 最深到 `h4`（小节），再深用加粗段落 |
| **报告里不出现读者个人信息** | 报告要能对外分享；个性化分析另做，不写进来 |

---

## 五、自检清单（交付前逐条过）

- [ ] 四大核心部分齐全、顺序正确，两个后置在它们之后，延伸发散在最后
- [ ] 定性卡按视频类型填了对应字段（对照 `video-types.md`）
- [ ] 每个判断都标了三源之一（视频说 / 评论说 / 我推断），没有裸判断
- [ ] 所有时间戳都是可点链接，秒数正确
- [ ] 图表都有 `figcaption`，且写了"怎么读 + 口径"
- [ ] 高图（小倍数图）加了 `tall` 类
- [ ] 折叠项默认不展开，且内容在打印时可见
- [ ] 全文零 emoji
- [ ] 全文无读者个人信息
- [ ] 渲染后**翻一遍 PDF**：有没有大面积空白页、有没有内容被切断
