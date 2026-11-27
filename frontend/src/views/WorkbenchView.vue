<template>
  <section class="page" :data-module="meta.key">
    <header class="page-head">
      <div>
        <h2>{{ meta.name }}</h2>
        <p class="page-desc">{{ meta.desc }}</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">
          登记{{ meta.entity }}
        </button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">
          导出{{ meta.name }}清单
        </button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <p class="status-legend">
      <span v-for="item in statusSummary" :key="item.status" class="legend-item">
        {{ item.status }}：{{ item.count }}
      </span>
    </p>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :data-testid="`filter-${field}`" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" data-testid="query" type="submit">查询</button>
      <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
    </form>

    <!-- 原料袋看板：拼配工作台与料房收货台共用，一袋一个有效位置 -->
    <div v-if="showStock" class="bag-board">
      <h4>原料袋看板（在库 / 在用 / 待收，一袋一个有效位置）</h4>
      <table class="data-table">
        <thead>
          <tr>
            <th>袋号</th>
            <th>批次号</th>
            <th>总量(kg)</th>
            <th>在库量(kg)</th>
            <th>位置</th>
            <th>占用配方</th>
            <th>办理回查</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="bag in bags" :key="String(bag.袋号)" :data-position="String(bag.位置)">
            <td>{{ bag.袋号 }}</td>
            <td>{{ bag.批次号 || '—' }}</td>
            <td>{{ bag.总量 }}</td>
            <td>{{ bag.在库量 }}</td>
            <td><span class="position-tag" :class="`pos-${bag.位置}`">{{ bag.位置 }}</span></td>
            <td>{{ bag.占用配方 || '—' }}</td>
            <td>
              <button class="link" type="button" :data-testid="`bag-status-${bag.袋号}`"
                @click="viewBagStatus(String(bag.袋号))">回查</button>
            </td>
          </tr>
          <tr v-if="!bags.length">
            <td colspan="7" class="empty-state">暂无原料袋，精制分级确认后原料自动入袋</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 袋办理状态：两个工作台各办成了什么、未办成原因、实际入库重量 -->
    <div v-if="bagStatus" class="trace-panel bag-status-panel">
      <h4>
        袋 {{ bagStatus.袋号 }} 办理状态（位置：{{ bagStatus.位置 }}，
        在库 {{ bagStatus.在库量 }}kg / 共 {{ bagStatus.总量 }}kg）
      </h4>
      <div class="side-grid">
        <div class="side-card">
          <strong>拼配工作台</strong>
          <p>配方：{{ bagStatus.拼配侧.配方编号 || '—' }}（#{{ bagStatus.拼配侧.配方ID ?? '—' }}）</p>
          <p>
            领出 {{ bagStatus.拼配侧.领出重量 ?? '—' }}kg ｜
            耗用 {{ bagStatus.拼配侧.已耗用重量 ?? '—' }}kg ｜
            剩余可退 {{ bagStatus.拼配侧.剩余可退 ?? '—' }}kg
          </p>
          <p>状态：{{ bagStatus.拼配侧.状态 }}</p>
        </div>
        <div class="side-card">
          <strong>料房收货台</strong>
          <p>退料签：{{ bagStatus.料房侧.登记编号 || '—' }}（#{{ bagStatus.料房侧.退料签ID ?? '—' }}）</p>
          <p>
            申请退回 {{ bagStatus.料房侧.申请退回重量 ?? '—' }}kg ｜
            实际入库 {{ bagStatus.料房侧.实收重量 == null ? '未实收' : `${bagStatus.料房侧.实收重量}kg` }}
          </p>
          <p>状态：{{ bagStatus.料房侧.状态 }}</p>
        </div>
      </div>
      <p v-if="bagStatus.未办成原因" class="error-text">未办成：{{ bagStatus.未办成原因 }}</p>
      <ol class="event-list">
        <li v-for="(event, idx) in bagStatus.办理记录" :key="idx">
          {{ event.module === 'material_return' ? '料房' : '拼配' }} · {{ event.action }} ·
          {{ formatEventDetail(event.detail) }}
        </li>
      </ol>
      <button class="btn ghost" type="button" @click="bagStatus = null">收起</button>
    </div>

    <!-- 配方用量回查：实际使用来源和用量 -->
    <div v-if="usageRows" class="trace-panel">
      <h4>配方#{{ usageFor }} 实际使用来源和用量</h4>
      <table class="data-table">
        <thead>
          <tr>
            <th>袋号</th>
            <th>来源</th>
            <th>领出(kg)</th>
            <th>耗用(kg)</th>
            <th>退回(kg)</th>
            <th>剩余可退(kg)</th>
            <th>状态</th>
            <th>退料签</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="u in usageRows" :key="u.id">
            <td>{{ u.袋号 }}</td>
            <td>{{ u.来源 }}</td>
            <td>{{ u.领出重量 }}</td>
            <td>{{ u.已耗用重量 }}</td>
            <td>{{ u.退回重量 }}</td>
            <td>{{ u.剩余可退 }}</td>
            <td>{{ u.状态 }}</td>
            <td>{{ formatSlips(u.退料签) }}</td>
          </tr>
          <tr v-if="!usageRows.length">
            <td colspan="8" class="empty-state">该配方尚未领料</td>
          </tr>
        </tbody>
      </table>
      <button class="btn ghost" type="button" @click="usageRows = null">收起</button>
    </div>

    <!-- 批次追溯链面板 -->
    <div v-if="showTrace && traceBatchNo" class="trace-panel">
      <h4>批次 {{ traceBatchNo }} 追溯链</h4>
      <div v-if="traceLoading">加载中...</div>
      <div v-else-if="traceError" class="error-text">{{ traceError }}</div>
      <div v-else class="trace-chain-inline">
        <div v-for="(node, idx) in traceChain" :key="idx" class="trace-node-inline"
          :class="{ frozen: node.frozen }">
          <div class="node-index">{{ idx + 1 }}</div>
          <div class="node-info">
            <strong>{{ getProcessName(node.module) }}</strong>
            <span>ID:{{ node.entry_id }}</span>
            <span class="node-status-tag" :class="node.status">{{ node.status }}</span>
          </div>
          <div v-if="node.data" class="node-meta">
            <span v-if="node.data['鲜叶重量'] || node.data['投入重量']">
              重量: {{ node.data['鲜叶重量'] || node.data['投入重量'] }}kg
            </span>
            <span v-if="node.data['产出重量']">→ 产出: {{ node.data['产出重量'] }}kg</span>
          </div>
          <div v-if="idx < traceChain.length - 1" class="trace-arrow">→</div>
        </div>
      </div>
    </div>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in meta.fields" :key="column">{{ column }}</th>
          <th>当前状态</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :data-testid="`row-${row.id}`" :data-status="String(row.status)">
          <td v-for="column in meta.fields" :key="column">{{ row[column] ?? '—' }}</td>
          <td>{{ row.status }}</td>
          <td class="row-actions">
            <button
              v-for="action in meta.actions"
              :key="action"
              class="link"
              type="button"
              :data-testid="`action-${row.id}-${action}`"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
            <template v-if="isBlending">
              <button class="link" type="button" :data-testid="`issue-${row.id}`"
                @click="issueBag(row)">领出</button>
              <button class="link" type="button" :data-testid="`consume-${row.id}`"
                @click="consumeBag(row)">耗用</button>
              <button class="link" type="button" :data-testid="`return-${row.id}`"
                @click="returnBag(row)">退料</button>
              <button class="link" type="button" :data-testid="`usage-${row.id}`"
                @click="viewUsage(row)">用量回查</button>
            </template>
            <button
              v-if="isMaterialReturn && row['袋号']"
              class="link"
              type="button"
              :data-testid="`status-${row.id}`"
              @click="viewBagStatus(String(row['袋号']))"
            >
              回查
            </button>
            <button
              v-if="row['批次号']"
              class="link trace-link"
              type="button"
              @click="viewBatchTrace(String(row['批次号']))"
              title="查看批次追溯">
              批次追溯
            </button>
            <button
              v-if="isProcessModule && row['上游记录ID']"
              class="link upstream-link"
              type="button"
              @click="viewUpstream(row)" title="查看上游工序">
              查看上游
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="meta.fields.length + 2" class="empty-state">
            暂无{{ meta.name }}数据，可先登记{{ meta.entity }}
          </td>
        </tr>
      </tbody>
    </table>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记{{ meta.entity }}</h3>
        <p v-if="isMaterialReturn" class="modal-hint">
          退料签对应一个真实领用：填袋号与已耗用重量，申请退回重量按真实剩余自动计算。
        </p>
        <label v-for="field in createFields" :key="field" class="create-field">
          <span>{{ field }}</span>
          <input v-model="createValues[field]" :data-testid="`create-${field}`"
            :required="requiredCreateFields.has(field)" />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showCreate = false">取消</button>
          <button class="btn primary" data-testid="submit-create" type="submit" :disabled="creating">
            {{ creating ? '提交中…' : '确认登记' }}
          </button>
        </div>
      </form>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条{{ meta.name }}记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import {
  createEntry,
  downloadEntries,
  listEntries,
  moduleMeta,
  runAction as applyAction,
} from '@/api/backend-service'
import {
  fetchBagStatus,
  fetchUsage,
  issueMaterial,
  listBags,
  receiveReturn,
  recordUsage,
  type BagRow,
  type BagStatus,
} from '@/api/material-client'
import { fetchTraceChain } from '@/api/pipeline-client'
import type { EntryRow } from '@/data/types'

