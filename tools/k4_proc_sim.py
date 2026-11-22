#!/usr/bin/env python3
"""模拟 KittenN 官方转换器 (home 包 module 27637) 的函数(自定义积木)处理流程。

官方源码未随仓库分发（按 docs/format-notes.md §2 自行抓取）。对应函数：
  - Yk.parseBlock           积木树重建
  - Vk                      抽出 procedures_2_defnoreturn
  - Xk                      写入 proceduresDict
  - Wk                      检测 def 内的 return_value
  - Jk                      按 name 回填调用块 def id
本脚本略去 qk() 的积木类型重映射(与函数定位无关)，其余逻辑逐行对齐。
"""
import json
import sys
import uuid
from collections import Counter, OrderedDict

# ---- 常量 (源码 Uk 表中与函数无关部分省略) ----
DEF_TYPE = "procedures_2_defnoreturn"
RETURN_VALUE = "procedures_2_return_value"
PARAM = "procedures_2_parameter"
STABLE_PARAM = "procedures_2_stable_parameter"
CALL_NORETURN = "procedures_2_callnoreturn"
CALL_RETURN = "procedures_2_callreturn"

# 源码 parseBlock: 这些类型 + 输入名的组合进 statements 而非 inputs
STATEMENT_INPUTS = {
    "controls_if": ("DO", "ELSE"),
    "when": ("DO",),
    "repeat_forever": ("DO",),
    "start_as_a_mirror": ("DO",),
    "self_listen": ("DO",),
    "repeat_n_times": ("DO",),
    "repeat_forever_until": ("DO",),
}
TO_NEXT_TYPES = {"on_keydown", "sprite_on_tap", "on_swipe", "bcm_translator_text_event_block"}


def new_id():
    return str(uuid.uuid4()).replace("-", "")[:20]


def translate_block_type(t):
    return t  # Uk 映射表里没有 procedures_2_*


FIELD_NAME_MAP = {
    "sound_id": "audio_id", "target": "sprite", "is_show": "show_hide",
    "screen": "screen_id", "scene": "screen_id", "MESSAGE": "message",
    "broadcast": "message", "sprite2": "sprite1", "sprite1": "sprite",
    "position": "type", "axis": "target", "actions": "type", "state": "showHide",
    "info": "type", "TYPE": "item", "FUNC": "show_hide", "PROPERTY": "type",
    "status": "camera_status", "actor": "sprite", "lang": "type",
    "self_prev_next_style": "prev_next", "prev_or_next": "prev_next",
}


def map_field_name(btype, name):
    if name == "VAR":
        return "variable" if ("variables" in btype or "variable" in btype) else "list"
    if name == "valname":
        return "variable" if ("variables" in btype or "variable" in btype) else "list"
    if name == "OP" and btype == "logic_compare":
        return "OP"
    if name == "OP":
        return "type"
    return FIELD_NAME_MAP.get(name, name)


def parse_blocks(bd):
    """Yk.parseBlock: 返回顶层积木树列表"""
    blocks = bd.get("blocks") or {}
    conns = bd.get("connections") or {}

    def build(bid):
        s = blocks.get(bid)
        if not s:
            return None
        btype = translate_block_type(s["type"])
        node = {
            "type": btype,
            "id": s["id"],
            "location": s.get("location"),
            "mutation": s.get("mutation", ""),
        }
        if s.get("is_shadow"):
            node["is_shadow"] = True
        fields = {map_field_name(s["type"], k): v for k, v in (s.get("fields") or {}).items()}
        if fields:
            node["fields"] = fields
        if s.get("shadows"):
            node["shadows"] = dict(s["shadows"])

        for cid, c in (conns.get(bid) or {}).items():
            if c.get("type") == "next":
                node["next"] = build(cid)
            elif c.get("type") == "input" and c.get("input_name"):
                z = build(cid)
                if not z:
                    continue
                z["parent_id"] = bid
                iname = map_field_name(btype, c["input_name"])
                if btype in TO_NEXT_TYPES:
                    node["next"] = z
                elif iname in STATEMENT_INPUTS.get(btype, ()):
                    node.setdefault("statements", {})[iname] = z
                else:
                    node.setdefault("inputs", {})[iname] = z
                # defnoreturn 的 STACK 在源码里先落进 inputs，Xk 再搬到 statements
        return node

    child_ids = {cid for m in conns.values() for cid in m}
    out = []
    for bid in blocks:
        if bid not in child_ids:
            n = build(bid)
            if n:
                out.append(n)
    return out


