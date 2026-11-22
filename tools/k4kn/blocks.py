"""K4 扁平积木图 -> KN 嵌套积木树。

K4 结构：
    block_data_json.blocks       {id: {type, fields, shadows, mutation, is_output, is_shadow, location, ...}}
    block_data_json.connections  {parent: {child: {type:'next'|'input', input_type:'value'|'statement', input_name}}}

KN 结构（nekoBlockJsonList 的元素）：
    {type, id, location, shield, mutation, fields, shadows, inputs, statements, next, parent_id}

KN 的 shadows 是 XML 字符串；K4 的 shadows 也是 XML 字符串（格式几乎一致），
所以这里只做类型/字段/取值改写，不重建 XML。
"""
import re
import uuid
import xml.sax.saxutils as sax

from . import maps, schema

XMLNS = schema.XMLNS

# get_3 + attribute -> (类型, 字段名, 字段值)
# KN 这三个「…of_sprite」积木的第一个参数都是 sprite（角色），后面才是具体属性；
# 官方转换器还会把取值再镜像一份到 attribute 上，这里保持一致。
APPEARANCE_ATTR = {
    0: ("coordinate_of_sprite", "coordinate", "x"),
    1: ("coordinate_of_sprite", "coordinate", "y"),
    2: ("style_of_sprite", "style", "style_of_sprite"),
    3: ("appearance_of_sprite", "appearance", "direction"),
    5: ("appearance_of_sprite", "appearance", "scale"),
    7: ("appearance_of_sprite", "effect", "0"),
    8: ("appearance_of_sprite", "effect", "1"),
    9: ("appearance_of_sprite", "effect", "2"),
    10: ("appearance_of_sprite", "effect", "3"),
    11: ("appearance_of_sprite", "effect", "4"),
    12: ("appearance_of_sprite", "effect", "5"),
    13: ("appearance_of_sprite", "effect", "6"),
    14: ("appearance_of_sprite", "effect", "?"),
    16: ("appearance_of_sprite", "appearance", "width"),
    17: ("appearance_of_sprite", "appearance", "height"),
}

# 造型编号：KN 只有 sprite 一个字段，官方额外写 attribute=style_index + style=style_of_sprite
STYLE_ATTR_FIELDS = {"attribute": "style_index", "style": "style_of_sprite"}

# K4 的布尔/数值字面量影子积木 -> KN 类型
SHADOW_TYPE_FIX = {
    "lists_get": "pure_list_get",
    "lists_get_value": "pure_list_get",
}

# 影子 XML 里字段名需要特殊处理
SHADOW_FIELD_FIX = {
    "pure_list_get": {"VAR": "list", "LISTS": "list"},
    "list_item": {"VAR": "list"},
}


def new_id():
    return str(uuid.uuid4())


def _sort_key(name):
    """DO0 < DO1 < DO2；没有数字后缀的排前面。"""
    m = re.search(r"(\d+)$", name or "")
    return (0, int(m.group(1))) if m else (0, -1)


def _chain_tail(node):
    """走 next 链到最后一块。"""
    guard = 0
    while isinstance(node.get("next"), dict) and guard < 100000:
        node = node["next"]
        guard += 1
    return node


def _xml_field(xml, name):
    """从影子 XML 里取 <field name="..."> 的值。"""
    if not xml:
        return None
    m = re.search(r'<field name="%s"\s*>([^<]*)</field>' % re.escape(name), xml)
    if not m:
        return None
    val = m.group(1).strip()
    return val or None


class Source:
    """一个实体（角色/场景/函数体）的 K4 积木图。"""

    def __init__(self, block_data, owner_id, owner_name, kind):
        self.blocks = (block_data or {}).get("blocks") or {}
        self.connections = (block_data or {}).get("connections") or {}
        self.owner_id = owner_id
        self.owner_name = owner_name
        self.kind = kind
        self._child_ids = set()
        for _, conns in self.connections.items():
            for cid in conns:
                self._child_ids.add(cid)

    def block(self, bid):
        return self.blocks.get(bid)

    def kids(self, bid):
        """[(child_id, kind, input_name)]，kind in {'next','value','statement'}"""
        out = []
        for cid, info in (self.connections.get(bid) or {}).items():
            if info.get("type") == "next":
                out.append((cid, "next", None))
            elif info.get("type") == "input":
                it = info.get("input_type")
                out.append((cid, "statement" if it == "statement" else "value", info.get("input_name")))
        return out

    def roots(self):
        return [bid for bid in self.blocks if bid not in self._child_ids]


