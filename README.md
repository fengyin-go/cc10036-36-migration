# 多维分析查询与指标平台

面向数据分析师和业务用户，提供多维聚合查询、指标口径、分群圈选和数据集市的数据分析平台

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/data/modules.ts              18个业务模块配置
│   ├── src/api/              统一请求封装
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/base.py     共享服务骨架
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 指标定义 | `metric_def` | 指标 | 指标编码、指标名称、业务域 |
| 多维立方 | `cube_builder` | OLAP立方 | 立方名称、事实表、维度列表 |
| 切片查询 | `slice_query` | 查询模板 | 模板名称、数据立方、查询维值 |
| 分群圈选 | `segment_rule` | 分群规则 | 分群名称、人群类型、筛选条件 |
| 看板布局 | `dashboard_layout` | 看板配置 | 看板名称、所属业务域、图表布局 |
| 即席导出 | `adhoc_export` | 导出任务 | 任务名称、查询SQL、导出格式 |
| 报表订阅 | `report_subscription` | 订阅配置 | 订阅名称、报表对象、推送周期 |
| 异常检测 | `anomaly_detect` | 检测规则 | 规则名称、监控指标、检测算法 |
| 下钻路径 | `drill_path` | 下钻配置 | 路径名称、起始维度、下钻层级 |
| 语义层 | `semantic_layer` | 语义定义 | 语义名称、物理表、字段映射 |
| 查询加速 | `query_accelerator` | 加速配置 | 配置名称、目标查询、加速方式 |
| 归因模型 | `attribution_model` | 归因配置 | 模型名称、归因对象、归因窗口 |
| 同期群分析 | `cohort_analysis` | 群组配置 | 群组名称、初始事件、回访事件 |
| 数据血缘 | `data_lineage` | 血缘节点 | 节点名称、节点类型、上游列表 |
| 查询调度 | `query_scheduler` | 调度计划 | 计划名称、查询语句、执行周期 |
| 结果缓存 | `result_cache` | 缓存条目 | 缓存键、查询描述、结果行数 |
| 数据范围 | `olap_scope` | 范围规则 | 规则名称、数据范围、授权对象 |
| 实验分析 | `ab_analysis` | 实验配置 | 实验名称、对照组、实验组 |

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 时间协议迁移（time-v2）

事件采集流水线迁移到**含单位和时区**的时间协议，解决旧协议裸 epoch 秒按
UTC 整除 7 天分周、把"周日深夜注册、周一回访"错算进同一周的问题。

### 协议与迁移产物

| 文件 | 职责 |
| --- | --- |
| `backend/app/time_protocol.py` | 新协议 `ProtocolTime(value, unit, tz)`：单位归一（s/ms/us/ns）、IANA 时区校验、旧秒级数据补 `unit=s` 与可信时区后继续可读、业务时区本地周（周一起）跨周定位 |
| `backend/app/fixtures.py` | 两组待迁移分区：`reg_2026w46_sun`（周日深夜注册，时区已确认，含 1 条污染单位事件）与 `ret_2026w47_mon`（周一凌晨回访，迟到且缺时间元数据） |
| `backend/app/migration_batch.py` | 构建时间迁移批次：逐条事件写入单位/时区/本地周，迟到缺元数据按批次默认时区推断并标 `assumed`，单位不可识别的事件进 `quarantine` |
| `backend/app/cohort_v2.py` | 基于迁移批次的本地周同期群引擎；矩阵显式写明 `valid_partitions`，并保留旧引擎基线供回退核对分母 |
| `backend/app/services/time_migration.py` | 批次注册、同期群取数、圈选（同步报告跨周范围）、回退核对、上线闸口 |
| `backend/app/routers/time_migration.py` | `/api/time-migration/*` 路由（先于通用 `/api/{module}` 注册） |
| `backend/tests/` | 37 个 unittest 用例，覆盖协议、批次、同期群、圈选、回退与 API 全链路 |
| `backend/scripts/verify_migration.py` | 部署前后核对脚本：样本所属周、圈选人数、留存、闸口、回退分母 |

### 关键口径

- **本地周**：按事件业务时区的本地日历，周一 00:00 起、下周一 00:00 止。
  事实样本中注册归属 `2026-W46`、回访归属 `2026-W47`，跨周范围 `W46 → W47`。
- **升级窗口照常交付**：回访分区迟到缺元数据不阻塞批次，按默认时区
  `Asia/Shanghai` 推断并逐条标注 `metadata_mode=assumed`。
- **上线闸口**：单位/时区无法识别的事件（如 `unit=jiffy`）一律隔离，
  不能加入新周期；闸口接口返回拦截明细。
- **回退核对**：`rollback-check` 输出旧 UTC 口径的注册分母与留存矩阵，
  回退后可与历史报告分母对齐（本批次旧分母 = 4）。

### 接口与核对

```bash
# 后端测试与部署前后核对（backend 目录）
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -I scripts/verify_migration.py

# 主要接口
POST /api/time-migration/batches                 # 构建迁移批次
GET  /api/time-migration/batches/{id}/cutover-gate   # 上线闸口
GET  /api/time-migration/batches/{id}/cross-week-span # 跨周归属范围
POST /api/time-migration/cohort                  # 同期群取数（依据迁移批次）
POST /api/time-migration/segment                 # 圈选并同步报告跨周范围
POST /api/time-migration/rollback-check          # 回退核对旧报告分母
```

前端导航新增「时间协议迁移」工作台（`/time-migration`），一屏展示分区迁移
结果、闸口拦截、跨周范围、留存矩阵、圈选人数与回退分母对照。

领域：数据工程
子方向：数据分析与数据应用
