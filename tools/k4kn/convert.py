"""K4 (.bcm4) -> KN (.bcmkn) 转换器。

用法：
    python -m k4kn.convert 输入.bcm4 -o 输出.bcmkn [--report 报告.md] [--dump-json 诊断.json]

设计原则：
  * 以 KN 编辑器自带的 blocksOptions 为唯一 schema 权威，只产出合法参数名；
  * 函数、变量/列表、样式、广播、场景/角色一律完整搬迁，不静默丢；
  * 转不了的类型降级成 KN 的 bcm_translator_text_* 文字块并在报告里点名。
"""
import argparse
import collections
import json
import math
import os
import re
import sys
import time

from . import blocks as blk
from . import procedures

STAGE_W, STAGE_H = 900, 562

VAR_TYPE = {"any": "any", "list": "list", "boolean": "boolean", "number": "number"}
# K4 的 theme 是变量监视器的配色/图标；KN 只有这套图标样式
VAR_STYLE = {
    "score": "icon_medal",
    "HP": "icon_heart",
    "health": "icon_heart",
    "clock": "icon_hourglass",
    "time": "icon_hourglass",
    "coin": "icon_coin",
    "pure": "text",
    "text": "text",
}
# 计时器用到时 KN 会把它摆在右上角（K4 没有这个字段，位置按 KN 默认给）
TIMER_POS = {"x": STAGE_W - 160 - 20, "y": 12}


class Report:
    def __init__(self):
        self.counter = collections.Counter()
        self.detail = collections.Counter()
        self.warnings = []

    def bump(self, key, n=1):
        self.counter[key] += n

    def warn(self, kind, detail):
        self.detail[(kind, detail)] += 1

    def dump(self, path):
        lines = ["# K4 → KN 转换报告", "", "生成时间：%s" % time.strftime("%Y-%m-%d %H:%M:%S"), ""]

        def row(*cells):
            lines.append("| " + " | ".join(str(c) for c in cells) + " |")

        lines += ["## 统计", "", "| 项目 | 数量 |", "| --- | --- |"]
        for k, v in sorted(self.counter.items(), key=lambda x: -x[1]):
            row(k, v)
        lines.append("")
        if self.detail:
            lines += ["## 需要人工看一眼的地方", "", "| 类别 | 明细 | 次数 |", "| --- | --- | --- |"]
            for (kind, detail), n in self.detail.most_common(120):
                row("%s" % kind, "`%s`" % detail, n)
            lines.append("")
        open(path, "w", encoding="utf-8").write("\n".join(lines))


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _uuid_like(s):
    if not isinstance(s, str) or len(s) != 36:
        return False
    parts = s.split("-")
    return [len(p) for p in parts] == [8, 4, 4, 4, 12]


def _num(v, default=0.0):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(f) or math.isinf(f) else f


# --------------------------------------------------------------------- 元数据
def build_styles(k4, report):
    out = {}
    for sid, s in (k4.get("theatre", {}).get("styles") or {}).items():
        url = s.get("cdn_url") or s.get("url") or ""
        item = {
            "id": sid,
            "name": s.get("name") or "",
            "url": url,
            "cdn_url": url,
        }
        for key in ("rotate_center", "pivot", "adaptive", "rotate_center_painter"):
            if key in s:
                item[key] = s[key]
        out[sid] = item
    report.bump("样式 styles", len(out))
    return {"stylesDict": out}


def build_audios(k4, report):
    src = k4.get("audio") or {}
    out, order = {}, []
    for aid, a in src.items():
        item = {
            "id": aid,
            "name": a.get("name") or "",
            "url": a.get("url") or a.get("cdn_url") or "",
        }
        for key in ("ext", "duration", "size", "cdn_url"):
            if key in a:
                item[key] = a[key]
        if a.get("cdn_url"):
            item["url"] = a["cdn_url"]
        out[aid] = item
        order.append(aid)
    report.bump("音频 audios", len(out))
    return {"audiosDict": out, "sortList": order, "currentAudioId": order[0] if order else ""}


