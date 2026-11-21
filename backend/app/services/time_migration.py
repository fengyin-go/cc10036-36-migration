"""时间迁移领域服务：批次注册、同期群取数、圈选跨周报告、回退核对与上线闸口。

服务持有内存中的迁移批次注册表（与 Store 的示例数据仓库平行），
同期群取数只接受已注册批次的产物，保证分析在升级窗口内基于同一事实交付。
"""
from __future__ import annotations

from typing import Any

from app.analytics_engine import parse_segment_conditions, segment_population
from app.cohort_v2 import (
    compare_engines,
    compute_local_week_cohort,
    legacy_cohort_baseline,
)
from app.fixtures import (
    BATCH_DEFAULT_TZ,
    SEGMENT_CONDITION,
    SEGMENT_RULE_NAME,
    build_source_partitions,
)
from app.migration_batch import (
    MigrationBatch,
    build_migration_batch,
    registration_return_span,
)


class TimeMigrationService:
    def __init__(self) -> None:
        self._batches: dict[str, MigrationBatch] = {}

    # ---- 批次管理 ----

    def build_and_register(
        self,
        partitions: list[dict[str, Any]] | None = None,
        *,
        batch_id: str = "batch-time-v2-001",
        default_tz: str = BATCH_DEFAULT_TZ,
    ) -> MigrationBatch:
        """构建时间迁移批次并注册（缺省使用周日注册/周一回访事实分区）。"""
        if partitions is None:
            partitions = build_source_partitions()
        batch = build_migration_batch(
            partitions, batch_id=batch_id, default_tz=default_tz,
        )
        self._batches[batch_id] = batch
        return batch

    def get_batch(self, batch_id: str) -> MigrationBatch | None:
        return self._batches.get(batch_id)

    def require_batch(self, batch_id: str) -> MigrationBatch:
        batch = self._batches.get(batch_id)
        if batch is None:
            raise KeyError(f"时间迁移批次 {batch_id} 不存在，请先构建")
        return batch

    def list_batches(self) -> list[dict[str, Any]]:
        return [
            {
                "batch_id": b.batch_id,
                "default_tz": b.default_tz,
                "source_partitions": b.source_partitions,
                "valid_partitions": b.valid_partitions,
                "migrated_count": len(b.migrated_events),
                "quarantine_count": len(b.quarantine),
            }
            for b in self._batches.values()
        ]

    # ---- 同期群取数（依据时间迁移批次数据） ----

    def cohort(
        self,
        batch_id: str,
        init_event: str = "注册",
        return_event: str = "登录",
        *,
        include_expr: str = "",
        exclude_expr: str = "",
        max_periods: int = 4,
    ) -> dict[str, Any]:
        batch = self.require_batch(batch_id)
        matrix = compute_local_week_cohort(
            batch, init_event, return_event,
            include_expr=include_expr, exclude_expr=exclude_expr,
            max_periods=max_periods,
        )
        return matrix.to_dict()

    # ---- 圈选：同步报告跨周范围 ----

    def segment_selection(
        self,
        batch_id: str,
        *,
        rule_name: str = SEGMENT_RULE_NAME,
        include_expr: str = SEGMENT_CONDITION,
        exclude_expr: str = "",
        init_event: str = "注册",
        return_event: str = "登录",
    ) -> dict[str, Any]:
        """圈选接口：在迁移批次上圈人，并同步报告本次注册回访的跨周范围。"""
        batch = self.require_batch(batch_id)
        events = batch.events_as_dicts()
        include_conditions = parse_segment_conditions(include_expr)
        exclude_conditions = parse_segment_conditions(exclude_expr)
        matched, count = segment_population(
            events, include_conditions, exclude_conditions, "all",
        )

        # 用命中事件构造一个受限批次视图，跨周范围只统计圈中人群
        matched_ids = {e["event_id"] for e in matched}
        scoped_batch = MigrationBatch(
            batch_id=batch.batch_id,
            default_tz=batch.default_tz,
            migrated_events=[e for e in batch.migrated_events if e.event_id in matched_ids],
            quarantine=list(batch.quarantine),
            source_partitions=list(batch.source_partitions),
            valid_partitions=sorted({e["source_partition"] for e in matched}),
            partition_stats=dict(batch.partition_stats),
            assumed_metadata_events=[
                eid for eid in batch.assumed_metadata_events if eid in matched_ids
            ],
        )
        span = registration_return_span(scoped_batch, init_event, return_event)
        matrix = compute_local_week_cohort(
            scoped_batch, init_event, return_event, max_periods=4,
        )

        return {
            "ok": True,
            "rule_name": rule_name,
            "include_conditions": [vars(c) for c in include_conditions],
            "exclude_conditions": [vars(c) for c in exclude_conditions],
            "population_total": len(events),
            "matched_count": count,
            "match_rate": round(count / len(events) * 100, 2) if events else 0.0,
            "matched_user_ids": sorted({e["user_id"] for e in matched}),
            "valid_partitions": scoped_batch.valid_partitions,
            "cross_week_scope": span,
            "cohort_matrix": matrix.to_dict(),
            "sample": matched[:5],
        }

    # ---- 回退核对：旧报告分母 ----

    def rollback_check(
        self,
        batch_id: str,
        init_event: str = "注册",
        return_event: str = "登录",
        *,
        include_expr: str = "",
        exclude_expr: str = "",
    ) -> dict[str, Any]:
        """回退核对：输出旧口径分母与新口径分母的对照，回退后可对齐旧报告。"""
        batch = self.require_batch(batch_id)
        comparison = compare_engines(
            batch, init_event, return_event,
            include_expr=include_expr, exclude_expr=exclude_expr,
        )
        legacy = legacy_cohort_baseline(
            batch, init_event, return_event,
            include_expr=include_expr, exclude_expr=exclude_expr,
        )
        return {
            "ok": True,
            "batch_id": batch_id,
            "rollback_engine": "legacy-utc-epoch-bucketing",
            "legacy_report_denominators": legacy["cohort_denominators"],
            "legacy_total_init": legacy["total_init_count"],
            "legacy_retention_matrix": legacy["retention_matrix"],
            "v2_report": comparison["v2"],
            "denominator_agreement": comparison["agreement"],
            "note": "回退后以 legacy_* 字段对齐历史报告；valid_partitions 写明本次有效事件分区",
        }

    # ---- 上线闸口：无法识别单位的事件不能加入新周期 ----

    def cutover_gate(self, batch_id: str) -> dict[str, Any]:
        batch = self.require_batch(batch_id)
        blocking = [q.to_dict() for q in batch.quarantine]
        admitted = [
            e.to_dict() for e in batch.migrated_events
            if e.metadata_mode == "confirmed"
        ]
        return {
            "ok": len(blocking) == 0,
            "gate": "time-v2-cutover",
            "batch_id": batch_id,
            "admitted_count": len(admitted),
            "assumed_count": len(batch.assumed_metadata_events),
            "blocked_count": len(blocking),
            "blocked_events": blocking,
            "rule": "单位或时区无法识别的事件一律隔离，不允许加入新周期",
            "valid_partitions": batch.valid_partitions,
        }


time_migration_service = TimeMigrationService()
