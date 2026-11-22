"""和官方转换产物逐块对账：同 id 的块，类型/字段/输入名是否一致。

用法：
    python tools/diff_official.py 我的.bcmkn 官方.bcmkn [--k4 原.bcm4]
"""
import argparse
import collections
import json
import os
import sys


def index(path):
    kn = json.load(open(path, encoding="utf-8"))
    out = {}

    def walk(n):
        if not isinstance(n, dict):
            return
        if n.get("id"):
            out.setdefault(n["id"], n)
        if isinstance(n.get("next"), dict):
            walk(n["next"])
        for key in ("inputs", "statements"):
            for v in (n.get(key) or {}).values():
                walk(v)

    for a in (kn.get("actors") or {}).get("actorsDict", {}).values():
        for n in a.get("nekoBlockJsonList") or []:
            walk(n)
    for s in (kn.get("scenes") or {}).get("scenesDict", {}).values():
        for n in s.get("nekoBlockJsonList") or []:
            walk(n)
    for p in (kn.get("procedures") or {}).get("proceduresDict", {}).values():
        for n in p.get("nekoBlockJsonList") or []:
            walk(n)
    return kn, out


def norm_fields(f):
    return {k: str(v) for k, v in (f or {}).items() if str(v) != ""}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("mine")
    ap.add_argument("official")
    ap.add_argument("--k4")
    args = ap.parse_args(argv)

    mine_kn, mine = index(args.mine)
    off_kn, off = index(args.official)

    k4type = {}
    k4shadow = {}
    if args.k4:
        k4 = json.load(open(args.k4, encoding="utf-8"))
        for key in ("scenes", "actors"):
            for o in (k4.get("theatre", {}).get(key) or {}).values():
                d = o.get("block_data_json") or {}
                for bid, b in (d.get("blocks") or {}).items():
                    k4type[bid] = b.get("type")
                    k4shadow[bid] = bool(b.get("is_shadow"))

    # 官方把影子块实体化成真块（math_number / pure_list_get / text ...），
    # 我们存成 shadows XML，所以这几类不算差异
    SHADOWISH = {"math_number", "text", "pure_list_get", "logic_empty", "math_number_with_dial",
                 "broadcast_input", "get_styles", "get_screens", "get_play_audio",
                 "default_value", "list_get", "on_bump_actor_param", "color_size_slider"}

    both = [bid for bid in off if bid in mine]
    only_off = [bid for bid in off if bid not in mine and off[bid].get("type") not in SHADOWISH]
    only_mine = [bid for bid in mine if bid not in off
                 and mine[bid].get("type") not in SHADOWISH]

    same_type = 0
    type_diff = collections.Counter()
    field_diff = collections.Counter()
    field_ok = 0
    for bid in both:
        a, b = mine[bid], off[bid]
        if b.get("type") in SHADOWISH:
            continue
        if a.get("type") == b.get("type"):
            same_type += 1
            fa, fb = norm_fields(a.get("fields")), norm_fields(b.get("fields"))
            if fa == fb:
                field_ok += 1
            else:
                for k in set(fa) | set(fb):
                    if fa.get(k) != fb.get(k):
                        field_diff["%s.%s: 我=%r 官方=%r" % (a["type"], k, fa.get(k), fb.get(k))] += 1
        else:
            type_diff["%s -> 我:%s 官方:%s" % (k4type.get(bid, "?"), a.get("type"), b.get("type"))] += 1

    print("== %s vs 官方 %s" % (os.path.basename(args.mine), os.path.basename(args.official)))
    print("  两边都有的块 %d（官方另有 %d 个影子实体化块没计入）"
          % (len(both), sum(1 for bid in both if off[bid].get("type") in SHADOWISH)))
    print("  类型一致 %d / 比对 %d" % (same_type, len(both) - sum(
        1 for bid in both if off[bid].get("type") in SHADOWISH)))
    if type_diff:
        print("  类型不同的（K4类型 -> 我 / 官方）：")
        for k, n in type_diff.most_common(15):
            print("     x%-4d %s" % (n, k))
    print("  字段完全一致 %d / %d" % (field_ok, same_type))
    if field_diff:
        print("  字段差异 Top：")
        for k, n in field_diff.most_common(15):
            print("     x%-4d %s" % (n, k))
    if only_off:
        print("  官方有我没有（非影子）%d 个，类型：%s"
              % (len(only_off), collections.Counter(off[b].get("type") for b in only_off).most_common(8)))
    print("  我有官方没有 %d 个（K4 里真实存在、官方丢了）" % len(only_mine))
    if only_mine and k4type:
        print("     其中在 K4 里确实是非影子的 %d 个，类型：%s"
              % (sum(1 for b in only_mine if not k4shadow.get(b)),
                 collections.Counter(k4type.get(b) for b in only_mine).most_common(10)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