def build_variables(k4, report):
    """K4 variables/cloud_variables -> KN variablesDict。

    KN 的 type 取值（编辑器枚举）：any / list / public / private / private_list。
    K4 用 type(any|list) + is_global 两个字段表达同一件事：
        全局  any -> any            list -> list
        角色内 any -> private        list -> private_list（配 currentEntityId）
    KN 的积木按 block 字段里的 id 到 variablesDict 里查，所以 id 必须原样保留。
    """
    out = {}
    seen_names = collections.Counter()
    for vid, v in (k4.get("variables") or {}).items():
        if not isinstance(v, dict):
            continue
        raw_type = v.get("type") or "any"
        if raw_type == "timer":            # K4 的计时器变量由舞台内建，不搬
            continue
        is_global = bool(v.get("is_global", True))
        entity = v.get("current_entity")
        if is_global:
            ktype = VAR_TYPE.get(raw_type, "any")
            entity = None
        else:
            ktype = "private_list" if raw_type == "list" else "private"
        pos = v.get("position") or {}
        name = v.get("name") or ""
        # KN 里同作用域重名会分不清（编辑器按 name+type+entity 反查），补个后缀
        scope = (ktype, entity)
        if name:
            seen_names[(scope, name)] += 1
            if seen_names[(scope, name)] > 1:
                fixed = "%s_%d" % (name, seen_names[(scope, name)])
                report.warn("变量重名（已加后缀）", "%s -> %s" % (name, fixed))
                name = fixed
        item = {
            "id": vid,
            "type": ktype,
            "name": name,
            "value": v.get("value", [] if raw_type == "list" else 0),
            "visible": bool(v.get("visible", False)),
            "position": {
                "x": _num(pos.get("x")) + STAGE_W / 2,
                "y": STAGE_H / 2 - _num(pos.get("y")),
            },
            "isGlobal": is_global,
            "createTime": int(time.time() * 1000),
            "scale": _num(v.get("scale"), 1) or 1,
            "style": VAR_STYLE.get(v.get("theme") or "", "default"),
        }
        if entity:
            item["currentEntityId"] = entity
        out[vid] = item
        report.bump("变量/列表 variables")
    for vid, v in (k4.get("cloud_variables") or {}).items():
        out[vid] = {
            "id": vid,
            "type": "public",
            "name": v.get("name") or "",
            "value": v.get("value", 0),
            "visible": False,
            "position": {"x": STAGE_W / 2, "y": STAGE_H / 2},
            "isGlobal": True,
            "createTime": int(time.time() * 1000),
            "scale": 1,
            "style": "default",
        }
        report.bump("云变量", 1)
    return {"variablesDict": out}


_MSG_FIELD = re.compile(r'<field name="(?:message|MESSAGE)">([^<]*)</field>')


def _used_broadcasts(k4):
    """脚本里真正用到的广播名。"""
    used = set()
    for key in ("actors", "scenes"):
        for o in ((k4.get("theatre") or {}).get(key) or {}).values():
            for b in ((o.get("block_data_json") or {}).get("blocks") or {}).values():
                t = b.get("type") or ""
                if not ("broadcast" in t or "listen" in t or "receive" in t):
                    continue
                for fname in ("MESSAGE", "message", "NAME"):
                    val = (b.get("fields") or {}).get(fname)
                    if isinstance(val, str) and val:
                        used.add(val)
                for xml in (b.get("shadows") or {}).values():
                    if isinstance(xml, str):
                        used.update(m for m in _MSG_FIELD.findall(xml) if m)
    return used


def build_broadcasts(k4, report):
    """K4 broadcasts{sceneId:[names]} -> KN broadcastsDict{sceneId:[names]}。

    K4 工程里这张表经常被写残（实测有个文件里堆了 7 万多条、'Hi' 重复 3 万次），
    KN 的下拉框会被这些死名字灌满，所以只留脚本里真用到的 + 场景自己声明的。
    """
    scenes = list((k4.get("theatre", {}).get("scenes") or {}).keys())
    first = scenes[0] if scenes else ""
    used = _used_broadcasts(k4)
    raw = k4.get("broadcasts") or {}

    out = {sid: [] for sid in scenes}
    seen = set()
    for sid in scenes:
        for name in raw.get(sid) or []:
            if not isinstance(name, str) or not name:
                continue
            if name in used and name not in seen:
                out[sid].append(name)
                seen.add(name)
    for name in sorted(used - seen):
        target = first
        if target:
            out[target].append(name)
            seen.add(name)

    kept = sum(len(v) for v in out.values())
    dropped = len({n for names in raw.values() for n in (names or []) if isinstance(n, str)}) - kept
    if dropped > 0:
        report.warn("广播名没被任何积木使用（已丢弃）", "共 %d 个" % dropped)
    report.bump("广播 broadcasts", kept)
    return {"broadcastsDict": out}


# ------------------------------------------------------------------- 场景角色
def build_scene(k4_scene, actor_ids, nodes):
    s = k4_scene
    return {
        "id": s.get("id"),
        "name": s.get("name") or "背景",
        "actors": list(s.get("actors") or []),
        "x": _num(s.get("x")),
        "y": _num(s.get("y")),
        "scale": _num(s.get("scale"), 100),
        "rotation": _num(s.get("rotation")),
        "rotation_type": s.get("rotation_type", 0),
        "draggable": bool(s.get("draggable")),
        "visible": bool(s.get("visible", True)),
        "workspace_offset": s.get("workspace_offset") or {"x": 0, "y": 0},
        "styles": list(s.get("styles") or []),
        "screen_name": s.get("screen_name") or "",
        "screenName": s.get("screen_name") or "",
        "actorIds": actor_ids,
        "currentStyleId": s.get("current_style_id") or "",
        "workspaceScrollXy": s.get("workspace_offset") or {"x": 0, "y": 0},
        "nekoBlockJsonList": nodes,
    }


