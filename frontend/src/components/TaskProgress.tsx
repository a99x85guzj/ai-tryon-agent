import { useMemo, useState } from 'react'
import { publicUrl } from '../api'
import type { ModelCandidate, TaskEvent, TaskRecord, TaskStatus } from '../types'

interface Props {
  task: TaskRecord | null
  status: TaskStatus
  events: TaskEvent[]
  onConfirm: (action: 'approve' | 'modify', payload?: unknown) => void
  onCancel: () => void
}

export function TaskProgress({ task, status, events, onConfirm, onCancel }: Props) {
  const interrupt = task?.interrupt
  const plan = interrupt?.plan || task?.state.confirmed_plan || {}
  const candidates = (plan.model_candidates || task?.state.model_candidates || []) as ModelCandidate[]
  const planPrompt = String(plan.prompt || '')
  const [promptEdit, setPromptEdit] = useState({ source: '', value: '' })
  const prompt = promptEdit.source === planPrompt ? promptEdit.value : planPrompt
  const [selectedModel, setSelectedModel] = useState('')
  const [answer, setAnswer] = useState('')
  const progress = useMemo(() => events.at(-1)?.data.progress ?? ({ idle: 0, pending: 5, running: 35, waiting_confirm: 55, completed: 100, failed: 100 }[status]), [events, status])
  const variants = Number(plan.variants || 1)
  const label = status === 'pending' ? '任务已进入队列…' : status === 'running' ? `正在生成 1/${variants}…` : status === 'waiting_confirm' ? '等待您确认方案' : status === 'completed' ? '生成完成' : status === 'failed' ? '任务未完成' : '等待提交任务'
  const active = ['pending', 'running'].includes(status)

  if (status === 'idle') return <EmptyProgress />
  return (
    <section className="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm">
      <div className="flex items-start justify-between gap-4"><div><p className="text-xs font-semibold uppercase tracking-[.18em] text-indigo-600">任务进度</p><h2 className="mt-1 text-lg font-semibold text-slate-900">{label}</h2></div><span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-medium text-slate-600">{Math.round(progress)}%</span></div>
      <div className="mt-4 h-2 overflow-hidden rounded-full bg-slate-100"><div className="h-full rounded-full bg-indigo-600 transition-all duration-700" style={{ width: `${progress}%` }} /></div>
      <div className="mt-5 grid grid-cols-3 gap-2 text-center text-xs"><Step done={status !== 'pending'} text="分析服装" /><Step done={['waiting_confirm', 'running', 'completed'].includes(status)} text="确认方案" /><Step done={status === 'completed'} text="生成产物" /></div>
      {status === 'running' && <p className="mt-4 rounded-xl bg-indigo-50 px-4 py-3 text-sm text-indigo-700">生成通常需要 30 秒以上，您可以保留此页面继续等待。</p>}
      {status === 'failed' && <p className="mt-4 rounded-xl bg-rose-50 px-4 py-3 text-sm text-rose-700">{task?.error || '任务执行失败，请检查图片和服务配置后重试。'}</p>}
      {status === 'waiting_confirm' && interrupt?.type === 'ask_user' && <div className="mt-5 rounded-2xl border border-amber-200 bg-amber-50 p-4"><p className="text-sm font-semibold text-amber-900">{interrupt.question || '请补充信息'}</p><div className="mt-3 flex gap-2"><input value={answer} onChange={(e) => setAnswer(e.target.value)} className="min-w-0 flex-1 rounded-xl border border-amber-200 bg-white px-3 py-2 text-sm outline-none focus:border-amber-500" placeholder="例如：上装" /><button type="button" disabled={!answer.trim()} onClick={() => onConfirm('modify', answer.trim())} className="rounded-xl bg-amber-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-40">继续</button></div></div>}
      {status === 'waiting_confirm' && interrupt?.type !== 'ask_user' && <div className="mt-5 rounded-2xl border border-indigo-200 bg-indigo-50/60 p-5"><div className="flex items-center justify-between"><h3 className="font-semibold text-slate-900">执行方案确认</h3><span className="text-xs text-indigo-700">费用产生前确认</span></div><div className="mt-3 flex flex-wrap gap-2 text-xs"><Tag text={`模式 ${plan.mode || '-'}`} /><Tag text={`${variants} 个变体`} /><Tag text={String(plan.angle_preset || '标准角度')} /></div>{candidates.length > 0 && <div className="mt-4"><p className="mb-2 text-xs font-semibold text-slate-600">选择模特</p><div className="flex gap-2 overflow-x-auto">{candidates.map((candidate, index) => { const key = String(candidate.path || candidate.name || index); const src = publicUrl(String(candidate.url || candidate.image || candidate.path || '')); return <button type="button" key={key} onClick={() => setSelectedModel(key)} className={`w-20 shrink-0 overflow-hidden rounded-xl border bg-white ${selectedModel === key ? 'border-indigo-600 ring-2 ring-indigo-200' : 'border-slate-200'}`}>{src ? <img src={src} className="h-16 w-full object-cover" alt="" /> : <div className="grid h-16 place-items-center bg-slate-100">模特</div>}<span className="block truncate px-1 py-1 text-[11px]">{String(candidate.name || `候选 ${index + 1}`)}</span></button>})}</div></div>}<label className="mt-4 block text-xs font-semibold text-slate-600">生成提示词<textarea value={prompt} onChange={(e) => setPromptEdit({ source: planPrompt, value: e.target.value })} rows={5} className="mt-2 w-full resize-none rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm font-normal text-slate-800 outline-none focus:border-indigo-500" /></label><div className="mt-4 flex gap-2"><button type="button" onClick={() => onConfirm('approve', { prompt, selected_model: selectedModel || undefined })} className="flex-1 rounded-xl bg-indigo-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-indigo-700">批准执行</button><button type="button" onClick={() => onConfirm('modify', { prompt, selected_model: selectedModel || undefined, changes: '按编辑后的方案执行' })} className="rounded-xl border border-indigo-200 bg-white px-4 py-2.5 text-sm font-semibold text-indigo-700 hover:bg-indigo-50">提交修改</button></div></div>}
      {active && <button type="button" onClick={onCancel} className="mt-5 w-full rounded-xl border border-rose-200 px-4 py-2.5 text-sm font-medium text-rose-600 hover:bg-rose-50">取消任务</button>}
    </section>
  )
}

function Step({ done, text }: { done: boolean; text: string }) { return <div className={done ? 'font-medium text-indigo-700' : 'text-slate-400'}><span className={`mx-auto mb-1 grid size-6 place-items-center rounded-full ${done ? 'bg-indigo-600 text-white' : 'bg-slate-100'}`}>{done ? '✓' : '·'}</span>{text}</div> }
function Tag({ text }: { text: string }) { return <span className="rounded-full border border-indigo-100 bg-white px-2.5 py-1 text-indigo-700">{text}</span> }
function EmptyProgress() { return <section className="grid min-h-72 place-items-center rounded-3xl border border-dashed border-slate-300 bg-slate-50/60 p-8 text-center"><div><div className="mx-auto grid size-14 place-items-center rounded-2xl bg-white text-2xl shadow-sm">✦</div><h2 className="mt-4 font-semibold text-slate-800">等待创建试衣任务</h2><p className="mt-1 max-w-sm text-sm leading-6 text-slate-500">上传素材并填写需求后，分析、确认和生成进度会显示在这里。</p></div></section> }
