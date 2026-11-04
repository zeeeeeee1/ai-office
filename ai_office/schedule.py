"""定期の仕事の書き方と、次に動く時刻の計算（端末のローカル時刻）。

書き方:
  daily 08:47        毎日 08:47
  weekdays 09:10     平日（月〜金）09:10
  weekly mon 10:05   毎週月曜 10:05（mon tue wed thu fri sat sun）
  every 30m / every 2h   一定間隔（最短15分）
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Optional

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WEEKDAY_JA = ["月", "火", "水", "木", "金", "土", "日"]
MIN_INTERVAL_MIN = 15

_TIME = r"([01]?\d|2[0-3]):([0-5]\d)"


class ScheduleError(ValueError):
    pass


def parse(spec: str) -> dict:
    s = " ".join(spec.strip().lower().split())
    m = re.fullmatch(r"daily " + _TIME, s)
    if m:
        return {"kind": "daily", "hour": int(m[1]), "minute": int(m[2])}
    m = re.fullmatch(r"weekdays " + _TIME, s)
    if m:
        return {"kind": "weekdays", "hour": int(m[1]), "minute": int(m[2])}
    m = re.fullmatch(r"weekly (mon|tue|wed|thu|fri|sat|sun) " + _TIME, s)
    if m:
        return {"kind": "weekly", "weekday": WEEKDAYS.index(m[1]), "hour": int(m[2]), "minute": int(m[3])}
    m = re.fullmatch(r"every (\d+)(m|h)", s)
    if m:
        minutes = int(m[1]) * (60 if m[2] == "h" else 1)
        if minutes < MIN_INTERVAL_MIN:
            raise ScheduleError(f"間隔は{MIN_INTERVAL_MIN}分以上にしてください")
        return {"kind": "every", "minutes": minutes}
    raise ScheduleError("書き方が違います。例: daily 08:47 / weekdays 09:10 / weekly mon 10:05 / every 2h")


def describe(spec: str) -> str:
    p = parse(spec)
    if p["kind"] == "every":
        mins = p["minutes"]
        return f"{mins // 60}時間ごと" if mins % 60 == 0 else f"{mins}分ごと"
    t = f"{p['hour']:02d}:{p['minute']:02d}"
    if p["kind"] == "daily":
        return f"毎日 {t}"
    if p["kind"] == "weekdays":
        return f"平日 {t}"
    return f"毎週{WEEKDAY_JA[p['weekday']]}曜 {t}"


def next_run(spec: str, after: Optional[datetime] = None) -> datetime:
    """after より後で、最初に動く時刻（ローカル・naive）を返す。"""
    p = parse(spec)
    after = (after or datetime.now()).replace(second=0, microsecond=0)
    if p["kind"] == "every":
        return after + timedelta(minutes=p["minutes"])
    cand = after.replace(hour=p["hour"], minute=p["minute"])
    if cand <= after:
        cand += timedelta(days=1)
    for _ in range(8):
        if p["kind"] == "daily":
            return cand
        if p["kind"] == "weekdays" and cand.weekday() < 5:
            return cand
        if p["kind"] == "weekly" and cand.weekday() == p["weekday"]:
            return cand
        cand += timedelta(days=1)
    raise ScheduleError("次の実行時刻を決められませんでした")
