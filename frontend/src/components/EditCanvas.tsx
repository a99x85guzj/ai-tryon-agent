import { useEffect, useMemo, useRef, useState } from 'react'
import { Image as KonvaImage, Layer, Rect, Stage, Transformer } from 'react-konva'
import type Konva from 'konva'
import type { KonvaEventObject } from 'konva/lib/Node'
import { api, errorMessage, fetchTask, streamTaskEvents } from '../api'
import type { TaskRecord } from '../types'

type Region = { x: number; y: number; width: number; height: number; replace: 'upper' | 'lower' }
type HistoryEntry = { image_url: string; edit_params: { region?: Region; description?: string; replace?: string } }

interface Props {
  imageUrl: string
  allImages: string[]
  onClose: () => void
  onImagesUpdated: (sources: string[], outputs: string[]) => void
  onError: (message: string) => void
}

export function EditCanvas({ imageUrl, allImages, onClose, onImagesUpdated, onError }: Props) {
  const [image, setImage] = useState<HTMLImageElement | null>(null)
  const [currentUrl, setCurrentUrl] = useState(imageUrl)
  const [region, setRegion] = useState<Region | null>(null)
  const [replace, setReplace] = useState<'upper' | 'lower'>('upper')
  const [description, setDescription] = useState('')
  const [garment, setGarment] = useState<File | null>(null)
  const [manual, setManual] = useState(false)
  const [drawing, setDrawing] = useState<{ x: number; y: number } | null>(null)
  const [busy, setBusy] = useState('')
  const [syncAll, setSyncAll] = useState(false)
  const [videoSelection, setVideoSelection] = useState<string[]>([])
  const [videoUrl, setVideoUrl] = useState('')
  const [history, setHistory] = useState<HistoryEntry[]>([{ image_url: imageUrl, edit_params: {} }])
  const [historyIndex, setHistoryIndex] = useState(0)
  const transformerRef = useRef<Konva.Transformer>(null)
  const rectRef = useRef<Konva.Rect>(null)

  useEffect(() => {
    const next = new window.Image()
    next.crossOrigin = 'anonymous'
    next.onload = () => setImage(next)
    next.onerror = () => onError('编辑图片加载失败。')
    next.src = currentUrl
  }, [currentUrl, onError])

  useEffect(() => {
    let active = true
    api.get('/edit/bbox', { params: { image: currentUrl }, timeout: 180_000 })
      .then(({ data }) => {
        if (!active) return
        const boxes = data.regions as Region[]
        const preferred = boxes.find((item) => item.replace === replace) || boxes[0]
        if (preferred) setRegion(preferred)
      })
      .catch((error) => onError(`区域识别失败：${errorMessage(error)}`))
      .finally(() => { if (active) setBusy('') })
    return () => { active = false }
  }, [currentUrl, onError, replace])

  useEffect(() => {
    if (transformerRef.current && rectRef.current) {
      transformerRef.current.nodes([rectRef.current])
      transformerRef.current.getLayer()?.batchDraw()
    }
  }, [region])

  const maxWidth = 760
  const maxHeight = 680
  const scale = image ? Math.min(maxWidth / image.naturalWidth, maxHeight / image.naturalHeight, 1) : 1
  const width = image ? image.naturalWidth * scale : maxWidth
  const height = image ? image.naturalHeight * scale : 520
  const shown = region && { ...region, x: region.x * scale, y: region.y * scale, width: region.width * scale, height: region.height * scale }
  const syncTargets = useMemo(() => allImages.filter((item) => item !== currentUrl), [allImages, currentUrl])

  function pointer(event: KonvaEventObject<MouseEvent | TouchEvent>) {
    const point = event.target.getStage()?.getPointerPosition()
    return point ? { x: point.x / scale, y: point.y / scale } : null
  }
  function drawStart(event: KonvaEventObject<MouseEvent | TouchEvent>) {
    if (!manual) return
    const point = pointer(event); if (!point) return
    setDrawing(point); setRegion({ ...point, width: 1, height: 1, replace })
  }
  function drawMove(event: KonvaEventObject<MouseEvent | TouchEvent>) {
    if (!drawing) return
    const point = pointer(event); if (!point) return
    setRegion({ x: Math.min(drawing.x, point.x), y: Math.min(drawing.y, point.y), width: Math.abs(point.x - drawing.x), height: Math.abs(point.y - drawing.y), replace })
  }
  function drawEnd() { setDrawing(null); setManual(false) }

  async function runTask(taskId: string): Promise<TaskRecord> {
    return new Promise((resolve, reject) => {
      const controller = new AbortController()
      void streamTaskEvents(taskId, async (message) => {
        if (!['completed', 'failed'].includes(message.status)) return
        controller.abort()
        try {
          const task = await fetchTask(taskId)
          if (task.status === 'completed') resolve(task)
          else reject(new Error(task.error || '任务执行失败'))
        } catch (error) { reject(error) }
      }, controller.signal).catch((error) => {
        if (!controller.signal.aborted) reject(error)
      })
    })
  }

  async function applyEdit() {
    if (!region || region.width < 5 || region.height < 5) return onError('请先框选有效的替换区域。')
    if (!description.trim() && !garment) return onError('请输入替换描述或上传新服装图。')
    setBusy(syncAll ? '正在同步替换多个变体…' : '正在替换服饰…')
    try {
      const form = new FormData()
      form.append('image', currentUrl)
      form.append('region', JSON.stringify(region))
      form.append('description', description)
      form.append('replace', replace)
      form.append('other_images', JSON.stringify(syncAll ? syncTargets : []))
      if (garment) form.append('garment_image', garment)
      const { data } = await api.post<{ task_id: string }>('/edit/replace', form)
      const completed = await runTask(data.task_id)
      const outputs = completed.artifacts
      const sources = [currentUrl, ...(syncAll ? syncTargets : [])]
      if (!outputs.length) throw new Error('编辑任务未返回图片')
      onImagesUpdated(sources, outputs)
      setCurrentUrl(outputs[0])
      const next = history.slice(0, historyIndex + 1).concat({ image_url: outputs[0], edit_params: { region, description, replace } })
      setHistory(next); setHistoryIndex(next.length - 1)
    } catch (error) { onError(errorMessage(error)) } finally { setBusy('') }
  }

  function moveHistory(nextIndex: number) {
    const entry = history[nextIndex]; if (!entry) return
    setHistoryIndex(nextIndex); setCurrentUrl(entry.image_url)
    if (entry.edit_params.region) setRegion(entry.edit_params.region)
    if (entry.edit_params.description !== undefined) setDescription(entry.edit_params.description)
    if (entry.edit_params.replace) setReplace(entry.edit_params.replace as 'upper' | 'lower')
  }

  async function createVideo() {
    if (videoSelection.length < 2 || videoSelection.length > 4) return onError('请选择 2 至 4 张成品图生成视频。')
    setBusy('正在生成展示视频，预计需要 1–3 分钟…')
    try {
      const { data } = await api.post<{ task_id: string }>('/edit/video', videoSelection)
      const completed = await runTask(data.task_id)
      if (!completed.artifacts[0]) throw new Error('视频任务未返回产物')
      setVideoUrl(completed.artifacts[0])
    } catch (error) { onError(errorMessage(error)) } finally { setBusy('') }
  }

  return <section className="mt-8 rounded-3xl border border-indigo-200 bg-white p-5 shadow-sm">
    <div className="mb-5 flex items-center justify-between"><div><p className="text-xs font-semibold uppercase tracking-[.18em] text-indigo-600">局部微调</p><h2 className="mt-1 text-xl font-semibold">框选服饰区域并替换</h2></div><button type="button" onClick={onClose} className="rounded-xl border border-slate-200 px-3 py-2 text-sm">退出编辑</button></div>
    <div className="grid gap-6 2xl:grid-cols-[minmax(0,760px)_340px]">
      <div className="overflow-auto rounded-2xl bg-slate-100 p-2"><Stage width={width} height={height} onMouseDown={drawStart} onMousemove={drawMove} onMouseup={drawEnd} onTouchStart={drawStart} onTouchMove={drawMove} onTouchEnd={drawEnd}><Layer>{image && <KonvaImage image={image} width={width} height={height} />}{shown && <Rect ref={rectRef} {...shown} draggable={!manual} stroke="#4f46e5" strokeWidth={2} fill="rgba(79,70,229,.12)" onDragEnd={(e) => setRegion({ ...region!, x: e.target.x() / scale, y: e.target.y() / scale })} onTransformEnd={() => { const node = rectRef.current; if (!node) return; const sx = node.scaleX(); const sy = node.scaleY(); node.scaleX(1); node.scaleY(1); setRegion({ ...region!, x: node.x() / scale, y: node.y() / scale, width: Math.max(5, node.width() * sx / scale), height: Math.max(5, node.height() * sy / scale) }) }} />}{shown && !manual && <Transformer ref={transformerRef} rotateEnabled={false} boundBoxFunc={(_, box) => box.width < 12 || box.height < 12 ? _ : box} />}</Layer></Stage></div>
      <aside className="space-y-4"><div className="flex gap-2"><button type="button" onClick={() => setManual(!manual)} className={`flex-1 rounded-xl px-3 py-2 text-sm font-semibold ${manual ? 'bg-indigo-600 text-white' : 'border border-slate-200'}`}>＋ 手动画框</button><button type="button" disabled={historyIndex === 0} onClick={() => moveHistory(historyIndex - 1)} className="rounded-xl border border-slate-200 px-3 disabled:opacity-30">撤销</button><button type="button" disabled={historyIndex >= history.length - 1} onClick={() => moveHistory(historyIndex + 1)} className="rounded-xl border border-slate-200 px-3 disabled:opacity-30">重做</button></div>
        <div className="grid grid-cols-2 gap-2">{(['upper', 'lower'] as const).map((item) => <button key={item} type="button" onClick={() => { setReplace(item); setRegion(region ? { ...region, replace: item } : null) }} className={`rounded-xl border px-3 py-2 text-sm ${replace === item ? 'border-indigo-600 bg-indigo-50 text-indigo-700' : 'border-slate-200'}`}>{item === 'upper' ? '替换上装' : '替换下装'}</button>)}</div>
        <textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={3} placeholder="例如：换成红色百褶裙" className="w-full resize-none rounded-xl border border-slate-200 px-3 py-2 text-sm outline-none focus:border-indigo-500" />
        <label className="block rounded-xl border border-dashed border-slate-300 p-3 text-sm text-slate-600">或上传替换服装图<input type="file" accept="image/jpeg,image/png,image/webp" onChange={(e) => setGarment(e.target.files?.[0] || null)} className="mt-2 block w-full text-xs" /></label>
        <label className="flex items-center gap-2 text-sm text-slate-700"><input type="checkbox" checked={syncAll} onChange={(e) => setSyncAll(e.target.checked)} className="accent-indigo-600" />同步应用到其他 {syncTargets.length} 个变体</label>
        <button type="button" disabled={!!busy} onClick={applyEdit} className="w-full rounded-xl bg-indigo-600 px-4 py-3 text-sm font-semibold text-white disabled:bg-slate-300">{busy || '确认替换'}</button>
        <div className="border-t border-slate-100 pt-4"><p className="mb-2 text-sm font-semibold">生成展示视频</p><div className="grid grid-cols-4 gap-2">{allImages.map((url, index) => <button type="button" key={url} onClick={() => setVideoSelection((items) => items.includes(url) ? items.filter((item) => item !== url) : items.length < 4 ? [...items, url] : items)} className={`overflow-hidden rounded-lg border-2 ${videoSelection.includes(url) ? 'border-indigo-600' : 'border-transparent'}`}><img src={url} className="aspect-[3/4] w-full object-cover" alt={`成品 ${index + 1}`} /></button>)}</div><button type="button" disabled={!!busy || videoSelection.length < 2} onClick={createVideo} className="mt-3 w-full rounded-xl border border-indigo-200 px-3 py-2 text-sm font-semibold text-indigo-700 disabled:opacity-40">用已选 {videoSelection.length} 张生成视频</button>{videoUrl && <video src={videoUrl} controls className="mt-3 w-full rounded-xl" />}</div>
        <details className="text-xs text-slate-500"><summary className="cursor-pointer font-medium">编辑链（{history.length} 步，可复现）</summary><pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded-lg bg-slate-50 p-2">{JSON.stringify(history, null, 2)}</pre></details>
      </aside>
    </div>
  </section>
}
