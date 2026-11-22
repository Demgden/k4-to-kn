"""KN 积木 schema（blocksOptions）。

数据来自 KN 编辑器打包的 block config 表：
    { 中文名: {type, args:[{type:'input_field'|'input_value'|'input_statement', name, check, options, embedBlock, ...}] } }
另有 topBlocks（帽子积木）、returnValues（返回值积木）、shadowDict（默认影子 XML）。

这个 schema 是「目标格式的唯一权威」：转换出来的每个 input/field 名，
都能在对应积木的 args 里找到，否则 KN 载入时会静默丢数据。
"""
import json
import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(_HERE, "data", "kncfg.json"), encoding="utf-8") as f:
    _C = json.load(f)

_BY_TYPE = {}
for _cn, _cfg in _C["blocksOptions"].items():
    _t = _cfg.get("type")
    if _t and _t not in _BY_TYPE:
        _BY_TYPE[_t] = _cfg

TOP_BLOCKS = set(_C["topBlocks"])
RETURN_VALUES = {tuple(x) if isinstance(x, list) else x for x in _C["returnValues"]}
SHADOW_DICT = _C["shadowDict"]
# embedBlock 里点名的内嵌影子积木（pure_list_get / broadcast_input / get_styles …）。
# 它们不在 blocksOptions 的积木清单里，但引擎照样认识 —— 是「参数槽的默认影子块」。
EMBED_BLOCKS = {a["embedBlock"]["blockName"]
                for cfg in _C["blocksOptions"].values()
                for a in (cfg.get("args") or [])
                if (a.get("embedBlock") or {}).get("blockName")}
EMBED_BLOCKS |= {"shadow_number", "shadow_text", "logic_empty"}
# 引擎认识的积木类型全集：blocksOptions(207) + 隐藏块(变量/列表/影子/函数/降级块)
# + 转换器源码里那份 returnValues 名单。产物里出现名单外的类型，KN 编译会报「未知指令」。
KNOWN_TYPES = set(_C.get("KNOWN_TYPES") or []) | EMBED_BLOCKS

XMLNS = 'xmlns="http://www.w3.org/1999/xhtml"'


def config(kn_type):
    return _BY_TYPE.get(kn_type)


def has_schema(kn_type):
    return kn_type in _BY_TYPE


def args(kn_type):
    cfg = _BY_TYPE.get(kn_type)
    return list(cfg.get("args") or []) if cfg else None


def field_args(kn_type):
    a = args(kn_type)
    return [x["name"] for x in a if x.get("type") == "input_field"] if a is not None else None


def value_args(kn_type):
    a = args(kn_type)
    return [x["name"] for x in a if x.get("type") == "input_value"] if a is not None else None


def statement_args(kn_type):
    a = args(kn_type)
    return [x["name"] for x in a if x.get("type") == "input_statement"] if a is not None else None


def arg_spec(kn_type, name):
    for x in (args(kn_type) or []):
        if x.get("name") == name:
            return x
    return None


def has_embed(kn_type, name):
    spec = arg_spec(kn_type, name)
    return bool(spec and spec.get("embedBlock"))


def embed_of(kn_type, name):
    spec = arg_spec(kn_type, name)
    return spec.get("embedBlock") if spec else None


def default_shadow(kn_type, name):
    """KN 自己声明的默认影子 XML（math_number / 滑杆 / 下拉等等）。"""
    table = SHADOW_DICT.get(kn_type)
    if not table:
        return None
    xml = table.get(name)
    if xml and "xmlns=" not in xml:
        xml = xml.replace("<shadow", "<shadow " + XMLNS, 1)
    return xml


def is_top_block(kn_type):
    return kn_type in TOP_BLOCKS


def returns_value(kn_type):
    return kn_type in RETURN_VALUES


# 动态展开积木：args 只声明第一个，其余靠 mutation 长出来
_DYNAMIC = {
    "controls_if": {"input_value": r"IF\d+", "input_statement": r"DO\d+|ELSE"},
    "text_join": {"input_value": r"ADD\d*"},
    "text_split": {"input_value": r"SPLIT\d*"},
    "list_get": {"input_value": r"ADD\d*"},
}


# blocksOptions 之外、KN 运行时照样认的冗余字段。
# 依据：官方转换器（KN 自己的导入通道）产出的文件里，坐标/外观/造型这几个
# 「…of_sprite」积木都同时写了 attribute 镜像字段和 style 选择字段。
EXTRA_FIELDS = {
    "coordinate_of_sprite": {"attribute"},
    "appearance_of_sprite": {"attribute"},
    "style_of_sprite": {"attribute", "style"},
}


def accepts(kn_type, kind, name):
    """name 是否是 KN 该积木在 kind 桶里认的参数名。None 表示无 schema（不做约束）。"""
    if kn_type.startswith("bcm_translator_text_"):
        # 降级文字块的字段由 mutation 里的占位符决定，没有声明式参数表
        return None
    cfg = _BY_TYPE.get(kn_type)
    if cfg is None:
        return None
    want = [a["name"] for a in (cfg.get("args") or []) if a.get("type") == kind]
    if name in want:
        return True
    if kind == "input_field" and name in EXTRA_FIELDS.get(kn_type, ()):
        return True
    dyn = _DYNAMIC.get(kn_type, {}).get(kind)
    if dyn and re.fullmatch(dyn, name or ""):
        return True
    exp = cfg.get("expansionArg")
    if exp and exp.get("type") == kind and name and name.startswith(exp.get("name", "")):
        tail = (name[len(exp.get("name")):] or "0")
        if tail.isdigit():
            return True
    return False


def expand_prefix(kn_type, kind):
    cfg = _BY_TYPE.get(kn_type) or {}
    exp = cfg.get("expansionArg")
    if exp and exp.get("type") == kind:
        return exp.get("name")
    return None

