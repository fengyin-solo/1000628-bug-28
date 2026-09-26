<template>
  <section class="page" data-module="reagent">
    <header class="page-head">
      <div>
        <h2>试剂耗材管理</h2>
        <p class="page-desc">维护试剂物料，围绕物料编号、物料名称、规格纯度、批号做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记试剂物料</button>
        <button class="btn" type="button" @click="exportRows">导出试剂耗材清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="applyFilters">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <label class="filter-item">
        <span>物料状态</span>
        <select v-model="statusFilter">
          <option value="">全部状态</option>
          <option v-for="status in statuses" :key="status" :value="status">{{ status }}</option>
        </select>
      </label>
      <label class="filter-item checkbox-item">
        <input v-model="hideFrozen" type="checkbox" />
        <span>隐藏已冻结物料</span>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th
            v-for="column in columns"
            :key="column"
            :class="{ sortable: sortableFields.includes(column) }"
            @click="toggleSort(column)"
          >
            {{ column }}
            <span v-if="sortField === column" class="sort-mark">{{ sortOrder === 'asc' ? '↑' : '↓' }}</span>
          </th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">
            <template v-if="column === '有效期至'">
              {{ row[column] ?? '—' }}
              <span v-if="row['临期']" class="tag tag-warn">临期</span>
            </template>
            <template v-else-if="column === '物料状态'">
              <span class="tag" :class="statusTagClass(row[column])">{{ row[column] ?? '—' }}</span>
            </template>
            <template v-else>{{ row[column] ?? '—' }}</template>
          </td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无符合条件的试剂耗材记录，可调整筛选条件</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条试剂耗材记录</span>
      <div class="pager">
        <button class="btn ghost" type="button" :disabled="page <= 1" @click="goPage(-1)">上一页</button>
        <span>第 {{ page }} / {{ pageCount }} 页</span>
        <button class="btn ghost" type="button" :disabled="page >= pageCount" @click="goPage(1)">下一页</button>
      </div>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { request } from '@/api/client'

type Row = Record<string, string | number | boolean | null>

const ENDPOINT = '/api/reagent'
const columns = ["物料编号", "物料名称", "规格纯度", "批号", "结存数量", "有效期至", "保管人员", "物料状态"]
const actions = ["冻结物料", "解冻物料", "登记耗尽"]
const statuses = ["正常可用", "临近有效期", "已冻结", "已耗尽"]
const sortableFields = ["有效期至", "结存数量"]
const PAGE_SIZE = 10

// 筛选框字段与后端查询参数的对应关系，查询、排序、翻页共用同一份条件
const filterParams: Record<string, string> = { "物料编号": "keyword", "规格纯度": "spec", "批号": "batch" }
const filterFields = Object.keys(filterParams)

const route = useRoute()
const router = useRouter()

const rows = ref<Row[]>([])
const total = ref(0)
const page = ref(1)
const sortField = ref('有效期至')
const sortOrder = ref<'asc' | 'desc'>('asc')
const statusFilter = ref('')
const hideFrozen = ref(false)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const stats = ref([
  { label: '可用物料', value: 0 },
  { label: '临期物料', value: 0 },
  { label: '已冻结物料', value: 0 },
])

const pageCount = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))

function readQuery() {
  const query = route.query
  const next: Record<string, string> = {}
  for (const [field, param] of Object.entries(filterParams)) {
    const value = query[param]
    if (typeof value === 'string' && value) next[field] = value
  }
  filters.value = next
  const status = query.status
  statusFilter.value = typeof status === 'string' && statuses.includes(status) ? status : ''
  hideFrozen.value = query.hide_frozen === '1'
  const sort = query.sort
  sortField.value = typeof sort === 'string' && sortableFields.includes(sort) ? sort : '有效期至'
  sortOrder.value = query.order === 'desc' ? 'desc' : 'asc'
  const rawPage = Number(query.page)
  page.value = Number.isInteger(rawPage) && rawPage > 0 ? rawPage : 1
}

function buildParams() {
  const params = new URLSearchParams()
  for (const [field, param] of Object.entries(filterParams)) {
    const value = (filters.value[field] ?? '').trim()
    if (value) params.set(param, value)
  }
  if (statusFilter.value) params.set('status', statusFilter.value)
  if (hideFrozen.value) params.set('hide_frozen', '1')
  params.set('sort', sortField.value)
  params.set('order', sortOrder.value)
  params.set('page', String(page.value))
  params.set('size', String(PAGE_SIZE))
  return params
}

function syncUrl() {
  // 条件写回地址栏，刷新后筛选、排序与页码都保留
  const query: Record<string, string> = {}
  buildParams().forEach((value, key) => {
    query[key] = value
  })
  void router.replace({ query })
}

async function reload() {
  errorMessage.value = ''
  syncUrl()
  try {
    const response = await request(`${ENDPOINT}?${buildParams().toString()}`)
    if (!response.ok) {
      throw new Error('试剂物料列表读取失败')
    }
    const payload = await response.json()
    const maxPage = Math.max(1, Math.ceil((payload.total ?? 0) / PAGE_SIZE))
    if (page.value > maxPage) {
      page.value = maxPage
      return reload()
    }
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    const summary = payload.summary ?? {}
    stats.value = [
      { label: '可用物料', value: summary['正常可用'] ?? 0 },
      { label: '临期物料', value: summary['临近有效期'] ?? 0 },
      { label: '已冻结物料', value: summary['已冻结'] ?? 0 },
    ]
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '试剂耗材列表读取失败'
  }
}

function applyFilters() {
  page.value = 1
  void reload()
}

function resetFilters() {
  filters.value = {}
  statusFilter.value = ''
  hideFrozen.value = false
  sortField.value = '有效期至'
  sortOrder.value = 'asc'
  page.value = 1
  void reload()
}

function toggleSort(column: string) {
  if (!sortableFields.includes(column)) return
  if (sortField.value === column) {
    sortOrder.value = sortOrder.value === 'asc' ? 'desc' : 'asc'
  } else {
    sortField.value = column
    sortOrder.value = 'asc'
  }
  void reload()
}

function goPage(step: number) {
  const next = page.value + step
  if (next < 1 || next > pageCount.value) return
  page.value = next
  void reload()
}

function exportRows() {
  const params = buildParams()
  params.delete('page')
  params.delete('size')
  window.open(`${ENDPOINT}/export?${params.toString()}`, '_blank')
}

function openCreate() {
  errorMessage.value = '试剂物料登记入口尚未接入审批流'
}

function statusTagClass(status: string | number | boolean | null) {
  switch (status) {
    case '临近有效期':
      return 'tag-warn'
    case '已冻结':
      return 'tag-frozen'
    case '已耗尽':
      return 'tag-out'
    default:
      return 'tag-ok'
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    if (!response.ok) {
      throw new Error('试剂耗材动作未生效，请稍后重试')
    }
    const result = await response.json()
    if (!result.ok) {
      throw new Error(result.message || '试剂耗材动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '试剂耗材操作失败'
  }
}

onMounted(() => {
  readQuery()
  void reload()
})
</script>
