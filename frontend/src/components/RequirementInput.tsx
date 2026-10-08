import { useState, type FormEvent } from 'react'

interface Props {
  busy: boolean
  onSubmit: (description: string) => void
  onError: (message: string) => void
}

const scenes = ['纯白棚拍', '城市街拍', '轻奢商场', '自然户外', '极简家居']

export function RequirementInput({ busy, onSubmit, onError }: Props) {
  const [description, setDescription] = useState('')
  const [part, setPart] = useState('')
  const [mode, setMode] = useState('B')
  const [scene, setScene] = useState('纯白棚拍')
  const [angle, setAngle] = useState('ecommerce')
  const [variants, setVariants] = useState(1)

  function submit(event: FormEvent) {
    event.preventDefault()
    if (!part) return onError('请选择服装部位；系统不会猜测默认值。')
    if (!description.trim()) return onError('请描述希望生成的试穿效果。')
    const assembled = `${description.trim()}。服装部位：${part}；模式：${mode}；场景：${scene}；角度预设：${angle}；生成 ${variants} 个变体。`
    onSubmit(assembled)
  }

  return (
    <form onSubmit={submit} className="space-y-5">
      <div>
        <label htmlFor="requirement" className="mb-2 block text-sm font-semibold text-slate-800">试衣需求</label>
        <textarea id="requirement" value={description} onChange={(e) => setDescription(e.target.value)} rows={4} placeholder="例如：这件白色 T 恤出 3 个颜色，适合夏季新品首页…" className="w-full resize-none rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm outline-none transition placeholder:text-slate-400 focus:border-indigo-500 focus:ring-4 focus:ring-indigo-100" />
      </div>
      <div className="grid grid-cols-2 gap-3">
        <Select label="服装部位 *" value={part} onChange={setPart} options={[['', '请选择'], ['上装', '上装'], ['下装', '下装'], ['套装', '套装'], ['连体', '连体']]} />
        <Select label="生成模式" value={mode} onChange={setMode} options={[['A', 'A · 精准试穿'], ['B', 'B · 创意生图']]} />
        <Select label="场景池" value={scene} onChange={setScene} options={scenes.map((item) => [item, item])} />
        <Select label="角度预设" value={angle} onChange={setAngle} options={[['ecommerce', '电商标准'], ['detail', '细节特写'], ['catwalk', '秀场走台'], ['lifestyle', '生活方式']]} />
      </div>
      <div>
        <div className="mb-2 flex justify-between text-sm"><span className="font-semibold text-slate-800">变体数量</span><span className="font-medium text-indigo-700">{variants} 张</span></div>
        <input type="range" min="1" max="9" value={variants} onChange={(e) => setVariants(Number(e.target.value))} className="w-full accent-indigo-600" />
      </div>
      <button disabled={busy} className="w-full rounded-2xl bg-indigo-600 px-5 py-3.5 text-sm font-semibold text-white shadow-lg shadow-indigo-200 transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:bg-slate-300 disabled:shadow-none">
        {busy ? '任务处理中…' : '生成试穿图'}
      </button>
    </form>
  )
}

function Select({ label, value, onChange, options }: { label: string; value: string; onChange: (value: string) => void; options: string[][] }) {
  return <label className="text-xs font-medium text-slate-500">{label}<select value={value} onChange={(e) => onChange(e.target.value)} className="mt-1.5 w-full rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm text-slate-800 outline-none focus:border-indigo-500">{options.map(([key, text]) => <option key={key || 'empty'} value={key}>{text}</option>)}</select></label>
}
