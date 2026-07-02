"""PhaseMarker：选择类浮层阶段的检测（海克斯 / 神明）。

只判「在不在这个界面」（存在性），**不识别**是哪 3 个海克斯 / 哪 2 个神明——后者要资产。

**为什么不用纯颜色占比**：实测（多帧真实对局）海克斯的「中间紫色占比」会把**选秀转盘**、
偶发紫色特效一起误判（选秀的紫色魔法 UI 占比也高）。改用**结构 + 颜色 + 固定回合**：

- **海克斯 augment_select**：中间横带有 **3 个等宽面板**（结构信号）。海克斯是 3 张并排卡片，
  列投影出 3 段等宽紫色区（实测 0.26/0.26/0.26）；选秀是转盘环、普通阶段无面板 → 都不是
  「3 等宽面板」，天然分开。比颜色占比稳得多。
- **神明 god_select**：全屏**深蓝紫背景占比** ≥ 0.40（实测神明 0.52–0.70，普通 0.02–0.05，
  26 帧零误报）+ 固定回合 `1-1` 兜底。神明立绘不规则、结构不干净，故神明用颜色。

**固定回合先验**：海克斯/神明回合固定（传统海克斯约 `2-1/3-2/4-2`、神明 `1-1`）。当前作为
输出里的软校验（`at_augment_round`），不硬 gate（怕补丁改动）；Set 17 确切回合待数据确认。

阈值 / ROI 从真实帧标定（`s_00012` 海克斯、`s_00001` 神明）；仍需多帧继续校准。
"""
from __future__ import annotations

import cv2
import numpy as np

from ..layout import LayoutMapper

# 海克斯回合先验（传统三次；Set 17 待确认，仅作软校验，不 gate）
_AUGMENT_ROUNDS = {(2, 1), (3, 2), (4, 2)}


def _bluepurple_frac(bgr: np.ndarray) -> float:
    """深蓝紫背景占比（神明界面底色）。HSV：H 100–140、饱和。"""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    m = (h >= 100) & (h <= 140) & (s >= 90) & (v >= 40)
    return float(m.mean())


def _panel_widths(band_bgr: np.ndarray, thr: float = 0.15) -> list[float]:
    """在中间横带里数「面板」：紫/蓝紫亮区的列投影 → 连续段。返回各段宽度（占带宽比例）。"""
    hsv = cv2.cvtColor(band_bgr, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    mask = ((s >= 60) & (v >= 70) & (h >= 110) & (h <= 155)).astype(np.float32)
    col = mask.mean(axis=0)
    k = max(3, col.size // 60)
    col = np.convolve(col, np.ones(k) / k, "same")
    on = col > thr
    widths, i, n = [], 0, col.size
    while i < n:
        if on[i]:
            j = i
            while j < n and on[j]:
                j += 1
            if j - i > n * 0.05:  # 够宽才算面板
                widths.append((j - i) / n)
            i = j
        else:
            i += 1
    return widths


def _is_three_even(widths: list[float]) -> bool:
    """恰 3 段、每段宽 0.18–0.35、彼此接近（max/min < 1.6）→ 海克斯 3 卡片。"""
    if len(widths) != 3:
        return False
    if not all(0.18 <= w <= 0.35 for w in widths):
        return False
    return max(widths) / min(widths) < 1.6


class PhaseMarker:
    def __init__(self, mapper: LayoutMapper, god_thr: float = 0.40):
        self.mapper = mapper
        self.god_thr = god_thr

    def detect(
        self, frame: np.ndarray, stage: int | None = None, rnd: int | None = None
    ) -> dict:
        """返回 {phase, n_panels, panel_widths, bluepurple_full, at_augment_round}。

        phase ∈ {'augment_select', 'god_select', None}。
        - 海克斯：中间 3 等宽面板（结构）。
        - 神明：全屏蓝紫 ≥ god_thr 且 stage ≤ 1（防中途蓝屏误判）。
        """
        band = self.mapper.crop(frame, "markers.augment_band")
        full = self.mapper.crop(frame, "markers.overlay_full")
        widths = _panel_widths(band)
        bp = _bluepurple_frac(full)

        phase = None
        if _is_three_even(widths):
            phase = "augment_select"
        elif bp >= self.god_thr and (stage is None or stage <= 1):
            phase = "god_select"

        return {
            "phase": phase,
            "n_panels": len(widths),
            "panel_widths": [round(w, 3) for w in widths],
            "bluepurple_full": round(bp, 3),
            "at_augment_round": (stage, rnd) in _AUGMENT_ROUNDS if stage is not None else None,
        }
