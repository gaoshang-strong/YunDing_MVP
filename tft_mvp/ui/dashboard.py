"""Dashboard：把对局状态 dict 渲染成一张面板图（OpenCV 绘制，深色主题）。

只读 state，不做识别——后续加了商店/装备等识别块，往这里加一个 section 即可。
框线 / 英文标签用 cv2 绘制；中文（卡名 / tag）cv2 画不了，统一收集进 _cjk 列表，
渲染末尾用 PIL + CJK 字体一次性叠加（Windows 微软雅黑 / Linux Noto；找不到
字体时降级为 ASCII 占位，不至于空白）。
"""
from __future__ import annotations

from pathlib import Path

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

# MetaTFT 5 档评级徽章色（BGR）；None（表里没有）灰底
TIER_COLORS = {
    "S": (0, 190, 255),
    "A": (90, 200, 120),
    "B": (245, 170, 70),
    "C": (160, 160, 160),
    "D": (70, 70, 230),
}

_FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyh.ttc",      # Windows 微软雅黑（上线环境）
    "C:/Windows/Fonts/simhei.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
]
_font_cache: dict[int, object] = {}


def _cjk_font(size: int):
    """按字号缓存 CJK 字体；全部候选缺失时返回 None（调用方降级）。"""
    if size not in _font_cache:
        from PIL import ImageFont
        font = None
        for p in _FONT_CANDIDATES:
            if Path(p).exists():
                try:
                    font = ImageFont.truetype(p, size)
                    break
                except OSError:
                    continue
        _font_cache[size] = font
    return _font_cache[size]


def _conf_color(c: float):
    if c >= 0.8:
        return ACCENT
    if c >= 0.6:
        return WARN
    return BAD


