"""推荐层数据源（外部评级表等）。"""
from .metatft_tiers import TAGS_ZH, load_tiers, translate_tags

__all__ = ["load_tiers", "translate_tags", "TAGS_ZH"]
