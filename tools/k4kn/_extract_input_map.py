"""从官方 27637.kittenBcmToNekoBcm.pretty.js 里抠 getMappedName 的类型内输入名映射表。

官方源码不随仓库分发。需要时按 docs/format-notes.md §2 抓一份，默认放在
    src/kn-official/27637.kittenBcmToNekoBcm.pretty.js
再运行：
    python tools/k4kn/_extract_input_map.py [源码路径]

结果合并进 tools/k4kn/data/maps.json 的 INPUT_NAME_MAP，并留一份原始表在
data/mapped_name_raw.json。依赖 node（把 JS 字面量求值后落成 JSON）。

提取出来的映射表已经入库，正常使用转换器**不需要**跑这个脚本。
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
DEFAULT_SRC = os.path.join(ROOT, "src", "kn-official", "27637.kittenBcmToNekoBcm.pretty.js")
OUT = os.path.join(HERE, "data", "maps.json")
RAW = os.path.join(HERE, "data", "mapped_name_raw.json")


def match_brace(s, i):
    depth = 0
    quote = None
    j = i
    while j < len(s):
        ch = s[j]
        if quote:
            if ch == "\\":
                j += 2
                continue
            if ch == quote:
                quote = None
            j += 1
            continue
        if ch in "\"'":
            quote = ch
            j += 1
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return j
        j += 1
    raise ValueError("unbalanced")


def main(argv):
    src = argv[1] if len(argv) > 1 else DEFAULT_SRC
    if not os.path.exists(src):
        sys.exit("找不到官方转换器源码：%s\n"
                 "先按 docs/format-notes.md §2 抓取，放到这个位置或作为第一个参数传入。" % src)

    s = open(src, encoding="utf-8").read()
    i = s.find('key: "getMappedName"')
    if i < 0:
        sys.exit("这份源码里没有 getMappedName，确认是 27637 模块的格式化产物。")
    i = s.find("var r = {", i)
    start = s.index("{", i)
    end = match_brace(s, start)
    literal = s[start:end + 1]

    tmp = os.path.join(tempfile.gettempdir(), "k4kn_mapped_name.js")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("var R=" + literal + ";\n")
        f.write("require('fs').writeFileSync(%s,JSON.stringify(R));\n"
                % json.dumps(RAW.replace("\\", "/")))
        f.write("console.log('ok',Object.keys(R).length);")

    print(subprocess.run(["node", tmp], capture_output=True, text=True, encoding="utf-8").stdout.strip())

    raw = json.load(open(RAW, encoding="utf-8"))
    # 表值统一成 str -> str
    clean = {}
    for t, m in raw.items():
        if isinstance(m, dict):
            clean[t] = {k: v for k, v in m.items() if isinstance(v, str)}

    data = json.load(open(OUT, encoding="utf-8"))
    data["INPUT_NAME_MAP"] = clean
    json.dump(data, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("types", len(clean), "saved")


if __name__ == "__main__":
    main(sys.argv)
