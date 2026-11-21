"""时间协议单元测试：单位归一、时区校验、旧秒数据可读、本地周跨周定位。"""
from __future__ import annotations

import unittest
from datetime import datetime

from app.time_protocol import (
    ProtocolTime,
    TimeProtocolError,
    build_protocol_time,
    canonical_unit,
    cross_week_span,
    local_week_range,
    parse_event_time,
    parse_legacy_seconds,
    resolve_timezone,
)

# 2026-11-15 23:30:00 +08:00（周日深夜）
SUN_REGISTER_EPOCH = 1794756600
# 2026-11-16 00:30:00 +08:00（周一凌晨）
MON_RETURN_EPOCH = 1794760200
TZ = "Asia/Shanghai"


class TimeProtocolTest(unittest.TestCase):
    def test_canonical_unit_aliases(self) -> None:
        self.assertEqual(canonical_unit("seconds"), "s")
        self.assertEqual(canonical_unit("MS"), "ms")
        self.assertEqual(canonical_unit("micros"), "us")
        self.assertEqual(canonical_unit("nano"), "ns")

    def test_unknown_and_missing_unit_rejected(self) -> None:
        with self.assertRaises(TimeProtocolError) as ctx:
            canonical_unit("jiffy")
        self.assertEqual(ctx.exception.code, "UNIT_UNKNOWN")
        with self.assertRaises(TimeProtocolError) as ctx:
            canonical_unit(None)
        self.assertEqual(ctx.exception.code, "UNIT_MISSING")

    def test_timezone_validation(self) -> None:
        self.assertEqual(resolve_timezone("Asia/Shanghai"), "Asia/Shanghai")
        with self.assertRaises(TimeProtocolError) as ctx:
            resolve_timezone("Mars/Olympus")
        self.assertEqual(ctx.exception.code, "TZ_UNKNOWN")
        with self.assertRaises(TimeProtocolError):
            resolve_timezone("")

    def test_millisecond_conversion(self) -> None:
        pt = build_protocol_time(MON_RETURN_EPOCH * 1_000, "ms", TZ)
        self.assertEqual(pt.unit, "ms")
        self.assertEqual(pt.epoch_seconds_int(), MON_RETURN_EPOCH)

    def test_legacy_seconds_remain_readable(self) -> None:
        # 已有秒级数据继续可读：单位补 s，时区取可信来源
        pt = parse_legacy_seconds(SUN_REGISTER_EPOCH, TZ)
        self.assertEqual(pt.unit, "s")
        self.assertEqual(pt.epoch_seconds_int(), SUN_REGISTER_EPOCH)
        local = pt.local_datetime()
        self.assertEqual((local.year, local.month, local.day), (2026, 11, 15))
        self.assertEqual(local.hour, 23)

    def test_parse_event_time_dual_path(self) -> None:
        legacy = parse_event_time({"event_time": SUN_REGISTER_EPOCH}, TZ)
        native = parse_event_time(
            {"time": {"value": MON_RETURN_EPOCH * 1_000, "unit": "ms", "tz": TZ}}, TZ,
        )
        self.assertEqual(legacy.unit, "s")
        self.assertEqual(native.unit, "ms")
        self.assertEqual(native.epoch_seconds_int(), MON_RETURN_EPOCH)
        with self.assertRaises(TimeProtocolError):
            parse_event_time({"event_id": "x"}, TZ)

    def test_local_week_sunday_vs_monday(self) -> None:
        reg_week = local_week_range(parse_legacy_seconds(SUN_REGISTER_EPOCH, TZ))
        ret_week = local_week_range(parse_legacy_seconds(MON_RETURN_EPOCH, TZ))
        # 本地周口径：周日在 W46，周一在 W47
        self.assertEqual(reg_week.week_key, "2026-W46")
        self.assertEqual(ret_week.week_key, "2026-W47")
        self.assertEqual(ret_week.week_index - reg_week.week_index, 1)

    def test_utc_view_same_day_but_local_weeks_differ(self) -> None:
        # UTC 视角两个时间点都在 2026-11-15，旧 UTC 整除会错归同一周
        reg = datetime.utcfromtimestamp(SUN_REGISTER_EPOCH)
        ret = datetime.utcfromtimestamp(MON_RETURN_EPOCH)
        self.assertEqual(reg.date(), ret.date())

    def test_cross_week_span_content(self) -> None:
        span = cross_week_span(
            parse_legacy_seconds(SUN_REGISTER_EPOCH, TZ),
            parse_legacy_seconds(MON_RETURN_EPOCH, TZ),
        )
        self.assertTrue(span["crosses_week_boundary"])
        self.assertEqual(span["week_offset"], 1)
        self.assertEqual(span["covered_weeks"], ["2026-W46", "2026-W47"])

    def test_protocol_time_immutable(self) -> None:
        pt = ProtocolTime(value=1, unit="s", tz=TZ)
        with self.assertRaises(Exception):
            pt.unit = "ms"  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
