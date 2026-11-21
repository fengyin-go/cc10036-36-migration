"""时间迁移 API 端到端测试：批次→闸口→同期群→圈选→回退全链路。"""
from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from app.main import app


class TimeMigrationApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    def test_01_protocol_document(self) -> None:
        resp = self.client.get("/api/time-migration/protocol")
        self.assertEqual(resp.status_code, 200)
        doc = resp.json()
        self.assertEqual(doc["protocol_version"], "time-v2")
        self.assertIn("ms", doc["canonical_units"])

    def test_02_source_partitions(self) -> None:
        resp = self.client.get("/api/time-migration/source-partitions")
        data = resp.json()
        self.assertEqual(data["count"], 2)
        arrivals = {p["partition_id"]: p["metadata"]["arrival"] for p in data["partitions"]}
        self.assertEqual(arrivals["reg_2026w46_sun"], "on_time")
        self.assertEqual(arrivals["ret_2026w47_mon"], "late")

    def test_03_build_batch(self) -> None:
        resp = self.client.post("/api/time-migration/batches", json={})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["stats"]["migrated_count"], 7)
        self.assertEqual(data["stats"]["quarantine_count"], 1)
        self.assertEqual(data["stats"]["assumed_metadata_count"], 2)
        self.assertEqual(
            data["valid_partitions"],
            ["reg_2026w46_sun", "ret_2026w47_mon"],
        )

    def test_04_cutover_gate(self) -> None:
        resp = self.client.get(
            "/api/time-migration/batches/batch-time-v2-001/cutover-gate",
        )
        gate = resp.json()
        self.assertFalse(gate["ok"])
        self.assertEqual(gate["blocked_events"][0]["event_id"], "reg-u5-badunit")

    def test_05_cross_week_span(self) -> None:
        resp = self.client.get(
            "/api/time-migration/batches/batch-time-v2-001/cross-week-span",
        )
        span = resp.json()
        self.assertTrue(span["batch_span"]["crosses_week_boundary"])
        self.assertEqual(
            span["batch_span"]["covered_weeks"], ["2026-W46", "2026-W47"],
        )

    def test_06_cohort(self) -> None:
        resp = self.client.post("/api/time-migration/cohort", json={})
        matrix = resp.json()
        self.assertEqual(matrix["engine"], "cohort-v2-local-week")
        self.assertEqual(matrix["cohort_weeks"], ["2026-W46"])
        self.assertEqual(matrix["cohort_sizes"], [4])
        self.assertEqual(matrix["retention_matrix"][0][1], 75.0)

    def test_07_segment_reports_scope(self) -> None:
        resp = self.client.post("/api/time-migration/segment", json={})
        data = resp.json()
        self.assertEqual(data["matched_user_ids"], ["u1", "u2", "u4"])
        self.assertTrue(data["cross_week_scope"]["batch_span"]["crosses_week_boundary"])
        self.assertEqual(data["cohort_matrix"]["retention_matrix"][0][1], 100.0)

    def test_08_rollback_check(self) -> None:
        resp = self.client.post("/api/time-migration/rollback-check", json={})
        data = resp.json()
        self.assertEqual(data["legacy_total_init"], 4)
        self.assertTrue(data["denominator_agreement"]["same_total_init"])
        # 旧口径把跨周回访算在当周（周偏移 0）
        self.assertEqual(data["legacy_retention_matrix"][0][0], 75.0)

    def test_09_batch_not_found(self) -> None:
        resp = self.client.get("/api/time-migration/batches/missing/cutover-gate")
        self.assertEqual(resp.status_code, 404)

    def test_10_resource_routes_still_work(self) -> None:
        # 新增路由不能影响既有 18 个模块的通用资源接口
        resp = self.client.get("/api/cohort_analysis")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("items", resp.json())


if __name__ == "__main__":
    unittest.main()
