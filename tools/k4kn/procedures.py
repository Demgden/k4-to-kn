"""函数（proceduresDict）转换。

K4：
    procedures_2_defnoreturn   函数头（fields.NAME=中文名），PARAMS{i} 接 procedures_2_stable_parameter
    procedures_2_parameter     函数体里引用参数（fields.param_name）
    procedures_2_return_value  返回语句，VALUE 接返回值（就长在函数体里）
    procedures_2_callnoreturn / procedures_2_callreturn   调用，inputs.ARG{i} 接实参
    调用的 mutation 里带 def_id（函数头 block id）+ 每个参数名的 parameter_shadow

KN（据编辑器自己的 AST->积木与积木->AST 代码确认）：
    proceduresDict[id] = {id, name, type: NORMAL|HEXAGONAL|ROUND, params, nekoBlockJsonList}
      * params[0] 是 Label（函数名），后面才是真正的参数
      * 只有一个条目 —— 有返回值只是 type=ROUND，不是再造一个同名函数
      * nekoBlockJsonList[0] 是 procedures_2_defnoreturn 头，函数体挂 statements.STACK
      * 返回值就是函数体里的 procedures_2_return_value 块（mutation items="1" type="ROUND"）
    调用块 fields.NAME = 目标条目 id，实参放在 inputs[<参数 id>] 里（不是 ARG0！）

官方转换器的坑（这里全部绕开）：
  1. Xk 把函数体从脚本里摘走，Jk 之后只遍历角色脚本 -> 函数体内的调用永远不解绑
  2. 同名函数跨角色乱绑（find(name) 命中第一条，不看 owner）
  3. 有返回值的定义被拆成同名 NORMAL/ROUND，find(name) 恒命中 NORMAL -> 返回值链断
  4. 函数体里的 return 块原样留着，但返回值没接上，函数永远返回空

本实现：
  * 绑定时优先用调用自带的 def_id（K4 项目里 408/409 命中），其次 owner+名字；
  * 定义缺失的调用用 def_id / parameter_shadow 造空壳函数，调用和实参都不丢；
  * 一个函数只生成一个条目，有返回值就是 ROUND，return 块和它的 VALUE 原样留在体内。
"""
import uuid

from . import blocks as blk

XMLNS = blk.XMLNS
LABEL_NS = uuid.UUID("6f1a3c1e-1c1a-4f6b-9e0e-000000000001")


def _arg_xml(params):
    """KN 的 <arg>：编辑器自己写 name，官方 K4 转换器写 content，两个都带上最稳。"""
    return "".join(
        '<arg id="%s" name="%s" content="%s" type="%s"></arg>'
        % (p["id"], p["name"].replace(" ", "_"), p["name"], p["type"])
        for p in params)


PARAM_MUT = '<mutation xmlns="http://www.w3.org/1999/xhtml" id="%s" type="%s"></mutation>'


def _walk(node):
    if not isinstance(node, dict):
        return
    yield node
    if isinstance(node.get("next"), dict):
        yield from _walk(node["next"])
    for key in ("inputs", "statements"):
        for v in (node.get(key) or {}).values():
            yield from _walk(v)


def bind_param_blocks(body, params):
    """函数体里的参数积木要靠 mutation.id 认领定义，否则编辑器一律画成灰色「未定义参数」。

    K4 的 procedures_2_parameter 只有 fields.param_name，mutation 是空的；KN 需要
    <mutation id="<参数 id>" type="String">（id 就是函数头上那个 stable_parameter 块的 id）。
    返回 (绑定数, 找不到同名参数的个数)。
    """
    by_name = {p["name"]: p for p in params if p["type"] != "Label"}
    fixed = missing = 0
    for node in _walk(body):
        if node.get("type") != "procedures_2_parameter":
            continue
        name = (node.get("fields") or {}).get("param_name") or ""
        p = (by_name.get(name) or by_name.get(name.strip())
             or by_name.get(name.replace(" ", "_")))
        if p is None:
            missing += 1
            continue
        node["mutation"] = PARAM_MUT % (p["id"], p["type"])
        node["is_output"] = True
        fixed += 1
    return fixed, missing


def _def_mutation(params):
    return '<mutation xmlns="http://www.w3.org/1999/xhtml">%s</mutation>' % _arg_xml(params)


def _call_mutation(entry, target_id):
    return ('<mutation xmlns="http://www.w3.org/1999/xhtml" def_id="%s" name="%s" type="%s">%s</mutation>'
            % (target_id, target_id, entry["type"], _arg_xml(entry["params"])))


