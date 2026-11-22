# 调研笔记：官方转换器做了什么，KN 的格式到底要什么

调研时间 2026-11-22，对象 https://kn.codemao.cn 的生产包。

官方转换器源码是混淆过的前端 JS，体积很大，**不随本仓库分发**。本文只保留结论和复现方法，
需要原始代码时按 §2 自己抓一份。

---

# 第一部分：官方转换器

## 1. 它是纯前端 JS

首页「导入」和编辑器「导入」走同一套代码，**没有服务端转换，也没有 OCR**：

```
读文件文本 → JSON.parse(.bcm4)
  → kittenBcmToNekoBcmUtils(k4json)              // 本地：结构 + 积木类型映射
  → HandlerApi.kittenBcmToNekoBcm(template, w)   // 本地：套模板、上传素材、改坐标
  → BcmHelpers.validateBcm(w) → setBcm → createWork
```

`HandlerApi.kittenBcmToNekoBcm(mode, bcm)` 的第一参只是「空白模板对象」（`isKids() ? kids : normal`），
不是远程接口。OCR 接口（`/public-api/ocr/task|status`）只用于「图片识别文字」，与积木转换无关。

## 2. 怎么拿到源码（复现用）

包地址：`https://creation.bcmcdn.com/neko/web/release/static/js/<name>.<hash>.js`

| 文件 | 内容 |
| --- | --- |
| `home.*.js` | 首页包，内含模块 **27637** = 转换器本体（含一份 KN 积木引擎） |
| `main.*.js` | 编辑器主包，内含模块 **40766** = 转换后处理（素材上传 / 坐标 / 模板合并） |
| `chunks/6818.*.chunk.js` | 编辑器导入流程（菜单、`.bcm4` 上传、登录校验、建作品） |

模块 27637 导出：`kittenBcmToNekoBcmUtils`、`nemoBcmToNekoBcmUtils`、`knBcmToText`、
`getTextToBcm`、`textToKnBlockJson`、`BlockParser`、`BlockGenerater`、
`compareBlockJsonMatch` / `calculateScore`（后两个用于「文本↔积木」匹配，不在导入路径上）。

抽出模块并格式化：

```bash
curl -s -o home.js https://creation.bcmcdn.com/neko/web/release/static/js/home.<hash>.js
python - <<'EOF'
import re
s = open('home.js', encoding='utf-8', errors='replace').read()
i = s.find('27637:function')
m = re.compile(r',\d{3,6}:function\(e,t,n\)\{').search(s, i + 10)
open('mod27637.js', 'w').write('x={' + s[i:m.start()] + '}')   # 包一层才合法
EOF
npx js-beautify -s 2 mod27637.js > 27637.pretty.js
```

## 3. 官方怎么处理函数（自定义积木）

| 函数 | 作用 |
| --- | --- |
| `Vk()` | 从 `block_data_json` 解析积木。**只有 `procedures_2_defnoreturn` 被当成函数定义抽走**，其余全留在脚本里 |
| `Xk()` | 定义 → `proceduresDict[id] = {id,name,type,params,nekoBlockJsonList}`；`params` 只产出 `Label` 与 `String` 两种 |
| `Wk()` | 函数体里若含 `procedures_2_return_value`，**拆成两条同名条目**（NORMAL 实体 + ROUND 副本），`Qk()` 给副本补 `VALUE` 输入 |
| `Jk()` | 脚本里的调用块按 **名字** 回填 id：`Object.values(t).find(e => e.name === r)`；找不到就 `if (!i) return e;` 静默返回 |

参数找不到时只有 `console.warn("找不到名为 X 的参数")`。

## 4. 实测出的 4 个缺陷

用 `tools/k4_proc_sim.py`（按 §3 逐行移植 `Vk/Xk/Jk/Wk`）跑真实作品：

### `scratch/out_noaudio.bcm4`（17 容器，27 个函数）

