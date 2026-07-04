"""决策卡识别：海克斯 3 选 1 / 神明 2 选 1 → apiName + 评级 + 中文 tag。

链路：帧（marker 判定为 augment_select / god_boon 时）→ 降采样到 1280 宽 →
RapidOCR → 卡片区文本按 x 间隙聚成 N 列 → 每列双通道封闭集匹配 →
查 MetaTFT 评级表（tier + tag，tag 译中文）→ decision 块。

匹配配方（tools/ocr_probe.py 离线标定，15 帧 12/12 全对）：
- 双通道各自打分，最终分 = max(名字, 描述)——两通道失效面不重叠
  （客户端改名 vs 描述数值修订），谁强信谁；合并 bigram 会互相稀释。
- 通道分 = 平滑 containment `hits / (|cand| + K_SMOOTH)`，交集 < MIN_HITS 记 0
  ——小 bigram 集语料（「获得随机纹章」类短描述）不再碰瓷。
- 名字通道对全列文本打分（标题切分不可靠），覆盖表显示名作为附加变体。
- 垃圾条目（name_zh 含 @ / 。——GainGold、奥索任务恩赐，名字即效果模板）
  整条剔出候选集。
- 台服→国服词汇归一 `_CANON`：CDragon zh_cn 是台服系用词（弈子/机率/潘朵拉），
  两侧统一后描述通道才可比。
- 覆盖表两层：`display_names_zh.json`（人工核对种子，api→屏幕名）+
  `name_overrides.json`（live 自举，屏幕名→api）。描述锚定身份而名字对不上时
  自动学习写入后者——客户端 Set 17 大面积主题化改名（12 卡中 10 卡），
  攒几局即与客户端同步。

神明卡（x-4 选神明）两步匹配：称号锚定神明（「财富之神」9 选 1，语料与屏幕一致）
→ 祝福在该神明的 offering 里匹配；评级用该神明的恩赐海克斯（*GodAugment*）做代理。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import cv2
import numpy as np

from ..reco.metatft_tiers import translate_tags

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ASSETS = PROJECT_ROOT / "assets" / "set17"
OVERRIDES_PATH = ASSETS / "name_overrides.json"          # live 自举（自动写入）
DISPLAY_NAMES_PATH = ASSETS / "display_names_zh.json"    # 人工核对种子

# 卡片区归一化 ROI（1280x720 标定；live 帧降采样后适用）
AUG_BAND = {"x": (0.14, 0.845), "y": (0.45, 0.90)}   # 海克斯 3 卡
GOD_BAND = {"x": (0.24, 0.74), "y": (0.28, 0.70)}    # 神明 2 卡（vs 布局）
COL_GAP = 0.05          # 列间隙阈值（相对宽度）
OCR_WIDTH = 1280        # OCR 前统一降采样宽度（实验证明足够，控制耗时）
TITLE_REL_Y = 0.10      # 列内最顶行向下此比例内算标题（仅用于 exact 捷径/自举）

K_SMOOTH = 3            # 平滑项：小候选集降权
MIN_HITS = 3            # 通道最小 bigram 交集（<4 字短名只能靠描述通道）
MIN_DESC_BG = 6         # 神明 offering 描述过短不走描述通道

_VAR_RE = re.compile(r"@[^@]*@")
_KEEP_RE = re.compile(r"[一-鿿A-Za-z]+")

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
    """去 @Var@ 模板变量，只留 CJK+字母，再做台服→国服词汇归一。"""
    s = "".join(_KEEP_RE.findall(_VAR_RE.sub("", text or "")))
    for a, b in _CANON:
        s = s.replace(a, b)
    return s


def bigrams(s: str) -> set[str]:
    if len(s) < 2:
        return {s} if s else set()
    return {s[i:i + 2] for i in range(len(s) - 1)}


def channel_score(cand: set[str], ocr: set[str]) -> float:
    """平滑 containment；交集不足 MIN_HITS 记 0。"""
    hits = len(cand & ocr)
    if hits < MIN_HITS:
        return 0.0
    return hits / (len(cand) + K_SMOOTH)


def containment(cand: set[str], ocr: set[str]) -> float:
    """朴素 containment——仅用于神明小语料（称号 / offering 名都很短）。"""
    return len(cand & ocr) / len(cand) if cand else 0.0


# --------------------------------------------------------------------------- #
# 语料
# --------------------------------------------------------------------------- #
class CardCorpus:
    """海克斯 + 神明语料 + 两层覆盖表 + 评级表。"""

    def __init__(self, tiers: dict | None = None):
        augs = json.loads((ASSETS / "augments_zh.json").read_text(encoding="utf-8"))["augments"]

        # 覆盖表：种子（api→屏幕名，人工核对）+ 自举（屏幕名→api，live 自动攒）
        seeds: dict[str, str] = {}
        if DISPLAY_NAMES_PATH.exists():
            seeds = json.loads(DISPLAY_NAMES_PATH.read_text(encoding="utf-8")).get("names", {})
        self._auto: dict[str, dict] = {}
        if OVERRIDES_PATH.exists():
            self._auto = json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))
        self.overrides: dict[str, str] = {normalize(d): api for api, d in seeds.items()}
        for key, v in self._auto.items():
            self.overrides.setdefault(key, v["api"])
        disp_by_api: dict[str, list[str]] = {}
        for key, api in self.overrides.items():
            disp_by_api.setdefault(api, []).append(key)

        self.entries: dict[str, dict] = {}
        self.exact_name: dict[str, str] = {}  # 归一化官方名 -> api
        for api, a in augs.items():
            raw = a["name_zh"] or ""
            if "@" in raw or "。" in raw:      # 垃圾条目：名字即效果模板，非可选卡
                continue
            name = normalize(raw)
            if len(name) < 2:
                continue
            name_vars = [bigrams(name)] + [bigrams(n) for n in disp_by_api.get(api, [])]
            self.entries[api] = {
                "api": api, "name_zh": raw, "name_en": a["name_en"],
                "display": seeds.get(api, raw),   # UI 展示优先国服屏幕名
                "name_vars": name_vars,
                "desc_bg": bigrams(normalize(a["desc_zh"])),
            }
            self.exact_name[name] = api

        gods = json.loads((ASSETS / "gods_zh.json").read_text(encoding="utf-8"))["gods"]
        self.gods: list[dict] = []
        for g in gods:
            champ = re.sub(r"Face$", "", g.get("image", ""))  # "ahriFace" -> "ahri"
            god_aug = next((a for a in augs
                            if champ and f"{champ}godaugment" in a.lower()), None)
            offerings = []
            for stage, offs in (g.get("offerings") or {}).items():
                for o in offs:
                    offerings.append({
                        "key": o["key"], "stage": stage, "name_zh": o["name"],
                        "name_bg": bigrams(normalize(o["name"])),
                        "desc_bg": bigrams(normalize(o.get("description", ""))),
                    })
            self.gods.append({
                "name_zh": g["name"], "subtitle": g["subtitle"], "champ": champ,
                "subtitle_bg": bigrams(normalize(g["subtitle"])),
                "god_augment": god_aug,  # 评级代理
                "offerings": offerings,
            })

        self.tiers = tiers or {}

    # ---- 自举覆盖表 ----
    def learn_override(self, screen_name: str, api: str, evidence: str) -> None:
        key = normalize(screen_name)
        if not key or key in self.overrides or key in self.exact_name:
            return
        self.overrides[key] = api
        self.entries[api]["name_vars"].append(bigrams(key))
        self._auto[key] = {"api": api, "evidence": evidence,
                           "learned_at": int(time.time() * 1000)}
        OVERRIDES_PATH.write_text(
            json.dumps(self._auto, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- 评级 ----
    def rate(self, api: str | None) -> tuple[str | None, str | None]:
        """api -> (tier, tags)。"""
        if not api or not self.tiers:
            return None, None
        return (self.tiers.get("by_id", {}).get(api),
                self.tiers.get("tags", {}).get(api))

    # ---- 海克斯卡匹配 ----
    def match_augment(self, title: str, desc: str) -> dict:
        """双通道 max 匹配一张卡。返回 option dict（含低置信输出）。"""
        t_norm = normalize(title)
        col_bg = bigrams(normalize(f"{title} {desc}"))

        # exact 捷径：覆盖表（含自举）优先，其次语料官方名
        exact_api = self.overrides.get(t_norm) or self.exact_name.get(t_norm)

        scored = []
        for api, e in self.entries.items():
            name_s = max(channel_score(v, col_bg) for v in e["name_vars"])
            desc_s = channel_score(e["desc_bg"], col_bg)
            scored.append((max(name_s, desc_s), name_s, desc_s, api))
        scored.sort(reverse=True)
        (s1, n1, d1, api1), s2 = scored[0], scored[1][0]

        if exact_api:
            api1, s1, n1 = exact_api, 1.0, 1.0
            s2 = 0.0 if api1 != scored[0][3] else scored[1][0]
        margin = s1 - s2
        conf = round(min(s1, 0.6 + margin), 2)  # 高分零 margin（同族 I/II）压到 0.6+

        # 自举：描述锚定身份、名字对不上 → 记屏幕显示名（宁缺毋滥，阈值偏严）
        if not exact_api and d1 >= 0.55 and n1 < 0.3 and margin >= 0.1 and t_norm:
            self.learn_override(title, api1, f"desc={d1:.2f} margin={margin:.2f}")

        tier, tags = self.rate(api1)
        e = self.entries[api1]
        return {"api": api1, "name_zh": e["display"], "official_zh": e["name_zh"],
                "screen_title": title,
                "score": round(s1, 2), "margin": round(margin, 2), "confidence": conf,
                "tier": tier, "tags": tags, "tags_zh": translate_tags(tags),
                "channels": {"name": round(n1, 2), "desc": round(d1, 2)}}

    # ---- 神明卡匹配（两步：称号锚神明 → offering 内匹配祝福）----
    def match_god(self, all_text: str) -> dict:
        bg = bigrams(normalize(all_text))
        god_scored = sorted(((containment(g["subtitle_bg"], bg), g) for g in self.gods),
                            key=lambda kv: kv[0], reverse=True)
        g_s, god = god_scored[0]
        boon, b_s = None, 0.0
        for o in god["offerings"]:
            s = max(containment(o["name_bg"], bg),
                    containment(o["desc_bg"], bg) if len(o["desc_bg"]) >= MIN_DESC_BG else 0.0)
            if s > b_s:
                boon, b_s = o, s
        tier, tags = self.rate(god["god_augment"])
        return {"god": god["name_zh"], "subtitle": god["subtitle"],
                "god_confidence": round(g_s, 2),
                "boon_key": boon["key"] if boon else None,
                "boon_name": boon["name_zh"] if boon else None,
                "boon_score": round(b_s, 2),
                "god_augment": god["god_augment"],
                "tier": tier, "tags": tags, "tags_zh": translate_tags(tags)}


# --------------------------------------------------------------------------- #
# 识别器（OCR + 分列 + 节流 + 投票）
# --------------------------------------------------------------------------- #
class CardRecognizer:
    """挂在 Pipeline 上：决策窗口内节流跑 OCR，2 帧一致锁定，输出 decision 块。"""

    MIN_INTERVAL = 1.2      # 未锁定时 OCR 最小间隔（秒）
    LOCKED_INTERVAL = 3.0   # 锁定后降频复核（捕捉重掷）
    VOTES_TO_LOCK = 2       # 连续一致帧数

    def __init__(self, tiers: dict | None = None):
        from rapidocr_onnxruntime import RapidOCR  # 缺依赖时在此抛出，由调用方降级
        self._ocr = RapidOCR()
        self.corpus = CardCorpus(tiers)
        self.reset()

    def reset(self) -> None:
        self._last_run = 0.0
        self._signature: tuple | None = None
        self._votes = 0
        self._decision: dict | None = None

    # ---- 帧入口 ----
    def update(self, frame: np.ndarray, scene: str | None) -> dict | None:
        if scene not in ("augment_select", "god_boon"):
            self.reset()
            return None
        now = time.time()
        locked = self._decision is not None and self._decision.get("locked")
        interval = self.LOCKED_INTERVAL if locked else self.MIN_INTERVAL
        if now - self._last_run < interval:
            return self._decision
        self._last_run = now

        options = self._read_cards(frame, scene)
        if not options:                 # 走动段 / 动画帧：保持现状
            return self._decision
        sig = tuple(o.get("api") or o.get("boon_key") for o in options)
        if sig == self._signature:
            self._votes += 1
        else:                           # 新选项组（首见或重掷）→ 重新投票
            self._signature, self._votes = sig, 1
        self._decision = {
            "type": scene, "options": options,
            "locked": self._votes >= self.VOTES_TO_LOCK,
            "votes": self._votes,
        }
        return self._decision

    # ---- OCR + 分列 + 匹配 ----
    def _read_cards(self, frame: np.ndarray, scene: str) -> list[dict]:
        h, w = frame.shape[:2]
        if w > OCR_WIDTH:
            scale = OCR_WIDTH / w
            frame = cv2.resize(frame, (OCR_WIDTH, int(h * scale)))
            h, w = frame.shape[:2]
        result, _ = self._ocr(frame)
        if not result:
            return []
        band = AUG_BAND if scene == "augment_select" else GOD_BAND
        cols = self._split_columns(result, w, h, band)
        expect = 3 if scene == "augment_select" else 2
        if len(cols) != expect:
            return []
        options = []
        for i, col in enumerate(cols):
            col.sort()
            if scene == "augment_select":
                top_y = col[0][0]
                title = " ".join(t for y, t in col if y <= top_y + TITLE_REL_Y * h)
                desc = " ".join(t for y, t in col if y > top_y + TITLE_REL_Y * h)
                opt = self.corpus.match_augment(title, desc)
            else:
                opt = self.corpus.match_god(" ".join(t for _, t in col))
                if opt["god_confidence"] < 0.3:  # 零证据帧（走动段误聚列）整次作废
                    return []
            opt["slot"] = i + 1
            options.append(opt)
        return options

    @staticmethod
    def _split_columns(lines: list, w: int, h: int, band: dict) -> list[list]:
        """卡片区文本按 x 区间间隙聚类成列（自适应 2/3/4 张卡）。"""
        x0, x1 = band["x"][0] * w, band["x"][1] * w
        y0, y1 = band["y"][0] * h, band["y"][1] * h
        boxes = []
        for box, text, _conf in lines:
            xs = [p[0] for p in box]
            cx, cy = sum(xs) / 4, sum(p[1] for p in box) / 4
            if x0 <= cx <= x1 and y0 <= cy <= y1:
                boxes.append((min(xs), max(xs), cy, text))
        boxes.sort()
        cols: list[dict] = []
        for bx0, bx1, cy, text in boxes:
            if cols and bx0 - cols[-1]["x_max"] < COL_GAP * w:
                cols[-1]["x_max"] = max(cols[-1]["x_max"], bx1)
                cols[-1]["lines"].append((cy, text))
            else:
                cols.append({"x_max": bx1, "lines": [(cy, text)]})
        return [c["lines"] for c in cols]