class Converter:
    def __init__(self, report=None, skip_defs=True):
        self.report = report
        self.skip_defs = skip_defs
        self.calls = []          # (node, owner_id, raw_name, is_return)
        self.defs = []           # (src, bid, owner_id)
        self._done = set()       # 已搬过的 K4 block id（防重复引用）
        self._active = set()     # 正在搬的整条祖先链（防成环）

    # ------------------------------------------------------------------ 入口
    def entity_roots(self, src):
        out = []
        for bid in src.roots():
            b = src.block(bid)
            if not b or b.get("is_shadow"):
                continue
            if b.get("type") in ("procedures_2_defnoreturn", "procedures_2_defreturn"):
                if self.skip_defs:
                    self.defs.append((src, bid, src.owner_id))
                    continue
            node = self.node(src, bid, None)
            if node:
                out.append(node)
        return out

    # ------------------------------------------------------------------ 单块
    def node(self, src, bid, parent_id=None, dup=False):
        """dup=True 表示这份是「第二次引用同一个 K4 块」的副本，所有 id 换新。"""
        b = src.block(bid)
        if not b:
            return None
        if bid in self._active:
            self._warn("积木成环（已截断）", "%s %s" % (b.get("type"), bid))
            return None
        if not dup and bid in self._done:
            # K4 允许同一个块被多个输入引用（值积木被复制成共享实例）。
            # KN 是树，一个 id 只能出现一次 —— 所以第二处起整棵子树复制换 id，
            # 而不是丢掉（丢掉就是凭空少一块代码）。
            self._warn("积木被多处引用（已复制）", "%s %s" % (b.get("type"), bid))
            dup = True
        if not dup:
            self._done.add(bid)
        self._active.add(bid)
        try:
            return self._build(src, bid, b, parent_id, dup)
        finally:
            self._active.discard(bid)

    def _build(self, src, bid, b, parent_id, dup):
        k4_type = b.get("type") or ""
        fields = dict(b.get("fields") or {})
        kn_type = maps.kn_type_of(k4_type)

        axis = fields.get("coordinary")
        if (k4_type, axis) in maps.AXIS_SPLIT:
            kn_type = maps.AXIS_SPLIT[(k4_type, axis)]
            fields.pop("coordinary", None)

        if k4_type == "get_3" and "attribute" in fields:
            try:
                info = APPEARANCE_ATTR.get(int(fields["attribute"]))
            except (TypeError, ValueError):
                info = None
            if info:
                kn_type, fname, fval = info
                sprite = maps.map_value("sprite", fields.get("sprite") or "__self")
                fields = {"sprite": sprite, fname: fval}
                if kn_type == "style_of_sprite":
                    fields.update(STYLE_ATTR_FIELDS)
                else:
                    fields["attribute"] = fval

        forced = maps.FORCE_FIELD.get(k4_type)
        if forced:
            fields.update(forced)

        degraded = maps.is_degraded(kn_type)

        node = {
            "type": kn_type,
            "id": new_id() if dup else bid,
            "location": list(b.get("location") or [0, 0]),
            "shield": False,
            "mutation": b.get("mutation") or "",
        }
        if b.get("disabled"):
            node["disabled"] = True
        if b.get("is_output"):
            # KN 用它标记「这是值积木」；函数参数积木尤其靠它，缺了就渲染成灰块
            node["is_output"] = True
        if parent_id:
            node["parent_id"] = parent_id
        if b.get("is_shadow"):
            node["is_shadow"] = True

        # ---- 子块
        kids = src.kids(bid)
        stmt_kids = [(cid, name) for cid, kind, name in kids if kind == "statement"]
        val_kids = [(cid, name) for cid, kind, name in kids if kind == "value"]
        next_kids = [cid for cid, kind, _ in kids if kind == "next"]
        # 影子子块：值已经写进 shadows XML，但列表这类「内嵌影子块」还要靠它取字段值
        shadow_kids = {}
        for cid, name in val_kids:
            cb = src.block(cid) or {}
            if cb.get("is_shadow"):
                shadow_kids[name] = cb

        if degraded:
            node["mutation"] = self._degraded_mutation(src, bid, k4_type, b)
            if maps.DEGRADED_SUFFIX.get(kn_type) == "事件":
                node["shadows"] = {"TITLE_HEAD": ""}
            # 降级块没有声明参数，但 K4 原来的字段（颜色、是否固定等）照样透传，
            # 官方产物里也是这么写的
            keep = {}
            for k, v in fields.items():
                keep[maps.map_field_name(kn_type, k)] = maps.map_value(k, v)
            if keep:
                node["fields"] = keep
            # 降级块本身只是一段说明文字，但它的语句体/后续积木是真实代码，
            # 必须挂到 next 上续下去 —— 否则一条链上的代码会整段凭空消失
            # （官方转换器就是这么丢的：set_pen_path 后面 100 多个块全没了）。
            self._drain_children(node, src, bid, stmt_kids, next_kids, val_kids, dup)
            return node

        # ---- fields
        out_fields = {}
        for k, v in fields.items():
            mname = maps.map_field_name(kn_type, k)
            mval = maps.map_value(k, v)
            if mname == "item":
                mval = maps.map_item(mval)
            out_fields[mname] = mval
        # 列表类积木的 list 是 input_value（内嵌 pure_list_get），不是字段
        if out_fields.get("list") and schema.embed_of(kn_type, "list"):
            out_fields.pop("list", None)
        if out_fields:
            node["fields"] = out_fields

        # ---- shadows
        shadows = self._shadows(src, b, k4_type, kn_type)
        if shadows:
            node["shadows"] = shadows

        # ---- mutation 特例
        node["mutation"] = self._fix_mutation(k4_type, kn_type, node["mutation"], src, b)

        inputs, statements = {}, {}
        if kn_type in schema.TOP_BLOCKS:
            # 帽子积木：身体挂在 next 上（KN 编译前会归一化成 statements.DO）
            body = sorted(stmt_kids, key=lambda x: _sort_key(x[1]))
            if body:
                child = self.node(src, body[0][0], node["id"], dup)
                if child:
                    node["next"] = child
            self._place_values(src, bid, kn_type, val_kids, inputs, dup, node["id"])
        else:
            self._place_statements(src, bid, kn_type, stmt_kids, statements, dup, node["id"])
            self._place_values(src, bid, kn_type, val_kids, inputs, dup, node["id"])

        if next_kids:
            child = self.node(src, next_kids[0], node["id"], dup)
            if child:
                node["next"] = child
        if inputs:
            node["inputs"] = inputs
        if statements:
            node["statements"] = statements
        # 放在 inputs 赋值之后：内嵌的影子积木要并进同一份 inputs
        self._ensure_embeds(node, kn_type, shadows, shadow_kids)
        # ---- 函数调用：登记，稍后统一重绑
        if k4_type in ("procedures_2_callnoreturn", "procedures_2_callreturn"):
            mut = b.get("mutation") or ""
            m = re.search(r'def_id="([^"]+)"', mut)
            shadow_params = re.findall(r'<procedures_2_parameter_shadow name="([^"]*)"', mut)
            self.calls.append({
                "node": node,
                "owner_id": src.owner_id,
                "name": fields.get("NAME"),
                "is_return": k4_type == "procedures_2_callreturn",
                "def_id": m.group(1) if m else None,
                "shadow_params": shadow_params,
            })

        return node

    # ------------------------------------------------------------- statements
    def _drain_children(self, node, src, bid, stmt_kids, next_kids, val_kids, dup=False):
        """降级块没法执行，但它内部/后面的代码要接着活下去。

        KN 的文字占位块没有 DO 槽，所以把语句体先接到它的 next 上，再把原来的
        next 链串到最后 —— 顺序和原来一致（体内先、体外后）。值类型的子块没有
        地方安放，只能记进报告。
        """
        order = [cid for cid, _ in sorted(stmt_kids, key=lambda x: _sort_key(x[1]))]
        order += list(next_kids)
        last = node
        for cid in order:
            child = self.node(src, cid, node["id"], dup)
            if not child:
                continue
            _chain_tail(last)["next"] = child
            last = child
        for cid, name in val_kids:
            child_block = src.block(cid) or {}
            if child_block.get("is_shadow"):
                continue
            self._warn("degraded-input-dropped",
                       "%s.%s" % (src.block(bid).get("type", ""), name))

    def _place_statements(self, src, bid, kn_type, stmt_kids, out, dup=False, parent_id=None):
        used = set()
        for cid, name in sorted(stmt_kids, key=lambda x: _sort_key(x[1])):
            target = self._resolve(k4_type=src.block(bid).get("type", ""),
                                   kn_type=kn_type, name=name, kind="input_statement", used=used)
            child = self.node(src, cid, parent_id or bid, dup)
            if not child:
                continue
            if target is None:
                self._warn("statement-unmapped", f"{kn_type}:{name}")
                target = name
            used.add(target)
            if target in out:
                self._warn("statement-dup", f"{kn_type}:{target}")
                continue
            out[target] = child

    # ----------------------------------------------------------------- inputs
    def _place_values(self, src, bid, kn_type, val_kids, out, dup=False, parent_id=None):
        used = set()
        k4_type = (src.block(bid) or {}).get("type", "")
        for cid, name in val_kids:
            child_block = src.block(cid) or {}
            if child_block.get("is_shadow"):
                continue        # 影子值已经写进 shadows XML
            target = self._resolve(k4_type, kn_type, name, "input_value", used)
            if target is None:
                self._warn("input-unmapped", f"{kn_type}:{name}")
                target = maps.map_input_name(k4_type, kn_type, name)
            used.add(target)
            if target in out:
                self._warn("input-dup", f"{kn_type}:{target}")
                continue
            child = self.node(src, cid, parent_id or bid, dup)
            if child:
                out[target] = child

    def _ensure_embeds(self, node, kn_type, shadows, shadow_kids):
        """KN 的列表槽是 embedBlock：`inputs.<arg>` 里必须真的放一个 pure_list_get 块。

        只写 `shadows.<arg>` 的 XML 只是「影子形态」，运行时按积木取不到值，
        列表参数于是变成 undefined —— 表现就是「仅支持列表类型，已处理为NaN」（读）、
        「已忽略执行，请改为列表类型」（写）。
        """
        args = schema.value_args(kn_type)
        if not args:
            return
        inputs = node.get("inputs") or {}
        added = False
        for arg in args:
            emb = schema.embed_of(kn_type, arg)
            if not emb or not emb.get("isShadow") or arg in inputs:
                continue
            fname = emb.get("name") or "list"
            xml = shadows.get(arg) or ""
            val = _xml_field(xml, fname)
            if val is None:
                blk = shadow_kids.get(arg) or {}
                f = blk.get("fields") or {}
                val = f.get(fname) or f.get("VAR") or f.get("LISTS")
            if not val:
                continue
            bid = new_id()
            # K4 的列表引用影像是复用的（同一实体里同一列表只有一个 shadow id），
            # 直接沿用会造成 KN 里 id 重复，所以换新 id 并同步改写 XML。
            if xml:
                if re.search(r'<shadow[^>]*\sid="[^"]*"', xml):
                    xml = re.sub(r'(<shadow[^>]*?)\sid="[^"]*"', r'\1 id="%s"' % bid, xml, count=1)
                else:
                    xml = xml.replace("<shadow", '<shadow id="%s"' % bid, 1)
                shadows[arg] = xml
            inputs[arg] = {
                "type": emb.get("blockName"),
                "id": bid,
                "location": [0, 0],
                "shield": False,
                "mutation": "",
                "fields": {fname: val},
                "shadows": {},
                "inputs": {},
                "statements": {},
                "parent_id": node["id"],
                "is_shadow": True,
                "is_output": True,
            }
            added = True
        if added:
            node["inputs"] = inputs
            if shadows:
                node["shadows"] = shadows

    def _resolve(self, k4_type, kn_type, name, kind, used):
        if name is None:
            return None
        cands = [
            name,
            maps.map_input_name(k4_type, kn_type, name),
            maps.map_field_name(kn_type, name),
            re.sub(r"\d+$", "", name),
        ]
        for cand in cands:
            if cand in used:
                continue
            ok = schema.accepts(kn_type, kind, cand)
            if ok is None:          # 没有 schema，只能信官方字典
                return cand
            if ok:
                return cand
        return None

    # ---------------------------------------------------------------- shadows
    def _shadows(self, src, b, k4_type, kn_type):
        raw = b.get("shadows") or {}
        want = schema.value_args(kn_type)
        allowed = set(want) if want is not None else None
        out = {}
        for name, xml in raw.items():
            target = self._resolve(k4_type, kn_type, name, "input_value", set(out))
            if target is None:
                target = maps.map_input_name(k4_type, kn_type, name)
            if allowed is not None and target not in allowed:
                continue            # KN 该积木没有这个参数名，塞进去只会是脏数据
            if not xml:
                continue            # 该槽接的是真实积木（或本来就空），不需要空影子
            out[target] = self.transform_shadow_xml(xml, k4_type, kn_type)
        # KN 自己声明了默认影子的（滑块、角度等）才补；补空串只会让编辑器
        # 多出一堆「缺影子」的槽（官方产物里也不写这些键）。
        for w in (want or []):
            if w in out:
                continue
            d = schema.default_shadow(kn_type, w)
            if d:
                out[w] = d
        return out

    def transform_shadow_xml(self, xml, k4_type, kn_type):
        """改写一段影子 XML：type + 字段名 + 字段值。"""
        def repl_type(m):
            t = m.group(1)
            t2 = maps.kn_type_of(t)
            if "list" in kn_type and t in SHADOW_TYPE_FIX:
                t2 = SHADOW_TYPE_FIX[t]
            return 'type="%s"' % t2

        out = re.sub(r'type="([A-Za-z0-9_]+)"', repl_type, xml, count=1)
        # 影子自身的 block 类型（用于字段改名判断）
        mt = re.search(r'type="([A-Za-z0-9_]+)"', out)
        shadow_type = mt.group(1) if mt else ""

        def repl_field(m):
            name, val = m.group(1), m.group(2)
            new_name = maps.map_field_name(shadow_type or kn_type, name)
            fix = SHADOW_FIELD_FIX.get(shadow_type, {})
            new_name = fix.get(name, new_name)
            new_val = maps.map_value(name, val)
            return '<field name="%s">%s</field>' % (new_name, new_val)

        out = re.sub(r'<field name="([^"]+)"\s*>([^<]*)</field>', repl_field, out)
        return out

    # --------------------------------------------------------------- mutation
    def _fix_mutation(self, k4_type, kn_type, mutation, src, b):
        if kn_type == "procedures_2_return_value":
            # KN 的返回块要带 type（ROUND=数/字符，HEXAGONAL=条件），K4 只写了 items
            if "type=" not in mutation:
                mutation = mutation.replace("<mutation", '<mutation type="ROUND"', 1) \
                    if mutation else '<mutation items="1" type="ROUND"></mutation>'
            return mutation
        if not mutation:
            return ""
        if k4_type == "text_select_changeable":
            m = re.search(r'items="(\d+)"', mutation)
            if m:
                mutation = mutation.replace(m.group(0), 'items="%d"' % max(0, int(m.group(1)) - 1))
        if kn_type == "controls_if":
            kids = src.kids(b.get("id"))
            vals = [n for _, k, n in kids if k == "value" and (n or "").startswith("IF")]
            stmts = [n for _, k, n in kids if k == "statement"]
            elsif = max(0, len(vals) - 1)
            has_else = 1 if any((n or "") == "ELSE" for n in stmts) else 0
            if "elseif=" in mutation:
                mutation = re.sub(r'elseif="\d+"', 'elseif="%d"' % elsif, mutation)
            else:
                mutation = mutation.replace("<mutation", '<mutation elseif="%d"' % elsif, 1)
            if "else=" in mutation:
                mutation = re.sub(r'else="\d+"', 'else="%d"' % has_else, mutation)
            else:
                mutation = mutation.replace("<mutation", '<mutation else="%d"' % has_else, 1)
        return mutation

    def _degraded_mutation(self, src, bid, k4_type, b):
        vals = [cid for cid, kind, _ in src.kids(bid) if kind == "value"]

        def resolve(i):
            if i >= len(vals):
                return ""
            child = src.block(vals[i]) or {}
            f = child.get("fields") or {}
            for key in ("TEXT", "NUM", "VAR"):
                if key in f:
                    v = f[key]
                    if key == "VAR":
                        return "变量"
                    return str(v)
            return "..."

        text = maps.text_of(k4_type, b, resolve)
        text = re.sub(r"\s{2,}", " ", text or "").strip() or k4_type
        return '<mutation xmlns="http://www.w3.org/1999/xhtml" items="0">%s</mutation>' % sax.escape(text)

    # ---------------------------------------------------------------- 报告
    def _warn(self, kind, detail):
        if self.report is not None:
            self.report.warn(kind, detail)
