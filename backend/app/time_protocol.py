"""含单位和时区的时间协议（v2）。

旧协议只保存裸 epoch 秒（``event_time`` 整数），无法表达时间单位与业务时区，
跨周归属按 UTC 整除 7 天计算，会把"周日深夜注册、周一回访"错算到同一周。

新协议把时间点统一建模为 :class:`ProtocolTime`：

* ``value``：epoch 计数值（整数）
* ``unit`` ：计数单位（秒/毫秒/微秒/纳秒），单位必须可识别，否则拒绝入新周期
* ``tz``   ：IANA 时区名（如 ``Asia/Shanghai``），周归属按此时区的本地日历计算

旧的秒级数据没有单位和时区字段，通过 :func:`parse_legacy_seconds` 显式补全：
单位固定推断为 ``s``，时区由可信来源（注册源元数据或批次默认时区）提供，
因此历史数据在新协议下继续可读，且读取路径与新数据完全一致。
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# 新协议认可的时间单位：名称 -> 1 个单位等于多少秒（纳秒用整数避免浮点误差）
TIME_UNITS: dict[str, int] = {
    "s": 1,
    "sec": 1,
    "second": 1,
    "seconds": 1,
    "ms": 1_000,
    "millis": 1_000,
    "millisecond": 1_000,
    "milliseconds": 1_000,
    "us": 1_000_000,
    "micros": 1_000_000,
    "microsecond": 1_000_000,
    "microseconds": 1_000_000,
    "ns": 1_000_000_000,
    "nano": 1_000_000_000,
    "nanosecond": 1_000_000_000,
    "nanoseconds": 1_000_000_000,
}

# 新周期事件只接受规范单位名（别名仅用于读取历史/外部数据时归一）
CANONICAL_UNITS = ("s", "ms", "us", "ns")

# 别名 -> 规范单位名
_UNIT_ALIAS_TO_CANONICAL: dict[str, str] = {}
for _canonical, _factor in (("s", 1), ("ms", 1_000), ("us", 1_000_000), ("ns", 1_000_000_000)):
    for _alias, _alias_factor in TIME_UNITS.items():
        if _alias_factor == _factor and _alias not in _UNIT_ALIAS_TO_CANONICAL:
            _UNIT_ALIAS_TO_CANONICAL[_alias] = _canonical

# 旧协议的裸秒字段名，命中即按"秒级历史数据"路径读取
LEGACY_TIME_FIELDS = ("event_time", "ts", "timestamp")


class TimeProtocolError(ValueError):
    """时间协议校验失败：单位无法识别、时区非法等。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True)
class ProtocolTime:
    """新协议时间点：epoch 计数值 + 单位 + IANA 时区。"""

    value: int
    unit: str
    tz: str

    def epoch_seconds(self) -> float:
        """换算为 epoch 秒（浮点，纳秒精度保留到小数）。"""
        divisor = TIME_UNITS[self.unit]
        return self.value / divisor

    def epoch_seconds_int(self) -> int:
        """换算为整数 epoch 秒（向零取整），供旧引擎回退核对。"""
        return int(self.value // TIME_UNITS[self.unit])

    def local_datetime(self) -> datetime:
        """返回业务时区下的本地日历时间（带 tzinfo）。"""
        return datetime.fromtimestamp(self.epoch_seconds(), tz=ZoneInfo(self.tz))

    def iso_local(self) -> str:
        """本地 ISO-8601 字符串，便于报告展示。"""
        return self.local_datetime().isoformat()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def canonical_unit(unit: str | None) -> str:
    """把单位别名归一到规范名；无法识别时抛 TimeProtocolError。"""
    if unit is None or str(unit).strip() == "":
        raise TimeProtocolError("UNIT_MISSING", "事件缺少时间单位，无法加入新周期")
    key = str(unit).strip().lower()
    canonical = _UNIT_ALIAS_TO_CANONICAL.get(key)
    if canonical is None:
        raise TimeProtocolError("UNIT_UNKNOWN", f"无法识别的时间单位：{unit!r}")
    return canonical


def resolve_timezone(tz_name: str | None) -> str:
    """校验 IANA 时区，返回规范名；非法时抛 TimeProtocolError。"""
    if not tz_name:
        raise TimeProtocolError("TZ_MISSING", "事件缺少时区，无法定位本地周归属")
    name = str(tz_name).strip()
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise TimeProtocolError("TZ_UNKNOWN", f"无法识别的时区：{tz_name!r}") from None
    return name


def build_protocol_time(value: Any, unit: str | None, tz: str | None) -> ProtocolTime:
    """构造新协议时间点，单位不可识别或时区非法时抛 TimeProtocolError。

    这是新协议上线后的唯一入周期闸口：任何过不了校验的事件都必须被隔离，
    不能进入新周期（见 migration_batch 的 quarantine 列表）。
    """
    canonical = canonical_unit(unit)
    tz_name = resolve_timezone(tz)
    try:
        int_value = int(value)
    except (TypeError, ValueError):
        raise TimeProtocolError(
            "VALUE_INVALID", f"时间计数值不是整数：{value!r}",
        ) from None
    return ProtocolTime(value=int_value, unit=canonical, tz=tz_name)


def parse_legacy_seconds(value: Any, trusted_tz: str) -> ProtocolTime:
    """读取旧协议裸秒数据：单位固定补 ``s``，时区取可信来源。

    旧数据本身不带时区，``trusted_tz`` 必须来自已确认的分区/来源元数据，
    而不是事件自报字段，避免脏数据伪造时区。
    """
    tz_name = resolve_timezone(trusted_tz)
    try:
        seconds = int(float(value))
    except (TypeError, ValueError):
        raise TimeProtocolError(
            "VALUE_INVALID", f"旧秒级时间无法解析：{value!r}",
        ) from None
    return ProtocolTime(value=seconds, unit="s", tz=tz_name)


def parse_event_time(event: dict[str, Any], trusted_tz: str) -> ProtocolTime:
    """统一事件时间读取入口，兼容新协议结构与旧裸秒字段。

    新协议结构：``event["time"] = {"value": ..., "unit": "ms", "tz": "Asia/Shanghai"}``
    旧协议结构：``event["event_time"] = 1763...``（裸 epoch 秒）
    """
    time_obj = event.get("time")
    if isinstance(time_obj, dict):
        return build_protocol_time(
            time_obj.get("value"), time_obj.get("unit"), time_obj.get("tz"),
        )
    for field in LEGACY_TIME_FIELDS:
        if field in event and event[field] is not None:
            return parse_legacy_seconds(event[field], trusted_tz)
    raise TimeProtocolError("TIME_MISSING", "事件既无新协议 time 结构，也无旧秒级时间字段")


# ---------------------------------------------------------------------------
# 本地周归属（跨周定位的核心）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WeekRange:
    """业务时区下的一个本地周（周一 00:00 到下周一 00:00）。"""

    week_key: str          # ISO 周键，如 2026-W46
    week_index: int        # 自 epoch 起的周序号，便于算周偏移
    start_utc: datetime    # 周起点（UTC 视图，带 tzinfo）
    end_utc: datetime      # 周终点（UTC 视图，开区间）
    tz: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "week_key": self.week_key,
            "week_index": self.week_index,
            "start_local": self.start_utc.astimezone(ZoneInfo(self.tz)).isoformat(),
            "end_local": self.end_utc.astimezone(ZoneInfo(self.tz)).isoformat(),
            "start_utc": self.start_utc.isoformat(),
            "end_utc": self.end_utc.isoformat(),
            "tz": self.tz,
        }


