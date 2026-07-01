"""ClockTrack：顶栏时钟的时间轴 track（live 模式在线维护）。

把每帧的时钟读数 `(stage, round, countdown, sr_status)` 逐帧记成一条时间序列，
并从序列里派生「事件」——这就是阶段判定的原料：倒计时的动态本身编码了阶段。

  倒计时稳定递减        → planning
  倒计时归零 / 消失     → 马上切 combat（countdown_expired 事件）
  回合号 +1、倒计时重置 → 新一轮 planning（round_advance / countdown_reset 事件）

本类只「记录 + 派生事件」，不下阶段结论（那是后续 SceneClassifier 的事，
它会消费这条 track）。设计上先把真实数据落成 JSON，用来观察规律、定阈值。

注意（实测）：海克斯 / 神明浮层会把顶栏调暗，DigitReader 当前阈值读不到 →
sr_status='miss'，countdown=None。miss 不更新单调锚点，故 track 不会被污染，
只是这几帧「时钟盲读」——这个 miss 模式本身也是浮层的一个信号。
"""
from __future__ import annotations


# 倒计时判定阈值（秒）。倒计时每秒 -1，轮询 ~0.5s，故正常帧间跌幅很小。
_RESET_JUMP = 4    # 倒计时回升 ≥ 此值 → 视作「重置」（新一轮开始）
_EXPIRE_MAX = 3    # 上一帧倒计时 ≤ 此值后变 None/0 → 视作「归零」（预判切 combat）


class ClockTrack:
    """维护整局的时钟 track：全量样本 + 派生事件。

    - samples：逐帧样本（全量，供录制导出 JSON）。
    - events：派生事件（round_advance / countdown_reset / countdown_expired）。
    - snapshot()：给 state dict 用的精简块（最近若干样本 + 事件 + 倒计时趋势）。
    """

    def __init__(self, recent: int = 10):
        self.recent = recent
        self.reset()

    def reset(self) -> None:
        """新一局（game_start）清空。"""
        self.samples: list[dict] = []
        self.events: list[dict] = []
        self.started_ts: int | None = None
        self._last_good_sr: tuple[int, int] | None = None  # 最近一次 ok 的 (stage, round)
        self._prev_cd: int | None = None                   # 上一帧倒计时（用于跳变检测）

    # ---- 每帧更新 ------------------------------------------------------ #
    def update(self, clock: dict, ts: int) -> dict:
        """喂入一帧时钟读数，追加样本并派生事件，返回该样本。"""
        if self.started_ts is None:
            self.started_ts = ts
        stage, rnd, cd = clock["stage"], clock["round"], clock["countdown"]
        sample = {
            "ts": ts,
            "stage": stage,
            "round": rnd,
            "countdown": cd,
            "sr_status": clock.get("sr_status"),
            "cd_conf": round(float(clock.get("cd_confidence", 0.0)), 3),
        }
        self.samples.append(sample)
        self._detect(stage, rnd, cd, clock.get("sr_status"), ts)
        self._prev_cd = cd
        return sample

    def _detect(self, stage, rnd, cd, sr_status, ts) -> None:
        # 回合前进：sr 干净读且 (stage,round) 严格增大
        if sr_status == "ok" and stage is not None:
            cur = (stage, rnd)
            if self._last_good_sr is not None and cur > self._last_good_sr:
                self._emit("round_advance", ts, stage, rnd,
                           **{"from": self._sr_str(self._last_good_sr), "to": self._sr_str(cur)})
            self._last_good_sr = cur

        # 倒计时回升 → 重置（新一轮 planning 开始）
        if cd is not None and self._prev_cd is not None and cd - self._prev_cd >= _RESET_JUMP:
            self._emit("countdown_reset", ts, stage, rnd, from_=self._prev_cd, to=cd)

        # 倒计时归零 / 消失（上一帧已很小，本帧读不到）→ 预判切 combat
        if cd is None and self._prev_cd is not None and self._prev_cd <= _EXPIRE_MAX:
            self._emit("countdown_expired", ts, stage, rnd, last=self._prev_cd)

    def _emit(self, etype: str, ts: int, stage, rnd, **extra) -> None:
        ev = {"ts": ts, "type": etype, "at": self._sr_str((stage, rnd)) if stage is not None else None}
        # from_ 关键字避开保留字，导出成 "from"
        if "from_" in extra:
            extra["from"] = extra.pop("from_")
        ev.update(extra)
        self.events.append(ev)

    @staticmethod
    def _sr_str(sr: tuple[int, int] | None) -> str | None:
        return f"{sr[0]}-{sr[1]}" if sr else None

    # ---- 派生：倒计时趋势（不下阶段结论，只给线索）--------------------- #
    def _cd_trend(self) -> str:
        """最近两个有效倒计时的走向：falling / rising / flat / none。"""
        cds = [s["countdown"] for s in self.samples if s["countdown"] is not None]
        if len(cds) < 2:
            return "none"
        d = cds[-1] - cds[-2]
        if d <= -1:
            return "falling"
        if d >= 1:
            return "rising"
        return "flat"

    # ---- 输出 ---------------------------------------------------------- #
    def snapshot(self) -> dict:
        """给 state dict 的精简 track 块（最近样本 + 全部事件 + 趋势）。"""
        return {
            "frame_count": len(self.samples),
            "started_ts": self.started_ts,
            "cd_trend": self._cd_trend(),
            "recent": self.samples[-self.recent:],
            "events": self.events,
        }

    def to_dict(self, meta: dict | None = None) -> dict:
        """整局全量导出（录制成 JSON 文件用）。"""
        return {
            "meta": {
                "started_ts": self.started_ts,
                "frame_count": len(self.samples),
                **(meta or {}),
            },
            "samples": self.samples,
            "events": self.events,
        }
