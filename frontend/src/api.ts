import axios from 'axios'
import type { ModelCandidate, TaskEvent, TaskRecord } from './types'

const apiKey = import.meta.env.VITE_API_KEY as string | undefined
export const api = axios.create({
  baseURL: '/api',
  timeout: 30_000,
  headers: apiKey ? { 'X-API-Key': apiKey } : undefined,
})

export function errorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail
    if (typeof detail === 'string') return detail
    if (error.code === 'ECONNABORTED') return '请求超时，请检查后端服务后重试。'
  }
  return error instanceof Error ? error.message : '操作失败，请稍后重试。'
}

export async function preflightImage(file: File, kind: 'garment' | 'model') {
  const form = new FormData()
  form.append('image', file)
  form.append('kind', kind)
  return (await api.post('/preflight', form)).data as { ok: boolean; elapsed: number }
}

export async function fetchTask(taskId: string) {
  return (await api.get<TaskRecord>(`/tasks/${taskId}`)).data
}

export async function fetchModels(): Promise<ModelCandidate[]> {
  const response = await api.get('/models')
  const payload = response.data.models
  if (Array.isArray(payload)) return payload
  if (Array.isArray(payload?.models)) return payload.models
  if (Array.isArray(payload?.result)) return payload.result
  return []
}

export function publicUrl(value?: string): string | undefined {
  if (!value) return undefined
  if (/^(https?:|blob:|data:)/.test(value)) return value
  const normalized = value.replace(/\\/g, '/')
  const marker = '/data/sessions/'
  const index = normalized.toLowerCase().indexOf(marker)
  if (index >= 0) return `/api/files/${normalized.slice(index + marker.length)}`
  return normalized.startsWith('/') ? normalized : undefined
}

export async function streamTaskEvents(
  taskId: string,
  onEvent: (event: TaskEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`/api/tasks/${taskId}/events`, {
    headers: apiKey ? { 'X-API-Key': apiKey } : undefined,
    signal,
  })
  if (!response.ok || !response.body) throw new Error(`进度订阅失败（${response.status}）`)
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { value, done } = await reader.read()
    buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, '\n')
    let boundary = buffer.indexOf('\n\n')
    while (boundary >= 0) {
      const block = buffer.slice(0, boundary)
      buffer = buffer.slice(boundary + 2)
      const data = block.split('\n').filter((line) => line.startsWith('data:')).map((line) => line.slice(5).trimStart()).join('\n')
      if (data) onEvent(JSON.parse(data) as TaskEvent)
      boundary = buffer.indexOf('\n\n')
    }
    if (done) break
  }
}
