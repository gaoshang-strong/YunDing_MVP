"""LayoutMapper：归一化 ROI（0–1，16:9 参考系）→ 实际帧像素坐标。

ROI 用相对坐标定义，运行时按当前帧分辨率换算。4K 录像帧可直接喂入、
1080p 上线零重标。ROI 以点号路径访问，如 "top_bar.stage_round"。
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np


class LayoutMapper:
    def __init__(self, profile_path: str | Path):
        self.profile_path = Path(profile_path)
        self.profile = json.loads(self.profile_path.read_text(encoding="utf-8"))

    def roi(self, key: str) -> dict:
        """取归一化 ROI（{x,y,w,h}）。key 为点号路径。"""
        node = self.profile
        for part in key.split("."):
            node = node[part]
        return node

    def to_pixels(self, key: str, width: int, height: int) -> tuple[int, int, int, int]:
        """归一化 ROI → (x0, y0, x1, y1) 像素，带越界裁剪。"""
        r = self.roi(key)
        x0 = int(round(r["x"] * width))
        y0 = int(round(r["y"] * height))
        x1 = int(round((r["x"] + r["w"]) * width))
        y1 = int(round((r["y"] + r["h"]) * height))
        x0, x1 = max(0, min(x0, width)), max(0, min(x1, width))
        y0, y1 = max(0, min(y0, height)), max(0, min(y1, height))
        return x0, y0, x1, y1

    def crop(self, frame: np.ndarray, key: str) -> np.ndarray:
        h, w = frame.shape[:2]
        x0, y0, x1, y1 = self.to_pixels(key, w, h)
        return frame[y0:y1, x0:x1]
