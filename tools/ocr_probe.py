#!/usr/bin/env python3
"""海克斯文字通道离线实验：OCR 帧 → 与 augments_zh.json 封闭集模糊匹配。

对 frames_out 里的 augment_select 帧逐张跑 RapidOCR，把卡片区文本按 x 聚成
3 列（3 张卡），每列与海克斯语料算双通道（名字 / 描述）bigram 相似度，
输出 top 命中 + margin，并按「重掷分段」检查同回合多帧一致性。

打分设计（v2，按首轮 15 帧基线的失败模式重构）：
  * 通道取 max(名字, 描述)：国服大面积主题化改名（12 卡中 10 卡名字对不上
    CDragon zh_cn），名字失效时描述扛住身份；反之 I/II/III 变体靠名字。
    首轮的「名字+描述合并 bigram」会互相稀释（珠光莲花名字 1.00 却输给
    合并 0.44 的错误候选）。
  * 平滑 containment：hits / (|cand| + K)。小 bigram 集（如「节外生枝」desc
    仅 5 个 bigram）不再轻易蹭高分。
  * 最小命中门槛 MIN_HITS：交集 < 3 个 bigram 的通道直接 0 分。
  * 垃圾条目过滤：name_zh 含 '@' 的条目（GainGold / 奥索任务恩赐——名字
    本身是效果模板）不参与匹配。
  * 台服→国服词汇归一：CDragon zh_cn 是台服系用词（弈子/机率/潘朵拉），
    国服屏幕是 英雄/几率/潘多拉，两侧统一后描述通道才可比。
  * 国服显示名覆盖表 display_names_zh.json：desc 锚定身份自举得来，
    作为名字通道附加变体。

用法：
  micromamba run -n YunDing_MVP python tools/ocr_probe.py
  micromamba run -n YunDing_MVP python tools/ocr_probe.py --frames frames_out --top 3
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 卡片区（1280x720 基准的归一化坐标；live 版将来由 PhaseMarker 的面板边界供给）
BAND_Y = (0.45, 0.90)      # 卡片纵向区间
BAND_X = (0.14, 0.845)     # 三张卡横向区间（避开左侧羁绊栏 / 右侧玩家列表）
TITLE_REL_Y = 0.10         # 列内最顶部文本行向下这个比例内都算标题

K_SMOOTH = 3               # 平滑项：小候选集降权
MIN_HITS = 3               # 通道最小 bigram 交集（<4 字的短名只能靠描述通道）

_VAR_RE = re.compile(r"@[^@]*@")
_KEEP_RE = re.compile(r"[一-鿿A-Za-z]+")
_FAMILY_RE = re.compile(r"[\sIVX+]+$")   # 去 I/II/III/+ 后缀，识别同族变体

# 台服(CDragon zh_cn) → 国服 屏幕用词归一。两侧文本都过这张表，方向不重要，
# 一致即可比。只放安全的同义合并，宁缺毋滥。
_CANON = [
    ("弈子", "英雄"),
    ("潘朵拉", "潘多拉"),
    ("机率", "几率"),
    ("备战席", "备战区"),
    ("金钱", "金币"),
    ("道具", "装备"),
    ("即刻", "立即"),
    ("棋盘上", "场上"),
]


def normalize(text: str) -> str:
    """去 @Var@ 模板变量，只留 CJK + 字母，再做台服→国服词汇归一。"""
    s = "".join(_KEEP_RE.findall(_VAR_RE.sub("", text)))
    for a, b in _CANON:
        s = s.replace(a, b)
    return s


def bigrams(s: str) -> set[str]:
    if len(s) < 2:
        return {s} if s else set()
    return {s[i:i + 2] for i in range(len(s) - 1)}


def load_overrides() -> dict[str, str]:
    path = PROJECT_ROOT / "assets" / "set17" / "display_names_zh.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8")).get("names", {})


def load_corpus() -> dict[str, dict]:
    path = PROJECT_ROOT / "assets" / "set17" / "augments_zh.json"
    augs = json.loads(path.read_text(encoding="utf-8"))["augments"]
    overrides = load_overrides()
    corpus = {}
    n_skip = 0
    for api, a in augs.items():
        name_zh = a["name_zh"] or ""
        if "@" in name_zh:          # GainGold / 任务恩赐：名字即效果模板，非可选卡
            n_skip += 1
            continue
        disp = overrides.get(api)
        name_vars = [bigrams(normalize(name_zh))]
        if disp:
            name_vars.append(bigrams(normalize(disp)))
        corpus[api] = {
            "name_zh": name_zh,
            "disp": disp,
            "family": _FAMILY_RE.sub("", name_zh),
            "name_vars": name_vars,
            "desc_bg": bigrams(normalize(a["desc_zh"] or "")),
        }
    print(f"[corpus] {len(corpus)} 条（过滤垃圾 {n_skip}，显示名覆盖 {len(overrides)}）")
    return corpus


def channel_score(cand: set[str], ocr: set[str]) -> float:
    """平滑 containment；交集不足 MIN_HITS 记 0。"""
    hits = len(cand & ocr)
    if hits < MIN_HITS:
        return 0.0
    return hits / (len(cand) + K_SMOOTH)


def score_entry(c: dict, ocr_bg: set[str]) -> tuple[float, float, float]:
    """-> (总分, 名字通道, 描述通道)。总分 = max(两通道)。"""
    name_s = max(channel_score(v, ocr_bg) for v in c["name_vars"])
    desc_s = channel_score(c["desc_bg"], ocr_bg)
    return max(name_s, desc_s), name_s, desc_s


def split_columns(lines: list, w: int, h: int) -> list[list]:
    """卡片区文本按 x 中心三等分成 3 列。lines = [(box, text, conf), ...]"""
    x0, x1 = BAND_X[0] * w, BAND_X[1] * w
    y0, y1 = BAND_Y[0] * h, BAND_Y[1] * h
    cols: list[list] = [[], [], []]
    for box, text, conf in lines:
        cx = sum(p[0] for p in box) / 4
        cy = sum(p[1] for p in box) / 4
        if not (x0 <= cx <= x1 and y0 <= cy <= y1):
            continue
        idx = min(2, int((cx - x0) / ((x1 - x0) / 3)))
        cols[idx].append((cy, text))
    return cols


def is_god_round(rnd: str) -> bool:
    """x-4（stage>=2）是神明祝福回合——旧检测误存的 augment_select 帧要跳过。"""
    m = re.fullmatch(r"(\d+)-(\d+)", rnd)
    return bool(m) and int(m.group(2)) == 4 and int(m.group(1)) >= 2


def main() -> None:
    ap = argparse.ArgumentParser(description="海克斯 OCR 封闭集匹配离线实验")
    ap.add_argument("--frames", type=Path, default=PROJECT_ROOT / "frames_out")
    ap.add_argument("--pattern", default="*augment_select*.png")
    ap.add_argument("--top", type=int, default=2, help="每列显示前 N 个候选")
    args = ap.parse_args()

    from rapidocr_onnxruntime import RapidOCR  # 延迟导入（模型加载慢）
    import cv2

    ocr = RapidOCR()
    corpus = load_corpus()
    frames = sorted(args.frames.glob(args.pattern))
    print(f"[probe] {len(frames)} 帧\n")

    # round_key -> [每帧的 (top1_api,) * 3 列]，做同回合重掷分段
    per_round: dict[str, list[tuple]] = {}

    for f in frames:
        rnd = f.stem.split("_")[2]  # f_{ts}_{stage-round}_{scene}
        if is_god_round(rnd):
            print(f"── {f.name}  [跳过: x-4 神明回合，非海克斯]")
            continue
        img = cv2.imread(str(f))
        h, w = img.shape[:2]
        result, _ = ocr(img)
        cols = split_columns(result or [], w, h)
        print(f"── {f.name}")
        tops = []
        for i, col in enumerate(cols):
            if not col:
                print(f"  卡{i + 1}: (无文本)")
                tops.append(None)
                continue
            col.sort()
            top_y = col[0][0]
            title_txt = normalize(" ".join(t for y, t in col
                                           if y <= top_y + TITLE_REL_Y * h))
            all_txt = normalize(" ".join(t for _, t in col))
            col_bg = bigrams(all_txt)

            scored = sorted(
                ((score_entry(c, col_bg), api) for api, c in corpus.items()),
                key=lambda kv: kv[0][0], reverse=True)
            (s1, n1, d1), a1 = scored[0]
            (s2, _, _), a2 = scored[1]
            margin = s1 - s2
            same_family = corpus[a1]["family"] == corpus[a2]["family"]
            tops.append(a1)

            c1 = corpus[a1]
            shown = c1["disp"] or c1["name_zh"]
            chan = "名字" if n1 >= d1 else "描述"
            fam = "（次名=同族变体，待色带仲裁）" if same_family else ""
            print(f"  卡{i + 1}: {shown:<14} ({a1})")
            print(f"        {s1:.2f}[{chan}胜] margin {margin:+.2f}{fam} | "
                  f"名字 {n1:.2f} 描述 {d1:.2f} | 标题「{title_txt}」")
            if s1 > 0 and n1 == 0.0 and not c1["disp"]:
                print(f"        ⚠ 疑似国服改名：屏幕标题「{title_txt[:14]}」→ "
                      f"官方名「{c1['name_zh']}」，考虑加入 display_names_zh.json")
        per_round.setdefault(rnd, []).append(tuple(tops))

    print("\n══ 同回合一致性（重掷分段）══")
    for rnd, picks in sorted(per_round.items()):
        # 连续相同的帧归为一段；段间变化 = 免费重掷（选项中途会换，属正常）
        segs: list[tuple[tuple, int]] = []
        for p in picks:
            if segs and segs[-1][0] == p:
                segs[-1] = (p, segs[-1][1] + 1)
            else:
                segs.append((p, 1))
        stable = all(n >= 2 for _, n in segs) or len(picks) == 1
        mark = "✓" if len(segs) == 1 else f"重掷 {len(segs) - 1} 次{'' if stable else '（有单帧段，存疑）'}"
        print(f"  {rnd}: {len(picks)} 帧 → {len(segs)} 段 {mark}")
        for p, n in segs:
            names = ", ".join(corpus[a]["disp"] or corpus[a]["name_zh"]
                              if a else "∅" for a in p)
            print(f"      [{n} 帧] {names}")


if __name__ == "__main__":
    main()
