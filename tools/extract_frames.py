#!/usr/bin/env python3
"""从实机录像抽帧，供 ROI 标定 / 模板裁取 / 识别验证用。

默认自动找 video/ 下第一个 mp4，按指定 fps 抽帧到 assets/frames/raw/。

用法：
  micromamba run -n YunDing_MVP python tools/extract_frames.py                 # 1 fps 全分辨率
  micromamba run -n YunDing_MVP python tools/extract_frames.py --fps 2
  micromamba run -n YunDing_MVP python tools/extract_frames.py --start 30 --duration 20
  micromamba run -n YunDing_MVP python tools/extract_frames.py --scale 1920:1080
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VIDEO_DIR = PROJECT_ROOT / "video"
DEFAULT_OUT = PROJECT_ROOT / "assets" / "frames" / "raw"


def find_video() -> Path:
    vids = sorted(VIDEO_DIR.glob("*.mp4"))
    if not vids:
        sys.exit(f"video/ 下没找到 mp4：{VIDEO_DIR}")
    return vids[0]


def main() -> None:
    ap = argparse.ArgumentParser(description="ffmpeg 抽帧")
    ap.add_argument("--video", type=Path, default=None, help="录像路径，默认自动找 video/*.mp4")
    ap.add_argument("--fps", type=float, default=1.0, help="每秒抽几帧（默认 1）")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="输出目录")
    ap.add_argument("--start", type=float, default=None, help="起始秒")
    ap.add_argument("--duration", type=float, default=None, help="持续秒")
    ap.add_argument("--scale", default=None, help="缩放，如 1920:1080（默认保持原分辨率）")
    ap.add_argument("--prefix", default="frame", help="文件名前缀")
    args = ap.parse_args()

    video = args.video or find_video()
    if not video.exists():
        sys.exit(f"录像不存在：{video}")
    args.out.mkdir(parents=True, exist_ok=True)

    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error"]
    if args.start is not None:
        cmd += ["-ss", str(args.start)]
    cmd += ["-i", str(video)]
    if args.duration is not None:
        cmd += ["-t", str(args.duration)]
    vf = [f"fps={args.fps}"]
    if args.scale:
        vf.append(f"scale={args.scale}")
    cmd += ["-vf", ",".join(vf), "-q:v", "2"]
    cmd += [str(args.out / f"{args.prefix}_%05d.png")]

    print(f"[抽帧] {video.name}  fps={args.fps}  scale={args.scale or '原始'}")
    print(f"[抽帧] → {args.out}")
    subprocess.run(cmd, check=True)
    n = len(list(args.out.glob(f"{args.prefix}_*.png")))
    print(f"[抽帧] 完成，目录现有 {n} 帧")


if __name__ == "__main__":
    main()
