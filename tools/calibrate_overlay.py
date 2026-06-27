#!/usr/bin/env python3
"""把 layout profile 里的 ROI 画到参考帧上，输出 overlay PNG，肉眼核对标定。

无头开发关键工具：编辑 profile → 重渲染 overlay → 看图核对 → 迭代收敛。

用法：
  micromamba run -n YunDing_MVP python tools/calibrate_overlay.py assets/frames/survey/s_00010.png
  micromamba run -n YunDing_MVP python tools/calibrate_overlay.py <frame> --keys top_bar.stage_round top_bar.countdown
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tft_mvp.layout import LayoutMapper

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROFILE = PROJECT_ROOT / "tft_mvp" / "layout" / "profiles" / "16_9.json"
COLORS = [(0, 255, 0), (0, 200, 255), (255, 0, 255), (255, 200, 0), (0, 0, 255)]


def collect_keys(node, prefix=""):
    """递归收集所有叶子 ROI（带 x/y/w/h 的 dict）的点号路径。"""
    keys = []
    for k, v in node.items():
        if k.startswith("_") or not isinstance(v, dict):
            continue
        path = f"{prefix}.{k}" if prefix else k
        if {"x", "y", "w", "h"} <= set(v):
            keys.append(path)
        else:
            keys.extend(collect_keys(v, path))
    return keys


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("frame", type=Path)
    ap.add_argument("--profile", type=Path, default=PROFILE)
    ap.add_argument("--keys", nargs="*", default=None, help="只画这些 ROI（默认全画）")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    mapper = LayoutMapper(args.profile)
    img = cv2.imread(str(args.frame))
    if img is None:
        sys.exit(f"读图失败：{args.frame}")
    h, w = img.shape[:2]

    keys = args.keys or collect_keys(mapper.profile)
    for i, key in enumerate(keys):
        x0, y0, x1, y1 = mapper.to_pixels(key, w, h)
        col = COLORS[i % len(COLORS)]
        cv2.rectangle(img, (x0, y0), (x1, y1), col, 2)
        cv2.putText(img, key, (x0, max(0, y0 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, col, 2)
        print(f"{key:<26} px=({x0},{y0})-({x1},{y1})")

    out = args.out or (PROJECT_ROOT / "assets" / "frames" / f"overlay_{args.frame.stem}.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), img)
    print(f"[overlay] → {out}")


if __name__ == "__main__":
    main()
