"""同期群 v2、圈选跨周范围、回退核对与上线闸口测试。"""
from __future__ import annotations

import unittest

from app.cohort_v2 import (
    compute_local_week_cohort,
    legacy_cohort_baseline,
)
from app.fixtures import SEGMENT_CONDITION, build_source_partitions
from app.migration_batch import build_migration_batch
from app.services.time_migration import TimeMigrationService


class CohortV2Test(unittest.TestCase):
    def setUp(self) -> None:
        self.batch = build_migration_batch(build_source_partitions())

    def test_single_cohort_week_w47_size_four(self) -> None:
        matrix = compute_local_week_cohort(self.batch, "注册", "登录")
        self.assertEqual(matrix.cohort_weeks, ["2026-W46"])
        self.assertEqual(matrix.cohort_sizes, [4])  # u5 被隔离，不进分母
        # 回访在次周（周偏移 1）：u1/u2/u4 共 3 人
        self.assertEqual(matrix.retained_counts[0][0], 0)
        self.assertEqual(matrix.retained_counts[0][1], 3)
        self.assertEqual(matrix.retention_matrix[0][1], 75.0)
        self.assertEqual(matrix.retention_matrix[0][0], 0.0)

    def test_matrix_declares_valid_partitions(self) -> None:
        matrix = compute_local_week_cohort(self.batch, "注册", "登录")
        self.assertEqual(
            matrix.valid_partitions,
            sorted(["reg_2026w46_sun", "ret_2026w47_mon"]),
        )
        self.assertEqual(matrix.quarantine_count, 1)
        self.assertEqual(matrix.assumed_metadata_count, 2)

    def test_legacy_engine_buckets_cross_week_into_same_week(self) -> None:
        # 旧口径 UTC 整除：周日注册和周一回访落入同一个 UTC 周桶
        legacy = legacy_cohort_baseline(self.batch, "注册", "登录")
        self.assertEqual(legacy["total_init_count"], 4)
        self.assertEqual(legacy["retention_matrix"][0][0], 75.0)
        # 旧报告分母可核对：每个 UTC 周桶的注册人数
        self.assertEqual(sum(legacy["cohort_denominators"].values()), 4)

    def test_segment_filtered_cohort_denominator(self) -> None:
        # 自然流量渠道：u1/u2/u4（u3 付费投放被排除，u5 被隔离）
        matrix = compute_local_week_cohort(
            self.batch, "注册", "登录", include_expr=SEGMENT_CONDITION,
        )
        self.assertEqual(matrix.cohort_sizes, [3])
        self.assertEqual(matrix.retention_matrix[0][1], 100.0)


class TimeMigrationServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.svc = TimeMigrationService()
        self.batch = self.svc.build_and_register()

    def test_segment_reports_cross_week_scope(self) -> None:
        result = self.svc.segment_selection("batch-time-v2-001")
        self.assertTrue(result["ok"])
        self.assertEqual(result["matched_count"], 6)  # 3 注册 + 3 回访
        self.assertEqual(
            result["matched_user_ids"], ["u1", "u2", "u4"],
        )
        span = result["cross_week_scope"]
        self.assertTrue(span["batch_span"]["crosses_week_boundary"])
        self.assertEqual(
            span["batch_span"]["covered_weeks"], ["2026-W46", "2026-W47"],
        )
        # 圈选结果上的留存矩阵：3 人注册、次周全部回访
        self.assertEqual(result["cohort_matrix"]["cohort_sizes"], [3])
        self.assertEqual(result["cohort_matrix"]["retention_matrix"][0][1], 100.0)

    def test_rollback_check_denominators(self) -> None:
        result = self.svc.rollback_check("batch-time-v2-001")
        self.assertTrue(result["ok"])
        self.assertEqual(result["legacy_total_init"], 4)
        self.assertEqual(
            result["denominator_agreement"]["v2_total_init"],
            result["denominator_agreement"]["legacy_total_init"],
        )
        self.assertIn("valid_partitions", result["v2_report"])

    def test_cutover_gate_blocks_unknown_unit(self) -> None:
        gate = self.svc.cutover_gate("batch-time-v2-001")
        self.assertFalse(gate["ok"])
        self.assertEqual(gate["blocked_count"], 1)
        self.assertEqual(gate["blocked_events"][0]["reason_code"], "UNIT_UNKNOWN")
        self.assertEqual(gate["admitted_count"], 5)  # 4 confirmed 迁移 + 1 原生回访
        self.assertEqual(gate["assumed_count"], 2)

    def test_unknown_batch_raises(self) -> None:
        with self.assertRaises(KeyError):
            self.svc.require_batch("not-exist")


if __name__ == "__main__":
    unittest.main()
