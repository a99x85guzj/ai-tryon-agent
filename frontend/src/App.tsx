import { Component, lazy, Suspense, useCallback, useState, type ErrorInfo, type ReactNode } from 'react'
import { ImageUploader } from './components/ImageUploader'
import { ModelSelector } from './components/ModelSelector'
import { RequirementInput } from './components/RequirementInput'
import { ResultGallery } from './components/ResultGallery'
import { TaskProgress } from './components/TaskProgress'
import { useGenerate } from './hooks/useGenerate'

const EditCanvas = lazy(() => import('./components/EditCanvas').then((module) => ({ default: module.EditCanvas })))

function resolveArtifact(url: string, overrides: Record<string, string>): string {
  let value = url
  const visited = new Set<string>()
  while (overrides[value] && !visited.has(value)) {
    visited.add(value)
    value = overrides[value]
  }
  return value
}

function Workspace() {
  const [garmentImage, setGarmentImage] = useState<File | null>(null)
  const [modelImage, setModelImage] = useState<File | null>(null)
  const [catalogModel, setCatalogModel] = useState('')
  const [toast, setToast] = useState('')
  const [editing, setEditing] = useState('')
  const [artifactOverrides, setArtifactOverrides] = useState<Record<string, string>>({})
  const notify = useCallback((message: string) => { setToast(message); window.setTimeout(() => setToast(''), 4500) }, [])
  const { task, status, events, submit, confirm, cancel } = useGenerate(notify)
  const busy = ['pending', 'running', 'waiting_confirm'].includes(status)
  const artifacts = (task?.artifacts || []).map((url) => resolveArtifact(url, artifactOverrides))

  return <div className="min-h-screen bg-[#f5f7fb] text-slate-900">
    <header className="border-b border-slate-200/80 bg-white/90 backdrop-blur"><div className="mx-auto flex max-w-[1480px] items-center justify-between px-5 py-4 lg:px-8"><div className="flex items-center gap-3"><div className="grid size-10 place-items-center rounded-xl bg-indigo-600 font-bold text-white shadow-lg shadow-indigo-200">AI</div><div><h1 className="font-semibold tracking-tight">虚拟试衣工作台</h1><p className="text-xs text-slate-400">面向电商团队的智能素材生产 Agent</p></div></div><div className="flex items-center gap-2 text-xs text-slate-500"><span className="size-2 rounded-full bg-emerald-500" />本地开发环境</div></div></header>
    <main className="mx-auto max-w-[1480px] px-5 py-7 lg:px-8"><div className="mb-7"><p className="text-sm font-semibold text-indigo-600">AI TRY-ON STUDIO</p><h2 className="mt-1 text-2xl font-semibold tracking-tight text-slate-950 md:text-3xl">从商品图到可投放素材，一条工作流完成</h2><p className="mt-2 text-sm text-slate-500">素材预检、Agent 策划、费用确认和批量出图全程可见。</p></div>
      <div className="grid gap-6 xl:grid-cols-[390px_minmax(0,1fr)]"><aside className="space-y-5"><section className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm"><div className="mb-5"><p className="text-xs font-semibold uppercase tracking-[.18em] text-indigo-600">01 · 素材与需求</p><h2 className="mt-1 text-lg font-semibold">创建试衣任务</h2></div><div className="grid gap-5 sm:grid-cols-2 xl:grid-cols-1"><ImageUploader kind="garment" label="服装商品图" file={garmentImage} onChange={setGarmentImage} onError={notify} /><ImageUploader kind="model" label="自有模特图" file={modelImage} onChange={setModelImage} onError={notify} /></div><div className="my-5 h-px bg-slate-100" /><RequirementInput busy={busy} onError={notify} onSubmit={(description) => submit({ description: `${description}${catalogModel ? `；优先使用模特：${catalogModel}` : ''}`, garmentImage, modelImage })} /></section><ModelSelector selected={catalogModel} onSelect={setCatalogModel} /></aside>
        <div><TaskProgress task={task} status={status} events={events} onConfirm={confirm} onCancel={cancel} /><ResultGallery urls={artifacts} plan={task?.state.confirmed_plan} onError={notify} onEdit={setEditing} />{editing && <Suspense fallback={<div className="mt-8 rounded-3xl border border-slate-200 bg-white p-8 text-center text-sm text-slate-500">正在加载编辑器…</div>}><EditCanvas key={editing} imageUrl={editing} allImages={artifacts} onClose={() => setEditing('')} onError={notify} onImagesUpdated={(sources, outputs) => setArtifactOverrides((current) => ({ ...current, ...Object.fromEntries(sources.map((source, index) => [source, outputs[index] || source])) }))} /></Suspense>}</div></div>
    </main>
    {toast && <div role="alert" className="fixed bottom-6 left-1/2 z-50 w-[min(92vw,520px)] -translate-x-1/2 rounded-2xl bg-slate-950 px-5 py-3.5 text-sm text-white shadow-2xl"><div className="flex items-start gap-3"><span className="text-amber-400">●</span><span>{toast}</span><button type="button" onClick={() => setToast('')} className="ml-auto text-slate-400 hover:text-white">×</button></div></div>}
  </div>
}

class ErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() { return { failed: true } }
  componentDidCatch(error: Error, info: ErrorInfo) { console.error('UI error', error, info) }
  render() { return this.state.failed ? <main className="grid min-h-screen place-items-center bg-slate-50 p-6"><div className="max-w-md rounded-3xl border border-rose-200 bg-white p-8 text-center shadow-sm"><div className="text-3xl">!</div><h1 className="mt-3 text-xl font-semibold">页面暂时遇到问题</h1><p className="mt-2 text-sm text-slate-500">您的任务数据仍保存在后端。请刷新页面后重试。</p><button onClick={() => window.location.reload()} className="mt-5 rounded-xl bg-indigo-600 px-5 py-2.5 text-sm font-semibold text-white">刷新页面</button></div></main> : this.props.children }
}

export default function App() { return <ErrorBoundary><Workspace /></ErrorBoundary> }
