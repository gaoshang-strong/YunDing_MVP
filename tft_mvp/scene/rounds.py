"""RoundTrack：把时钟读数结构化成「回合 → 4 小阶段」。

云顶每个回合固定 4 个小阶段，每段各倒数一次（一条倒计时 ramp）：

  ① planning  策划（长，~30–50s）
  ② trans_pc  策划→战斗 转场（短，~5s）
  ③ combat    战斗（长，~30s）
  ④ trans_cp  战斗→下一回合 转场（短，~2–5s）

切分规则：
- **回合边界**：stage-round 前进（round_advance）——实测 100% 可靠。
- **小阶段边界**：倒计时「回升」（本帧比上帧大 ≥ _NEW_RAMP_JUMP）。回合内倒计时只降不升，
  一旦回升就是进入下一段（trans_cp 起始仅 ~2，故阈值取 2，比事件用的 4 更灵敏）。
- **标签**：回合内按出现顺序贴 planning/trans_pc/combat/trans_cp；超出 4 段的贴 extra（待观察）。
- 每段附 span=long/short（按 cd_max 判），用于交叉校验「长短长短」的预期节律。

内存：全量回合都留（很小，一局 ~26 回合 × 4 段）；snapshot() 只吐最近 N 回合给下游。
miss（countdown=None）不打断 ramp——回升检测拿持久化的上一有效值比对，跨 miss 仍成立。
"""
from __future__ import annotations

SUBPHASE_LABELS = ["planning", "trans_pc", "combat", "trans_cp"]
_NEW_RAMP_JUMP = 2   # 倒计时回升 ≥ 此值 → 新小阶段（trans_cp 起始仅 ~2）
_LONG_MIN = 12       # ramp 的 cd_max ≥ 此值算「长」段（策划/战斗），否则「短」（转场）


class RoundTrack:
    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self.rounds: list[dict] = []       # 已结束的回合（全量）
        self._cur_round: dict | None = None
        self._cur_ramp: dict | None = None
        self._sr: tuple[int, int] | None = None

    # ---- 每帧更新 ------------------------------------------------------ #
    def update(self, stage, rnd, countdown, sr_status, ts) -> None:
        # 1. 回合边界：stage-round 前进
        if sr_status == "ok" and stage is not None:
            sr = (stage, rnd)
            if self._sr is None:
                self._start_round(sr, ts)
            elif sr > self._sr:
                self._finalize_round(ts)
                self._start_round(sr, ts)
            self._sr = sr

        # 2. 小阶段 ramp 切分（仅在已有回合时）
        if self._cur_round is not None and countdown is not None:
            if self._cur_ramp is None:
                self._start_ramp(countdown, ts)
            elif countdown - self._cur_ramp["_last"] >= _NEW_RAMP_JUMP:
                self._close_ramp()
                self._start_ramp(countdown, ts)
            else:
                self._extend_ramp(countdown, ts)

    # ---- 回合 ---------------------------------------------------------- #
    def _start_round(self, sr, ts) -> None:
        self._cur_round = {
            "stage": sr[0], "round": sr[1], "sr": f"{sr[0]}-{sr[1]}",
            "ts_start": ts, "_ramps": [],
        }
        self._cur_ramp = None

    def _finalize_round(self, ts) -> None:
        if self._cur_round is None:
            return
        self._close_ramp()
        self._cur_round["ts_end"] = ts
        self.rounds.append(self._cur_round)
        self._cur_round = None

    # ---- 小阶段 ramp --------------------------------------------------- #
    # 标签「延后到输出时」按顺序贴：先丢掉空 ramp（cd_max==0，回合开头上一回合残留的
    # 0 读数），再对真实 ramp 依次贴 planning/trans_pc/combat/trans_cp，避免空 ramp 顶偏标签。
    def _start_ramp(self, cd, ts) -> None:
        self._cur_ramp = {
            "cd_start": cd, "cd_end": cd, "cd_max": cd,
            "n": 1, "ts_start": ts, "ts_end": ts, "_last": cd,
        }

    def _extend_ramp(self, cd, ts) -> None:
        r = self._cur_ramp
        r["cd_end"] = cd
        r["cd_max"] = max(r["cd_max"], cd)
        r["n"] += 1
        r["ts_end"] = ts
        r["_last"] = cd

    def _close_ramp(self) -> None:
        if self._cur_ramp is None:
            return
        self._cur_round["_ramps"].append(self._cur_ramp)
        self._cur_ramp = None

    @staticmethod
    def _ramp_view(r: dict, label: str, active: bool = False) -> dict:
        """ramp 的只读输出视图（贴标签，补 dur_s / span，去掉内部字段）。"""
        v = {
            "label": label, "cd_start": r["cd_start"], "cd_end": r["cd_end"],
            "cd_max": r["cd_max"], "n": r["n"],
            "dur_s": round((r["ts_end"] - r["ts_start"]) / 1000, 1),
            "span": "long" if r["cd_max"] >= _LONG_MIN else "short",
        }
        if active:
            v["active"] = True
        return v

    def _round_view(self, rd: dict, cur: bool = False) -> dict:
        """回合的只读输出视图；进行中的回合把未闭合 ramp 也带上（active=True）。"""
        raw = list(rd["_ramps"])
        active_ramp = self._cur_ramp if cur else None
        # 丢掉空 ramp（从未出现 >0 的倒计时）
        reals = [(r, False) for r in raw if r["cd_max"] > 0]
        if active_ramp is not None and active_ramp["cd_max"] > 0:
            reals.append((active_ramp, True))
        subs = [
            self._ramp_view(r, SUBPHASE_LABELS[i] if i < len(SUBPHASE_LABELS) else "extra", active=a)
            for i, (r, a) in enumerate(reals)
        ]
        return {
            "sr": rd["sr"], "stage": rd["stage"], "round": rd["round"],
            "subphases": subs,
        }

    # ---- 输出 ---------------------------------------------------------- #
    def snapshot(self, n: int = 2) -> list[dict]:
        """最近 n 个回合（含进行中的当前回合）。"""
        views = [self._round_view(r) for r in self.rounds]
        if self._cur_round is not None:
            views.append(self._round_view(self._cur_round, cur=True))
        return views[-n:]

    def to_dict(self) -> list[dict]:
        """全量回合（录制导出用）。"""
        return self.snapshot(n=len(self.rounds) + 1)
