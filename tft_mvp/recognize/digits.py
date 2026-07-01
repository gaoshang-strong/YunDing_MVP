"""DigitReader：固定字体数字识别（阈值化 + 连通域切分 + 二值模板匹配）。

字体 / 位置固定，二值模板匹配最稳。位置无关：在 ROI 内切分出各字形，
不依赖数字个数或精确位置（顶栏居中、随图标数量左右漂移，故 ROI 取宽带）。

两类字色：
- stage-round：奶白，整备 / 战斗都一样 → 灰度阈值 + 破折号锚点取两侧数字。
- 倒计时数字：整备奶白、战斗金黄 → 用「暖色」掩膜（高 R 高 G）兼容两者。
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

CANON = (24, 40)  # 字形归一尺寸 (w, h)
REF_ROI_H = 77    # 标定时的 ROI 像素高（4K, 0.0356*2160）。其余尺寸阈值按实际 ROI 高等比缩放，
                  # 使识别与分辨率无关：1080p 下 ROI 高减半，字形阈值同步减半。


class DigitReader:
    def __init__(
        self,
        template_dir: str | Path,
        gray_thr: int = 150,
        warm_rg: tuple[int, int] = (140, 115),  # 暖色阈值 (R>, G>)
        canon: tuple[int, int] = CANON,
    ):
        self.gray_thr = gray_thr
        self.warm_rg = warm_rg
        self.canon = canon
        self.templates: dict[str, np.ndarray] = {}
        tdir = Path(template_dir)
        for d in range(10):
            p = tdir / f"{d}.png"
            img = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
            if img is None:
                raise FileNotFoundError(f"缺数字模板：{p}")
            self.templates[str(d)] = self._prep(img)

    # ---- ROI 归一 ------------------------------------------------------ #
    def _norm_roi(self, roi_bgr: np.ndarray) -> np.ndarray:
        """把 ROI 缩放到标定时的高度（REF_ROI_H），使阈值与分辨率无关。

        小分辨率（如原生 1080p）会上采样，笔画更连续、切分更稳；高分辨率下采样。
        """
        h = roi_bgr.shape[0]
        if h == REF_ROI_H or h == 0:
            return roi_bgr
        scale = REF_ROI_H / h
        interp = cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA
        return cv2.resize(roi_bgr, None, fx=scale, fy=scale, interpolation=interp)

    # ---- 掩膜 ---------------------------------------------------------- #
    def _mask_gray(self, roi_bgr: np.ndarray) -> np.ndarray:
        g = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2GRAY)
        _, m = cv2.threshold(g, self.gray_thr, 255, cv2.THRESH_BINARY)
        return m

    def _mask_warm(self, roi_bgr: np.ndarray) -> np.ndarray:
        b, g, r = cv2.split(roi_bgr.astype(np.int16))
        rmin, gmin = self.warm_rg
        return ((r > rmin) & (g > gmin)).astype(np.uint8) * 255

    # ---- 连通域 -------------------------------------------------------- #
    @staticmethod
    def _comps(mask, hmin, hmax, wmin, wmax, amin):
        n, _, st, _ = cv2.connectedComponentsWithStats(mask, 8)
        out = []
        for i in range(1, n):
            x, y, w, h, a = st[i]
            if hmin <= h <= hmax and wmin <= w <= wmax and a >= amin:
                out.append((int(x), int(y), int(w), int(h)))
        out.sort()
        return out

    @staticmethod
    def _dashes(mask, s=1.0):
        n, _, st, _ = cv2.connectedComponentsWithStats(mask, 8)
        out = []
        for i in range(1, n):
            x, y, w, h, a = st[i]
            if 12 * s <= w <= 32 * s and 2 * s <= h <= 12 * s:  # 宽而扁
                out.append((int(x), int(y), int(w), int(h)))
        out.sort()
        return out

    @staticmethod
    def _rightmost_group(digits):
        """按水平间隙把数字分簇，只返回最右一簇。

        倒计时永远紧邻时钟图标、位于 ROI 最右；PVE 等界面在其左侧泄入的数字
        （实测 3-7 的 `40`、4-7 的 `30`）与倒计时之间有明显间隙，据此甩掉。
        簇内相邻数字间隙小，跨 UI 元素的间隙大 → 用「典型字宽」定阈值。
        digits 已按 x 升序。
        """
        if len(digits) <= 1:
            return digits
        med_w = float(np.median([w for (_, _, w, _) in digits]))
        gap_thr = med_w * 1.2  # 簇内间隙 << 此值；跨元素间隙 >> 此值
        group = [digits[-1]]
        for x, y, w, h in reversed(digits[:-1]):
            if group[0][0] - (x + w) <= gap_thr:  # 与当前簇最左数字的间隙
                group.insert(0, (x, y, w, h))
            else:
                break
        return group

    # ---- 分类 ---------------------------------------------------------- #
    def _prep(self, glyph_gray: np.ndarray) -> np.ndarray:
        _, b = cv2.threshold(glyph_gray, 127, 255, cv2.THRESH_BINARY)
        return cv2.resize(b, self.canon, interpolation=cv2.INTER_NEAREST)

    def _classify(self, glyph_mask: np.ndarray) -> tuple[str, float]:
        g = self._prep(glyph_mask)
        best, best_score = "?", -1.0
        for d, t in self.templates.items():
            score = float(cv2.matchTemplate(g, t, cv2.TM_CCOEFF_NORMED)[0, 0])
            if score > best_score:
                best_score, best = score, d
        return best, best_score

    @staticmethod
    def _crop(mask, b):
        x, y, w, h = b
        return mask[y:y + h, x:x + w]

    # ---- 公开 API ------------------------------------------------------ #
    def read_number(self, roi_bgr: np.ndarray, max_value: int = 99):
        """读倒计时整数（兼容奶白/金黄、随位置漂移、与分辨率无关）。返回 (value|None, conf)。

        两道防线抗「旁边数字泄入」（如 PVE 回合 3-7/4-7 读出 4019/3049 的垃圾值）：
        ① 只取最右侧数字簇（倒计时紧邻时钟图标、总在最右），甩掉左侧泄入的 40/30；
        ② 范围校验：读出值 > max_value（默认 99，倒计时 ≤ 60）判无效返回 None。
        """
        roi_bgr = self._norm_roi(roi_bgr)
        mask = self._mask_warm(roi_bgr)
        digits = self._comps(mask, hmin=24, hmax=42, wmin=8, wmax=28, amin=180)
        if not digits:
            return None, 0.0
        digits = self._rightmost_group(digits)  # ① 只保留最右簇
        s, scores = "", []
        for b in digits:
            d, sc = self._classify(self._crop(mask, b))
            s += d
            scores.append(sc)
        if not s or "?" in s:
            return None, 0.0
        val = int(s)
        if val > max_value:  # ② 越界判无效（兜底：万一簇切分没干净）
            return None, 0.0
        return val, float(np.mean(scores))

    def read_stage_round(self, roi_bgr: np.ndarray):
        """读 stage-round（D-D），破折号做锚点取左右最近数字 → 抗漂移、抗图标碎片。

        返回 (stage:int, round:int, conf:float) 或 None。
        """
        roi_bgr = self._norm_roi(roi_bgr)
        mask = self._mask_gray(roi_bgr)
        digits = self._comps(mask, hmin=24, hmax=62, wmin=8, wmax=45, amin=110)
        dashes = self._dashes(mask)
        if not dashes or not digits:
            return None
        dash = max(dashes, key=lambda d: d[2])
        dcx = dash[0] + dash[2] / 2
        left = [d for d in digits if d[0] + d[2] <= dcx + 4]
        right = [d for d in digits if d[0] >= dcx - 4]
        if not left or not right:
            return None
        sb = max(left, key=lambda d: d[0])
        rb = min(right, key=lambda d: d[0])
        sd, ss = self._classify(self._crop(mask, sb))
        rd, rs = self._classify(self._crop(mask, rb))
        if "?" in (sd, rd):
            return None
        return int(sd), int(rd), float(min(ss, rs))
