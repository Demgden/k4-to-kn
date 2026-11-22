"""K4 -> KN 映射表。

数据来源（官方 KN 打包产物 static/js/main-vendors.*.js 与 27637.*.js）：
  * TYPE_MAP     K4 积木类型 -> KN 积木类型（缺少的会降级为 bcm_translator_text_*）
  * TEXT_MSG     降级积木的中文文案模板，{value}/{value1} 为占位
  * VALUE_MAP    字段取值字典（__self -> --self, ADD -> add ...）
  * FIELD_MAP    字段名/输入名字典（actor -> sprite, VAR -> variable/list ...）
"""
import json
import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(_HERE, "data", "maps.json"), encoding="utf-8") as f:
    _M = json.load(f)

TYPE_MAP = _M["TYPE_MAP"]
TEXT_MSG = _M["TEXT_MSG"]
VALUE_MAP = _M["VALUE_MAP"]
FIELD_MAP = _M["FIELD_MAP_VAR"]      # 变量/列表类积木的字段映射
FIELD_MAP_LISTS = _M["FIELD_MAP_LISTS"]
INPUT_NAME_MAP = _M.get("INPUT_NAME_MAP", {})   # 官方 getMappedName：按类型精确改输入名

TEXT_BLOCK_PREFIX = "bcm_translator_text_"

# K4 里这些类型等价于 KN 的「降级为文字」占位块
DEGRADED_SUFFIX = {
    "bcm_translator_text_execution_block": "执行",
    "bcm_translator_text_event_block": "事件",
    "bcm_translator_text_return_value_block": "值",
    "bcm_translator_text_return_boolean_block": "条件",
}

# ---------------------------------------------------------------- 特殊类型改写
# 官方 parseBlock 里对同类型不同语义做的拆分（K4 用一个类型 + 字段区分坐标轴）
AXIS_SPLIT = {
    ("self_set_position", "x"): "self_set_position_x",
    ("self_set_position", "y"): "self_set_position_y",
    ("self_change_coordinate", "x"): "self_change_coordinate_x",
    ("self_change_coordinate", "y"): "self_change_coordinate_y",
    ("self_glide_coordinate", "x"): "self_glide_coordinate_x",
    ("self_glide_coordinate", "y"): "self_glide_coordinate_y",
}

# 需要补字段值的类型（官方在 parseBlock 里硬编码）
FORCE_FIELD = {
    "self_disappear": {"value": "disappear"},
    "self_gradually_show_hide": {"show_hide": "hide"},
    "terminate": {"scope": "0"},
    "get_face_age": {"type": "age"},
    "get_emotion_result": {"type": "emotion"},
    "get_face_shape_result": {"type": "faceShape"},
    "self_appear": {"value": "appear"},
}

# ---------------------------------------------------------------- 输入名覆盖
# (K4 类型, K4 输入名) -> KN 参数名。官方靠 mapFieldName 的粗粒度字典，
# 这里按参考工程实测出来的差异补齐，命中率更高且不会串名字。
INPUT_OVERRIDES = {
    ("variables_set", "VALUE"): "value",
    ("change_variable", "n"): "value",
    ("change_variable", "VALUE"): "value",
    ("random", "a"): "A",
    ("random", "b"): "B",
    ("logic_negate", "BOOL"): "logic",
    ("math_round", "NUM"): "num",
    ("math_single", "NUM"): "num",
    ("divisible_by", "NUMBER_TO_CHECK"): "A",
    ("divisible_by", "DIVISOR"): "B",
    ("text_contain", "TEXT1"): "A",
    ("text_contain", "TEXT2"): "B",
    ("lists_get_value", "VAR"): "list",
    ("lists_get_value", "INDEX"): "list_index",
    ("lists_get_value", "ITEM"): "item",
    ("lists_replace", "VAR"): "list",
    ("lists_replace", "INDEX"): "list_index",
    ("lists_replace", "VALUE"): "list_item_value",
    ("lists_insert_value", "VAR"): "list",
    ("lists_insert_value", "INDEX"): "list_index",
    ("lists_insert_value", "VALUE"): "list_item_value",
    ("lists_delete", "VAR"): "list",
    ("lists_delete", "INDEX"): "list_index",
    ("lists_append", "VAR"): "list",
    ("lists_append", "VALUE"): "list_item_value",
    ("lists_length", "VAR"): "list",
    ("lists_index_of", "VAR"): "list",
    ("lists_index_of", "VALUE"): "list_item_value",
    ("lists_is_exist", "VAR"): "list",
    ("lists_is_exist", "VALUE"): "list_item_value",
    ("show_hide_list", "VAR"): "list",
    ("lists_copy", "VAR"): "list",
    ("lists_get", "VAR"): "list",
    ("list_get", "VAR"): "list",
    ("self_ask", "text"): "text",
    ("self_broadcast", "MESSAGE"): "message",
    ("self_listen", "MESSAGE"): "message",
}

