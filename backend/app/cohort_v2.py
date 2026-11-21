"""同期群 v2 引擎：基于时间迁移批次、按业务时区本地周计算留存。

与旧引擎（``analytics_engine.compute_cohort_retention``，UTC 整除 7 天）的区别：

1. 周归属取迁移事件上的 ``week_key``（业务时区本地周一为起点），周日深夜注册
   与周一回访不再被错算进同一周；
2. 留存矩阵显式写明本次参与计算的有效事件分区（valid_partitions），
   被隔离事件不进分母；
3. 同期群取数只认时间迁移批次产物，不直接读裸秒事件。

回退场景由 :func:`legacy_cohort_baseline` 保留旧口径，分母可与历史报告核对。
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from app.analytics_engine import (
    compute_cohort_retention,
    parse_segment_conditions,
    segment_population,
)
from app.migration_batch import MigrationBatch


@dataclass
class LocalWeekCohortMatrix:
    """本地周同期群矩阵。"""

    cohort_weeks: list[str]
    period_labels: list[str]
    retention_matrix: list[list[float]]
    cohort_sizes: list[int]
    retained_counts: list[list[int]]
    valid_partitions: list[str]
    tz: str
    quarantine_count: int
    assumed_metadata_count: int
    rows: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "engine": "cohort-v2-local-week",
            "tz": self.tz,
            "cohort_weeks": self.cohort_weeks,
            "period_labels": self.period_labels,
            "retention_matrix": self.retention_matrix,
            "cohort_sizes": self.cohort_sizes,
            "retained_counts": self.retained_counts,
            "valid_partitions": self.valid_partitions,
            "quarantine_count": self.quarantine_count,
            "assumed_metadata_count": self.assumed_metadata_count,
            "rows": self.rows,
        }


def compute_local_week_cohort(
    batch: MigrationBatch,
    init_event_name: str,
    return_event_name: str,
    *,
    include_expr: str = "",
    exclude_expr: str = "",
    max_periods: int = 4,
) -> LocalWeekCohortMatrix:
    """按本地周计算同期群留存。

    圈选条件（可选）在迁移事件上过滤后再入同期群，保证"圈选人数"和
    "留存分母"是同一批人。周期 0 = 注册当周，周期 1 = 次周，依此类推。
    """
    events = list(batch.events_as_dicts())

    # 圈选过滤（复用既有条件解析口径）
    include_conditions = parse_segment_conditions(include_expr)
    exclude_conditions = parse_segment_conditions(exclude_expr)
    if include_conditions or exclude_conditions:
        events, _ = segment_population(
            events, include_conditions, exclude_conditions, "all",
        )

    # 每用户首次初始事件 / 首次之后的回访
    user_init: dict[str, dict[str, Any]] = {}
    user_returns: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        uid = event["user_id"]
        if event["event_name"] == init_event_name:
            if uid not in user_init or event["epoch_seconds"] < user_init[uid]["epoch_seconds"]:
                user_init[uid] = event
        elif event["event_name"] == return_event_name:
            user_returns[uid].append(event)

    # 按注册周分同期群
    cohorts: dict[str, set[str]] = defaultdict(set)
    for uid, event in user_init.items():
        cohorts[event["week_key"]].add(uid)

    cohort_weeks = sorted(cohorts)
    matrix: list[list[float]] = []
    sizes: list[int] = []
    retained_matrix: list[list[int]] = []
    detail_rows: list[dict[str, Any]] = []

    for week_key in cohort_weeks:
        users = cohorts[week_key]
        total = len(users)
        sizes.append(total)
        period_counts: list[int] = []
        period_rates: list[float] = []
        for period in range(max_periods):
            retained = 0
            for uid in users:
                init_event = user_init[uid]
                init_index = init_event["week_index"]
                target_index = init_index + period
                returns = user_returns.get(uid, [])
                if any(
                    r["epoch_seconds"] > init_event["epoch_seconds"]
                    and r["week_index"] == target_index
                    for r in returns
                ):
                    retained += 1
            period_counts.append(retained)
            period_rates.append(round(retained / total * 100, 2) if total else 0.0)
        retained_matrix.append(period_counts)
        matrix.append(period_rates)
        detail_rows.append({
            "cohort_week": week_key,
            "cohort_size": total,
            "user_ids": sorted(users),
            "retained_counts": period_counts,
            "retention_pct": period_rates,
        })

    return LocalWeekCohortMatrix(
        cohort_weeks=cohort_weeks,
        period_labels=[f"周偏移{offset}" for offset in range(max_periods)],
        retention_matrix=matrix,
        cohort_sizes=sizes,
        retained_counts=retained_matrix,
        valid_partitions=batch.valid_partitions,
        tz=batch.default_tz,
        quarantine_count=len(batch.quarantine),
        assumed_metadata_count=len(batch.assumed_metadata_events),
        rows=detail_rows,
    )


def legacy_cohort_baseline(
    batch: MigrationBatch,
    init_event_name: str,
    return_event_name: str,
    *,
    include_expr: str = "",
    exclude_expr: str = "",
) -> dict[str, Any]:
    """旧口径基线：把迁移事件还原成裸秒事件走旧引擎，供回退核对分母。

    注意旧引擎按 UTC 整除 7 天分周，跨周场景会与 v2 结果不同，
    这正是迁移要修正的口径问题；回退时需要这份基线对齐历史报告。
    """
    events = list(batch.events_as_dicts())
    include_conditions = parse_segment_conditions(include_expr)
    exclude_conditions = parse_segment_conditions(exclude_expr)
    if include_conditions or exclude_conditions:
        events, _ = segment_population(
            events, include_conditions, exclude_conditions, "all",
        )

    legacy_events = [
        {
            "user_id": e["user_id"],
            "event_name": e["event_name"],
            # 旧协议形态：裸 epoch 秒
            "event_time": e["epoch_seconds"],
        }
        for e in events
    ]
    old_matrix = compute_cohort_retention(
        legacy_events, init_event_name, return_event_name, period_days=7, max_periods=4,
    )
    # 旧引擎各周分母（cohort size），回退核对旧报告分母用
    cohort_sizes: dict[str, int] = defaultdict(int)
    for e in legacy_events:
        if e["event_name"] == init_event_name:
            week = int(e["event_time"] // (7 * 86400))
            cohort_sizes[str(week)] += 1

    return {
        "engine": "legacy-utc-epoch-bucketing",
        "cohort_ids": old_matrix.cohort_ids,
        "period_labels": old_matrix.period_labels,
        "retention_matrix": old_matrix.retention_matrix,
        "cohort_denominators": dict(sorted(cohort_sizes.items())),
        "total_init_count": sum(cohort_sizes.values()),
        "note": "旧口径按 UTC 整除 7 天分周，仅用于回退核对历史报告分母",
    }


def compare_engines(
    batch: MigrationBatch,
    init_event_name: str,
    return_event_name: str,
    *,
    include_expr: str = "",
    exclude_expr: str = "",
) -> dict[str, Any]:
    """部署前后核对：同一批次上旧口径与 v2 口径并列对比。"""
    v2 = compute_local_week_cohort(
        batch, init_event_name, return_event_name,
        include_expr=include_expr, exclude_expr=exclude_expr,
    )
    legacy = legacy_cohort_baseline(
        batch, init_event_name, return_event_name,
        include_expr=include_expr, exclude_expr=exclude_expr,
    )
    return {
        "v2": v2.to_dict(),
        "legacy": legacy,
        "agreement": {
            "same_total_init": sum(v2.cohort_sizes) == legacy["total_init_count"],
            "v2_total_init": sum(v2.cohort_sizes),
            "legacy_total_init": legacy["total_init_count"],
        },
    }
