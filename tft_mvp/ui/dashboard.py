"""Dashboard：把对局状态 dict 渲染成一张面板图（OpenCV 绘制，深色主题）。

只读 state，不做识别——后续加了商店/装备等识别块，往这里加一个 section 即可。
用英文标签避免 cv2 不支持中文字形的问题。
"""
from __future__ import annotations

import cv2
import numpy as np

W, H = 760, 920
BG = (24, 22, 20)
PANEL = (38, 34, 30)
FG = (235, 235, 235)
MUTED = (150, 150, 150)
ACCENT = (90, 200, 120)
WARN = (70, 170, 245)
BAD = (70, 70, 230)

F = cv2.FONT_HERSHEY_SIMPLEX


def _conf_color(c: float):
    if c >= 0.8:
        return ACCENT
    if c >= 0.6:
        return WARN
    return BAD


class Dashboard:
    def __init__(self, width: int = W, height: int = H):
        self.width, self.height = width, height

    def render(self, state: dict, fps: float | None = None) -> np.ndarray:
        img = np.full((self.height, self.width, 3), BG, np.uint8)
        ps = state.get("player_state", {})
        clk = state.get("_clock", {})

        # 标题
        cv2.putText(img, "TFT PERCEPTION  ·  LIVE", (28, 52), F, 0.9, FG, 2)
        cv2.line(img, (28, 70), (self.width - 28, 70), (60, 56, 50), 1)

        # 两个大读数：STAGE-ROUND / COUNTDOWN
        sr = (f"{ps.get('stage')}-{ps.get('round')}"
              if ps.get("stage") is not None else "--")
        cd = str(ps.get("countdown")) if ps.get("countdown") is not None else "--"
        self._hero(img, 28, 96, "STAGE - ROUND", sr, clk.get("sr_confidence", 0.0))
        self._hero(img, self.width // 2 + 6, 96, "COUNTDOWN", cd, clk.get("cd_confidence", 0.0))

        # 详情区
        y = 320
        sr_status = clk.get("sr_status", "-")
        rows = [
            ("Scene", str(state.get("scene") or "-")),
            ("Resolution", f"{state['screen']['width']}x{state['screen']['height']}"
             if "screen" in state else "-"),
            ("Stage-round read", sr_status),
            ("SR confidence", f"{clk.get('sr_confidence', 0.0):.2f}"),
            ("CD confidence", f"{clk.get('cd_confidence', 0.0):.2f}"),
        ]
        y = self._section(img, "CLOCK", rows, y)

        # 预留：后续识别块（商店/装备/数值…）在此追加 self._section(...)

        # 页脚
        fy = self.height - 28
        foot = f"ts={state.get('timestamp', 0)}"
        if fps is not None:
            foot += f"   fps={fps:.1f}"
        foot += "    [q/esc] quit"
        cv2.putText(img, foot, (28, fy), F, 0.5, MUTED, 1)
        return img

    # ---- 绘制原语 ------------------------------------------------------ #
    def _hero(self, img, x, y, label, value, conf):
        w = self.width // 2 - 34
        cv2.rectangle(img, (x, y), (x + w, y + 190), PANEL, -1)
        cv2.putText(img, label, (x + 18, y + 34), F, 0.62, MUTED, 1)
        scale = 3.4 if len(value) <= 3 else 2.4
        (tw, th), _ = cv2.getTextSize(value, F, scale, 6)
        cv2.putText(img, value, (x + (w - tw) // 2, y + 130), F, scale, FG, 6)
        cv2.rectangle(img, (x, y + 174), (x + w, y + 190), _conf_color(conf), -1)

    def _section(self, img, title, rows, y):
        cv2.putText(img, title, (28, y), F, 0.7, ACCENT, 2)
        y += 14
        cv2.line(img, (28, y), (self.width - 28, y), (60, 56, 50), 1)
        y += 36
        for label, value in rows:
            cv2.putText(img, label, (40, y), F, 0.62, MUTED, 1)
            cv2.putText(img, value, (340, y), F, 0.62, FG, 1)
            y += 40
        return y + 20