def walk(node):
    if not node:
        return
    yield node
    for v in (node.get("inputs") or {}).values():
        yield from walk(v)
    for v in (node.get("statements") or {}).values():
        yield from walk(v)
    if node.get("next"):
        yield from walk(node["next"])


def find_return_value(node):
    """Wk()"""
    for n in walk(node):
        if n["type"] == RETURN_VALUE and (n.get("inputs") or {}).get("VALUE"):
            return n
    return None


def find_any_return_value(node):
    for n in walk(node):
        if n["type"] == RETURN_VALUE:
            return n
    return None


def vk(actor_like):
    """Vk(): 拆出函数定义块"""
    tops = parse_blocks(actor_like.get("block_data_json") or {})
    procs = [b for b in tops if b["type"] == DEF_TYPE]
    others = [b for b in tops if b["type"] != DEF_TYPE]
    return procs, others


def xk(def_block, procs_dict, log, owner=""):
    """Xk(): 注册 proceduresDict 条目"""
    name = (def_block.get("fields") or {}).get("NAME", "")
    params = [{"id": new_id(), "type": "Label", "name": name}]

    inputs = def_block.get("inputs")
    if inputs:
        idx = []
        for k, v in inputs.items():
            if k.startswith("PARAMS") and v and v.get("type") == STABLE_PARAM:
                idx.append((int(k.replace("PARAMS", "") or 0), v))
        for _, v in sorted(idx, key=lambda x: x[0]):
            pname = (v.get("fields") or {}).get("param_name")
            if pname:
                params.append({"id": v["id"], "type": "String", "name": pname})

        if inputs.get("STACK"):
            def_block.setdefault("statements", {})["STACK"] = inputs["STACK"]
            del inputs["STACK"]

        stack = (def_block.get("statements") or {}).get("STACK")
        if stack:
            for n in walk(stack):
                for iv in (n.get("inputs") or {}).values():
                    if iv and iv.get("type") == PARAM:
                        pname = (iv.get("fields") or {}).get("param_name")
                        hit = next((p for p in params if p["name"] == pname), None)
                        if hit:
                            iv["mutation"] = '<mutation xmlns="http://www.w3.org/1999/xhtml" id="%s"></mutation>' % hit["id"]
                            iv["is_output"] = True
                        else:
                            log.append("找不到名为 %s 的参数" % pname)

    arg_xml = "".join('<arg id="%s" name="%s" type="%s"></arg>' % (p["id"], p["name"], p["type"]) for p in params)

    body = dict(def_block)
    body["mutation"] = '<mutation xmlns="http://www.w3.org/1999/xhtml">%s</mutation>' % arg_xml
    body["fields"] = {"NAME": body["id"]}
    body["deletable"] = False
    body["editable"] = False

    if find_return_value(def_block):
        # 有返回值：拆成同名两条 (NORMAL 无 return 内容 / ROUND 带 return)
        normal = json.loads(json.dumps(body))
        normal_id = normal["id"]
        round_ = json.loads(json.dumps(body))
        round_["id"] = new_id()
        procs_dict[normal_id] = {
            "id": normal_id, "name": name, "type": "NORMAL",
            "params": params, "nekoBlockJsonList": [normal], "owner": owner,
        }
        procs_dict[round_["id"]] = {
            "id": round_["id"], "name": name, "type": "ROUND",
            "params": params, "nekoBlockJsonList": [round_], "owner": owner,
        }
        return "NORMAL+ROUND", name
    procs_dict[body["id"]] = {
        "id": body["id"], "name": name, "type": "NORMAL",
        "params": params, "nekoBlockJsonList": [body], "owner": owner,
    }
    return "NORMAL", name


