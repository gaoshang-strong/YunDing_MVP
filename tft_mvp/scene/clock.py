"""TopBarClock：时间轴骨架。

顶栏（stage-round + 倒计时）每帧 eager 读，不走差分闸门——它是心跳。
- stage-round = 单调锚点：只增不减；读到倒退即判坏帧丢弃，沿用上次好值。
- 倒计时 = 阶段内位置（在走=planning，归零→预判切 combat）。

返回字段供 SceneClassifier 与 StateTracker 消费。
"""
from __future__ import annotations

import numpy as np

from ..layout import LayoutMapper
from ..recognize import DigitReader


class TopBarClock:
    def __init__(self, mapper: LayoutMapper, reader: DigitReader):
        self.mapper = mapper
        self.reader = reader
        self.last_stage: int | None = None
        self.last_round: int | None = None

    def reset(self) -> None:
        """新一局（game_start）时清空时间轴锚点。"""
        self.last_stage = None
        self.last_round = None

    def _monotonic_ok(self, stage: int, rnd: int) -> bool:
        """stage-round 不应倒退。允许相等或前进。"""
        if self.last_stage is None:
            return True
        return (stage, rnd) >= (self.last_stage, self.last_round)

    def read(self, frame: np.ndarray) -> dict:
        sr_roi = self.mapper.crop(frame, "top_bar.stage_round")
        cd_roi = self.mapper.crop(frame, "top_bar.countdown")
        sr = self.reader.read_stage_round(sr_roi)
        cd_val, cd_conf = self.reader.read_number(cd_roi)

        out = {
            "stage": self.last_stage,
            "round": self.last_round,
            "countdown": cd_val,
            "sr_confidence": 0.0,
            "cd_confidence": cd_conf,
            "sr_status": "miss",  # ok / rejected(单调) / miss
        }
        if sr is not None:
            stage, rnd, conf = sr
            if self._monotonic_ok(stage, rnd):
                self.last_stage, self.last_round = stage, rnd
                out.update(stage=stage, round=rnd, sr_confidence=conf, sr_status="ok")
            else:
                out["sr_status"] = "rejected"  # 倒退 → 坏帧，沿用 last
        return out
