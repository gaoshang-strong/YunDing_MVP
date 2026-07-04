"""PhaseMarker：选择类浮层阶段的检测（海克斯 / 加载 / 神明介绍 / 神明祝福）。

只判「在不在这个界面」（存在性），**不识别**是哪 3 个海克斯 / 哪 2 个祝福——后者要资产/文字。

**信号 = 结构 + 颜色 + 回合先验 + 迟滞**，抗补丁、不需资产：

- **海克斯 augment_select**（`2-1/3-2/4-2`，完整局实测确认）：中间横带有 **3 个等宽面板**
  （结构信号，实测 0.26/0.26/0.26），且全屏蓝紫占比不高（见仲裁）。
- **全屏深蓝紫 ≥ 0.40 = 神明领域 / 加载**，按时钟上下文细分三类：
  - `loading`：还没读到过 stage（开局加载画面，紫底玩家名片，bp≈0.47）；
  - `god_intro`：stage ≤ 1（`1-1` 神明介绍——只告知本局两位神明，**无决策**）；
  - `god_boon`：round == 4 且 stage ≥ 2（`2-4/3-4/4-4` 神明祝福 2 选 1）；
  - 都不满足 → None（宁缺勿错；实测 `4-7→5-1` 转场有 7 帧紫屏，就该拒掉）。

**god_boon 回合内锁存（实测标定）**：三个神明回合形态一致——回合开头 ~4 帧领域高蓝紫
（0.5–0.65），随后 **2 选 1 浮层反而把画面压暗到 bp 0.26–0.34**（与真海克斯 0.18–0.30
重叠，不能降阈值），选完回到走动阶段（0.47–0.58）。所以 bp 只在「进领域」时可靠：
连续 `boon_on` 帧 bp≥thr 且 round==4 → **锁存整回合为 god_boon**，直到回合号推进才解除。
决策浮层（恰在回合开头 cd~32–24）由锁存覆盖。普通 x-4（5-4/6-4，神明系列 4-4 选满即止）
bp ≤ 0.15，不会误锁。

**仲裁（实测标定）**：神明领域偶发凑出 3 等宽面板（2-4 有连续 2 帧），但真海克斯的全屏
蓝紫 ≤ 0.30、神明领域 ≥ 0.55 → `bp >= god_thr` 时不判海克斯。再叠**迟滞**：海克斯需连续
`aug_on` 帧确认才激活（吞掉偶发误判），激活后允许 `aug_off`-1 帧内的读数抖动（浮层动画
会让单帧结构检测间歇失败，实测坏帧最长连续 2 帧）。

**历史教训**：最初用「中带紫色占比 ≥0.08」判海克斯，单帧分离 50×，多帧翻车——把神明
领域回合（x-4，当时误以为是选秀转盘；Set 17 实测**没有选秀**）一起误判。后改「结构 +
stage≤1 gate」，完整局又发现 gate 语义拧了：把真实的 x-4 神明祝福回合全压掉、命中的
反而是加载画面。单帧标定的阈值/先验必须拿整局数据回归。

阈值 / ROI 标定来源：`s_00012`（海克斯）、`s_00001`（神明）、完整局 track.json（37 回合）。
"""
from __future__ import annotations

import cv2
import numpy as np

from ..layout import LayoutMapper

# 海克斯回合先验（Set 17 完整局实测确认 = 传统三次；仅作软校验输出，不硬 gate）。
# 注意：海克斯浮层可能在回合切换瞬间弹出并调暗顶栏（4-2 实测），此时时钟锚点
# 还停在上一回合（显示 4-1）→ at_augment_round 会假 False，只能参考不能一票否决。
_AUGMENT_ROUNDS = {(2, 1), (3, 2), (4, 2)}

# 神明祝福回合 = x-4（stage ≥ 2；完整局实测 2-4/3-4/4-4，4-4 选满后 5-4/6-4 是普通回合，
# 但普通 x-4 的 bp 很低（≤0.15）不会触发，故 round==4 作硬 gate 是安全的）
_GOD_BOON_ROUND = 4


