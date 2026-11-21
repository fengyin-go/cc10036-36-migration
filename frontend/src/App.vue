<template>
  <div class="app-shell">
    <aside class="app-side">
      <h1 class="app-title">多维分析查询与指标平台</h1>
      <nav class="nav-list">
        <RouterLink v-for="item in navItems" :key="item.path" :to="item.path" class="nav-item">
          {{ item.label }}
        </RouterLink>
      </nav>
    </aside>
    <main class="app-main">
      <header class="app-head">
        <span class="head-desc">面向数据分析师和业务用户，提供多维聚合查询、指标口径、分群圈选和数据集市的数据分析平台</span>
        <span class="head-user">当前值班：数据工程组</span>
      </header>
      <RouterView />
    </main>
  </div>
</template>

<script setup lang="ts">
const navItems = [{ label: "运营概览", path: "/" }, { label: "时间协议迁移", path: "/time-migration" }, { label: "指标定义", path: "/metric_def" }, { label: "多维立方", path: "/cube_builder" }, { label: "切片查询", path: "/slice_query" }, { label: "分群圈选", path: "/segment_rule" }, { label: "看板布局", path: "/dashboard_layout" }, { label: "即席导出", path: "/adhoc_export" }, { label: "报表订阅", path: "/report_subscription" }, { label: "异常检测", path: "/anomaly_detect" }, { label: "下钻路径", path: "/drill_path" }, { label: "语义层", path: "/semantic_layer" }, { label: "查询加速", path: "/query_accelerator" }, { label: "归因模型", path: "/attribution_model" }, { label: "同期群分析", path: "/cohort_analysis" }, { label: "数据血缘", path: "/data_lineage" }, { label: "查询调度", path: "/query_scheduler" }, { label: "结果缓存", path: "/result_cache" }, { label: "数据范围", path: "/olap_scope" }, { label: "实验分析", path: "/ab_analysis" }]
</script>
