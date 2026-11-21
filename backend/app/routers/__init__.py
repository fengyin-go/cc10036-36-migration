"""业务路由汇总：时间迁移等固定前缀路由先注册，通用资源路由兜底。"""
from __future__ import annotations

from app.routers import resource, time_migration

# 固定前缀路由必须排在 /api/{module} 动态路由之前，否则会被抢占
ROUTERS = [time_migration, resource]
