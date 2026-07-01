"""PhaseMarker：选择类浮层阶段的检测（海克斯 / 神明）。

只判「在不在这个界面」（存在性），**不识别**是哪 3 个海克斯 / 哪 2 个神明——后者要
海克斯 / 神明图标资产（尚未下载）。存在性靠常驻、极轻量的颜色区域信号，抗补丁：

- **海克斯 augment_select**：中间三面板横带的**紫/品红占比**。实测海克斯 0.216，其余阶段
  0.003–0.008（50× 分离）。阈值 0.08。
- **神明 god_select**：全屏**深蓝紫背景占比**。实测神明 0.577，普通 0.02–0.05（10×+）。
  阈值 0.40。再用固定回合 `1-1` 兜底（神明期间顶栏没被调暗、stage-round 读得到）。

判定优先级：先看海克斯（中带紫），再看神明（全屏蓝紫 + 1-1）。两信号用不同区域/色相，
互不干扰（海克斯全屏蓝紫 0.287 < 0.40，不会误判成神明；神明中带紫 0.049 < 0.08）。

注意：阈值从**单帧**真实数据标定（s_00012 海克斯 / s_00001 神明），分离度很大（数十倍），
但仍需下次录制多帧校准（不同海克斯组合 / 不同神明 / 转场动画帧）。
"""
from __future__ import annotations

import cv2
import numpy as np

from ..layout import LayoutMapper


def _purple_frac(bgr: np.ndarray) -> float:
    """紫/品红占比（海克斯面板色）。HSV：H 125–155、够饱和够亮。"""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    m = (h >= 125) & (h <= 155) & (s >= 80) & (v >= 80)
    return float(m.mean())


def _bluepurple_frac(bgr: np.ndarray) -> float:
    """深蓝紫背景占比（神明界面底色）。HSV：H 100–140、饱和。"""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    m = (h >= 100) & (h <= 140) & (s >= 90) & (v >= 40)
    return float(m.mean())


class PhaseMarker:
    def __init__(
        self,
        mapper: LayoutMapper,
        augment_thr: float = 0.08,
        god_thr: float = 0.40,
    ):
        self.mapper = mapper
        self.augment_thr = augment_thr
        self.god_thr = god_thr

    def detect(self, frame: np.ndarray, stage: int | None = None) -> dict:
        """返回 {phase, purple_center, bluepurple_full}。

        phase ∈ {'augment_select', 'god_select', None}。stage 用于神明的 1-1 兜底
        （传入当前时钟的 stage；god 只在 stage 为 None 或 1 时才认，防中途蓝屏误判）。
        """
        band = self.mapper.crop(frame, "markers.augment_band")
        full = self.mapper.crop(frame, "markers.overlay_full")
        pc = _purple_frac(band)
        bp = _bluepurple_frac(full)

        phase = None
        if pc >= self.augment_thr:
            phase = "augment_select"
        elif bp >= self.god_thr and (stage is None or stage <= 1):
            phase = "god_select"
        return {
            "phase": phase,
            "purple_center": round(pc, 3),
            "bluepurple_full": round(bp, 3),
        }
