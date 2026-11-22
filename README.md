# K4 → KN 转换器

把编程猫 **Kitten4** 作品（`.bcm4`）转成 **KittenN** 作品（`.bcmkn`）。

官方转换器（kn.codemao.cn 首页的「导入」）在**函数**和**变量/列表**这两块是坏的：
转出来的文件 `proceduresDict` 和 `variablesDict` 都是空的，可积木里还留着几千处 uuid 引用。
导入之后自定义积木整段消失、变量全部报错。这个仓库是一份自己写的转换器，目标是**不丢东西**。

纯 Python 标准库，无第三方依赖，Python 3.8+。

---

## 快速开始

```bash
# 转换
python tools/k4_to_kn.py 作品.bcm4 -o 作品.bcmkn --report 报告.md

# 校验（结构 + 对账 K4 源 + 变量/广播引用完整性）
python tools/validate_kn.py 作品.bcmkn --k4 作品.bcm4 [--official 官方产物.bcmkn]

# 和官方产物逐块对账（类型/字段/输入名）
python tools/diff_official.py 我的.bcmkn 官方.bcmkn [--k4 作品.bcm4]
```

`--report` 输出的 Markdown 会列出统计表和「需要人工看一眼的地方」（降级积木、空壳函数、
K4 源里的悬空引用等）。

---

## 对比

同一个源文件（`florr-重置版-ownlastest.bcm4`，17.98 MB）两个转换器的结果：

| | 官方 | 本实现 |
| --- | --- | --- |
| 函数 `proceduresDict` | **0** | **135** |
| 变量/列表 `variablesDict` | **0** | **289**（8837 处引用全部命中） |
| 非影子积木 | 丢 **10437** 个 | 100% 搬迁 |
| 函数体里的积木 | 0 | 12512 |
| 广播 | **1** 条（脚本里用到 11 个） | 11 条，被引用 58 处全部命中 |

除了「整块清空」，官方转换器还有 4 个具体缺陷（详见 `docs/format-notes.md` 第一部分）：
不遍历函数体所以体内调用绑不上、跨角色同名函数绑错主人、有返回值的函数被拆成同名两条且永远命中错的那条、
以及失败时静默 `return` 不报错。本实现按 `def_id` 绑定，绕开了这些问题。

---

## 转换了什么

| K4 | KN |
| --- | --- |
| `theatre.scenes` / `theatre.actors` | `scenes.scenesDict` / `actors.actorsDict`（含图层顺序、工作区偏移、当前造型） |
| `block_data_json.blocks` + `connections`（扁平图） | 每个实体的 `nekoBlockJsonList`（嵌套树） |
| `variables`（`any`/`list` + `is_global`） | `variablesDict`（`any`/`list`/`private`/`private_list`/`public`，uuid 引用） |
| 函数头 `procedures_2_defnoreturn` + 函数体 | `proceduresDict` **单条**记录，有返回值就是 `type:"ROUND"` |
| `broadcasts`（常被写坏） | `broadcastsDict`，按「脚本里真用到」裁剪 |
| `theatre.styles`（内嵌 `data:` SVG） | `styles.stylesDict`（优先用 CDN 地址） |
| 舞台 1280×720 | 900×562（竖屏则 562×900） |
| `theatre.timer` | `timerPosition`（K4 没存位置，给 KN 默认右上角） |

---

## 几个必须踩准的格式约定

这些都是从 KN 编辑器自己的编译链路（`BlockGenerater` / `AstToBlock`）和实测里反推出来的，
写错一条就会静默失效。完整说明在 `docs/format-notes.md` 第二部分。

| 约定 | 踩错的后果 |
| --- | --- |
| 一个函数只有一条 `proceduresDict` 记录；有返回值是 `type:"ROUND"` | 拆成两条同名 NORMAL/ROUND，调用永远绑到没返回值那条 |
| 返回值是**函数体里**的 `procedures_2_return_value` 块（`mutation items="1" type="ROUND"`） | 挂到函数头上的 VALUE 上，等于没返回值 |
| 调用实参放 `inputs[<参数 id>]`，不是 `ARG0/ARG1` | 编译器不读 ARG，参数全丢 |
| 函数体里的 `procedures_2_parameter` 必须带 `<mutation id="<参数id>" type="..">` + `is_output` | 参数积木渲染成**灰色**「未定义参数」，刷新也没用 |
| 列表槽（`list`/`target_list`）要在 `inputs` 里放真正的 `pure_list_get` 块，同时写 `shadows` 同 id 的 XML | 只写 XML 时编辑器能画出来，但运行时是 `undefined`：「仅支持列表类型，已处理为NaN」/「已忽略执行，请改为列表类型」/「恢复列表」 |
| 降级文字块（`bcm_translator_text_*`）没有语句槽，后续代码必须挂到它的 `next` | 一条链上后面几十块整段消失（官方在 `set_pen_path` 上丢了 109 块） |
| K4 允许同一个值块被多个输入共享；KN 是树 | 直接复用会 id 重复，需要整棵子树复制换 id |

---

## 校验器查什么

`tools/validate_kn.py`：

