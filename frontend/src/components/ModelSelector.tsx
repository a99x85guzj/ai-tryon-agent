import { useQuery } from '@tanstack/react-query'
import { fetchModels, publicUrl } from '../api'
import type { ModelCandidate } from '../types'

interface Props { selected?: string; onSelect: (value: string) => void }

export function ModelSelector({ selected, onSelect }: Props) {
  const { data = [], isLoading, isError } = useQuery({ queryKey: ['models'], queryFn: fetchModels, staleTime: 5 * 60_000, retry: 1 })
  const groups = data.reduce<Record<string, ModelCandidate[]>>((acc, model) => {
    const group = String(model.group || model.category || '推荐模特')
    ;(acc[group] ||= []).push(model)
    return acc
  }, {})
  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-5">
      <div className="mb-4 flex items-center justify-between"><h3 className="font-semibold text-slate-900">模特库</h3><span className="text-xs text-slate-400">可选</span></div>
      {isLoading && <p className="text-sm text-slate-500">正在加载模特…</p>}
      {isError && <p className="text-sm text-amber-600">模特库暂不可用，仍可上传自有模特图。</p>}
      {Object.entries(groups).map(([group, models]) => <div key={group} className="mb-4"><p className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-400">{group}</p><div className="grid grid-cols-3 gap-2">{models.map((model, index) => { const key = String(model.path || model.name || index); const src = publicUrl(String(model.url || model.image || model.path || '')); return <button type="button" key={key} onClick={() => onSelect(key)} className={`overflow-hidden rounded-xl border text-left transition ${selected === key ? 'border-indigo-600 ring-2 ring-indigo-100' : 'border-slate-200 hover:border-indigo-300'}`}>{src ? <img src={src} className="h-20 w-full object-cover" alt="" /> : <div className="grid h-20 place-items-center bg-slate-100 text-2xl">◯</div>}<span className="block truncate px-2 py-1.5 text-xs text-slate-700">{String(model.name || `模特 ${index + 1}`)}</span></button>})}</div></div>)}
    </section>
  )
}