# K4 与 KN 同名但语义/参数不同的类型，手工纠偏（官方字典漏掉的）
TYPE_OVERRIDE = {
    "get_timer": "timer",             # KN 的 timer = 「计时器值」返回值积木
}

# 字段值补丁：KN 某些积木除了当前字段，还要求把取值镜像到 attribute 上
# （官方转换器就同时写两份，运行时读哪一份都不会错）
MIRROR_FIELD = {
    "coordinate_of_sprite": "attribute",
    "appearance_of_sprite": "attribute",
}

# 值输入中“列表槽”，KN 要求是 pure_list_get 影子积木而不是普通变量块
LIST_INPUT_ARGS = {"list", "VAR", "list_value"}

# 列表取项方式：K4 用 first/last/all，KN 的下拉只有「第 / 最后一项 / 所有项」。
# first 在 KN 里要选「第」并把索引填 1（K4 的 INDEX 影子本来就带着 1）。
ITEM_VALUE = {"first": "any", "last": "last", "all": "all"}


def kn_type_of(k4_type):
    return TYPE_OVERRIDE.get(k4_type) or TYPE_MAP.get(k4_type, k4_type)


def is_degraded(kn_type):
    return kn_type.startswith(TEXT_BLOCK_PREFIX)


def map_field_name(kn_type, field_name):
    """字段名映射。变量/列表类积木的 VAR 要拆成 variable / list。"""
    if field_name == "OP":
        # 官方字典是 type 相关的：只有 logic_compare 保留 OP，其它改成 type
        return "OP" if kn_type == "logic_compare" else "type"
    if field_name in ("VAR", "valname"):
        if "variable" in kn_type or "variables" in kn_type:
            return "variable"
        if "list" in kn_type:
            return "list"
        return "variable"
    return FIELD_MAP.get(field_name, field_name)


def map_input_name(k4_type, kn_type, input_name):
    table = INPUT_NAME_MAP.get(k4_type)
    if table and input_name in table:
        return table[input_name]
    key = (k4_type, input_name)
    if key in INPUT_OVERRIDES:
        return INPUT_OVERRIDES[key]
    return map_field_name(kn_type, input_name)


def map_value(field_name, value):
    table = VALUE_MAP.get(field_name)
    if not table:
        return value
    return table.get(value, value)


def map_item(value):
    """列表取项方式（K4 的 TYPE 字段 -> KN 的 item 下拉）。"""
    return ITEM_VALUE.get(value, value)


def text_of(k4_type, block=None, resolve=None):
    """降级积木的显示文案。

    模板里的占位符有两种：`{value}`/`{valueN}` 取第 N 个值输入（由 resolve 提供），
    `{name}` 取积木自己的字段。取不到就留空，别把 `{index}` 这种原样贴到积木上。
    """
    tpl = TEXT_MSG.get(k4_type)
    if tpl is None:
        return k4_type
    if isinstance(tpl, dict):            # 多语言表
        tpl = tpl.get("zh-hans") or next(iter(tpl.values()))
    if not tpl:
        return k4_type
    fields = (block or {}).get("fields") or {}

    def repl(m):
        key = m.group(1)
        idx = None
        if key == "value":
            idx = 0
        elif key.startswith("value") and key[5:].isdigit():
            idx = int(key[5:])
        if idx is not None:
            return (resolve(idx) if resolve else "") or ""
        val = fields.get(key)
        return "" if val is None else str(val)

    return re.sub(r"\{(\w+)\}", repl, tpl)
