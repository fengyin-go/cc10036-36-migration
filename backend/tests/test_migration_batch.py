"""时间迁移批次测试：两组分区、推断元数据、隔离闸口、跨周范围。"""
from __future__ import annotations

import unittest

from app.fixtures import (
    BATCH_DEFAULT_TZ,
    REGISTRATION_PARTITION_ID,
    RETURN_PARTITION_ID,
    build_source_partitions,
)
from app.migration_batch import (
    build_migration_batch,
    registration_return_span,
)


class MigrationBatchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.batch = build_migration_batch(build_source_partitions())

    def test_two_source_partitions_registered(self) -> None:
        self.assertEqual(
            sorted(self.batch.source_partitions),
            sorted([REGISTRATION_PARTITION_ID, RETURN_PARTITION_ID]),
        )

    def test_valid_partitions_written_explicitly(self) -> None:
        # 矩阵依据的有效事件分区必须显式写明，且两个分区都有有效事件
        self.assertEqual(
            self.batch.valid_partitions,
            sorted([REGISTRATION_PARTITION_ID, RETURN_PARTITION_ID]),
        )

    def test_bad_unit_quarantined_not_admitted(self) -> None:
        self.assertEqual(len(self.batch.quarantine), 1)
        q = self.batch.quarantine[0]
        self.assertEqual(q.event_id, "reg-u5-badunit")
        self.assertEqual(q.reason_code, "UNIT_UNKNOWN")
        migrated_ids = {e.event_id for e in self.batch.migrated_events}
        self.assertNotIn("reg-u5-badunit", migrated_ids)

    def test_late_partition_metadata_assumed(self) -> None:
        # 回访源迟到缺元数据：裸秒事件按默认时区推断并标注 assumed
        assumed = {
            e.event_id: e for e in self.batch.migrated_events
            if e.metadata_mode == "assumed"
        }
        self.assertEqual(set(assumed), {"ret-u1", "ret-u2"})
        for event in assumed.values():
            self.assertEqual(event.source_partition, RETURN_PARTITION_ID)
            self.assertEqual(event.protocol_time.tz, BATCH_DEFAULT_TZ)
        # 同分区的新协议原生毫秒事件仍是 confirmed
        ret_native = next(
            e for e in self.batch.migrated_events if e.event_id == "ret-u4"
        )
        self.assertEqual(ret_native.metadata_mode, "confirmed")
        self.assertEqual(ret_native.protocol_time.unit, "ms")

    def test_registration_partition_confirmed(self) -> None:
        for event in self.batch.migrated_events:
            if event.source_partition == REGISTRATION_PARTITION_ID:
                self.assertEqual(event.metadata_mode, "confirmed")

    def test_local_week_attribution_crosses_boundary(self) -> None:
        by_id = {e.event_id: e for e in self.batch.migrated_events}
        self.assertEqual(by_id["reg-u1"].week_key, "2026-W46")
        self.assertEqual(by_id["ret-u1"].week_key, "2026-W47")

    def test_partition_stats_counts(self) -> None:
        reg_stats = self.batch.partition_stats[REGISTRATION_PARTITION_ID]
        ret_stats = self.batch.partition_stats[RETURN_PARTITION_ID]
        self.assertEqual(reg_stats["source_event_count"], 5)
        self.assertEqual(reg_stats["migrated_count"], 4)
        self.assertEqual(reg_stats["quarantine_count"], 1)
        self.assertEqual(ret_stats["source_event_count"], 3)
        self.assertEqual(ret_stats["assumed_metadata_count"], 2)
        self.assertFalse(ret_stats["timezone_confirmed"])
        self.assertEqual(ret_stats["effective_timezone"], BATCH_DEFAULT_TZ)

    def test_registration_return_span(self) -> None:
        span = registration_return_span(self.batch)
        self.assertIsNotNone(span["batch_span"])
        self.assertTrue(span["batch_span"]["crosses_week_boundary"])
        self.assertEqual(
            span["batch_span"]["covered_weeks"], ["2026-W46", "2026-W47"],
        )
        u1 = next(row for row in span["per_user"] if row["user_id"] == "u1")
        self.assertTrue(u1["crosses_week_boundary"])
        self.assertEqual(u1["week_offset"], 1)
        # u3 无回访
        u3 = next(row for row in span["per_user"] if row["user_id"] == "u3")
        self.assertIsNone(u3["return_week"])

    def test_events_carry_legacy_compatible_seconds(self) -> None:
        # 迁移事件同时保留 epoch_seconds，旧引擎回退路径可直接消费
        for event in self.batch.events_as_dicts():
            self.assertIsInstance(event["epoch_seconds"], int)


if __name__ == "__main__":
    unittest.main()
