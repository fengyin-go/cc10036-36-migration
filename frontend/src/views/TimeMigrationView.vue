<template>
  <section class="page" data-module="time-migration">
    <header class="page-head">
      <div>
        <h2>时间协议迁移</h2>
        <p class="page-desc">
          事件采集流水线迁移至含单位和时区的时间协议：秒级历史数据继续可读，
          按业务时区本地周定位跨周归属，同期群取数依据时间迁移批次。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="build-batch" type="button" :disabled="loading" @click="runAll">
          {{ loading ? '迁移中…' : '构建迁移批次并核对' }}
        </button>
      </div>
    </header>

    <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>

    <template v-if="batch">
      <div class="stat-row">
        <article class="stat-card">
          <span class="stat-label">源事件 / 已迁移</span>
          <strong class="stat-value">{{ batch.stats.source_event_count }} / {{ batch.stats.migrated_count }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">推断元数据（迟到缺字段）</span>
          <strong class="stat-value">{{ batch.stats.assumed_metadata_count }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">隔离事件（单位不可识别）</span>
          <strong class="stat-value">{{ batch.stats.quarantine_count }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">有效事件分区</span>
          <strong class="stat-value">{{ batch.valid_partitions.length }}</strong>
        </article>
      </div>

      <h3>待迁移分区与迁移结果</h3>
      <table class="data-table" data-testid="partition-table">
        <thead>
          <tr>
            <th>分区</th>
            <th>来源</th>
            <th>到达</th>
            <th>时区确认</th>
            <th>生效时区</th>
            <th>源事件</th>
            <th>迁移</th>
            <th>推断</th>
            <th>隔离</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(stats, pid) in batch.partition_stats" :key="pid" :data-testid="`partition-${pid}`">
            <td>{{ pid }}</td>
            <td>{{ stats.source }}</td>
            <td>{{ stats.arrival === 'late' ? '迟到' : '准时' }}</td>
            <td>{{ stats.timezone_confirmed ? '已确认' : '缺失（推断）' }}</td>
            <td>{{ stats.effective_timezone }}</td>
            <td>{{ stats.source_event_count }}</td>
            <td>{{ stats.migrated_count }}</td>
            <td>{{ stats.assumed_metadata_count }}</td>
            <td>{{ stats.quarantine_count }}</td>
          </tr>
        </tbody>
      </table>

      <div v-if="gate" class="panel" data-testid="gate-panel">
        <h3>新协议上线闸口</h3>
        <p :class="gate.ok ? 'gate-ok' : 'gate-blocked'">
          {{ gate.ok ? '全部事件可入新周期' : `拦截 ${gate.blocked_count} 条无法识别单位的事件，不允许加入新周期` }}
        </p>
        <ul>
          <li v-for="item in gate.blocked_events" :key="String(item.event_id)">
            {{ item.event_id }}（{{ item.source_partition }}）：{{ item.reason_message }}
          </li>
        </ul>
        <p class="page-desc">准入 {{ gate.admitted_count }} 条（确认态），推断态 {{ gate.assumed_count }} 条仅用于升级窗口交付。</p>
      </div>

      <div v-if="span?.batch_span" class="panel" data-testid="span-panel">
        <h3>注册回访跨周范围（{{ span.tz }}）</h3>
        <p>
          跨周：<strong>{{ span.batch_span.crosses_week_boundary ? '是' : '否' }}</strong>
          ，周偏移：<strong>{{ span.batch_span.week_offset }}</strong>
          ，覆盖周：<strong>{{ span.batch_span.covered_weeks.join(' → ') }}</strong>
        </p>
        <table class="data-table">
          <thead>
            <tr><th>用户</th><th>注册周</th><th>注册本地时间</th><th>回访周</th><th>回访本地时间</th><th>跨周</th></tr>
          </thead>
          <tbody>
            <tr v-for="row in span.per_user" :key="String(row.user_id)">
              <td>{{ row.user_id }}</td>
              <td>{{ row.register_week }}</td>
              <td>{{ row.register_iso_local }}</td>
              <td>{{ row.return_week ?? '—' }}</td>
              <td>{{ row.return_iso_local ?? '—' }}</td>
              <td>{{ row.crosses_week_boundary ? '是' : '否' }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-if="cohort" class="panel" data-testid="cohort-panel">
        <h3>同期群留存矩阵（本地周口径，有效分区：{{ cohort.valid_partitions.join('、') }}）</h3>
        <table class="data-table">
          <thead>
            <tr>
              <th>注册周</th>
              <th>群大小（分母）</th>
              <th v-for="label in cohort.period_labels" :key="label">{{ label }} 留存率</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(week, index) in cohort.cohort_weeks" :key="week">
              <td>{{ week }}</td>
              <td>{{ cohort.cohort_sizes[index] }}</td>
              <td v-for="(rate, pIndex) in cohort.retention_matrix[index]" :key="pIndex">{{ rate }}%</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-if="segment" class="panel" data-testid="segment-panel">
        <h3>圈选：{{ segment.rule_name }}</h3>
        <p>
          命中 <strong>{{ segment.matched_count }}</strong> 条事件 /
          用户 <strong>{{ segment.matched_user_ids.join('、') }}</strong>
          （命中率 {{ segment.match_rate }}%）
        </p>
        <p v-if="segment.cross_week_scope.batch_span">
          本次注册回访所用跨周范围：
          <strong>{{ segment.cross_week_scope.batch_span.covered_weeks.join(' → ') }}</strong>
        </p>
        <p class="page-desc">
          圈选人群次周留存：{{ segment.cohort_matrix.retention_matrix[0]?.[1] ?? '—' }}%，
          有效事件分区：{{ segment.valid_partitions.join('、') }}
        </p>
      </div>

      <div v-if="rollback" class="panel" data-testid="rollback-panel">
        <h3>回退核对：旧报告分母</h3>
        <table class="data-table">
          <thead>
            <tr><th>口径</th><th>注册分母</th><th>周偏移0 留存</th><th>周偏移1 留存</th></tr>
          </thead>
          <tbody>
            <tr>
              <td>旧（UTC 整除，回退基线）</td>
              <td>{{ rollback.legacy_total_init }}</td>
              <td>{{ rollback.legacy_retention_matrix[0]?.[0] }}%</td>
              <td>{{ rollback.legacy_retention_matrix[0]?.[1] }}%</td>
            </tr>
            <tr>
              <td>新（业务时区本地周）</td>
              <td>{{ rollback.denominator_agreement.v2_total_init }}</td>
              <td>{{ rollback.v2_report.retention_matrix[0]?.[0] }}%</td>
              <td>{{ rollback.v2_report.retention_matrix[0]?.[1] }}%</td>
            </tr>
          </tbody>
        </table>
        <p class="page-desc">
          分母一致性：{{ rollback.denominator_agreement.same_total_init ? '一致' : '不一致' }}；
          旧口径把跨周回访错算在当周，回退后以旧分母 {{ rollback.legacy_total_init }} 对齐历史报告。
        </p>
      </div>
    </template>
  </section>
</template>

<script setup lang="ts">
import { ref } from 'vue'

import {
  buildBatch,
  getCrossWeekSpan,
  getCutoverGate,
  runCohort,
  runRollbackCheck,
  runSegment,
} from '@/api/time-migration'
import type {
  CohortMatrix,
  CutoverGate,
  MigrationBatch,
  RollbackResult,
  SegmentResult,
} from '@/api/time-migration'

const BATCH_ID = 'batch-time-v2-001'
const loading = ref(false)
const errorMessage = ref('')
const batch = ref<MigrationBatch | null>(null)
const gate = ref<CutoverGate | null>(null)
const span = ref<Awaited<ReturnType<typeof getCrossWeekSpan>> | null>(null)
const cohort = ref<CohortMatrix | null>(null)
const segment = ref<SegmentResult | null>(null)
const rollback = ref<RollbackResult | null>(null)

async function runAll() {
  loading.value = true
  errorMessage.value = ''
  try {
    batch.value = await buildBatch()
    const [gateResult, spanResult, cohortResult, segmentResult, rollbackResult] = await Promise.all([
      getCutoverGate(BATCH_ID),
      getCrossWeekSpan(BATCH_ID),
      runCohort(BATCH_ID),
      runSegment(BATCH_ID),
      runRollbackCheck(BATCH_ID),
    ])
    gate.value = gateResult
    span.value = spanResult
    cohort.value = cohortResult
    segment.value = segmentResult
    rollback.value = rollbackResult
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '迁移批次构建失败'
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.panel {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px 14px;
  margin-top: 16px;
}

.gate-ok { color: #157f3b; font-weight: 600; }
.gate-blocked { color: #b42318; font-weight: 600; }

.error-text { color: #b42318; }
</style>
