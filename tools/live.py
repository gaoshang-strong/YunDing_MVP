#!/usr/bin/env python3
"""实时识别 + 仪表盘（Windows 上线用）。

主屏开云顶，本程序抓主屏 → 跑识别 → 在副屏弹出仪表盘，实时刷新回合 / 倒计时。

先看有哪些显示器：
  micromamba run -n YunDing_MVP python tools/live.py --list

抓 1 号屏、仪表盘放 2 号屏，每 0.5s 刷新：
  micromamba run -n YunDing_MVP python tools/live.py --game-monitor 1 --display-monitor 2

开一把并把时间轴 track 录成 JSON（打完发回审查）：
  micromamba run -n YunDing_MVP python tools/live.py --game-monitor 1 --record track.json

调试（无第二屏 / 用一张图代替抓屏）：
  micromamba run -n YunDing_MVP python tools/live.py --image assets/frames/survey/s_00010.png

窗口里按 q 或 Esc 退出（退出时会把完整 track 落盘）。
"""
from __future__ import annotations

import argparse
import json
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
    ap.add_argument("--record", type=Path, default=None,
                    help="把整局时间轴 track 录成 JSON（退出时落盘，并每 40 帧增量保存）")
    ap.add_argument("--save-frames", type=Path, default=None,
                    help="录制时存降采样关键帧到该目录（周期性 + 进海克斯/神明时），供离线标定/校准")
    ap.add_argument("--frame-interval", type=float, default=4.0,
                    help="--save-frames 的周期间隔秒，默认 4")
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

    def dump_track() -> None:
        """把整局 track 写成 JSON（含元信息）。"""
        if not args.record:
            return
        payload = pipe.track.to_dict(meta={
            "profile": "16_9",
            "interval": args.interval,
            "source": "image" if args.image else f"monitor{args.game_monitor}",
        })
        args.record.parent.mkdir(parents=True, exist_ok=True)
        args.record.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def save_frame(frame, state, ts, tag):
        """存一张降采样帧（宽 1280），文件名带 ts / 阶段 / scene，便于和 track.json 对齐。"""
        d = args.save_frames
        d.mkdir(parents=True, exist_ok=True)
        h, w = frame.shape[:2]
        small = cv2.resize(frame, (1280, int(1280 * h / w))) if w > 1280 else frame
        ps = state.get("player_state", {})
        sr = f"{ps.get('stage')}-{ps.get('round')}"
        cv2.imwrite(str(d / f"f_{ts}_{sr}_{tag}.png"), small)

    last, fps = time.time(), 0.0
    seen_events = 0     # 已打印到控制台的事件数，用于增量播报
    last_frame_t = 0.0  # 上次周期性存帧时刻
    last_scene = None   # 上次的 scene（用于「进入选择界面即存」）
    n_saved = 0
    print(f"[live] 开始：game-monitor={args.game_monitor} interval={args.interval}s  (窗口内 q/Esc 退出)")
    if args.record:
        print(f"[live] 录制 track → {args.record}")
    if args.save_frames:
        print(f"[live] 存关键帧 → {args.save_frames}（每 {args.frame_interval}s + 进海克斯/神明时）")
    try:
        while True:
            t0 = time.time()
            frame = cap.grab()
            state = pipe.process(frame)
            panel = dash.render(state, fps=fps)
            cv2.imshow(WIN, panel)

            # 存关键帧：周期性 + scene 变为选择界面时立刻存
            if args.save_frames:
                scene = state.get("scene")
                if t0 - last_frame_t >= args.frame_interval:
                    save_frame(frame, state, state["timestamp"], "tick"); n_saved += 1
                    last_frame_t = t0
                elif scene and scene != last_scene:
                    save_frame(frame, state, state["timestamp"], scene); n_saved += 1
                last_scene = scene

            # 新事件实时播报（round 前进 / 倒计时重置 / 归零）
            evs = pipe.track.events
            for ev in evs[seen_events:]:
                print(f"[event] {ev['type']:<18} at={ev.get('at')}  {({k: v for k, v in ev.items() if k not in ('type', 'at', 'ts')})}")
            if len(evs) != seen_events:
                seen_events = len(evs)

            # 增量落盘：每 40 帧存一次，防中途崩溃丢数据
            if args.record and pipe.track.samples and len(pipe.track.samples) % 40 == 0:
                dump_track()

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
        dump_track()
        if args.record:
            print(f"[live] track 已保存：{args.record}（{len(pipe.track.samples)} 帧, {len(pipe.track.events)} 事件）")
        if args.save_frames:
            print(f"[live] 关键帧已存：{args.save_frames}（{n_saved} 张）")
        print("[live] 结束")


if __name__ == "__main__":
    main()