def _num_shadow():
    return ('<shadow xmlns="http://www.w3.org/1999/xhtml" type="math_number" id="%s" visible="visible">'
            '<field constraints="-Infinity,Infinity,0," allow_text="true" name="NUM">0</field></shadow>'
            % blk.new_id())


def collect_params(src, def_bid):
    """函数头 PARAMS{i} 上的 stable_parameter -> [(i, param_id, 参数名)]"""
    params = []
    for cid, kind, name in src.kids(def_bid):
        if kind != "value" or not (name or "").startswith("PARAMS"):
            continue
        try:
            idx = int(name.replace("PARAMS", ""))
        except ValueError:
            continue
        child = src.block(cid) or {}
        pname = (child.get("fields") or {}).get("param_name")
        params.append((idx, child.get("id") or cid, pname or ("参数%d" % idx)))
    params.sort()
    return params


def _label_only(meta_id, name):
    return {"id": str(uuid.uuid5(LABEL_NS, meta_id + "#label")), "type": "Label", "name": name}


def _has_return_block(node):
    if not isinstance(node, dict):
        return False
    if node.get("type") == "procedures_2_return_value":
        return True
    if isinstance(node.get("next"), dict) and _has_return_block(node["next"]):
        return True
    for key in ("inputs", "statements"):
        for v in (node.get(key) or {}).values():
            if _has_return_block(v):
                return True
    return False


def build(conv):
    """conv.defs / conv.calls -> (proceduresDict, metas)"""
    metas = []
    by_id = {}
    for src, bid, owner_id in conv.defs:
        b = src.block(bid) or {}
        name = (b.get("fields") or {}).get("NAME") or "未命名函数"
        params = collect_params(src, bid)
        # params[0] 是函数名 Label，KN 的编译器会跳过它
        plist = [_label_only(bid, name)] + [{"id": pid, "type": "String", "name": pname}
                                           for _, pid, pname in params]
        meta = {
            "id": bid,
            "name": name,
            "owner_id": owner_id,
            "src": src,
            "location": list(b.get("location") or [0, 0]),
            "params": plist,
            "has_return": False,
            "type": "NORMAL",
            "stub": False,
            "body": None,
            "param_nodes": [],
        }
        metas.append(meta)
        by_id[bid] = meta

    # 1) 函数体：参数块 + STACK（返回块留在体内，KN 就是这么存的）
    for meta in metas:
        src = meta["src"]
        param_nodes = []
        for cid, kind, name in src.kids(meta["id"]):
            if kind == "value" and (name or "").startswith("PARAMS"):
                pnode = conv.node(src, cid, meta["id"])
                if pnode:
                    param_nodes.append(pnode)
        meta["param_nodes"] = param_nodes
        body = None
        for cid, kind, name in src.kids(meta["id"]):
            if kind == "statement" and (name or "STACK") == "STACK":
                body = conv.node(src, cid, meta["id"])
                break
        meta["body"] = body
        if _has_return_block(body):
            meta["has_return"] = True
        fixed, missing = bind_param_blocks(body, meta["params"])
        meta["params_bound"] = fixed
        if missing and conv.report is not None:
            conv.report.warn("函数体里有认不到的参数积木", "%s (%d 个)" % (meta["name"], missing))

    # 2) 有 callreturn 指着它，也当成有返回值
    for call in conv.calls:
        meta = _resolve_meta(call, metas, by_id)
        if meta is not None and call["is_return"]:
            meta["has_return"] = True

    # 3) 定义缺失的调用：造空壳函数，别把调用（连同实参积木）变成文字块
    for call in conv.calls:
        if _resolve_meta(call, metas, by_id) is not None:
            continue
        name = call.get("name") or "未知名函数"
        def_id = call.get("def_id") or str(
            uuid.uuid5(LABEL_NS, "stub#%s#%s" % (call["owner_id"], name)))
        plist = [_label_only(def_id, name)] + [
            {"id": str(uuid.uuid5(LABEL_NS, "%s#p%d" % (def_id, i))), "type": "String", "name": n}
            for i, n in enumerate(call.get("shadow_params") or [])
        ]
        meta = {
            "id": def_id, "name": name, "owner_id": call["owner_id"], "src": None,
            "location": [0, 0], "params": plist, "has_return": bool(call["is_return"]),
            "type": "NORMAL", "stub": True, "body": None, "param_nodes": [],
        }
        metas.append(meta)
        by_id[def_id] = meta
        if conv.report is not None:
            conv.report.warn("函数定义缺失（已建空壳）", "%s @ %s" % (name, call["owner_id"]))

    # 4) 生成 proceduresDict：一个函数一个条目
    procs = {}
    for meta in metas:
        meta["type"] = "ROUND" if meta["has_return"] else "NORMAL"
        procs[meta["id"]] = _entry(meta, _def_node(meta, meta["id"]))
    return procs, metas


