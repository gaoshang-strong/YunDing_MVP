"""MetaTFT 海克斯/神明恩赐评级表：每天第一次启动时抓一次，其余时候读缓存。

背景（2026-07 调研）：Riot 2023-09 从 match API 删除海克斯选择数据后，
Set 17 海克斯/神明**胜率**无任何公开源；MetaTFT 海克斯页实际展示的是
职业选手人工评级（5 档 S/A/B/C/D）。该评级 JSON 免鉴权、结构稳定，
id 就是 apiName——与识别通道的 `augments_zh.json` 天然对齐，
且包含神明恩赐（TFT17_Augment_*GodAugment*）。作为推荐 v1 的外部数据表。

注意：x-4 神明回合里「捡宝珠 2 选 1」的 offering（TFT17_Benefit_*，金币/经验类）
不在此表——那类选项没有公开评级，推荐层需要时另行处理。

用法：
    from tft_mvp.reco import load_tiers
    tiers = load_tiers()                      # 当天已抓过 → 读缓存，否则抓一次
    tiers["by_id"].get("TFT17_Augment_AhriGodAugment")   # -> "A"

    # 手动刷新：
    micromamba run -n YunDing_MVP python -m tft_mvp.reco.metatft_tiers --force
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import requests

TIERS_URL = "https://api-hc.metatft.com/tft-stat-api/augments_tiers"

# MetaTFT tags 字段词表 → 中文（词表实测就 6 个；未知新词原样透传）。
TAGS_ZH = {
    "econ": "经济",
    "combat": "战斗",
    "items": "装备",
    "trait": "羁绊",
    "scaling": "成长",
    "misc": "功能",
}


def translate_tags(tags: str | None) -> list[str]:
    """MetaTFT tag 串译中文："econ,items" -> ["经济", "装备"]。"""
    if not tags:
        return []
    return [TAGS_ZH.get(t.strip(), t.strip()) for t in str(tags).split(",") if t.strip()]

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CACHE_PATH = PROJECT_ROOT / "assets" / "cache" / "metatft_tiers.json"


def _parse(raw: dict) -> dict:
    """把 MetaTFT 原始响应压成推荐层要的形状。结构不符时抛 ValueError。"""
    try:
        inner = raw["content"]["content"]
        tier_list = inner["tierList"]
        author = raw["content"].get("author", {}).get("gameName", "")
    except (KeyError, TypeError) as e:
        raise ValueError(f"augments_tiers 响应结构变了: {e}") from e

    tiers: dict[str, list[str]] = {}
    by_id: dict[str, str] = {}
    for tier in tier_list:
        label = tier.get("label", "?")
        ids = [e["id"] for e in tier.get("content", []) if e.get("type") == "augment"]
        tiers[label] = ids
        for aid in ids:
            by_id[aid] = label
    if not by_id:
        raise ValueError("augments_tiers 解析出 0 个条目")

    now = datetime.now()
    return {
        "fetched_at": now.isoformat(timespec="seconds"),
        "fetched_date": now.date().isoformat(),
        "source": TIERS_URL,
        "tft_set": raw.get("tft_set"),
        "queue_id": raw.get("queue_id"),
        "author": author,                      # 人工评级作者（如 100T Spencer）
        "source_updated": raw.get("updated"),  # MetaTFT 侧的更新时间戳
        "tiers": tiers,                        # {"S": [apiName...], ...} 档内保序
        "by_id": by_id,                        # apiName -> "S"/"A"/... 推荐层主查表
        "tags": inner.get("tags", {}),         # apiName -> "econ,items" 分类标签
    }


def fetch_tiers() -> dict:
    """抓一次评级表（网络请求，约 100KB）。"""
    r = requests.get(TIERS_URL, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    return _parse(r.json())


def load_tiers(force: bool = False) -> dict | None:
    """当天第一次调用抓取并落缓存，之后直接读缓存；抓取失败回退旧缓存。

    返回 None 仅发生在「没有任何缓存且网络失败」——调用方（感知/推荐层）
    应视为「推荐不可用」继续运行，不要因此中断。
    """
    cached = None
    if CACHE_PATH.exists():
        try:
            cached = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            cached = None

    today = datetime.now().date().isoformat()
    if cached and cached.get("fetched_date") == today and not force:
        print(f"[tiers] 今日已抓过（{cached['fetched_at']}），读缓存 "
              f"{sum(len(v) for v in cached['tiers'].values())} 条")
        return cached

    try:
        data = fetch_tiers()
    except Exception as e:  # noqa: BLE001  网络/结构问题都走同一条回退
        if cached:
            print(f"[tiers] 抓取失败（{e}），沿用 {cached['fetched_date']} 的旧缓存")
            return cached
        print(f"[tiers] 抓取失败且无缓存（{e}），评级表不可用")
        return None

    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    counts = {k: len(v) for k, v in data["tiers"].items()}
    print(f"[tiers] 已抓取 MetaTFT 评级（作者 {data['author']}）: {counts} → {CACHE_PATH.name}")
    return data


def main() -> None:
    ap = argparse.ArgumentParser(description="抓取/查看 MetaTFT 海克斯评级表")
    ap.add_argument("--force", action="store_true", help="忽略当日缓存强制重抓")
    args = ap.parse_args()
    data = load_tiers(force=args.force)
    if data is None:
        raise SystemExit(1)
    gods = {k: v for k, v in data["by_id"].items() if "GodAugment" in k}
    print(f"  共 {len(data['by_id'])} 条，其中神明恩赐 {len(gods)} 条:")
    for aid, tier in sorted(gods.items(), key=lambda kv: kv[1]):
        print(f"    [{tier}] {aid}")


if __name__ == "__main__":
    main()
