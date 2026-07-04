#!/usr/bin/env python3
"""下载云顶之弈「类别一」目录素材（棋子头像 / 装备图标 / 羁绊图标）并生成 catalog manifest。

数据源：
  - 元数据：CommunityDragon (CDragon) en_us.json + zh_cn.json（latest）
  - 图片  ：CDragon 原始游戏贴图，tex 路径转 png

用法：
  micromamba run -n YunDing_MVP python scripts/download_assets.py            # 自动用最新赛季
  micromamba run -n YunDing_MVP python scripts/download_assets.py --set 17   # 指定赛季
  micromamba run -n YunDing_MVP python scripts/download_assets.py --no-images # 只生成 manifest
  micromamba run -n YunDing_MVP python scripts/download_assets.py --refresh   # 强制重下 json

产物（默认在项目根 assets/setNN/ 下）：
  champions/<apiName>.png
  items/<apiName>.png
  traits/<apiName>.png
  manifest.json
  augments_zh.json   海克斯中英文名+中文描述+effects（文字通道识别语料，含神明恩赐）
  gods_zh.json       神明目录（名字/称号/各stage offering，Blitz gods-v2 zh_cn 原样）
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

CDRAGON_META = "https://raw.communitydragon.org/latest/cdragon/tft/{lang}.json"
CDRAGON_GAME = "https://raw.communitydragon.org/latest/game/"
# Blitz.gg 的静态数据目录：9 位神明 + 各 stage offering，中文 locale（无 UA 会 403）
GODS_ZH_URL = "https://utils.iesdev.com/static/json/tftTest/set{n}/zh_cn/gods-v2"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = PROJECT_ROOT / "assets"
CACHE_DIR = DEFAULT_OUT / "cache"


# --------------------------------------------------------------------------- #
# 数据获取
# --------------------------------------------------------------------------- #
def fetch_meta(lang: str, refresh: bool) -> dict:
    """下载（带本地缓存）CDragon 元数据 json。"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / f"cdragon_{lang}.json"
    if cache.exists() and not refresh:
        print(f"[meta] 使用缓存 {cache.name}")
        return json.loads(cache.read_text(encoding="utf-8"))
    url = CDRAGON_META.format(lang=lang)
    print(f"[meta] 下载 {url}")
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    cache.write_text(r.text, encoding="utf-8")
    return r.json()


def fetch_gods(set_number: int, refresh: bool) -> list | None:
    """下载（带本地缓存）Blitz gods-v2 中文神明目录；失败返回 None 不中断主流程。"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / f"gods_zh_set{set_number}.json"
    if cache.exists() and not refresh:
        print(f"[gods] 使用缓存 {cache.name}")
        return json.loads(cache.read_text(encoding="utf-8"))
    url = GODS_ZH_URL.format(n=set_number)
    print(f"[gods] 下载 {url}")
    try:
        r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        cache.write_text(r.text, encoding="utf-8")
        return r.json()
    except Exception as e:  # noqa: BLE001
        print(f"[warn] gods-v2 下载失败（{e}），跳过 gods_zh.json")
        return None


def pick_set(data: dict, set_number: int | None) -> dict:
    """选出目标赛季的 setData 条目。默认取最新赛季的主 mutator (TFTSetNN)。"""
    sets = data["setData"]
    if set_number is None:
        set_number = max(s.get("number", 0) for s in sets)
    target = f"TFTSet{set_number}"
    for s in sets:
        if s.get("mutator") == target:
            return s
    # 兜底：number 匹配的第一个
    for s in sets:
        if s.get("number") == set_number:
            print(f"[set] 未找到 mutator={target}，回退到 {s.get('mutator')}")
            return s
    sys.exit(f"找不到赛季 {set_number} 的数据")


# --------------------------------------------------------------------------- #
# 路径 / URL 转换
# --------------------------------------------------------------------------- #
def tex_to_url(tex_path: str) -> str | None:
    """ASSETS/.../X.tex  ->  https://raw.communitydragon.org/latest/game/assets/.../x.png"""
    if not tex_path:
        return None
    p = tex_path.lower()
    for ext in (".tex", ".dds"):
        if p.endswith(ext):
            p = p[: -len(ext)] + ".png"
            break
    return CDRAGON_GAME + p


