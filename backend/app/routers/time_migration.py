"""时间迁移路由：批次构建、同期群取数、圈选跨周报告、回退核对与上线闸口。

路径统一挂在 ``/api/time-migration`` 前缀下，先于通用资源路由
``/api/{module}`` 注册，避免被动态路径抢占。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.fixtures import (
    BATCH_DEFAULT_TZ,
    SEGMENT_CONDITION,
    SEGMENT_RULE_NAME,
    build_source_partitions,
)
from app.migration_batch import registration_return_span
from app.services.time_migration import time_migration_service
from app.time_protocol import protocol_document

router = APIRouter(prefix="/api/time-migration", tags=["时间协议迁移"])


class BuildBatchPayload(BaseModel):
    batch_id: str = "batch-time-v2-001"
    default_tz: str = BATCH_DEFAULT_TZ
    # 允许调用方传入自定义分区；为空时使用周日注册/周一回访事实分区
    partitions: list[dict[str, Any]] | None = None


class CohortPayload(BaseModel):
    batch_id: str = "batch-time-v2-001"
    init_event: str = "注册"
    return_event: str = "登录"
    include_expr: str = ""
    exclude_expr: str = ""
    max_periods: int = Field(default=4, ge=1, le=12)


class SegmentPayload(BaseModel):
    batch_id: str = "batch-time-v2-001"
    rule_name: str = SEGMENT_RULE_NAME
    include_expr: str = SEGMENT_CONDITION
    exclude_expr: str = ""
    init_event: str = "注册"
    return_event: str = "登录"


class RollbackPayload(BaseModel):
    batch_id: str = "batch-time-v2-001"
    init_event: str = "注册"
    return_event: str = "登录"
    include_expr: str = ""
    exclude_expr: str = ""


@router.get("/protocol")
def time_protocol_doc() -> dict[str, Any]:
    """新时间协议说明（单位、时区、周口径、旧数据读取路径、上线闸口）。"""
    return protocol_document()


@router.get("/source-partitions")
def source_partitions() -> dict[str, Any]:
    """查看两组待迁移分区的原始形态（迁移前）。"""
    partitions = build_source_partitions()
    return {
        "ok": True,
        "count": len(partitions),
        "partitions": partitions,
        "default_tz": BATCH_DEFAULT_TZ,
    }


@router.post("/batches")
def build_batch(payload: BuildBatchPayload) -> dict[str, Any]:
    """构建并注册时间迁移批次。"""
    try:
        batch = time_migration_service.build_and_register(
            payload.partitions,
            batch_id=payload.batch_id,
            default_tz=payload.default_tz,
        )
    except Exception as exc:  # 时区非法等构建期错误
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return batch.to_dict()


@router.get("/batches")
def list_batches() -> dict[str, Any]:
    return {"ok": True, "batches": time_migration_service.list_batches()}


@router.get("/batches/{batch_id}")
def get_batch(batch_id: str) -> dict[str, Any]:
    try:
        batch = time_migration_service.require_batch(batch_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    return batch.to_dict()


@router.get("/batches/{batch_id}/cross-week-span")
def cross_week_span(batch_id: str) -> dict[str, Any]:
    """定位批次内注册→回访的跨周归属范围。"""
    try:
        batch = time_migration_service.require_batch(batch_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    return registration_return_span(batch)


@router.post("/cohort")
def cohort(payload: CohortPayload) -> dict[str, Any]:
    """同期群取数：依据时间迁移批次数据，按本地周输出留存矩阵。"""
    try:
        return time_migration_service.cohort(
            payload.batch_id,
            payload.init_event,
            payload.return_event,
            include_expr=payload.include_expr,
            exclude_expr=payload.exclude_expr,
            max_periods=payload.max_periods,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None


@router.post("/segment")
def segment(payload: SegmentPayload) -> dict[str, Any]:
    """圈选接口：圈人并同步报告本次注册回访所用的跨周范围。"""
    try:
        return time_migration_service.segment_selection(
            payload.batch_id,
            rule_name=payload.rule_name,
            include_expr=payload.include_expr,
            exclude_expr=payload.exclude_expr,
            init_event=payload.init_event,
            return_event=payload.return_event,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None


@router.post("/rollback-check")
def rollback_check(payload: RollbackPayload) -> dict[str, Any]:
    """回退核对：输出旧报告分母与新口径对照。"""
    try:
        return time_migration_service.rollback_check(
            payload.batch_id,
            payload.init_event,
            payload.return_event,
            include_expr=payload.include_expr,
            exclude_expr=payload.exclude_expr,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None


@router.get("/batches/{batch_id}/cutover-gate")
def cutover_gate(batch_id: str) -> dict[str, Any]:
    """上线闸口：无法识别单位的事件不能加入新周期。"""
    try:
        return time_migration_service.cutover_gate(batch_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