def _entry(meta, node):
    return {
        "id": meta["id"],
        "name": meta["name"],
        "type": meta["type"],
        "params": meta["params"],
        "nekoBlockJsonList": [node] if node else [],
        "workspaceScrollXy": {"x": meta["location"][0] if meta["location"] else 100,
                              "y": meta["location"][1] if meta["location"] else 30},
        "comments": {},
    }


def _def_node(meta, entry_id):
    node = {
        "type": "procedures_2_defnoreturn",
        "id": entry_id,
        "location": list(meta["location"]),
        "shield": False,
        "mutation": _def_mutation(meta["params"]),
        "fields": {"NAME": entry_id},
        "shadows": {"STACK": ""},
    }
    if meta.get("body"):
        node["statements"] = {"STACK": meta["body"]}

    # 函数头参数槽：KN 用 1 基 PARAMS（K4 是 0 基），槽里放 stable_parameter 块
    inputs, shadows = {}, {}
    for i, p in enumerate(meta.get("param_nodes") or []):
        key = "PARAMS%d" % (i + 1)
        inputs[key] = p
        shadows[key] = _num_shadow()
    if inputs:
        node["inputs"] = inputs
    node["shadows"].update(shadows)
    return node


def _resolve_meta(call, metas, by_id):
    if call.get("def_id") and call["def_id"] in by_id:
        return by_id[call["def_id"]]
    name = call.get("name")
    for m in metas:
        if m["name"] == name and m["owner_id"] == call["owner_id"]:
            return m
    return None


def rebind(conv, procs, metas):
    """把调用块的实参从 K4 的 ARG{i} 改挂到 KN 的参数 id 上。"""
    by_id = {m["id"]: m for m in metas}
    by_owner_name = {}
    by_name = {}
    for m in metas:
        by_owner_name.setdefault((m["owner_id"], m["name"]), []).append(m)
        by_name.setdefault(m["name"], []).append(m)

    unresolved = []
    for call in conv.calls:
        node = call["node"]
        meta = _resolve_meta(call, metas, by_id)
        if meta is None:
            cands = by_owner_name.get((call["owner_id"], call.get("name"))) or by_name.get(call.get("name"))
            meta = cands[0] if cands else None
        if meta is None:
            unresolved.append((call["owner_id"], call.get("name")))
            node["type"] = "bcm_translator_text_execution_block"
            node["mutation"] = ('<mutation xmlns="http://www.w3.org/1999/xhtml" items="0">'
                                '未解析函数：%s</mutation>' % (call.get("name") or "?"))
            node.pop("fields", None)
            node.pop("inputs", None)
            continue
        entry = procs[meta["id"]]
        node["type"] = "procedures_2_callreturn" if call["is_return"] else "procedures_2_callnoreturn"
        node["fields"] = {"NAME": meta["id"]}
        node["mutation"] = _call_mutation(entry, meta["id"])

        # 实参：K4 的 ARG0.. 按位置对应到第一个非 Label 参数
        old_inputs = node.get("inputs") or {}
        real_params = [p for p in entry["params"] if p["type"] != "Label"]
        new_inputs = {}
        for i, p in enumerate(real_params):
            val = old_inputs.get("ARG%d" % i)
            if val is None:
                val = old_inputs.get(p["id"])
            if val is not None:
                new_inputs[p["id"]] = val
        extra = [k for k in old_inputs if k.startswith("ARG") and
                 int(k[3:] or -1) >= len(real_params)]
        if extra:
            conv.report and conv.report.warn("调用实参多于参数（已丢弃）",
                                            "%s %s" % (meta["name"], ",".join(extra)))
        if new_inputs:
            node["inputs"] = new_inputs
        else:
            node.pop("inputs", None)

        shadows = {"NAME": ""}
        for p in real_params:
            shadows[p["id"]] = _num_shadow()
        node["shadows"] = shadows
    return unresolved
