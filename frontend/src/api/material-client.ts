import { request } from './client'

// 原料袋与领退流转的接口封装：拼配工作台与料房收货台共用。

export type BagRow = {
  id: number
  袋号: string
  批次号: string
  总量: number
  在库量: number
  位置: string
  占用配方: string
  当前领用ID: number | null
  [key: string]: unknown
}

export type BagStatus = {
  袋号: string
  批次号: string
  位置: string
  总量: number
  在库量: number
  占用配方ID: number | null
  拼配侧: Record<string, unknown>
  料房侧: Record<string, unknown>
  未办成原因: string
  领用办理: Record<string, unknown>[]
  退料签: Record<string, unknown>[]
  办理记录: { ts: number; module: string; entry_id: number; action: string; detail: Record<string, unknown> }[]
}

async function postJson(path: string, body: unknown): Promise<any> {
  const response = await request(path, { method: 'POST', body: JSON.stringify(body) })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new Error((data as any)?.detail || `接口返回 ${response.status}`)
  }
  return data
}

async function getJson(path: string, errorMessage: string): Promise<any> {
  const response = await request(path)
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new Error((data as any)?.detail || errorMessage)
  }
  return data
}

export async function listBags(): Promise<BagRow[]> {
  const data = await getJson('/api/material-bags', '原料袋看板读取失败')
  return data.items
}

export async function fetchBagStatus(seal: string): Promise<BagStatus> {
  const data = await getJson(
    `/api/material-bags/${encodeURIComponent(seal)}/status`,
    '袋状态回查失败',
  )
  return data.status
}

export function issueMaterial(entryId: number, seal: string): Promise<any> {
  return postJson(`/api/blending/${entryId}/issue`, { 袋号: seal })
}

export function recordUsage(entryId: number, seal: string, weight: number): Promise<any> {
  return postJson(`/api/blending/${entryId}/consume`, { 袋号: seal, 耗用重量: weight })
}

export async function fetchUsage(entryId: number): Promise<any[]> {
  const data = await getJson(`/api/blending/${entryId}/usage`, '配方用量回查失败')
  return data.items
}

export function receiveReturn(entryId: number, actualWeight?: number): Promise<any> {
  return postJson(`/api/material_return/${entryId}/receive`, {
    实收重量: actualWeight ?? null,
  })
}