- 重复 block id、缺 `type`、引擎不认识（`KNOWN_TYPES` = `blocksOptions` + 所有 `embedBlock.blockName` + 影子/函数/降级块）
- 每个 `inputs`/`statements`/`fields` 的名字是否在该积木的 schema 里（含动态展开的 `IF0..`/`DO0..`/`ADD0..`）
- `proceduresDict` 键与 `id` 一致、函数头 `NAME` 与条目 id 一致、`mutation` 里的 `<arg>` 与 `params` 一致
- 调用是否指向存在的函数
- 变量/列表引用、广播名是否都能在定义表里查到
- `mutation` / `shadows` 里的 XML 是否可解析
- `--k4`：K4 里每个非影子积木是否都在产物里（`return` 块折算进 ROUND 的单独计数）
- `--official`：和官方产物比节点数、类型直方图

---

## 已知限制

1. **KN 里没有对应积木的类型会被降级成文字块**，只保留说明文字、不执行

2. **K4 源里悬空的变量/列表引用**：脚本引用了某个 uuid，但 `variables` 表里已经没有它
   （原作删了变量没清引用）。转换时会补一条占位定义，命名为 `缺失列表1`、`缺失变量1`……
   值为空、默认隐藏。这样积木不会变成空槽、运行时也不会是 `NaN`，但**数据是空的**，
   需要人工补。数量会在报告的「K4 源里引用已删除的变量（已补占位）」里逐条列出。

3. **K4 源里函数头丢失的调用**：调用块带 `def_id` 和参数名，但文件里没有对应的函数头。
   转换器按 `def_id` + 参数名建一个**空壳函数**（同名同参数，函数体为空），让调用至少能绑上。

4. **云变量**只搬了定义，没有做过联动验证。

5. **真机回归只做了小样本**：`球体物理` 已在 KN 编辑器里导入并逐轮修过实测问题
   （函数参数积木、变量引用、列表读写），`florr` 那种量级的作品还没完整跑过一遍。

6.**编辑器语义差别**：部分k4代码照搬到kn会因为解析方式不同导致意外的问题（就好比你不能把js代码用c++编译），**这部分需要手动修复**。例：k4中克隆体不会受到广播，而kn会；k4中函数不一定需要返回值，而kn中要求所有条件分支都有return。

---

## 目录

```
tools/
  k4_to_kn.py            CLI 入口（把 tools 塞进 sys.path 后转调 k4kn.convert.main）
  k4kn/
    __init__.py          包说明
    maps.py              类型/字段/取值/输入名映射 + 降级文案
    schema.py            KN 积木 schema（blocksOptions）与参数名校验
    blocks.py            K4 扁平图 → KN 嵌套树（单块转换、影子、mutation、内嵌影子块）
    procedures.py        函数收集、参数绑定、调用重绑（含 ROUND/NORMAL）
    convert.py           顶层编排（样式/变量/广播/场景/角色/舞台）+ 报告
    data/
      maps.json          从生产包抽出的映射表（类型 367 / 文案 197 / 取值 21 / 输入名 69）
      kncfg.json         KN 的 blocksOptions（207 种积木）+ 帽子/返回值/默认影子表
  validate_kn.py         结构校验 + K4 对账 + 官方对账
  diff_official.py       与官方产物逐块对账
  make_miao_link.js      浏览器端上传脚本（生成「喵链接」）
  k4_proc_sim.py         官方转换器函数处理的 Python 移植版（用来复现它的 4 个缺陷）
  diag_visited.py        诊断：哪些 K4 块没被访问 / 访问后丢失

docs/
  format-notes.md        全部结论：官方缺陷、KN 目标格式的硬事实、喵链接、参数/列表绑定
```

> 仓库**不包含**官方转换器的源码、`.bcm4`/`.bcmkn` 作品样本、转换产物。
> 官方源码按 `docs/format-notes.md` §2 自己抓；`tools/k4kn/data/` 里是从生产包抽出来的映射表，
> 转换和校验只依赖这两份 JSON，不需要官方源码在场。

---

## 导入到 KN

KN 的「导入喵链接」就是浏览器里 `GET(url)` → 校验 → 载入，所以需要把 `.bcmkn` 传到一个
**跨域可访问**的地址。codemao 自己的 CDN 地址长这样：

```
https://creation.bcmcdn.com/922/user-files/<内容哈希>.bcmkn
```

那个 32 位名字不是随机 id，是**文件内容的哈希**：先算 SHA-1；文件超过 4 MB 时按 4 MB 分块、
每块各算 SHA-1、把摘要拼起来再算一次。然后在前缀（单块 `0x16` / 多块 `0x96`）后面接上摘要，
按十六进制转成 21 个字节，再 base64url。所以文件改一个字节，链接就会变。

拿链接的方法、以及可直接在已登录页面控制台粘贴的上传脚本见：
- `docs/format-notes.md` 第三部分
- `tools/make_miao_link.js`

---

## 仓库里没有什么

| 内容 | 为什么 |
| --- | --- |
| 官方转换器源码 | 混淆过、体积几 MB，且不是本仓库的代码；需要时按 `docs/format-notes.md` §2 自行抓取 |
| `.bcm4` / `.bcmkn` 作品样本 | 体积大、是他人作品，不适合作为开源样例分发 |
| `out/`（转换产物与报告） | 每次转换都会重新生成 |

转换与校验只需要 `tools/k4kn/data/` 下的两份映射表，不需要官方源码在场。
