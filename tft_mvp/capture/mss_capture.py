"""MSSCapture：用 python-mss 实时抓屏（Windows 上线用）。

注意是 python-mss，不是 mslib。每个 mss 实例需在使用它的线程里创建。
"""
from __future__ import annotations

import cv2
import numpy as np

from .base import ScreenCapture


class MSSCapture(ScreenCapture):
    """抓取指定显示器整屏。

    monitor 索引遵循 mss 约定：
      0 = 所有显示器拼合，1 = 第一个物理屏，2 = 第二个屏 …
    云顶请全屏 / 无边框全屏运行，使「整屏 == 游戏画面」，归一化 ROI 才对得上。
    """

    def __init__(self, monitor: int = 1):
        import mss  # 延迟导入：Linux 无显示环境时不至于 import 即失败

        self._sct = mss.mss()
        mons = self._sct.monitors
        if not (0 <= monitor < len(mons)):
            raise ValueError(f"显示器索引 {monitor} 越界，可用 0..{len(mons) - 1}")
        self.monitor_index = monitor
        self.region = mons[monitor]

    def grab(self) -> np.ndarray:
        raw = self._sct.grab(self.region)
        img = np.asarray(raw)  # BGRA
        return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)

    @staticmethod
    def list_monitors() -> list[dict]:
        import mss

        with mss.mss() as sct:
            return list(sct.monitors)
