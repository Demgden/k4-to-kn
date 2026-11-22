"""K4 -> KN 转换器入口（等价于 python -m k4kn.convert）。"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from k4kn.convert import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
