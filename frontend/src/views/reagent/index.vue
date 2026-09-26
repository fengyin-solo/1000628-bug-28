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
      <article v-for="item in statCards" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="applyFilters">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model.trim="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <label class="filter-item">
        <span>物料状态</span>
        <select v-model="filters.status">
          <option value="">全部状态</option>
          <option v-for="status in statuses" :key="status" :value="status">{{ status }}</option>
        </select>
      </label>
      <label class="filter-check">
        <input v-model="excludeFrozen" type="checkbox" />
        <span>筛除已冻结物料</span>
      </label>
      <label class="filter-item">
        <span>排序方式</span>
        <select v-model="sort">
          <option value="expiry_asc">有效期（近 → 远）</option>
          <option value="expiry_desc">有效期（远 → 近）</option>
          <option value="id_asc">登记顺序</option>
        </select>
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
            :class="{ sortable: column === '有效期至' }"
            @click="column === '有效期至' && toggleSort()"
          >
            {{ column }}
            <span v-if="column === '有效期至'" class="sort-mark">{{ sortMark }}</span>
          </th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">
            <template v-if="column === '有效期至'">
              <span>{{ row[column] ?? '—' }}</span>
              <span v-if="row['临期标记']" class="tag tag-near">临期</span>
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
          <td :colspan="columns.length + 1" class="empty-state">暂无符合条件的试剂耗材数据，可调整条件或先登记试剂物料</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条试剂耗材记录，第 {{ page }} / {{ totalPages }} 页</span>
      <span class="pager">
        <button class="btn" type="button" :disabled="page <= 1" @click="goPage(page - 1)">上一页</button>
        <button
          v-for="pageNo in pageNumbers"
          :key="pageNo"
          class="btn"
          type="button"
          :class="{ primary: pageNo === page }"
          @click="goPage(pageNo)"
        >
          {{ pageNo }}
        </button>
        <button class="btn" type="button" :disabled="page >= totalPages" @click="goPage(page + 1)">下一页</button>
      </span>
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
const PAGE_SIZE = 10
const columns = ["物料编号", "物料名称", "规格纯度", "批号", "结存数量", "有效期至", "保管人员", "物料状态"]
const filterFields = ["物料编号", "物料名称", "规格纯度", "批号"]
const actions = ["冻结物料", "解冻物料", "登记耗尽"]
const statuses = ["正常可用", "临近有效期", "已冻结", "已耗尽"]
// URL 里保留的查询条件键，刷新页面后据此还原筛选、排序与翻页。
const QUERY_KEYS = [...filterFields, 'status', 'excludeFrozen', 'sort', 'page']

const route = useRoute()
const router = useRouter()

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const excludeFrozen = ref(false)
const sort = ref('expiry_asc')
const page = ref(1)
const cardStats = ref<Record<string, number>>({})

const statCards = computed(() => [
  { label: '可用物料', value: cardStats.value['可用物料'] ?? 0 },
  { label: '临期物料', value: cardStats.value['临期物料'] ?? 0 },
  { label: '已冻结物料', value: cardStats.value['已冻结物料'] ?? 0 },
])

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))
const pageNumbers = computed(() => {
  const last = totalPages.value
  const start = Math.min(Math.max(1, page.value - 2), Math.max(1, last - 4))
  return Array.from({ length: Math.min(5, last) }, (_, index) => start + index).filter(no => no <= last)
})
const sortMark = computed(() => (sort.value === 'expiry_desc' ? '▼' : sort.value === 'id_asc' ? '·' : '▲'))

function statusTagClass(statusValue: unknown): string {
  if (statusValue === '临近有效期') return 'tag-near'
  if (statusValue === '已冻结') return 'tag-frozen'
  if (statusValue === '已耗尽') return 'tag-exhausted'
  return 'tag-ok'
}

// 筛选、排序、翻页共用这一份条件，任何请求都不会漏带参数。
function buildParams(): Record<string, string> {
  const params: Record<string, string> = { page: String(page.value), size: String(PAGE_SIZE), sort: sort.value }
  for (const field of filterFields) {
    if (filters.value[field]) params[field] = filters.value[field]
  }
  if (filters.value.status) params.status = filters.value.status
  if (excludeFrozen.value) params.exclude_frozen = 'true'
  return params
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(buildParams()).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('试剂物料列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    cardStats.value = payload.stats ?? {}
    // 过滤后结果变少，当前页超出范围时退回最后一页，避免翻到空白页又混入口径不一致的数据。
    if (rows.value.length === 0 && page.value > 1 && total.value > 0) {
      page.value = Math.max(1, Math.ceil(total.value / PAGE_SIZE))
      await reload()
      return
    }
    syncUrl()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '试剂耗材列表读取失败'
  }
}

function syncUrl() {
  const query: Record<string, string> = {}
  const params = buildParams()
  for (const key of QUERY_KEYS) {
    const mapped = key === 'excludeFrozen' ? 'exclude_frozen' : key
    const value = params[mapped]
    if (value && !(key === 'page' && value === '1') && !(key === 'sort' && value === 'expiry_asc')) {
      query[key] = value
    }
  }
  void router.replace({ query })
}

function restoreFromUrl() {
  for (const field of filterFields) {
    const value = route.query[field]
    filters.value[field] = typeof value === 'string' ? value : ''
  }
  const statusValue = route.query.status
  filters.value.status = typeof statusValue === 'string' && statuses.includes(statusValue) ? statusValue : ''
  excludeFrozen.value = route.query.excludeFrozen === 'true'
  const sortValue = route.query.sort
  sort.value = typeof sortValue === 'string' && ['expiry_asc', 'expiry_desc', 'id_asc'].includes(sortValue) ? sortValue : 'expiry_asc'
  page.value = Math.max(1, Number(route.query.page) || 1)
}

function applyFilters() {
  page.value = 1
  void reload()
}

function resetFilters() {
  filters.value = {}
  excludeFrozen.value = false
  sort.value = 'expiry_asc'
  page.value = 1
  void reload()
}

function goPage(pageNo: number) {
  if (pageNo < 1 || pageNo > totalPages.value || pageNo === page.value) return
  page.value = pageNo
  void reload()
}

function toggleSort() {
  sort.value = sort.value === 'expiry_asc' ? 'expiry_desc' : 'expiry_asc'
  page.value = 1
  void reload()
}

function exportRows() {
  // 导出与列表、翻页同一份条件，只是去掉分页上限。
  const params = buildParams()
  delete params.page
  delete params.size
  window.open(`${ENDPOINT}/export?${new URLSearchParams(params).toString()}`, '_blank')
}

function openCreate() {
  errorMessage.value = '试剂物料登记入口尚未接入审批流'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('试剂耗材动作未生效，请稍后重试')
    }
    // 动作完成后沿用当前条件与页码刷新，冻结/解冻入口保持不变。
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '试剂耗材操作失败'
  }
}

onMounted(() => {
  restoreFromUrl()
  void reload()
})
</script>
