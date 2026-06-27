#!/usr/bin/env python3
"""实时识别 + 仪表盘（Windows 上线用）。

主屏开云顶，本程序抓主屏 → 跑识别 → 在副屏弹出仪表盘，实时刷新回合 / 倒计时。

先看有哪些显示器：
  micromamba run -n YunDing_MVP python tools/live.py --list

抓 1 号屏、仪表盘放 2 号屏，每 0.5s 刷新：
  micromamba run -n YunDing_MVP python tools/live.py --game-monitor 1 --display-monitor 2

调试（无第二屏 / 用一张图代替抓屏）：
  micromamba run -n YunDing_MVP python tools/live.py --image assets/frames/survey/s_00010.png

窗口里按 q 或 Esc 退出。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tft_mvp.pipeline import Pipeline
from tft_mvp.ui import Dashboard

WIN = "TFT Perception"


def list_monitors() -> None:
    from tft_mvp.capture import MSSCapture

    for i, m in enumerate(MSSCapture.list_monitors()):
        tag = " (全部拼合)" if i == 0 else ""
        print(f"  [{i}] {m['width']}x{m['height']}  @ ({m['left']},{m['top']}){tag}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="列出显示器后退出")
    ap.add_argument("--game-monitor", type=int, default=1, help="抓哪个屏（游戏所在），默认 1")
    ap.add_argument("--display-monitor", type=int, default=None,
                    help="仪表盘放哪个屏，默认跟随窗口管理器")
    ap.add_argument("--interval", type=float, default=0.5, help="刷新间隔秒，默认 0.5")
    ap.add_argument("--image", type=Path, default=None, help="调试：用静态图代替实时抓屏")
    ap.add_argument("--snapshot", type=Path, default=None,
                    help="抓一帧存成 PNG 后退出（用于核对分辨率 / ROI 标定）")
    args = ap.parse_args()

    if args.list:
        list_monitors()
        return

    if args.snapshot:
        from tft_mvp.capture import MSSCapture
        frame = MSSCapture(args.game_monitor).grab()
        args.snapshot.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(args.snapshot), frame)
        print(f"[snapshot] {frame.shape[1]}x{frame.shape[0]} -> {args.snapshot}")
        return

    pipe = Pipeline()
    dash = Dashboard()

    # 抓屏源：实时 or 静态图（调试）
    if args.image:
        from tft_mvp.capture import FileCapture
        cap = FileCapture(args.image)
        mons = None
    else:
        from tft_mvp.capture import MSSCapture
        cap = MSSCapture(args.game_monitor)
        mons = MSSCapture.list_monitors()

    cv2.namedWindow(WIN, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(WIN, dash.width, dash.height)
    if mons and args.display_monitor is not None and args.display_monitor < len(mons):
        d = mons[args.display_monitor]
        cv2.moveWindow(WIN, d["left"] + 40, d["top"] + 40)

    last, fps = time.time(), 0.0
    print(f"[live] 开始：game-monitor={args.game_monitor} interval={args.interval}s  (窗口内 q/Esc 退出)")
    try:
        while True:
            t0 = time.time()
            frame = cap.grab()
            state = pipe.process(frame)
            panel = dash.render(state, fps=fps)
            cv2.imshow(WIN, panel)

            now = time.time()
            fps = 0.9 * fps + 0.1 * (1.0 / max(now - last, 1e-3))
            last = now

            wait = max(1, int((args.interval - (time.time() - t0)) * 1000))
            key = cv2.waitKey(wait) & 0xFF
            if key in (ord("q"), 27):  # q / Esc
                break
            if cv2.getWindowProperty(WIN, cv2.WND_PROP_VISIBLE) < 1:
                break
    finally:
        cv2.destroyAllWindows()
        print("[live] 结束")


if __name__ == "__main__":
    main()