def jk(actor_like, procs_dict, stat, owner=""):
    """Jk(): 把调用块按 name 回填 def id"""

    def fix(node):
        if not node:
            return node
        if node["type"] in (CALL_NORETURN, CALL_RETURN):
            cname = (node.get("fields") or {}).get("NAME")
            hit = next((v for v in procs_dict.values() if v["name"] == cname), None)
            stat["calls"] += 1
            if hit is None:
                stat["unresolved"].append((cname, node["type"]))
            else:
                same = (hit["name"] == cname)
                if node["type"] == CALL_RETURN:
                    if hit["type"] == "ROUND":
                        stat["callreturn_to_ROUND"] += 1
                    else:
                        stat["callreturn_to_" + hit["type"]] += 1
                        stat["mismatch"].append((cname, node["type"], hit["type"]))
                else:
                    stat["callnoreturn_to_" + hit["type"]] += 1
                if hit.get("owner") and owner and hit["owner"] != owner:
                    stat["cross_owner"].append((owner, cname, hit["owner"]))
                node["fields"] = {"NAME": hit["id"]}
                stat["rebound"] += 1
        for v in (node.get("inputs") or {}).values():
            fix(v)
        for v in (node.get("statements") or {}).values():
            fix(v)
        fix(node.get("next"))
        return node

    for n in (actor_like.get("nekoBlockJsonList") or []):
        fix(n)


def as_list(x):
    if isinstance(x, dict):
        return list(x.values())
    return list(x or [])


def run(path):
    d = json.load(open(path, encoding="utf-8"))
    th = d.get("theatre") or {}
    procs_dict = OrderedDict()
    log = []
    stat = Counter(calls=0, rebound=0)
    stat["cross_owner"] = []
    stat["unresolved"] = []
    stat["mismatch"] = []
    targets = []
    for kind in ("scenes", "actors"):
        for obj in as_list(th.get(kind)):
            targets.append((kind, obj))

    defs = []
    for kind, obj in targets:
        if not (obj.get("block_data_json") or {}).get("blocks"):
            continue
        procs, others = vk(obj)
        for p in procs:
            defs.append({
                "owner": "%s:%s" % (kind, obj.get("name")),
                "name": (p.get("fields") or {}).get("NAME"),
                "id": p["id"],
                "has_return": bool(find_any_return_value(p)),
                "params": [k for k in (p.get("inputs") or {}) if k.startswith("PARAMS")],
            })
            reg_type, _ = xk(p, procs_dict, log, "%s:%s" % (kind, obj.get("name")))
            defs[-1]["registered_as"] = reg_type
        obj["nekoBlockJsonList"] = others

    for kind, obj in targets:
        jk(obj, procs_dict, stat, "%s:%s" % (kind, obj.get("name")))

    # 函数体内部的调用：Xk 在 Jk 之前把定义移出脚本，之后 Jk 只遍历 nekoBlockJsonList
    inner = []
    for v in procs_dict.values():
        for b in v["nekoBlockJsonList"]:
            for n in walk(b):
                if n["type"] in (CALL_NORETURN, CALL_RETURN):
                    inner.append(((n.get("fields") or {}).get("NAME"), n["type"]))
    names = Counter(v["name"] for v in procs_dict.values())
    dup = {k: v for k, v in names.items() if v > 1}

    print("=" * 70)
    print("文件:", path)
    print("容器: %d 个 (scenes+actors)" % len(targets))
    print("defnoreturn 定义块: %d" % len(defs))
    for f in defs:
        print("  - %-22s name=%-8s return=%-5s params=%-18s -> %s"
              % (f["owner"], f["name"], f["has_return"], f["params"], f["registered_as"]))
    print("proceduresDict 条目: %d" % len(procs_dict))
    for k, v in procs_dict.items():
        print("   * %-20s %-8s %-7s params=%s" % (k, v["name"], v["type"], [p["name"] for p in v["params"]]))
    print("重名(同名多条): %d %s" % (len(dup), dup))
    print("调用块: %d  已重绑: %d" % (stat["calls"], stat["rebound"]))
    print("  跨角色绑定: %d %s" % (len(stat["cross_owner"]), stat["cross_owner"][:6]))
    print("  callnoreturn -> NORMAL %d / ROUND %d"
          % (stat["callnoreturn_to_NORMAL"], stat["callnoreturn_to_ROUND"]))
    print("  callreturn   -> NORMAL %d / ROUND %d"
          % (stat["callreturn_to_NORMAL"], stat["callreturn_to_ROUND"]))
    print("  绑定错(返回型调用绑到非 ROUND): %d %s" % (len(stat["mismatch"]), stat["mismatch"][:6]))
    print("  未解析(NAME 未变成 id): %d %s" % (len(stat["unresolved"]), stat["unresolved"][:6]))
    print("函数体内调用(Jk 不处理, NAME 保持字符串): %d %s" % (len(inner), inner[:6]))
    if log:
        print("  警告: %s" % log[:6])
    return stat


if __name__ == "__main__":
    for p in sys.argv[1:]:
        run(p)