# --------------------------------------------------------------------------- #
# catalog 构建
# --------------------------------------------------------------------------- #
def build_catalog(en_set: dict, en_items: list, zh: dict, zh_set: dict) -> dict:
    """构建 champions / items / traits 三个 catalog（含中英文名、图标 url）。"""
    # 中文名查表（按 apiName）
    zh_champ = {c["apiName"]: c.get("name", "") for c in zh_set.get("champions", [])}
    zh_trait = {t["apiName"]: t.get("name", "") for t in zh_set.get("traits", [])}
    zh_item = {i["apiName"]: i.get("name", "") for i in zh.get("items", [])}

    # --- 棋子：cost 1-5 + 有 squareIcon + 有羁绊（剔除铁砧/PVE/道具单位）---
    champions = {}
    for c in en_set.get("champions", []):
        api = c.get("apiName")
        if not api or not c.get("squareIcon"):
            continue
        if not (1 <= (c.get("cost") or 0) <= 5):
            continue
        if not c.get("traits"):
            continue
        champions[api] = {
            "name_en": c.get("name", ""),
            "name_zh": zh_champ.get(api, ""),
            "cost": c.get("cost"),
            "traits": c.get("traits", []),
            "icon_url": tex_to_url(c["squareIcon"]),
            "icon": f"champions/{api}.png",
        }

    # --- 装备：散件(tag=component) 或 成装(composition 非空) ---
    items = {}
    for it in en_items:
        api = it.get("apiName")
        icon = it.get("icon")
        if not api or not icon:
            continue
        tags = [t.lower() for t in (it.get("tags") or [])]
        is_component = "component" in tags
        is_completed = bool(it.get("composition"))
        if not (is_component or is_completed):
            continue
        items[api] = {
            "name_en": it.get("name", ""),
            "name_zh": zh_item.get(api, ""),
            "type": "component" if is_component else "completed",
            "composition": it.get("composition", []),
            "tags": it.get("tags", []),
            "icon_url": tex_to_url(icon),
            "icon": f"items/{api}.png",
        }

    # --- 羁绊 ---
    traits = {}
    for t in en_set.get("traits", []):
        api = t.get("apiName")
        if not api or not t.get("icon"):
            continue
        traits[api] = {
            "name_en": t.get("name", ""),
            "name_zh": zh_trait.get(api, ""),
            "icon_url": tex_to_url(t["icon"]),
            "icon": f"traits/{api}.png",
        }

    return {"champions": champions, "items": items, "traits": traits}


def build_augments(en_set: dict, en_items: list, zh_set: dict, zh_items: list) -> dict:
    """构建海克斯语料表：apiName → 中英文名 + 中文描述 + effects + 图标 url。

    赛季 augments 字段只是 apiName 列表（含跨赛季复用的 TFT10_/TFT11_ 等），
    详情统一在顶层 items 里查。desc 带 @Var@ 模板变量，数值在 effects 里，
    文字通道匹配用 CJK-only 归一化时可直接忽略。
    """
    zh_by_api = {i.get("apiName"): i for i in zh_items}
    en_by_api = {i.get("apiName"): i for i in en_items}

    augments = {}
    for api in zh_set.get("augments", []):
        zh_it = zh_by_api.get(api)
        if not zh_it:
            continue
        en_it = en_by_api.get(api, {})
        augments[api] = {
            "name_zh": zh_it.get("name") or "",
            "name_en": en_it.get("name") or "",
            "desc_zh": zh_it.get("desc") or "",  # 个别条目 desc 为 null

            "effects": zh_it.get("effects", {}),
            "icon_url": tex_to_url(zh_it.get("icon", "")),
        }
    return augments


