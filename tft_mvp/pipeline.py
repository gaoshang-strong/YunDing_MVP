"""Pipeline：把一帧截图跑成对局状态 dict。

目前只挂了顶栏时钟（TopBarClock）。后续按设计依次挂 SceneClassifier /
ShopRecognizer / TextRecognizer …，每个识别器把结果写进 state 的对应块即可，
UI（Dashboard）只读 state，无需改动。
"""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from .layout import LayoutMapper
from .recognize import DigitReader
from .scene import ClockTrack, PhaseMarker, TopBarClock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROFILE = Path(__file__).resolve().parent / "layout" / "profiles" / "16_9.json"
DEFAULT_TEMPLATES = PROJECT_ROOT / "assets" / "set17" / "templates" / "topbar"


class Pipeline:
    def __init__(
        self,
        profile_path: str | Path = DEFAULT_PROFILE,
        template_dir: str | Path = DEFAULT_TEMPLATES,
    ):
        self.mapper = LayoutMapper(profile_path)
        self.reader = DigitReader(template_dir)
        self.clock = TopBarClock(self.mapper, self.reader)
        self.track = ClockTrack()  # 时间轴 track：逐帧记时钟 + 派生事件
        self.marker = PhaseMarker(self.mapper)  # 选择类浮层检测（海克斯 / 神明）
        # 后续：self.scene = SceneClassifier(...); self.shop = ShopRecognizer(...)

    def reset(self) -> None:
        """新一局（game_start）重置累积状态。"""
        self.clock.reset()
        self.track.reset()

    def process(self, frame: np.ndarray) -> dict:
        h, w = frame.shape[:2]
        ts = int(time.time() * 1000)
        clock = self.clock.read(frame)
        marker = self.marker.detect(frame, stage=clock["stage"], rnd=clock["round"])
        self.track.update(clock, ts, marker)  # marker 结果也逐帧记入 track，供校准
        # scene：目前只判选择类浮层（海克斯 / 神明）；planning/combat 待 SceneClassifier
        return {
            "timestamp": ts,
            "screen": {"width": int(w), "height": int(h)},
            "scene": marker["phase"],  # augment_select / god_select / None
            "player_state": {
                "stage": clock["stage"],
                "round": clock["round"],
                "countdown": clock["countdown"],
            },
            "track": self.track.snapshot(),  # 时间轴 track（最近样本 + 事件 + 趋势）
            "_clock": clock,   # 原始（含置信度 / 状态），调试 / UI 用
            "_marker": marker,  # 浮层信号原始值（紫/蓝紫占比），调试 / 校准用
        }