def build_actor(k4_actor, nodes):
    a = k4_actor
    scale = _num(a.get("scale"), 100)
    if scale > 1:
        scale = math.floor(scale)
    return {
        "name": a.get("name") or "",
        "scale": scale,
        "rotation": _num(a.get("rotation")),
        "rotation_type": a.get("rotation_type", 0),
        "id": a.get("id"),
        "draggable": bool(a.get("draggable")),
        "lock": bool(a.get("lock")),
        "scene": a.get("scene"),
        "workspace_offset": a.get("workspace_offset") or {"x": 0, "y": 0},
        "visible": bool(a.get("visible", True)),
        "styles": list(a.get("styles") or []),
        "currentStyleId": a.get("current_style_id") or "",
        "position": {"x": _num(a.get("x")), "y": _num(a.get("y"))},
        "workspaceScrollXy": a.get("workspace_offset") or {"x": 0, "y": 0},
        "nekoBlockJsonList": nodes,
    }


def _iter_nodes(kn):
    def walk(n):
        if not isinstance(n, dict):
            return
        yield n
        if isinstance(n.get("next"), dict):
            yield from walk(n["next"])
        for key in ("inputs", "statements"):
            for v in (n.get(key) or {}).values():
                yield from walk(v)

    for a in (kn.get("actors") or {}).get("actorsDict", {}).values():
        for n in a.get("nekoBlockJsonList") or []:
            yield from walk(n)
    for s in (kn.get("scenes") or {}).get("scenesDict", {}).values():
        for n in s.get("nekoBlockJsonList") or []:
            yield from walk(n)
    for p in (kn.get("procedures") or {}).get("proceduresDict", {}).values():
        for n in p.get("nekoBlockJsonList") or []:
            yield from walk(n)


_SHADOW_REF = re.compile(r'<field name="(list|variable)">([^<]+)</field>')


def repair_variable_refs(kn, report):
    """K4 里可能残留指向已删除变量的引用；补一条占位定义，别让积木变空槽。"""
    vd = kn["variables"]["variablesDict"]
    missing = {}
    for n in _iter_nodes(kn):
        val = (n.get("fields") or {}).get("variable")
        if isinstance(val, str) and val and val not in vd:
            missing.setdefault(val, "any")
        for name, xml in (n.get("shadows") or {}).items():
            if not isinstance(xml, str):
                continue
            for kind, ref in _SHADOW_REF.findall(xml):
                if ref in vd or len(ref) < 8:
                    continue
                missing[ref] = "list" if kind == "list" else "any"
    for i, (vid, vtype) in enumerate(sorted(missing.items())):
        vd[vid] = {
            "id": vid,
            "type": vtype,
            "name": ("缺失列表%d" if vtype == "list" else "缺失变量%d") % (i + 1),
            "value": [] if vtype == "list" else 0,
            "visible": False,
            "position": {"x": STAGE_W / 2, "y": STAGE_H / 2},
            "isGlobal": True,
            "createTime": int(time.time() * 1000),
            "scale": 1,
            "style": "default",
        }
        report.warn("K4 源里引用已删除的变量（已补占位）",
                    "%s → %s" % (vid, vd[vid]["name"]))
    return len(missing)


def _uses_timer(k4):
    """作品里有没有用到计时器（决定要不要写 timerPosition）。"""
    if (k4.get("theatre") or {}).get("timer"):
        return True
    for key in ("actors", "scenes"):
        for o in ((k4.get("theatre") or {}).get(key) or {}).values():
            for b in ((o.get("block_data_json") or {}).get("blocks") or {}).values():
                if b.get("type") in ("get_timer", "set_timer_state", "show_hide_timer"):
                    return True
    return False


def actor_order(k4, scene_id):
    """按 K4 图层组顺序排角色，未入组的按原顺序补后面。"""
    theatre = k4.get("theatre") or {}
    actors = theatre.get("actors") or {}
    order = []
    for gid in (theatre.get("scenes", {}).get(scene_id, {}).get("group_order") or []):
        g = (theatre.get("groups") or {}).get(gid) or {}
        for aid in (g.get("actors") or []):
            a = actors.get(aid)
            if a and a.get("scene") == scene_id and aid not in order:
                order.append(aid)
    for aid, a in actors.items():
        if a.get("scene") == scene_id and aid not in order:
            order.append(aid)
    return order