| 项 | 结果 |
| --- | --- |
| `proceduresDict` | 27 条，全部 NORMAL |
| 同名 | `position` ×5（Player/Hammer/Level/Background/Comment）、`position it` ×3 |
| 脚本内调用 | 28 个：12 已重绑，16 个名字无定义（原文件断链） |
| **跨角色错绑** | **4 例**（Hammer→Player 的 `position`、Background→Level 的 `position it` ×1、Comment→Level ×2） |
| **函数体内调用** | **33 个完全没被处理**（NAME 仍是字符串，不是 def id） |

1. **`Jk` 只遍历 `actor.nekoBlockJsonList`，不遍历 `proceduresDict` 里的函数体。**
   `Xk` 先把定义搬进字典，`Jk` 之后只处理脚本 → 函数体内所有调用永远是「名字」而不是 def id，
   KN 解析不到 → 块消失。上面 61 个调用里有 33 个中招。
2. **跨角色同名串味**：`proceduresDict` 是项目级全局，`Jk` 只按 `name` 找第一条，
   不校验 owner。同名函数的调用会绑到别的角色的定义上，参数个数不同 → 结构不合法 → 消失。
3. **同名拆条**：有返回值的定义被拆成 NORMAL + ROUND 且 `name` 相同，
   `find(name)` 永远命中先插入的 NORMAL → 返回型调用绑到无返回值定义，`callreturn → ROUND` 恒为 0。
4. **静默失败**：找不到定义时 `if (!i) return e;`，NAME 保留中文名，无 toast、无日志。

> 「类型不匹配」的猜测不成立：K4 真机数据里就是 `procedures_2_defnoreturn` + `STACK`，
> KN 运行时本身也认这个类型。

## 5. 复现

```bash
python tools/k4_proc_sim.py 作品.bcm4
```

---

# 第二部分：KN 目标格式的硬事实

来源：KN 编辑器自己的编译链路（`BlockGenerater` 积木→AST、`AstToBlock` AST→积木）。
这些是**写入 `.bcmkn` 时必须遵守**的约定，官方转换器违反了其中好几条。

## 5.1 函数（`proceduresDict`）

```js
proceduresDict[id] = { id, name, type: "NORMAL"|"HEXAGONAL"|"ROUND",
                       params: [ {id,type,name} ... ], nekoBlockJsonList: [头部块] }
```

- `params[0]` 必须是 `type:"Label"`（函数名），后面才是真参数；编译器跳过 Label，其余各生成一个形参。
- **一个函数只有一条记录**。有返回值 = `type:"ROUND"`，不是再造一条同名 NORMAL。
- 头部块是 `procedures_2_defnoreturn`，函数体挂在 `statements.STACK`；
  参数槽是 **1 基** `PARAMS1..PARAMSn`，槽里放 `procedures_2_stable_parameter`。
- 返回值是**函数体里的** `procedures_2_return_value` 块，mutation 为
  `<mutation items="1" type="ROUND">`（条件函数是 `HEXAGONAL`），VALUE 槽接表达式。
  不是挂在函数头上的 VALUE。
- 调用块：`fields.NAME = 目标条目 id`；实参放 **`inputs[<参数 id>]`**，
  不是 `ARG0/ARG1`（`ARG{i}` 是 K4 的叫法，编译器根本不读）。
  `Actor / Variable / Custom` 类参数走 `fields[<参数 id>]`。

## 5.2 函数参数积木必须自带 mutation id（否则编辑器一律画成灰色）

```json
{"type":"procedures_2_parameter",
 "fields":{"param_name":"x1"},                                        // 只用于显示/兜底匹配
 "mutation":"<mutation id=\"<参数id>\" type=\"String\"></mutation>",  // 真正绑定
 "is_output":true}
```

- `<参数id>` = 函数头 `PARAMS{n}` 槽里 `procedures_2_stable_parameter` 块的 id，
  也就是 `proceduresDict[id].params[n].id`。