# --------------------------------------------------------------------------- #
# 图片下载
# --------------------------------------------------------------------------- #
def download_one(url: str, dest: Path) -> tuple[bool, str]:
    if dest.exists() and dest.stat().st_size > 0:
        return True, "skip"
    try:
        r = requests.get(url, timeout=60)
        if r.status_code != 200 or not r.content:
            return False, f"HTTP {r.status_code}"
        dest.write_bytes(r.content)
        return True, "ok"
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def download_images(catalog: dict, out_dir: Path, workers: int) -> list[str]:
    jobs = []  # (url, dest, label)
    for kind in ("champions", "items", "traits"):
        (out_dir / kind).mkdir(parents=True, exist_ok=True)
        for api, meta in catalog[kind].items():
            if meta.get("icon_url"):
                jobs.append((meta["icon_url"], out_dir / meta["icon"], f"{kind}/{api}"))

    print(f"[img] 待下载 {len(jobs)} 张 (workers={workers})")
    failures, done = [], 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(download_one, url, dest): label for url, dest, label in jobs}
        for fut in as_completed(futs):
            label = futs[fut]
            ok, msg = fut.result()
            done += 1
            if not ok:
                failures.append(f"{label}: {msg}")
            if done % 50 == 0 or done == len(jobs):
                print(f"  {done}/{len(jobs)}  失败 {len(failures)}", end="\r")
    print()
    return failures


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(description="下载 TFT 棋子/装备/羁绊图标 + 生成 manifest")
    ap.add_argument("--set", type=int, default=None, help="赛季编号，默认最新")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="输出根目录 (默认 项目/assets)")
    ap.add_argument("--version", default="16.13.1", help="记录用的补丁号 (仅写入 manifest)")
    ap.add_argument("--workers", type=int, default=16, help="下载并发数")
    ap.add_argument("--no-images", action="store_true", help="只生成 manifest，不下图")
    ap.add_argument("--refresh", action="store_true", help="强制重新下载 CDragon json")
    args = ap.parse_args()

    en = fetch_meta("en_us", args.refresh)
    zh = fetch_meta("zh_cn", args.refresh)

    en_set = pick_set(en, args.set)
    zh_set = pick_set(zh, en_set.get("number"))
    set_number = en_set.get("number")
    print(f"[set] 赛季 {set_number} ({en_set.get('mutator')})")

    catalog = build_catalog(en_set, en.get("items", []), zh, zh_set)
    print(f"[catalog] 棋子 {len(catalog['champions'])} | "
          f"装备 {len(catalog['items'])} | 羁绊 {len(catalog['traits'])}")

    out_dir = args.out / f"set{set_number}"
    out_dir.mkdir(parents=True, exist_ok=True)

    # --- 海克斯语料（文字通道识别用，含神明恩赐 GodAugment）---
    augments = build_augments(en_set, en.get("items", []), zh_set, zh.get("items", []))
    god_augs = sum(1 for a in augments if "GodAugment" in a)
    aug_path = out_dir / "augments_zh.json"
    aug_path.write_text(
        json.dumps({"set": set_number, "count": len(augments), "augments": augments},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[augments] {len(augments)} 个（含神明恩赐 {god_augs}）→ {aug_path.name}")

    # --- 神明目录（1-1 横幅 + x-4 offering 识别用）---
    gods = fetch_gods(set_number, args.refresh)
    if gods is not None:
        gods_path = out_dir / "gods_zh.json"
        gods_path.write_text(
            json.dumps({"set": set_number, "source": "blitz-gods-v2", "gods": gods},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        offering_n = sum(len(v) for g in gods for v in g.get("offerings", {}).values())
        print(f"[gods] {len(gods)} 位神明 / {offering_n} 个 offering → {gods_path.name}")

    failures = []
    if not args.no_images:
        failures = download_images(catalog, out_dir, args.workers)

    manifest = {
        "set": set_number,
        "mutator": en_set.get("mutator"),
        "patch": args.version,
        "source": "communitydragon-latest",
        "counts": {k: len(v) for k, v in catalog.items()},
        **catalog,
    }
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[manifest] 已写入 {manifest_path}")

    if failures:
        fail_log = out_dir / "download_failures.txt"
        fail_log.write_text("\n".join(failures), encoding="utf-8")
        print(f"[warn] {len(failures)} 张下载失败，详见 {fail_log}")
    else:
        print("[done] 全部完成")


if __name__ == "__main__":
    main()