# ------------------------------------------------------------------------- 主流程
def convert(k4, report=None, verbose=True):
    report = report or Report()
    conv = blk.Converter(report=report)

    theatre = k4.get("theatre") or {}
    scenes_raw = theatre.get("scenes") or {}
    actors_raw = theatre.get("actors") or {}

    sources = {}
    nodes_by_id = {}
    for key, kind in (("scenes", "scene"), ("actors", "actor")):
        for oid, o in (theatre.get(key) or {}).items():
            src = blk.Source(o.get("block_data_json"), oid, o.get("name"), kind)
            sources[oid] = src
            nodes_by_id[oid] = conv.entity_roots(src)

    procs, metas = procedures.build(conv)
    unresolved = procedures.rebind(conv, procs, metas)

    report.bump("脚本实体", len(sources))
    report.bump("积木总数", sum(len(s.blocks) for s in sources.values()))
    report.bump("顶层积木", sum(len(v) for v in nodes_by_id.values()))
    report.bump("函数定义", len(metas))
    report.bump("含返回值函数", sum(1 for m in metas if m["has_return"]))
    report.bump("函数调用", len(conv.calls))
    report.bump("未解析调用", len(unresolved))
    for oid, name in unresolved:
        report.warn("未解析函数调用", "%s @ %s" % (name, oid))

    scenes = {}
    for sid, s in scenes_raw.items():
        scenes[sid] = build_scene(s, actor_order(k4, sid), nodes_by_id.get(sid, []))
    actors = {}
    for aid, a in actors_raw.items():
        actors[aid] = build_actor(a, nodes_by_id.get(aid, []))

    current = (theatre.get("scenes_order") or list(scenes))[0] if scenes else ""

    size = k4.get("size") or {}
    w, h = _num(size.get("width"), 1280), _num(size.get("height"), 720)
    stage = {"width": STAGE_W, "height": STAGE_H} if w >= h else {"width": STAGE_H, "height": STAGE_W}

    kn = {
        "projectName": k4.get("project_name") or "转换作品",
        "scenes": {
            "scenesDict": scenes,
            "currentSceneId": current,
            "sortList": list(theatre.get("scenes_order") or scenes.keys()),
        },
        "styles": build_styles(k4, report),
        "stageSize": stage,
        "variables": build_variables(k4, report),
        "broadcasts": build_broadcasts(k4, report),
        "actors": {"actorsDict": actors},
        "audios": build_audios(k4, report),
        "procedures": {"proceduresDict": procs},
        "version": "0.27.4",
        "hidden_toolbox": {"toolbox": [], "blocks": [], "forcibleShowBlocks": [], "isCombine": False},
        "courseMaterials": {"courseMaterialsDict": {}, "sortList": []},
        "previewUrl": "https://creation.bcmcdn.com/neko/assets/image/bcm-preview-url-0.5.1.png",
        "camera": {"status": "default"},
        "guideUrl": "",
        "source": "",
    }

    # 统计降级/映射情况
    for (kind, detail), n in report.detail.items():
        pass
    report.bump("proceduresDict 条目", len(procs))

    # 用到计时器的作品，KN 需要一个摆放位置（K4 没有对应字段）
    if _uses_timer(k4):
        kn["timerPosition"] = dict(TIMER_POS)
        report.bump("计时器位置 timerPosition", 1)

    repaired = repair_variable_refs(kn, report)
    if repaired:
        report.bump("补回的变量定义", repaired)

    if verbose:
        print("[k4kn] 角色 %d / 场景 %d / 函数 %d / 变量 %d / 积木 %d"
              % (len(actors), len(scenes), len(metas),
                 len(kn["variables"]["variablesDict"]), report.counter["积木总数"]))
    return kn, report, conv


def main(argv=None):
    ap = argparse.ArgumentParser(description="K4 bcm4 -> KN bcmkn 转换器")
    ap.add_argument("input")
    ap.add_argument("-o", "--output")
    ap.add_argument("--report")
    args = ap.parse_args(argv)

    k4 = _load(args.input)
    kn, report, conv = convert(k4)
    out = args.output or os.path.splitext(args.input)[0] + ".bcmkn"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(kn, f, ensure_ascii=False, separators=(",", ":"))
    print("[k4kn] 写出", out, "%.1f MB" % (os.path.getsize(out) / 1e6))
    if args.report:
        report.dump(args.report)
        print("[k4kn] 报告", args.report)
    if report.detail:
        print("[k4kn] 需要留意的项：")
        for (kind, detail), n in report.detail.most_common(15):
            print("   %-24s %-40s x%d" % (kind, detail, n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
