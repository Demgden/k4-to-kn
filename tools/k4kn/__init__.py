"""K4 -> KN (kitten4 bcm4 -> neko bcmkn) converter.

Modules:
    maps     官方映射表（类型/字段/取值/降级文案）
    schema   KN 积木 schema（来自 KN 编辑器 blocksOptions）
    blocks   K4 扁平图 -> KN 嵌套积木树
    procedures  函数（proceduresDict）收集与重绑
    convert  顶层编排 + CLI
"""
__all__ = ["maps", "schema", "blocks", "procedures", "convert"]
