"""FileCapture：从单张 PNG 或一个帧目录读取（Linux 开发用）。"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from .base import ScreenCapture


class FileCapture(ScreenCapture):
    """从静态图片读帧。

    - 传单个文件：每次 grab() 都返回它。
    - 传目录：按文件名排序，grab() 依次返回下一张，读完抛 StopIteration。
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        if self.path.is_dir():
            self._files = sorted(self.path.glob("*.png"))
            if not self._files:
                raise FileNotFoundError(f"目录无 png：{self.path}")
        else:
            if not self.path.exists():
                raise FileNotFoundError(self.path)
            self._files = [self.path]
        self._i = 0

    def __len__(self) -> int:
        return len(self._files)

    def grab(self) -> np.ndarray:
        if self._i >= len(self._files):
            if len(self._files) == 1:
                self._i = 0  # 单文件循环
            else:
                raise StopIteration("帧目录已读完")
        f = self._files[self._i]
        self._i += 1
        img = cv2.imread(str(f))
        if img is None:
            raise IOError(f"读图失败：{f}")
        return img

    @staticmethod
    def read(path: str | Path) -> np.ndarray:
        img = cv2.imread(str(path))
        if img is None:
            raise IOError(f"读图失败：{path}")
        return img
