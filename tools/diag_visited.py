"""诊断：哪些 K4 块压根没被 Converter.node 访问（= 结构上就没搬），
哪些访问过但最终没出现在产物里（= 后续被替换/克隆掉了）。"""
import json
import sys
import os
import collections

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import k4kn.blocks as blk
from k4kn.convert import convert, Report
from k4kn import maps

K4 = sys.argv[1] if len(sys.argv) > 1 else None
if not K4:
    sys.exit("用法: python tools/diag_visited.py 作品.bcm4")

VISITED = set()
_orig = blk.Converter.node


def node(self, src, bid, parent_id=None, dup=False):
    VISITED.add(bid)
    return _orig(self, src, bid, parent_id, dup)


blk.Converter.node = node


def collect_ids(kn):
    out = set()

    def walk(n):
        if not isinstance(n, dict):
            return
        if n.get("id"):
            out.add(n["id"])
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
    return out


def main():
    k4 = json.load(open(K4, encoding="utf-8"))
    rep = Report()
    kn, rep, conv = convert(k4, rep, verbose=False)
    got = collect_ids(kn)

    blocks, conns = {}, {}
    for key in ("actors", "scenes"):
        for o in (k4["theatre"].get(key) or {}).values():
            d = o.get("block_data_json") or {}
            blocks.update(d.get("blocks") or {})
            conns.update(d.get("connections") or {})

    parents = {}
    for pid, tbl in conns.items():
        for cid, info in tbl.items():
            parents[cid] = (pid, info.get("type"), info.get("input_name"))

    missing = [b for b, v in blocks.items() if not v.get("is_shadow") and b not in got]
    never = [b for b in missing if b not in VISITED]
    lost = [b for b in missing if b in VISITED]

    print("缺失 %d = 从没被访问 %d + 访问过但丢了 %d" % (len(missing), len(never), len(lost)))

    print("\n-- 从没被访问的，按「最近的已访问祖先」分组 --")
    grp = collections.Counter()
    for bid in never:
        cur, anchor, guard = bid, None, 0
        while guard < 30:
            p = parents.get(cur)
            if not p:
                break
            if p[0] in VISITED:
                anchor = (p[0], p[1], p[2])
                break
            cur = p[0]
            guard += 1
        if anchor is None:
            grp[("<未连接>", "", "")] += 1
        else:
            ab = blocks.get(anchor[0]) or {}
            kn_t = maps.kn_type_of(ab.get("type") or "")
            grp[(ab.get("type"), anchor[1], anchor[2])] += 1
    for k, n in grp.most_common(20):
        print("   x%-4d 父=%s 连接=%s/%s" % (n, k[0], k[1], k[2]))

    print("\n-- 访问过但丢了的，按类型 --")
    c = collections.Counter(blocks[b].get("type") for b in lost)
    for k, n in c.most_common(20):
        print("   x%-4d %s" % (n, k))


if __name__ == "__main__":
    main()
