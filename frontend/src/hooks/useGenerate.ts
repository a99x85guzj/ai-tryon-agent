import { useCallback, useEffect, useRef, useState } from 'react'
import { api, errorMessage, fetchTask, streamTaskEvents } from '../api'
import type { GenerateInput, TaskEvent, TaskRecord, TaskStatus } from '../types'

export function useGenerate(onError: (message: string) => void) {
  const [task, setTask] = useState<TaskRecord | null>(null)
  const [taskId, setTaskId] = useState('')
  const [status, setStatus] = useState<TaskStatus>('idle')
  const [events, setEvents] = useState<TaskEvent[]>([])
  const sourceRef = useRef<AbortController | null>(null)

  const refresh = useCallback(async (taskId: string) => {
    const next = await fetchTask(taskId)
    setTask(next)
    setStatus(next.status)
    return next
  }, [])

  const connect = useCallback((taskId: string) => {
    sourceRef.current?.abort()
    const source = new AbortController()
    sourceRef.current = source
    void streamTaskEvents(taskId, (event: TaskEvent) => {
        setEvents((current) => current.some((item) => item.id === event.id) ? current : [...current, event])
        setStatus(event.status)
        if (['waiting_confirm', 'completed', 'failed'].includes(event.status)) {
          source.abort()
          void refresh(taskId).catch((error) => onError(errorMessage(error)))
        }
    }, source.signal).catch((error) => {
      if (!source.signal.aborted) onError(errorMessage(error))
    })
  }, [onError, refresh])

  useEffect(() => () => sourceRef.current?.abort(), [])

  const submit = useCallback(async (input: GenerateInput) => {
    try {
      sourceRef.current?.abort()
      setEvents([])
      setTask(null)
      setStatus('pending')
      const form = new FormData()
      form.append('description', input.description)
      if (input.garmentImage) form.append('garment_image', input.garmentImage)
      if (input.modelImage) form.append('model_image', input.modelImage)
      const { data } = await api.post<{ task_id: string }>('/generate', form)
      setTaskId(data.task_id)
      connect(data.task_id)
    } catch (error) {
      setStatus('failed')
      onError(errorMessage(error))
    }
  }, [connect, onError])

  const confirm = useCallback(async (action: 'approve' | 'modify', payload?: unknown) => {
    const currentTaskId = task?.task_id || taskId
    if (!currentTaskId) return
    try {
      await api.post(`/tasks/${currentTaskId}/confirm`, { action, payload })
      setStatus('pending')
      connect(currentTaskId)
    } catch (error) {
      onError(errorMessage(error))
    }
  }, [connect, onError, task, taskId])

  const cancel = useCallback(async () => {
    const currentTaskId = task?.task_id || taskId
    if (!currentTaskId) return
    try {
      sourceRef.current?.abort()
      const { data } = await api.post<TaskRecord>(`/tasks/${currentTaskId}/cancel`)
      setTask(data)
      setStatus(data.status)
    } catch (error) {
      onError(errorMessage(error))
    }
  }, [onError, task, taskId])

  return { task, status, events, submit, confirm, cancel }
}