class Dashboard:
    def __init__(self, width: int = W, height: int = H):
        self.width, self.height = width, height
        self._cjk: list[tuple] = []  # (text, x, y, size, bgr) 渲染末尾统一叠加

    def render(self, state: dict, fps: float | None = None) -> np.ndarray:
        img = np.full((self.height, self.width, 3), BG, np.uint8)
        self._cjk = []
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

        y = 320

        # 决策区：海克斯 / 神明选项 + 评级 + 中文 tag（检测到即置顶展示）
        dec = state.get("decision")
        scene = state.get("scene")
        if dec:
            y = self._decision_section(img, dec, y)
        elif scene in ("augment_select", "god_boon"):
            cv2.putText(img, "DECISION  ·  OCR READING ...", (28, y), F, 0.7, WARN, 2)
            y += 46

        # 详情区
        sr_status = clk.get("sr_status", "-")
        rows = [
            ("Scene", str(scene or "-")),
            ("Resolution", f"{state['screen']['width']}x{state['screen']['height']}"
             if "screen" in state else "-"),
            ("Stage-round read", sr_status),
            ("SR confidence", f"{clk.get('sr_confidence', 0.0):.2f}"),
            ("CD confidence", f"{clk.get('cd_confidence', 0.0):.2f}"),
        ]
        y = self._section(img, "CLOCK", rows, y)

        # 时间轴 track：帧数 / 倒计时趋势 / 当前小阶段 / 最近事件
        trk = state.get("track", {})
        evs = trk.get("events", [])
        last_ev = evs[-1] if evs else None
        ev_txt = (f"{last_ev['type']} @{last_ev.get('at')}" if last_ev else "-")
        rounds = trk.get("rounds", [])
        sub_txt = "-"
        if rounds and rounds[-1].get("subphases"):
            cur = rounds[-1]
            sp = cur["subphases"][-1]
            sub_txt = f"{cur['sr']}  {sp['label']} ({sp['span']}, {sp['cd_start']}->{sp['cd_end']})"
        trows = [
            ("Frames", str(trk.get("frame_count", 0))),
            ("Countdown trend", str(trk.get("cd_trend", "-"))),
            ("Sub-phase", sub_txt),
            ("Events", str(len(evs))),
            ("Last event", ev_txt),
        ]
        y = self._section(img, "TRACK", trows, y)

        # 预留：后续识别块（商店/装备/数值…）在此追加 self._section(...)

        # 页脚
        fy = self.height - 28
        foot = f"ts={state.get('timestamp', 0)}"
        if fps is not None:
            foot += f"   fps={fps:.1f}"
        foot += "    [q/esc] quit"
        cv2.putText(img, foot, (28, fy), F, 0.5, MUTED, 1)
        return self._flush_cjk(img)

    # ---- 决策区 -------------------------------------------------------- #
    def _decision_section(self, img, dec: dict, y: int) -> int:
        typ = dec.get("type") or ""
        label = "AUGMENT 3-PICK" if typ == "augment_select" else "GOD BOON"
        cv2.putText(img, f"DECISION  ·  {label}", (28, y), F, 0.7, WARN, 2)
        status = "LOCKED" if dec.get("locked") else f"READING {dec.get('votes', 0)}/2"
        (tw, _), _ = cv2.getTextSize(status, F, 0.55, 2)
        cv2.putText(img, status, (self.width - 28 - tw, y), F, 0.55,
                    ACCENT if dec.get("locked") else MUTED, 2)
        y += 14
        cv2.line(img, (28, y), (self.width - 28, y), (60, 56, 50), 1)
        y += 12
        for opt in dec.get("options", []):
            y = self._option_row(img, opt, y)
        return y + 12

    def _option_row(self, img, opt: dict, y: int) -> int:
        x, w, hrow = 28, self.width - 56, 74
        cv2.rectangle(img, (x, y), (x + w, y + hrow), PANEL, -1)

        # 右侧 tier 徽章
        tier = opt.get("tier")
        bw = 64
        bx = x + w - bw - 10
        cv2.rectangle(img, (bx, y + 12), (bx + bw, y + hrow - 12),
                      TIER_COLORS.get(tier, (90, 86, 80)), -1)
        letter = tier or "-"
        (tw, th), _ = cv2.getTextSize(letter, F, 1.1, 3)
        cv2.putText(img, letter, (bx + (bw - tw) // 2, y + (hrow + th) // 2),
                    F, 1.1, (25, 22, 20), 3)

        # 名称行 + tag 行（中文，PIL 叠加）
        slot = opt.get("slot", "?")
        tags = " · ".join(opt.get("tags_zh") or [])
        if "god" in opt:  # 神明卡：神明·称号 / 祝福名
            name = f"{slot}. {opt.get('god')}·{opt.get('subtitle')}"
            sub = f"祝福: {opt.get('boon_name') or '?'}" + (f"   {tags}" if tags else "")
            conf = opt.get("god_confidence")
        else:             # 海克斯卡
            name = f"{slot}. {opt.get('name_zh')}"
            sub = tags or "—"
            conf = opt.get("confidence")
        if conf is not None:
            sub += f"    conf {conf:.2f}"
        self._text_zh(name, x + 16, y + 8, 24, FG)
        self._text_zh(sub, x + 16, y + 42, 18, MUTED)
        return y + hrow + 10

    # ---- 绘制原语 ------------------------------------------------------ #
    def _text_zh(self, text, x, y, size, color) -> None:
        """登记一条中文文本（渲染末尾 PIL 统一叠加；y 为文本顶部）。"""
        self._cjk.append((str(text), x, y, size, color))

    def _flush_cjk(self, img):
        if not self._cjk:
            return img
        if _cjk_font(20) is None:  # 无 CJK 字体：ASCII 占位降级
            for text, x, y, size, color in self._cjk:
                cv2.putText(img, text.encode("ascii", "replace").decode(),
                            (x, y + size), F, 0.55, color, 1)
            return img
        from PIL import Image, ImageDraw
        pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
        draw = ImageDraw.Draw(pil)
        for text, x, y, size, (b, g, r) in self._cjk:
            draw.text((x, y), text, font=_cjk_font(size), fill=(int(r), int(g), int(b)))
        return cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)

    def _hero(self, img, x, y, label, value, conf):
        w = self.width // 2 - 34
        cv2.rectangle(img, (x, y), (x + w, y + 190), PANEL, -1)
        cv2.putText(img, label, (x + 18, y + 34), F, 0.62, MUTED, 1)
        scale = 3.4 if len(value) <= 3 else 2.4
        (tw, th), _ = cv2.getTextSize(value, F, scale, 6)
        cv2.putText(img, value, (x + (w - tw) // 2, y + 130), F, scale, FG, 6)
        cv2.rectangle(img, (x, y + 174), (x + w, y + 190), _conf_color(conf), -1)

    def _section(self, img, title, rows, y):
        # 空间不足（决策区占位时）整段跳过，避免画出下边界
        if y + 50 + len(rows) * 36 > self.height - 44:
            return y
        cv2.putText(img, title, (28, y), F, 0.7, ACCENT, 2)
        y += 14
        cv2.line(img, (28, y), (self.width - 28, y), (60, 56, 50), 1)
        y += 34
        for label, value in rows:
            cv2.putText(img, label, (40, y), F, 0.62, MUTED, 1)
            cv2.putText(img, value, (340, y), F, 0.62, FG, 1)
            y += 36
        return y + 20
