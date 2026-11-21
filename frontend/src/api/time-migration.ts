/** 时间协议迁移 API：批次构建、跨周范围、同期群、圈选、回退核对与上线闸口。 */
import { request } from './client'

export type BatchStats = {
  source_event_count: number
  migrated_count: number
  quarantine_count: number
  assumed_metadata_count: number
  confirmed_metadata_count: number
}

export type MigrationBatch = {
  batch_id: string
  protocol_version: string
  default_tz: string
  source_partitions: string[]
  valid_partitions: string[]
  stats: BatchStats
  partition_stats: Record<string, Record<string, unknown>>
  migrated_events: Record<string, unknown>[]
  quarantine: Record<string, unknown>[]
}

export type CohortMatrix = {
  engine: string
  tz: string
  cohort_weeks: string[]
  period_labels: string[]
  retention_matrix: number[][]
  cohort_sizes: number[]
  retained_counts: number[][]
  valid_partitions: string[]
  quarantine_count: number
  assumed_metadata_count: number
  rows: Record<string, unknown>[]
}

export type SegmentResult = {
  ok: boolean
  rule_name: string
  population_total: number
  matched_count: number
  match_rate: number
  matched_user_ids: string[]
  valid_partitions: string[]
  cross_week_scope: {
    batch_id: string
    tz: string
    batch_span: {
      crosses_week_boundary: boolean
      week_offset: number
      covered_weeks: string[]
    } | null
    per_user: Record<string, unknown>[]
  }
  cohort_matrix: CohortMatrix
  sample: Record<string, unknown>[]
}

export type RollbackResult = {
  ok: boolean
  batch_id: string
  rollback_engine: string
  legacy_report_denominators: Record<string, number>
  legacy_total_init: number
  legacy_retention_matrix: number[][]
  v2_report: CohortMatrix
  denominator_agreement: {
    same_total_init: boolean
    v2_total_init: number
    legacy_total_init: number
  }
}

export type CutoverGate = {
  ok: boolean
  gate: string
  batch_id: string
  admitted_count: number
  assumed_count: number
  blocked_count: number
  blocked_events: Record<string, unknown>[]
  rule: string
  valid_partitions: string[]
}

async function postJson<T>(path: string, body: unknown = {}): Promise<T> {
  const response = await request(path, { method: 'POST', body: JSON.stringify(body) })
  if (!response.ok) throw new Error(`接口返回 ${response.status}`)
  return (await response.json()) as T
}

async function getJson<T>(path: string): Promise<T> {
  const response = await request(path)
  if (!response.ok) throw new Error(`接口返回 ${response.status}`)
  return (await response.json()) as T
}

export function buildBatch(): Promise<MigrationBatch> {
  return postJson('/api/time-migration/batches', {})
}

export function getBatch(batchId: string): Promise<MigrationBatch> {
  return getJson(`/api/time-migration/batches/${batchId}`)
}

export function getCrossWeekSpan(batchId: string) {
  return getJson<{
    batch_id: string
    tz: string
    batch_span: { covered_weeks: string[]; crosses_week_boundary: boolean; week_offset: number } | null
    per_user: Record<string, unknown>[]
  }>(`/api/time-migration/batches/${batchId}/cross-week-span`)
}

export function runCohort(batchId: string): Promise<CohortMatrix> {
  return postJson('/api/time-migration/cohort', { batch_id: batchId })
}

export function runSegment(batchId: string): Promise<SegmentResult> {
  return postJson('/api/time-migration/segment', { batch_id: batchId })
}

export function runRollbackCheck(batchId: string): Promise<RollbackResult> {
  return postJson('/api/time-migration/rollback-check', { batch_id: batchId })
}

export function getCutoverGate(batchId: string): Promise<CutoverGate> {
  return getJson(`/api/time-migration/batches/${batchId}/cutover-gate`)
}

export function getProtocolDoc() {
  return getJson<Record<string, unknown>>('/api/time-migration/protocol')
}