def _bluepurple_frac(bgr: np.ndarray) -> float:
    """深蓝紫背景占比（神明领域 / 加载画面底色）。HSV：H 100–140、饱和。"""
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
    """有状态（海克斯迟滞跨帧计数）——新一局要 reset()。"""

    def __init__(
        self,
        mapper: LayoutMapper,
        god_thr: float = 0.40,
        aug_on: int = 3,
        aug_off: int = 3,
        boon_on: int = 2,
    ):
        self.mapper = mapper
        self.god_thr = god_thr
        self.aug_on = aug_on    # 连续 N 帧检出才激活海克斯（吞偶发误判）
        self.aug_off = aug_off  # 激活后连续 N 帧检不出才退出（容忍浮层动画坏帧）
        self.boon_on = boon_on  # 连续 N 帧高蓝紫才锁存神明回合（领域期实测连续 4+ 帧）
        self.reset()

    def reset(self) -> None:
        """新一局（game_start）清空迟滞 / 锁存状态。"""
        self._aug_active = False
        self._on_streak = 0
        self._off_streak = 0
        self._boon_streak = 0
        self._boon_sr: tuple[int, int] | None = None  # 锁存的神明回合 (stage, round)

    def detect(
        self, frame: np.ndarray, stage: int | None = None, rnd: int | None = None
    ) -> dict:
        """返回 {phase, raw_augment, n_panels, panel_widths, bluepurple_full, at_augment_round}。

        phase ∈ {'augment_select', 'loading', 'god_intro', 'god_boon', None}。
        stage/rnd 传时钟的**最近有效锚点**（浮层调暗顶栏时当前帧读不到，用沿用值）。
        """
        band = self.mapper.crop(frame, "markers.augment_band")
        full = self.mapper.crop(frame, "markers.overlay_full")
        widths = _panel_widths(band)
        bp = _bluepurple_frac(full)

        # 海克斯单帧原始判定：3 等宽面板 + 全屏蓝紫不高（高 → 是神明领域，仲裁掉）
        raw_aug = _is_three_even(widths) and bp < self.god_thr

        # 迟滞：进入要连续 aug_on 帧，退出要连续 aug_off 帧
        if raw_aug:
            self._on_streak += 1
            self._off_streak = 0
        else:
            self._off_streak += 1
            self._on_streak = 0
        if not self._aug_active and self._on_streak >= self.aug_on:
            self._aug_active = True
        elif self._aug_active and self._off_streak >= self.aug_off:
            self._aug_active = False

        # god_boon 回合内锁存：回合号推进即解除；连续 boon_on 帧高蓝紫即锁存
        if self._boon_sr is not None and (stage, rnd) != self._boon_sr:
            self._boon_sr = None
            self._boon_streak = 0
        is_boon_frame = (
            bp >= self.god_thr
            and stage is not None and stage >= 2
            and rnd == _GOD_BOON_ROUND
        )
        if is_boon_frame:
            self._boon_streak += 1
            if self._boon_streak >= self.boon_on:
                self._boon_sr = (stage, rnd)
        else:
            self._boon_streak = 0

        phase = None
        if self._boon_sr is not None and (stage, rnd) == self._boon_sr:
            phase = "god_boon"  # 锁存的神明回合内不可能是海克斯（回合先验互斥）
        elif self._aug_active:
            phase = "augment_select"
        elif bp >= self.god_thr:
            if stage is None:
                phase = "loading"
            elif stage <= 1:
                phase = "god_intro"
            elif rnd == _GOD_BOON_ROUND:
                phase = "god_boon"  # 锁存确认前的即时判定（领域高蓝紫帧）
            # else: 紫屏但对不上任何已知上下文 → None，宁缺勿错

        return {
            "phase": phase,
            "raw_augment": raw_aug,
            "n_panels": len(widths),
            "panel_widths": [round(w, 3) for w in widths],
            "bluepurple_full": round(bp, 3),
            "at_augment_round": (stage, rnd) in _AUGMENT_ROUNDS if stage is not None else None,
        }
