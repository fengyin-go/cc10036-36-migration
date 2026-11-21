"""时间迁移事实数据：两组待迁移分区。

场景（业务时区 Asia/Shanghai）：

* 注册分区 ``reg_2026w46_sun``：周日（2026-11-15）深夜注册，注册源已确认时区
  和秒级单位，其中混入一个单位被污染（``unit=jiffy``）的事件，用于验证上线闸口。
* 回访分区 ``ret_2026w47_mon``：周一（2026-11-16）凌晨回访，来自另一个分区，
  数据迟到且分区仍缺时间元数据（无 unit、无 tz），升级窗口内按批次默认时区
  以"推断"方式继续交付，事件逐条标注 metadata_mode=assumed。

UTC 视角下两批事件落在同一个 UTC 自然日（11-15），旧引擎按 UTC 整除 7 天
会把二者归入同一周；新协议按上海本地周可正确识别出跨周（2026-W46 → W47）。

另外各放一个新协议原生事件（毫秒单位），证明新协议通道与历史秒级通道并存。
"""
from __future__ import annotations

from typing import Any

# 可信的注册源时区（注册源已能确认时区）
REGISTRATION_TZ = "Asia/Shanghai"
# 回访源迟到、缺时间元数据时使用的批次默认时区
BATCH_DEFAULT_TZ = "Asia/Shanghai"

# 关键事实时间点（epoch 秒）
# 2026-11-15 23:30:00 +08:00（周日深夜）= 2026-11-15 15:30:00 UTC
SUN_REGISTER_EPOCH = 1794756600
# 2026-11-16 00:30:00 +08:00（周一凌晨）= 2026-11-15 16:30:00 UTC
MON_RETURN_EPOCH = 1794760200
# 2026-11-16 08:00:00 +08:00（周一上午，毫秒原生事件）
MON_RETURN_MS_EPOCH = 1794787200

REGISTRATION_PARTITION_ID = "reg_2026w46_sun"
RETURN_PARTITION_ID = "ret_2026w47_mon"

# 升级窗口内用于演示的圈选规则（自然流量渠道的注册用户）
SEGMENT_RULE_NAME = "跨周注册回访人群"
SEGMENT_CONDITION = "渠道:eq:自然流量"


def build_source_partitions() -> list[dict[str, Any]]:
    """构造两组待迁移分区（迁移前的原始形态）。"""
    registration_partition: dict[str, Any] = {
        "partition_id": REGISTRATION_PARTITION_ID,
        "source": "registration",
        "metadata": {
            # 注册源已能确认时区与单位
            "time_unit": "s",
            "timezone": REGISTRATION_TZ,
            "timezone_confirmed": True,
            "arrival": "on_time",
        },
        "events": [
            {
                "event_id": "reg-u1",
                "user_id": "u1",
                "event_name": "注册",
                "event_time": SUN_REGISTER_EPOCH,
                "渠道": "自然流量",
            },
            {
                "event_id": "reg-u2",
                "user_id": "u2",
                "event_name": "注册",
                "event_time": SUN_REGISTER_EPOCH + 120,
                "渠道": "自然流量",
            },
            {
                "event_id": "reg-u3",
                "user_id": "u3",
                "event_name": "注册",
                "event_time": SUN_REGISTER_EPOCH + 240,
                "渠道": "付费投放",
            },
            {
                # 新协议原生事件：毫秒单位、自带时区，走新协议通道
                "event_id": "reg-u4",
                "user_id": "u4",
                "event_name": "注册",
                "time": {
                    "value": (SUN_REGISTER_EPOCH + 360) * 1_000,
                    "unit": "ms",
                    "tz": REGISTRATION_TZ,
                },
                "渠道": "自然流量",
            },
            {
                # 单位被污染：新协议上线时无法识别，必须隔离，不能加入新周期
                "event_id": "reg-u5-badunit",
                "user_id": "u5",
                "event_name": "注册",
                "time": {
                    "value": (SUN_REGISTER_EPOCH + 480) * 1_000,
                    "unit": "jiffy",
                    "tz": REGISTRATION_TZ,
                },
                "渠道": "自然流量",
            },
        ],
    }

    return_partition: dict[str, Any] = {
        "partition_id": RETURN_PARTITION_ID,
        "source": "return_visit",
        "metadata": {
            # 回访源迟到且还缺时间元数据
            "time_unit": None,
            "timezone": None,
            "timezone_confirmed": False,
            "arrival": "late",
        },
        "events": [
            {
                "event_id": "ret-u1",
                "user_id": "u1",
                "event_name": "登录",
                "event_time": MON_RETURN_EPOCH,
                "渠道": "自然流量",
            },
            {
                "event_id": "ret-u2",
                "user_id": "u2",
                "event_name": "登录",
                "event_time": MON_RETURN_EPOCH + 180,
                "渠道": "自然流量",
            },
            # u3 注册在付费投放渠道、不回访，用于验证圈选分母
            {
                # 新协议原生毫秒事件，与缺元数据的迟到裸秒事件同分区并存
                "event_id": "ret-u4",
                "user_id": "u4",
                "event_name": "登录",
                "time": {
                    "value": MON_RETURN_MS_EPOCH * 1_000,
                    "unit": "ms",
                    "tz": REGISTRATION_TZ,
                },
                "渠道": "自然流量",
            },
        ],
    }

    return [registration_partition, return_partition]
