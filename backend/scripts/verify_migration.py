#!/usr/bin/env python3
"""时间协议迁移：部署前后核对脚本。

用周日注册、周一回访的两组待迁移分区，分别在部署前（旧 UTC 口径）
与部署后（新本地周口径）核对三件事：

1. 样本所属周：旧口径把跨周事件错归同一周，新口径正确拆到 W46/W47；
2. 圈选人数：自然流量注册用户 3 人（u1/u2/u4），污染单位事件被隔离；
3. 留存：旧口径回访落在周偏移 0，新口径落在周偏移 1（次周 100% 留存）。

同时输出上线闸口结论与回退核对所需的旧报告分母。

用法（backend 目录下）：
    .venv/bin/python scripts/verify_migration.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# 允许直接从仓库 backend/ 目录运行
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.cohort_v2 import compare_engines, legacy_cohort_baseline  # noqa: E402
from app.fixtures import (  # noqa: E402
    REGISTRATION_PARTITION_ID,
    RETURN_PARTITION_ID,
    SEGMENT_CONDITION,
    build_source_partitions,
)
from app.migration_batch import (  # noqa: E402
    build_migration_batch,
    registration_return_span,
)
from app.services.time_migration import TimeMigrationService  # noqa: E402

BATCH_ID = "verify-batch"
INIT_EVENT = "注册"
RETURN_EVENT = "登录"


def _print(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def main() -> int:
    svc = TimeMigrationService()
    svc.build_and_register(build_source_partitions(), batch_id=BATCH_ID)
    batch = svc.require_batch(BATCH_ID)

    failures: list[str] = []

    # ---- 迁移批次总览：有效事件分区 + 隔离 ----
    _print("一、迁移批次与有效事件分区")
    overview = batch.to_dict()
    print(json.dumps({
        "batch_id": overview["batch_id"],
        "default_tz": overview["default_tz"],
        "source_partitions": overview["source_partitions"],
        "valid_partitions": overview["valid_partitions"],
        "stats": overview["stats"],
        "quarantine": overview["quarantine"],
    }, ensure_ascii=False, indent=2))
    if overview["valid_partitions"] != sorted(
        [REGISTRATION_PARTITION_ID, RETURN_PARTITION_ID],
    ):
        failures.append("有效事件分区与两组待迁移分区不一致")
    if overview["stats"]["quarantine_count"] != 1:
        failures.append("污染单位事件未被隔离")

    # ---- 部署前：旧口径（回退基线） ----
    _print("二、部署前核对（旧协议：UTC 整除 7 天分周，裸秒事件）")
    legacy = legacy_cohort_baseline(batch, INIT_EVENT, RETURN_EVENT)
    print(json.dumps(legacy, ensure_ascii=False, indent=2))
    pre_week0_retention = legacy["retention_matrix"][0][0]
    if pre_week0_retention != 75.0:
        failures.append("旧口径当周留存不是预期的 75.0（跨周回访被错算到当周）")

    # ---- 部署后：新口径（本地周） ----
    _print("三、部署后核对（新协议：业务时区本地周）")
    comparison = compare_engines(batch, INIT_EVENT, RETURN_EVENT)
    v2 = comparison["v2"]
    print(json.dumps(v2, ensure_ascii=False, indent=2))
    if v2["cohort_weeks"] != ["2026-W46"]:
        failures.append("注册样本所属周不是 2026-W46")
    if v2["retention_matrix"][0][0] != 0.0 or v2["retention_matrix"][0][1] != 75.0:
        failures.append("新口径次周留存不是预期的 75.0")
    if set(v2["valid_partitions"]) != {REGISTRATION_PARTITION_ID, RETURN_PARTITION_ID}:
        failures.append("留存矩阵未写明本次有效事件分区")

    # ---- 跨周归属 ----
    _print("四、样本所属周与跨周范围")
    span = registration_return_span(batch)
    print(json.dumps(span, ensure_ascii=False, indent=2))
    if not span["batch_span"]["crosses_week_boundary"]:
        failures.append("未识别出注册→回访跨周")
    if span["batch_span"]["covered_weeks"] != ["2026-W46", "2026-W47"]:
        failures.append("跨周覆盖范围不是 W46→W47")

    # ---- 圈选人数（同步报告跨周范围） ----
    _print("五、圈选接口核对（条件：%s）" % SEGMENT_CONDITION)
    segment = svc.segment_selection(BATCH_ID, include_expr=SEGMENT_CONDITION)
    print(json.dumps({
        "rule_name": segment["rule_name"],
        "matched_count": segment["matched_count"],
        "matched_user_ids": segment["matched_user_ids"],
        "valid_partitions": segment["valid_partitions"],
        "cross_week_scope_weeks":
            segment["cross_week_scope"]["batch_span"]["covered_weeks"],
        "cohort_sizes": segment["cohort_matrix"]["cohort_sizes"],
        "retention_matrix": segment["cohort_matrix"]["retention_matrix"],
    }, ensure_ascii=False, indent=2))
    if segment["matched_user_ids"] != ["u1", "u2", "u4"]:
        failures.append("圈选人数或人群与预期（u1/u2/u4）不符")
    if segment["cohort_matrix"]["retention_matrix"][0][1] != 100.0:
        failures.append("圈选人群次周留存不是预期的 100.0")

    # ---- 上线闸口 ----
    _print("六、新协议上线闸口（无法识别单位的事件不能加入新周期）")
    gate = svc.cutover_gate(BATCH_ID)
    print(json.dumps(gate, ensure_ascii=False, indent=2))
    if gate["ok"]:
        failures.append("闸口未拦截单位无法识别的事件")
    if gate["blocked_events"][0]["event_id"] != "reg-u5-badunit":
        failures.append("被隔离事件不是 reg-u5-badunit")

    # ---- 回退核对：旧报告分母 ----
    _print("七、回退核对：旧报告分母")
    rollback = svc.rollback_check(BATCH_ID)
    print(json.dumps({
        "legacy_report_denominators": rollback["legacy_report_denominators"],
        "legacy_total_init": rollback["legacy_total_init"],
        "denominator_agreement": rollback["denominator_agreement"],
        "rollback_engine": rollback["rollback_engine"],
    }, ensure_ascii=False, indent=2))
    if rollback["legacy_total_init"] != 4:
        failures.append("旧报告分母（注册数）不是预期的 4")

    _print("核对结论")
    if failures:
        print("未通过：")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("全部通过：")
    print("  1. 周日注册归属 2026-W46、周一回访归属 2026-W47，跨周范围 W46→W47")
    print("  2. 圈选命中 u1/u2/u4 共 3 名注册用户，污染单位事件已隔离")
    print("  3. 新口径次周留存 100%（圈选人群）/75%（全量），旧口径错算在当周")
    print("  4. 矩阵写明有效事件分区，回退分母 4 与旧报告对齐")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
