#!/usr/bin/env python3
"""在一批帧上跑 TopBarClock，打印 stage-round / 倒计时。

用法：
  micromamba run -n YunDing_MVP python tools/run_clock.py assets/frames/planning_seq
  micromamba run -n YunDing_MVP python tools/run_clock.py assets/frames/survey --no-monotonic
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tft_mvp.capture import FileCapture
from tft_mvp.layout import LayoutMapper
from tft_mvp.recognize import DigitReader
from tft_mvp.scene import TopBarClock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROFILE = PROJECT_ROOT / "tft_mvp" / "layout" / "profiles" / "16_9.json"
TEMPLATES = PROJECT_ROOT / "assets" / "set17" / "templates" / "topbar"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("frames", type=Path, help="帧目录或单张图")
    ap.add_argument("--no-monotonic", action="store_true", help="每帧独立读，不做单调约束")
    args = ap.parse_args()

    mapper = LayoutMapper(PROFILE)
    reader = DigitReader(TEMPLATES)
    clock = TopBarClock(mapper, reader)

    files = sorted(args.frames.glob("*.png")) if args.frames.is_dir() else [args.frames]
    print(f"{'frame':<14}{'stage-round':<14}{'countdown':<12}{'sr':<10}{'conf'}")
    print("-" * 60)
    for f in files:
        if args.no_monotonic:
            clock.reset()
        frame = FileCapture.read(f)
        r = clock.read(frame)
        sr = f"{r['stage']}-{r['round']}" if r["stage"] is not None else "--"
        cd = str(r["countdown"]) if r["countdown"] is not None else "--"
        conf = f"sr={r['sr_confidence']:.2f} cd={r['cd_confidence']:.2f}"
        print(f"{f.stem:<14}{sr:<14}{cd:<12}{r['sr_status']:<10}{conf}")


if __name__ == "__main__":
    main()
