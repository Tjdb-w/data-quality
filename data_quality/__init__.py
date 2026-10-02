"""data_quality: 独立的数据质量规则校验工具（暂不实现血缘追溯）。"""

from .validator import validate

__all__ = ["validate"]
__version__ = "0.1.0"
