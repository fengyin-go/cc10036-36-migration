"""时间迁移批次：把含旧秒级数据的分区迁到含单位和时区的新时间协议。

批次（:class:`MigrationBatch`）是同期群取数的唯一事实来源，每条事件都携带：

* ``protocol_time``：归一后的新协议时间（value/unit/tz + 本地周归属）
* ``source_partition``：来自哪个待迁移分区（矩阵需写明有效事件分区）
* ``metadata_mode``：``confirmed``（新协议原生或注册源已确认）还是
  ``assumed``（回访源迟到缺元数据，按批次默认时区推断）
* ``week_key`` / ``week_index``：业务时区本地周，支持跨周定位

无法识别单位/时区的事件不进入 ``migrated_events``，而是进入 ``quarantine``，
新协议上线闸口据此拒绝把它们加入新周期。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

from app.fixtures import BATCH_DEFAULT_TZ
from app.time_protocol import (
    LEGACY_TIME_FIELDS,
    ProtocolTime,
    TimeProtocolError,
    cross_week_span,
    local_week_range,
    parse_event_time,
)


@dataclass
class MigratedEvent:
    """迁移后的单条事件。"""

    event_id: str
    user_id: str
    event_name: str
    source_partition: str
    source: str
    metadata_mode: str          # confirmed / assumed
    protocol_time: ProtocolTime
    week_key: str
    week_index: int
    iso_local: str
    epoch_seconds: int
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        # 合并原始业务字段（如渠道），圈选条件与业务过滤直接在迁移事件视图上执行；
        # 协议字段优先，避免被原始裸秒时间字段覆盖。
        merged = dict(self.raw)
        merged.update({
            "event_id": self.event_id,
            "user_id": self.user_id,
            "event_name": self.event_name,
            "source_partition": self.source_partition,
            "source": self.source,
            "metadata_mode": self.metadata_mode,
            "protocol_time": self.protocol_time.to_dict(),
            "week_key": self.week_key,
            "week_index": self.week_index,
            "iso_local": self.iso_local,
            "epoch_seconds": self.epoch_seconds,
        })
        return merged


@dataclass
class QuarantinedEvent:
    """被隔离的事件：过不了新协议校验，不能加入新周期。"""

    event_id: str
    source_partition: str
    reason_code: str
    reason_message: str
    raw: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MigrationBatch:
    """一次时间迁移批次的完整产物。"""

    batch_id: str
    default_tz: str
    migrated_events: list[MigratedEvent]
    quarantine: list[QuarantinedEvent]
    source_partitions: list[str]
    # 有效事件分区：实际产出迁移事件的分区（矩阵必须写明）
    valid_partitions: list[str]
    partition_stats: dict[str, dict[str, Any]]
    assumed_metadata_events: list[str]

    def events_as_dicts(self) -> list[dict[str, Any]]:
        """给同期群/圈选引擎消费的事件视图（含旧引擎兼容字段 event_time）。"""
        return [event.to_dict() for event in self.migrated_events]

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "protocol_version": "time-v2",
            "default_tz": self.default_tz,
            "source_partitions": self.source_partitions,
            "valid_partitions": self.valid_partitions,
            "stats": {
                "source_event_count": sum(
                    s["source_event_count"] for s in self.partition_stats.values()
                ),
                "migrated_count": len(self.migrated_events),
                "quarantine_count": len(self.quarantine),
                "assumed_metadata_count": len(self.assumed_metadata_events),
                "confirmed_metadata_count": len(self.migrated_events)
                - len(self.assumed_metadata_events),
            },
            "partition_stats": self.partition_stats,
            "migrated_events": self.events_as_dicts(),
            "quarantine": [q.to_dict() for q in self.quarantine],
        }


def _event_metadata_mode(event: dict[str, Any], partition_meta: dict[str, Any]) -> str:
    """判定事件时间元数据是确认态还是推断态。

    新协议原生 ``time`` 结构自带单位和时区 -> confirmed；
    旧裸秒事件只有在分区元数据确认了时区时才是 confirmed，
    回访源迟到缺元数据的裸秒事件一律 assumed。
    """
    if isinstance(event.get("time"), dict):
        return "confirmed"
    if any(f in event for f in LEGACY_TIME_FIELDS) and partition_meta.get(
        "timezone_confirmed", False,
    ):
        return "confirmed"
    return "assumed"


def build_migration_batch(
    partitions: list[dict[str, Any]],
    *,
    batch_id: str = "batch-time-v2-001",
    default_tz: str = BATCH_DEFAULT_TZ,
) -> MigrationBatch:
    """对两组（或更多）待迁移分区执行时间协议迁移。

    升级窗口内分析照常交付：缺元数据的迟到分区不阻塞批次，
    其裸秒事件按 ``default_tz`` 推断并显式标注 assumed。
    """
    migrated: list[MigratedEvent] = []
    quarantine: list[QuarantinedEvent] = []
    partition_ids: list[str] = []
    partition_stats: dict[str, dict[str, Any]] = {}
    assumed_ids: list[str] = []

    for partition in partitions:
        pid = str(partition["partition_id"])
        partition_ids.append(pid)
        meta = partition.get("metadata", {}) or {}
        events = partition.get("events", [])
        ok_count = 0
        q_count = 0
        assumed_count = 0

        for raw_event in events:
            event_id = str(raw_event.get("event_id", f"{pid}-evt"))
            mode = _event_metadata_mode(raw_event, meta)
            # 可信时区：优先用分区已确认时区；缺元数据的迟到源回退到批次默认值
            trusted_tz = meta.get("timezone") or default_tz
            try:
                pt = parse_event_time(raw_event, trusted_tz=trusted_tz)
            except TimeProtocolError as exc:
                quarantine.append(QuarantinedEvent(
                    event_id=event_id,
                    source_partition=pid,
                    reason_code=exc.code,
                    reason_message=exc.message,
                    raw=raw_event,
                ))
                q_count += 1
                continue

            week = local_week_range(pt)
            migrated.append(MigratedEvent(
                event_id=event_id,
                user_id=str(raw_event.get("user_id", "")),
                event_name=str(raw_event.get("event_name", "")),
                source_partition=pid,
                source=str(partition.get("source", "")),
                metadata_mode=mode,
                protocol_time=pt,
                week_key=week.week_key,
                week_index=week.week_index,
                iso_local=pt.iso_local(),
                epoch_seconds=pt.epoch_seconds_int(),
                raw=raw_event,
            ))
            ok_count += 1
            if mode == "assumed":
                assumed_count += 1
                assumed_ids.append(event_id)

        partition_stats[pid] = {
            "source": partition.get("source", ""),
            "arrival": meta.get("arrival"),
            "timezone_confirmed": bool(meta.get("timezone_confirmed", False)),
            "source_event_count": len(events),
            "migrated_count": ok_count,
            "quarantine_count": q_count,
            "assumed_metadata_count": assumed_count,
            "metadata_time_unit": meta.get("time_unit"),
            "metadata_timezone": meta.get("timezone"),
            "effective_timezone": meta.get("timezone") or default_tz,
        }

    valid_partitions = sorted({
        event.source_partition for event in migrated
    })

    return MigrationBatch(
        batch_id=batch_id,
        default_tz=default_tz,
        migrated_events=migrated,
        quarantine=quarantine,
        source_partitions=partition_ids,
        valid_partitions=valid_partitions,
        partition_stats=partition_stats,
        assumed_metadata_events=assumed_ids,
    )


# ---------------------------------------------------------------------------
# 跨周范围：注册 -> 回访
# ---------------------------------------------------------------------------

def registration_return_span(
    batch: MigrationBatch,
    init_event_name: str = "注册",
    return_event_name: str = "登录",
) -> dict[str, Any]:
    """基于迁移批次定位本次注册回访实际跨越的周范围。

    取每个用户首次注册与首次回访时间，汇总批次级跨周范围，
    圈选接口同步报告该范围。
    """
    per_user: list[dict[str, Any]] = []
    first_init: ProtocolTime | None = None
    last_return: ProtocolTime | None = None

    for user_id in sorted({e.user_id for e in batch.migrated_events}):
        user_events = [e for e in batch.migrated_events if e.user_id == user_id]
        inits = sorted(
            (e for e in user_events if e.event_name == init_event_name),
            key=lambda e: e.epoch_seconds,
        )
        returns = sorted(
            (e for e in user_events if e.event_name == return_event_name),
            key=lambda e: e.epoch_seconds,
        )
        if not inits:
            continue
        init_pt = inits[0].protocol_time
        return_pt = returns[0].protocol_time if returns else None
        entry: dict[str, Any] = {
            "user_id": user_id,
            "register_week": inits[0].week_key,
            "register_iso_local": inits[0].iso_local,
        }
        if return_pt is not None and return_pt.epoch_seconds() > init_pt.epoch_seconds():
            span = cross_week_span(init_pt, return_pt)
            entry.update({
                "return_week": returns[0].week_key,
                "return_iso_local": returns[0].iso_local,
                "crosses_week_boundary": span["crosses_week_boundary"],
                "week_offset": span["week_offset"],
                "covered_weeks": span["covered_weeks"],
            })
            if last_return is None or return_pt.epoch_seconds() > last_return.epoch_seconds():
                last_return = return_pt
        else:
            entry.update({
                "return_week": None,
                "crosses_week_boundary": False,
                "week_offset": None,
                "covered_weeks": [inits[0].week_key],
            })
        if first_init is None or init_pt.epoch_seconds() < first_init.epoch_seconds():
            first_init = init_pt
        per_user.append(entry)

    batch_span = cross_week_span(first_init, last_return) if (
        first_init is not None and last_return is not None
    ) else None
    return {
        "batch_id": batch.batch_id,
        "tz": batch.default_tz,
        "per_user": per_user,
        "batch_span": batch_span,
    }