def local_week_range(pt: ProtocolTime) -> WeekRange:
    """定位时间点在业务时区下所属的本地周。

    周口径固定为周一 00:00 起、下周一 00:00 止（开区间），与 ISO-8601 一致；
    中国业务日历本身以周一为一周起点，避免旧实现 UTC 整除带来的周日错位。
    """
    tzinfo = ZoneInfo(pt.tz)
    local_dt = pt.local_datetime()
    monday = (local_dt - timedelta(days=local_dt.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    next_monday = monday + timedelta(days=7)
    iso_year, iso_week, _ = monday.isocalendar()
    # 周序号：用 UTC 整除 7 天得到稳定整数（仅用于偏移计算，展示用 week_key）
    week_index = int(monday.astimezone(timezone.utc).timestamp() // (7 * 86400))
    return WeekRange(
        week_key=f"{iso_year}-W{iso_week:02d}",
        week_index=week_index,
        start_utc=monday.astimezone(timezone.utc),
        end_utc=next_monday.astimezone(timezone.utc),
        tz=pt.tz,
    )


def cross_week_span(start_pt: ProtocolTime, end_pt: ProtocolTime) -> dict[str, Any]:
    """构造一次注册→回访跨越的周范围（圈选接口同步报告用）。"""
    start_week = local_week_range(start_pt)
    end_week = local_week_range(end_pt)
    return {
        "tz": start_pt.tz,
        "start": {
            "iso_local": start_pt.iso_local(),
            "epoch_seconds": start_pt.epoch_seconds_int(),
            "week": start_week.to_dict(),
        },
        "end": {
            "iso_local": end_pt.iso_local(),
            "epoch_seconds": end_pt.epoch_seconds_int(),
            "week": end_week.to_dict(),
        },
        "crosses_week_boundary": start_week.week_key != end_week.week_key,
        "week_offset": end_week.week_index - start_week.week_index,
        "covered_weeks": _enumerate_weeks(start_week, end_week),
    }


def _enumerate_weeks(start_week: WeekRange, end_week: WeekRange) -> list[str]:
    """列出跨周范围覆盖的全部 week_key（含首尾）。"""
    if start_week.week_key == end_week.week_key:
        return [start_week.week_key]
    tzinfo = ZoneInfo(start_week.tz)
    cursor = start_week.start_utc.astimezone(tzinfo)
    keys: list[str] = []
    guard = 0
    while cursor <= end_week.start_utc.astimezone(tzinfo) and guard < 10_000:
        iso_year, iso_week, _ = cursor.isocalendar()
        keys.append(f"{iso_year}-W{iso_week:02d}")
        cursor += timedelta(days=7)
        guard += 1
    return keys


def protocol_document() -> dict[str, Any]:
    """返回新协议说明文档（供 /protocol 接口与报告引用）。"""
    return {
        "protocol_version": "time-v2",
        "canonical_units": list(CANONICAL_UNITS),
        "unit_aliases": sorted(TIME_UNITS),
        "timezone_source": "IANA tz database (zoneinfo)",
        "week_rule": "业务时区本地周，周一 00:00 起、下周一 00:00 止（开区间）",
        "legacy_read_path": "裸 epoch 秒经 parse_legacy_seconds 补 unit=s 与可信时区后继续可读",
        "cutover_gate": "单位无法识别的事件隔离至 quarantine，不进入新周期",
    }
