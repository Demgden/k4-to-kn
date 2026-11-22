"""校验转换产物：KN schema 合法性 + 与 K4 原文件的对账。

用法：
    python tools/validate_kn.py 输出.bcmkn [--k4 原文件.bcm4] [--official 官方转换.bcmkn]
"""
import argparse
import collections
import json
import re
import sys
import os
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from k4kn import schema  # noqa: E402

def walk(node, acc):
    if not isinstance(node, dict):
        return
    acc.append(node)
    if isinstance(node.get("next"), dict):
        walk(node["next"], acc)
    for key in ("inputs", "statements"):
        for v in (node.get(key) or {}).values():
            walk(v, acc)


def collect(kn):
    nodes = []
    for a in (kn.get("actors") or {}).get("actorsDict", {}).values():
        for b in a.get("nekoBlockJsonList") or []:
            walk(b, nodes)
    for s in (kn.get("scenes") or {}).get("scenesDict", {}).values():
        for b in s.get("nekoBlockJsonList") or []:
            walk(b, nodes)
    proc_nodes = []
    for p in (kn.get("procedures") or {}).get("proceduresDict", {}).values():
        for b in p.get("nekoBlockJsonList") or []:
            walk(b, proc_nodes)
    return nodes, proc_nodes


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("kn")
    ap.add_argument("--k4")
    ap.add_argument("--official")
    args = ap.parse_args(argv)

    kn = json.load(open(args.kn, encoding="utf-8"))
    nodes, proc_nodes = collect(kn)
    allnodes = nodes + proc_nodes
    problems = collections.Counter()
    examples = collections.defaultdict(list)

    def bad(kind, detail):
        problems[kind] += 1
        if len(examples[kind]) < 5:
            examples[kind].append(detail)

    ids = collections.Counter(n.get("id") for n in allnodes)
    dup = [i for i, c in ids.items() if c > 1 and i]
    if dup:
        bad("重复 block id", "%d 个，例如 %s" % (len(dup), dup[:3]))

    types = collections.Counter(n.get("type") for n in allnodes)
    for n in allnodes:
        t = n.get("type")
        if not t:
            bad("缺 type", str(n.get("id")))
            continue
        known = (schema.has_schema(t) or t in schema.KNOWN_TYPES
                 or t.startswith("bcm_translator_text_"))
        if not known:
            bad("KN 不认识的积木类型", t)
        for key, kind in (("fields", "input_field"), ("inputs", "input_value"), ("statements", "input_statement")):
            for name in (n.get(key) or {}):
                ok = schema.accepts(t, kind, name)
                if ok is False:
                    bad("参数名不被 %s 接受" % kind, "%s.%s" % (t, name))
                elif ok is None:
                    pass

    procs = (kn.get("procedures") or {}).get("proceduresDict", {})
    for pid, p in procs.items():
        if p.get("id") != pid:
            bad("proceduresDict 键与 id 不一致", pid)
        for b in p.get("nekoBlockJsonList") or []:
            if (b.get("fields") or {}).get("NAME") != pid:
                bad("函数头 NAME 与条目 id 不一致", "%s != %s" % ((b.get("fields") or {}).get("NAME"), pid))
        margs = re.findall(r'<arg id="([^"]+)"', (p["nekoBlockJsonList"][0].get("mutation") if p.get("nekoBlockJsonList") else "") or "")
        declared = [x["id"] for x in p.get("params") or []]
        if margs and margs != declared:
            bad("mutation arg 与 params 不一致", pid)

    for n in allnodes:
        if n.get("type") in ("procedures_2_callnoreturn", "procedures_2_callreturn"):
            name = (n.get("fields") or {}).get("NAME")
            if name not in procs:
                bad("调用指向不存在的函数", name)

    # mutation / shadows 都是 XML 片段，KN 载入时会直接解析，坏一个就整块丢
    badxml = 0
    for n in allnodes:
        for xml in [n.get("mutation")] + list((n.get("shadows") or {}).values()):
            if not isinstance(xml, str) or not xml.strip():
                continue
            try:
                ET.fromstring(xml.replace('xmlns="http://www.w3.org/1999/xhtml"', ""))
            except ET.ParseError as exc:
                badxml += 1
                if badxml <= 3:
                    bad("mutation/shadows 不是合法 XML", "%s: %s" % (n.get("type"), exc))
    if not badxml:
        print("  XML 片段（mutation/shadows）全部可解析")

    # 变量/列表引用对账：block 字段里写的 id 必须在 variablesDict 里存在
    vd = (kn.get("variables") or {}).get("variablesDict", {})
    refs = collections.Counter()
    dangling = collections.Counter()
    shadow_ref = re.compile(r'<field name="(list|variable)">([^<]+)</field>')
    for n in allnodes:
        for key in ("variable", "list"):
            val = (n.get("fields") or {}).get(key)
            if not isinstance(val, str) or not val:
                continue
            refs[key] += 1
            if val not in vd:
                dangling["%s=%s" % (key, val[:12])] += 1
        for xml in (n.get("shadows") or {}).values():
            if not isinstance(xml, str):
                continue
            for kind, val in shadow_ref.findall(xml):
                refs[kind] += 1
                if val not in vd:
                    dangling["%s=%s" % (kind, val[:12])] += 1
    vtypes = collections.Counter(v.get("type") for v in vd.values())
    if dangling:
        bad("变量引用指向不存在的定义", "%d 处，例如 %s" % (sum(dangling.values()),
                                                        list(dangling)[:3]))

    # 广播引用对账：积木里写的消息名要在场景的 broadcastsDict 列表里
    bd = (kn.get("broadcasts") or {}).get("broadcastsDict", {})
    all_msgs = {m for v in bd.values() for m in (v or [])}
    msg_ref = re.compile(r'<field name="(?:message|MESSAGE)">([^<]*)</field>')
    missing_msg = collections.Counter()
    msg_count = 0
    for n in allnodes:
        names = []
        for key in ("message", "MESSAGE"):
            val = (n.get("fields") or {}).get(key)
            if isinstance(val, str) and val:
                names.append(val)
        for xml in (n.get("shadows") or {}).values():
            if isinstance(xml, str):
                names += msg_ref.findall(xml)
        for m in names:
            msg_count += 1
            if m not in all_msgs:
                missing_msg[m] += 1
    if missing_msg:
        bad("广播名不在 broadcastsDict 里", "%d 处，例如 %s"
            % (sum(missing_msg.values()), list(missing_msg)[:3]))

    print("== %s" % os.path.basename(args.kn))
    print("  角色 %d / 场景 %d / 函数 %d / 变量 %d"
          % (len(kn["actors"]["actorsDict"]), len(kn["scenes"]["scenesDict"]), len(procs),
             len(vd)))
    if vd:
        print("  变量类型分布：%s" % dict(vtypes))
        print("  变量引用 %d 处（variable %d / list %d），全部命中定义：%s"
              % (sum(refs.values()), refs["variable"], refs["list"], not dangling))
    if kn.get("timerPosition"):
        print("  timerPosition: %s" % kn["timerPosition"])
    print("  广播 %d 个，被引用 %d 处（缺失 %d 处）"
          % (sum(len(v) for v in bd.values()), msg_count, sum(missing_msg.values())))
    print("  脚本积木节点 %d / 函数体积木 %d" % (len(nodes), len(proc_nodes)))
    print("  类型数 %d，Top10: %s" % (len(types), types.most_common(10)))
    if args.official:
        off = json.load(open(args.official, encoding="utf-8"))
        onodes, oproc = collect(off)
        print("  官方产物：节点 %d（函数体 %d），函数 %d，变量 %d"
              % (len(onodes), len(oproc), len((off.get("procedures") or {}).get("proceduresDict", {})),
                 len(off["variables"]["variablesDict"])))
        mine = collections.Counter(n.get("type") for n in nodes)
        theirs = collections.Counter(n.get("type") for n in onodes)
        missing = {k: v for k, v in theirs.items() if mine.get(k, 0) == 0}
        print("  官方有而本实现没有的类型：%s" % (missing or "无"))
        more = {k: (mine.get(k, 0), v) for k, v in theirs.items() if mine.get(k, 0) and mine[k] != v}
        print("  同类数量差异（本实现/官方）：%s" % dict(list(more.items())[:12]))
    if args.k4:
        k4 = json.load(open(args.k4, encoding="utf-8"))
        total = 0
        real = 0
        missing = collections.Counter()
        folded = 0
        out_ids = {n.get("id") for n in allnodes}
        for key in ("scenes", "actors"):
            for o in (k4.get("theatre", {}).get(key) or {}).values():
                blocks = ((o.get("block_data_json") or {}).get("blocks") or {})
                total += len(blocks)
                for bid, b in blocks.items():
                    if b.get("is_shadow"):
                        continue
                    real += 1
                    if bid in out_ids:
                        continue
                    # return 块本身不进 KN，它的 VALUE 表达式被折算到 ROUND 函数上
                    if b.get("type") == "procedures_2_return_value":
                        folded += 1
                    else:
                        missing[b.get("type")] += 1
        print("  K4 积木总数 %d（其中非影子 %d） -> 输出节点 %d" % (total, real, len(allnodes)))
        if folded:
            print("  · 已折算：%d 个 return 块 -> ROUND 函数的 VALUE 表达式" % folded)
        if missing:
            print("  !! K4 里没搬过来的非影子积木 %d 个：%s" % (sum(missing.values()), missing.most_common(12)))
        else:
            print("  ✓ K4 所有非影子积木都在产物里")

    if problems:
        print("  !! 问题：")
        for kind, n in problems.most_common():
            print("     %-28s x%d  %s" % (kind, n, examples[kind][:3]))
    else:
        print("  ✓ 未发现结构性问题")
    return 0


if __name__ == "__main__":
    sys.exit(main())