- K4 的 `procedures_2_parameter` 只有 `fields.param_name`，`mutation` 是空串。直接搬过去
  编辑器认不出来（刷新也没用，加载路径里没有补绑逻辑，只有「从定义拖出来」才带 mutation）。
- 编辑器自己有一段归一化代码干这事：拿 `param_name` 去函数头里找，找到才写 `mutation id` +
  `is_output:true`，找不到 `console.warn("找不到名为 X 的参数")`。
  转换器按同样规则写好即可，见 `procedures.bind_param_blocks`。
- `is_output` 的用途：值积木都要标 `true`。
- `<arg>` 两种写法都存在：编辑器写 `id/name/type`，官方 K4 转换器写 `id/content/type`；
  两个属性都带上，谁的解析器都能读。

## 5.3 变量 / 列表

- 枚举：`any | list | public | private | private_list`。
  角色内变量用 `private` / `private_list` 且带 `currentEntityId`；`isGlobal` 与 `currentEntityId`
  一起决定作用域。
- 条目字段：`id,type,name,value,visible,position,isGlobal,createTime,scale,style,currentEntityId`。
  `position = {x + 舞台宽/2, 舞台高/2 - y}`。样式只有
  `default|icon_medal|icon_heart|icon_coin|icon_hourglass|text`。
- **积木通过 uuid 引用**：`fields.variable = <uuid>`（`variablesDict[uuid]` 直查）。
  官方转换器留下了 6000+ 处引用却交了一张空的 `variablesDict`，所以导入后变量全废。
- 编辑器内部还有一套「`变量_` / `角色变量_` / `本地变量_` / `列表_` / `云变量_` + 名字」的兼容 key，
  只在名字解析失败时兜底，正常写 uuid 即可。

## 5.4 列表槽：只看 shadows 不够，必须在 inputs 里放真正的 pure_list_get 块

`list_item / list_length / list_append / replace_list_item / delete_list_item / list_copy /
list_index_of / list_is_exist` 的 `list`（`list_copy` 还有 `target_list`）在 `blocksOptions` 里都是：

```json
{"type":"input_value","name":"list",
 "embedBlock":{"blockName":"pure_list_get","bcmPropertyType":"list","type":"input_field","name":"list","isShadow":true}}
```

即「带内嵌影子的值槽」。正确写法是**两处同时写**：

```json
"inputs":  {"list": {"type":"pure_list_get","id":"<新 id>","is_shadow":true,"is_output":true,
                      "fields":{"list":"<列表 uuid>"},"parent_id":"<父块 id>"}},
"shadows": {"list": "<shadow type=\"pure_list_get\" id=\"<同一个 id>\" …><field name=\"list\">uuid</field></shadow>"}
```

- 只写 `shadows.list` 的 XML、不放 `inputs.list`，运行时按积木取不到列表，列表参数是 `undefined`：
  读 → 「仅支持列表类型，已处理为NaN」；写 → 「已忽略执行，请改为列表类型」；
  编辑器还会给每个相关积木挂上「恢复列表」的修复入口。
- K4 的列表引用影像是**复用**的（同一实体里同一列表只有一个 shadow id，跨文件都可能相同），
  直接沿用会在 KN 里 id 重复，必须换新 id 并把 XML 里的 id 一起改掉。
- 取项方式：K4 `fields.TYPE` = `first|last|all`，KN 的 `item` 下拉只有「第 / 最后一项（/ 所有项）」。
  `first → any` 且此时索引影子（`list_index`，`NUM=1`）必须保留；`last → last` 无索引。
- 空影子不要写：`"list_index": ""` 这类空串只会让编辑器多出一堆「缺影子」的槽。

实现：`blocks.Converter._ensure_embeds`。任何带 `embedBlock.isShadow` 的值槽都走这条路，
KN 以后新增同类槽不用改代码。

## 5.5 影子积木类型集合

