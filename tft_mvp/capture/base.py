"""ScreenCapture：平台无关的薄抓屏接口。

下游只吃「图(BGR ndarray) + 分辨率」，不关心来源。
- FileCapture：Linux 开发，从 PNG / 录像帧读。
- MSSCapture：Windows 实时抓屏（上线时实现）。
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np


class ScreenCapture(ABC):
    @abstractmethod
    def grab(self) -> np.ndarray:
        """返回当前帧，BGR uint8 ndarray (H, W, 3)。"""
        raise NotImplementedError
