"""Pipeline：把一帧截图跑成对局状态 dict。

目前挂了顶栏时钟（TopBarClock）+ 浮层检测（PhaseMarker）+ 决策卡识别
（CardRecognizer，OCR 通道，海克斯/神明窗口内节流运行）。后续按设计依次挂
SceneClassifier / ShopRecognizer …，每个识别器把结果写进 state 的对应块即可，
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
        enable_cards: bool = True,
    ):
        self.mapper = LayoutMapper(profile_path)
        self.reader = DigitReader(template_dir)
        self.clock = TopBarClock(self.mapper, self.reader)
        self.track = ClockTrack()  # 时间轴 track：逐帧记时钟 + 派生事件
        self.marker = PhaseMarker(self.mapper)  # 选择类浮层检测（海克斯 / 加载 / 神明介绍 / 神明祝福）
        # 决策卡识别（OCR + 评级表）：缺 rapidocr 等依赖时降级为 None，感知主链不受影响
        self.cards = None
        self.cards_error: str | None = None
        if enable_cards:
            try:
                from .reco import load_tiers
                from .recognize.cards import CardRecognizer
                self.cards = CardRecognizer(load_tiers())
            except Exception as e:  # noqa: BLE001
                self.cards_error = str(e)
                print(f"[pipeline] 决策卡识别不可用（{e}），decision 块将为空")
        elif not enable_cards:
            self.cards_error = "disabled"
        # 后续：self.scene = SceneClassifier(...); self.shop = ShopRecognizer(...)

    def reset(self) -> None:
        """新一局（game_start）重置累积状态。"""
        self.clock.reset()
        self.track.reset()
        self.marker.reset()  # 海克斯迟滞计数跨帧，有状态
        if self.cards:
            self.cards.reset()

    def process(self, frame: np.ndarray) -> dict:
        h, w = frame.shape[:2]
        ts = int(time.time() * 1000)
        clock = self.clock.read(frame)
        marker = self.marker.detect(frame, stage=clock["stage"], rnd=clock["round"])
        # 决策窗口内节流跑 OCR 识别选项（海克斯 3 选 1 / 神明 2 选 1）
        decision = self.cards.update(frame, marker["phase"]) if self.cards else None
        self.track.update(clock, ts, marker, decision)  # marker/decision 一并记入 track
        # scene：目前只判选择类浮层（海克斯 / 神明）；planning/combat 待 SceneClassifier
        return {
            "timestamp": ts,
            "screen": {"width": int(w), "height": int(h)},
            "scene": marker["phase"],  # augment_select / loading / god_intro / god_boon / None
            "player_state": {
                "stage": clock["stage"],
                "round": clock["round"],
                "countdown": clock["countdown"],
            },
            "decision": decision,  # 选择类阶段的选项 + 评级/中文 tag（推荐引擎直接输入）
            "_cards": {  # 决策卡识别器状态（UI 诊断用：区分「不可用」vs「识别中」）
                "enabled": self.cards is not None,
                "error": self.cards_error,
                "debug": self.cards.debug if self.cards else None,
            },
            "track": self.track.snapshot(),  # 时间轴 track（最近样本 + 事件 + 趋势）
            "_clock": clock,   # 原始（含置信度 / 状态），调试 / UI 用
            "_marker": marker,  # 浮层信号原始值（紫/蓝紫占比），调试 / 校准用
        }