`pure_list_get / broadcast_input / get_styles / shadow_number / shadow_text / logic_empty …`
不在 `blocksOptions` 的积木清单里，但引擎都认识，属于「内嵌影子块」。
`schema.py` 直接从所有 `args[].embedBlock.blockName` 扫出这份集合并入 `KNOWN_TYPES`，
少一个校验器就会误报「KN 不认识的积木类型」。

## 5.6 广播

`broadcastsDict{sceneId: [名字...]}`，名字同时被 `broadcast_input` 影子的
`<field name="message">名字</field>` 引用。

K4 工程的 `broadcasts` 表经常被写残（实测一个 3.9 MB 的文件里堆了 75920 条、
`Hi` 重复 34818 次、去重后 15414 个名字），直接照搬会让 KN 的广播下拉框卡死。
转换时按「脚本里真用到」裁剪。

## 5.7 其它

- 顶帽子积木（`on_running_group_activated` 等）在 KN 里身体挂 `next`，编译器再归一化。
- `controls_if` / `text_join` / `list_get` 是动态展开块，参数名 `IF0..,DO0..,ELSE`、
  `ADD0..`，靠 mutation 的 `elseif`/`else`/`items` 数目对齐。
- 用计时器的作品需要根上的 `timerPosition`（`{x: 舞台宽-180, y: 12}`）。
- 降级块（`bcm_translator_text_*`）没有语句槽：**接在它后面的代码必须挂到它的 `next`**，
  否则一条链上剩下几十块会凭空消失（官方在 `set_pen_path` 上就丢了 109 块）。

## 5.8 「缺失列表1」不是报错，是转换器补的占位定义

K4 作品里常见「脚本引用了某变量/列表的 uuid，但 `variables` 表里没有它」——原作删过变量、
引用没清干净。`convert.repair_variable_refs` 会给这些 uuid 补一条占位定义：

```json
{"id":"<原 uuid>","type":"list","name":"缺失列表1","value":[],"visible":false,"isGlobal":true}
```

- 名字用中性的陈述（`缺失列表N` / `缺失变量N`），不要用动词开头的名字，否则会被误认成
  编辑器的修复动作。
- 每条都写进转换报告的「K4 源里引用已删除的变量（已补占位）」，格式 `uuid → 缺失列表1`。
- 不补的话积木指向不存在的列表，运行时是 `NaN`，编辑器还会挂「恢复列表」。
- 降级文字块的文案模板里有 `{index}` 这类占位，`maps.text_of` 会先按 `{value}`/`{valueN}`
  取值输入，其余按积木自己的 `fields` 取，取不到留空。

---

# 第三部分：导入到 KN 的喵链接

KN 的「导入喵链接」是浏览器里 `axios.get(url)` → `isVersionOk` → `validateBcm` → `setBcm`，
所以任何**跨域可访问**的 `.bcmkn` 直链都能用。

codemao 自己 CDN 上的地址：

```
https://creation.bcmcdn.com/922/user-files/<内容哈希>.bcmkn
```

那个 32 位名字不是随机 id，是**文件内容的哈希**：

```
key = "922/user-files/" + base64url(前缀 + SHA1) + ".bcmkn"
前缀 = "16"（≤4MB 单块） / "96"（>4MB：按 4MB 分块各算 SHA-1，20 字节摘要拼接后再 SHA-1）
```

上传分两步（第二步才是真正上传）：

```
GET  https://open-service.codemao.cn/cdn/qi-niu/tokens/uploading
     ?cdnName=qiniu&insertOnly=true&filePaths=<key>&projectName=neko    ← 需要登录态，匿名 403
POST https://upload.qiniup.com/  (multipart: key / file / token)
最终 URL = bucket_url + key
```

`tools/make_miao_link.js` 是这段流程的浏览器版：登录 kn.codemao.cn 后在控制台粘贴，
选文件即可拿到链接（内容哈希 → 同一份文件重复上传不会产生第二份副本）。