const props = defineProps<{ moduleKey: string }>()
const router = useRouter()
const meta = moduleMeta(props.moduleKey)
const rows = ref<EntryRow[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = meta.fields.slice(0, 3)
const showCreate = ref(false)
const creating = ref(false)
const createValues = ref<Record<string, string>>({})

// 工序模块判断
const PROCESS_ORDER = ['picking', 'withering', 'fixing', 'rolling', 'fermentation', 'drying',
  'grading', 'blending', 'packing', 'trace_code', 'shipment']
const PROCESS_NAMES: Record<string, string> = {
  picking: '鲜叶采摘', withering: '萎凋', fixing: '杀青',
  rolling: '揉捻', fermentation: '发酵', drying: '干燥',
  grading: '精制分级', blending: '拼配', packing: '包装',
  trace_code: '溯源码段', shipment: '发运交接',
}
const isProcessModule = computed(() => PROCESS_ORDER.includes(meta.key))

// 原料领退流转：拼配工作台与料房收货台（配方退料签）
const isBlending = computed(() => meta.key === 'blending')
const isMaterialReturn = computed(() => meta.key === 'material_return')
const showStock = computed(() => isBlending.value || isMaterialReturn.value)
const bags = ref<BagRow[]>([])
const bagStatus = ref<BagStatus | null>(null)
const usageRows = ref<any[] | null>(null)
const usageFor = ref<number | null>(null)

// 退料签登记只需袋号与已耗用重量，其余由系统按真实剩余计算
const createFields = computed(() =>
  isMaterialReturn.value
    ? ['袋号', '已耗用重量', '登记编号', '负责人', '备注说明']
    : meta.fields,
)
const requiredCreateFields = computed(
  () => new Set(isMaterialReturn.value ? ['袋号', '已耗用重量'] : meta.fields),
)
// 批次追溯
const showTrace = ref(false)
const traceBatchNo = ref('')
const traceChain = ref<any[]>([])
const traceLoading = ref(false)
const traceError = ref('')

const stats = computed(() => [
  { label: meta.metrics[0], value: total.value },
  { label: meta.metrics[1], value: rows.value.filter((row) => row.status === meta.statuses[1]).length },
  {
    label: meta.metrics[2],
    value: rows.value.filter((row) => row.status === meta.statuses[meta.statuses.length - 1]).length,
  },
])
const statusSummary = computed(() =>
  meta.statuses.map((status) => ({
    status,
    count: rows.value.filter((row) => String(row.status) === status).length,
  })),
)

function getProcessName(module: string): string {
  return PROCESS_NAMES[module] || module
}

function resetFilters() {
  filters.value = {}
  void reload()
}

async function exportRows() {
  errorMessage.value = ''
  try {
    await downloadEntries(meta.key, filters.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

function openCreate() {
  createValues.value = Object.fromEntries(createFields.value.map((field) => [field, '']))
  errorMessage.value = ''
  showCreate.value = true
}

async function submitCreate() {
  creating.value = true
  errorMessage.value = ''
  try {
    const result = await createEntry(meta.key, createValues.value)
    if (!result.ok) throw new Error(result.message)
    showCreate.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记失败'
  } finally {
    creating.value = false
  }
}

async function runAction(action: string, row: EntryRow) {
  errorMessage.value = ''
  try {
    if (isMaterialReturn.value && action === '实收归仓') {
      // 料房收货台实收：可录入实收重量，留空按申请退回重量
      const text = window.prompt(
        '收货台实收重量（kg），留空按申请退回重量',
        String(row['申请退回重量'] ?? ''),
      )
      if (text === null) return
      await receiveReturn(Number(row.id), text.trim() ? Number(text) : undefined)
    } else {
      const result = await applyAction(meta.key, Number(row.id), action)
      if (!result.ok) throw new Error(result.message)
    }
    await reload()
    await reloadBags()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '操作失败'
  }
}

// ── 原料领退流转 ──

async function reloadBags() {
  if (!showStock.value) return
  try {
    bags.value = await listBags()
  } catch {
    // 看板读取失败不阻塞主列表
  }
}

async function issueBag(row: EntryRow) {
  errorMessage.value = ''
  const seal = window.prompt(`为配方 ${row['登记编号'] || row.id} 领料，输入在库袋号（整袋领出）`)
  if (!seal) return
  try {
    await issueMaterial(Number(row.id), seal.trim())
    await reload()
    await reloadBags()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '领出失败'
  }
}

async function consumeBag(row: EntryRow) {
  errorMessage.value = ''
  const seal = window.prompt('登记耗用：输入袋号')
  if (!seal) return
  const weightText = window.prompt(`袋 ${seal.trim()} 本次耗用重量（kg）`)
  if (!weightText) return
  try {
    await recordUsage(Number(row.id), seal.trim(), Number(weightText))
    await reload()
    await reloadBags()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '耗用登记失败'
  }
}

async function returnBag(row: EntryRow) {
  errorMessage.value = ''
  const seal = window.prompt('提交退料：输入袋号')
  if (!seal) return
  const usedText = window.prompt(`袋 ${seal.trim()} 已耗用重量（kg），剩余将申请退回`)
  if (usedText === null) return
  try {
    const result = await createEntry('material_return', {
      袋号: seal.trim(),
      已耗用重量: usedText.trim(),
    })
    if (!result.ok) throw new Error(result.message)
    await reload()
    await reloadBags()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '退料登记失败'
  }
}

async function viewUsage(row: EntryRow) {
  errorMessage.value = ''
  try {
    usageRows.value = await fetchUsage(Number(row.id))
    usageFor.value = Number(row.id)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '用量回查失败'
  }
}

async function viewBagStatus(seal: string) {
  errorMessage.value = ''
  try {
    bagStatus.value = await fetchBagStatus(seal)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '袋状态回查失败'
  }
}

function formatEventDetail(detail: Record<string, unknown>): string {
  if (!detail) return ''
  return Object.entries(detail)
    .map(([key, value]) => `${key}:${value}`)
    .join(' ')
}

function formatSlips(slips: any[]): string {
  if (!slips || !slips.length) return '—'
  return slips.map((s) => `#${s.id}${s.status}`).join('、')
}

async function reload() {
  errorMessage.value = ''
  try {
    const payload = await listEntries(meta.key, filters.value)
    rows.value = payload.items
    total.value = payload.total
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '业务列表读取失败'
  }
}

async function viewBatchTrace(batchNo: string) {
  traceBatchNo.value = batchNo
  showTrace.value = true
  traceLoading.value = true
  traceError.value = ''
  try {
    const result = await fetchTraceChain(batchNo)
    traceChain.value = (result.chain || []).map((node: any) => ({
      ...node,
      frozen: false,
      status: node.data?.status || '',
    }))
  } catch (err: any) {
    traceError.value = err.message || '追溯链查询失败'
    traceChain.value = []
  } finally {
    traceLoading.value = false
  }
}

function viewUpstream(row: EntryRow) {
  const upstreamModule = row['上游工序']
  const upstreamId = row['上游记录ID']
  if (upstreamModule && upstreamId) {
    void router.push({ name: 'module', params: { moduleKey: String(upstreamModule) } })
  }
}

onMounted(() => {
  void reload()
  void reloadBags()
})
</script>

<style scoped>
.trace-panel {
  margin-bottom: 12px;
  padding: 12px 16px;
  background: #f3f8ff;
  border-radius: 6px;
  border-left: 3px solid #3498db;
}
.trace-panel h4 {
  margin: 0 0 8px;
  font-size: 14px;
  color: #2c3e50;
}
.trace-chain-inline {
  display: flex;
  align-items: flex-start;
  overflow-x: auto;
  gap: 4px;
  padding: 8px 0;
}
.trace-node-inline {
  flex: 0 0 auto;
  min-width: 130px;
  padding: 8px;
  background: white;
  border-radius: 4px;
  border: 1px solid #ddd;
  font-size: 11px;
}
.trace-node-inline.frozen {
  border-color: #e74c3c;
  background: #fce4ec;
}
.node-index {
  width: 18px;
  height: 18px;
  background: #3498db;
  color: white;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 10px;
  margin-right: 4px;
}
.node-info strong {
  display: block;
  font-size: 11px;
}
.node-info span {
  display: block;
  color: #7f8c8d;
  font-size: 10px;
}
.node-status-tag {
  padding: 1px 4px;
  border-radius: 2px;
  font-size: 9px;
  display: inline-block;
}
.node-status-tag.已确认 { background: #d4edda; color: #155724; }
.node-status-tag.草稿 { background: #fff3cd; color: #856404; }
.node-status-tag.待确认 { background: #cce5ff; color: #004085; }
.node-status-tag.已撤回 { background: #f8d7da; color: #721c24; }
.node-meta {
  margin-top: 2px;
  font-size: 9px;
  color: #7f8c8d;
}
.trace-arrow {
  color: #3498db;
  font-size: 14px;
  margin: 0 2px;
  padding-top: 8px;
}
.trace-link, .upstream-link {
  color: #27ae60 !important;
  margin-left: 6px;
  padding: 2px 6px;
  font-size: 11px;
  border: 1px solid #27ae60;
  border-radius: 3px;
  text-decoration: none;
  cursor: pointer;
}
.trace-link:hover, .upstream-link:hover {
  background: #27ae60;
  color: white !important;
}
.bag-board {
  margin-bottom: 12px;
  padding: 12px 16px;
  background: #f6fbf7;
  border-radius: 6px;
  border-left: 3px solid #27ae60;
}
.bag-board h4 {
  margin: 0 0 8px;
  font-size: 14px;
  color: #2c3e50;
}
.position-tag {
  padding: 2px 8px;
  border-radius: 3px;
  font-size: 12px;
}
.position-tag.pos-在库 { background: #d4edda; color: #155724; }
.position-tag.pos-在用 { background: #fff3cd; color: #856404; }
.position-tag.pos-待收 { background: #cce5ff; color: #004085; }
.bag-status-panel .side-grid {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}
.bag-status-panel .side-card {
  flex: 1 1 260px;
  background: white;
  border: 1px solid #ddd;
  border-radius: 4px;
  padding: 8px 12px;
  font-size: 12px;
}
.bag-status-panel .side-card strong {
  display: block;
  margin-bottom: 4px;
  color: #2c3e50;
}
.bag-status-panel .side-card p {
  margin: 2px 0;
  color: #555;
}
.event-list {
  margin: 8px 0;
  padding-left: 20px;
  font-size: 12px;
  color: #555;
}
.modal-hint {
  font-size: 12px;
  color: #7f8c8d;
  margin: 0 0 8px;
}
</style>