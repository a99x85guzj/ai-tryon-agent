import JSZip from 'jszip'
import type { ConfirmPlan } from '../types'

interface Props { urls: string[]; plan?: ConfirmPlan; onError: (message: string) => void; onEdit: (url: string) => void }

export function ResultGallery({ urls, plan, onError, onEdit }: Props) {
  if (!urls.length) return null
  async function download(url: string, name: string) {
    try {
      const blob = await fetch(url).then((response) => { if (!response.ok) throw new Error('下载失败'); return response.blob() })
      const anchor = document.createElement('a'); anchor.href = URL.createObjectURL(blob); anchor.download = name; anchor.click(); URL.revokeObjectURL(anchor.href)
    } catch { onError('图片下载失败，请检查产物地址是否仍然有效。') }
  }
  async function downloadZip() {
    try {
      const zip = new JSZip()
      await Promise.all(urls.map(async (url, index) => {
        const response = await fetch(url)
        if (!response.ok) throw new Error('下载失败')
        const blob: Blob = await response.blob()
        zip.file(`tryon-${index + 1}.${url.split('.').pop()?.split('?')[0] || 'png'}`, blob)
      }))
      const blob = await zip.generateAsync({ type: 'blob' })
      const anchor = document.createElement('a'); anchor.href = URL.createObjectURL(blob); anchor.download = `tryon-results-${Date.now()}.zip`; anchor.click(); URL.revokeObjectURL(anchor.href)
    } catch { onError('批量打包失败，请尝试单张下载。') }
  }
  const tags = [`模式 ${plan?.mode || '-'}`, String(plan?.angle_preset || '标准角度'), `${plan?.variants || urls.length} 变体`]
  return <section className="mt-8"><div className="mb-4 flex items-center justify-between"><div><p className="text-xs font-semibold uppercase tracking-[.18em] text-emerald-600">生成结果</p><h2 className="mt-1 text-xl font-semibold text-slate-900">可交付素材</h2></div><button type="button" onClick={downloadZip} className="rounded-xl border border-slate-200 bg-white px-4 py-2 text-sm font-semibold text-slate-700 hover:border-indigo-300">批量下载 ZIP</button></div><div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">{urls.map((url, index) => <article key={`${url}-${index}`} className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm"><button type="button" onClick={() => onEdit(url)} className="group relative block w-full"><img src={url} alt={`试穿结果 ${index + 1}`} className="aspect-[3/4] w-full bg-slate-100 object-cover" /><span className="absolute inset-x-3 bottom-3 rounded-xl bg-slate-950/75 px-3 py-2 text-sm font-semibold text-white opacity-0 backdrop-blur transition group-hover:opacity-100">框选微调此图</span></button><div className="p-4"><div className="flex flex-wrap gap-1.5">{tags.map((tag) => <span key={tag} className="rounded-full bg-slate-100 px-2 py-1 text-[11px] text-slate-600">{tag}</span>)}</div><button type="button" onClick={() => download(url, `tryon-${index + 1}.png`)} className="mt-3 w-full rounded-xl bg-slate-900 px-3 py-2 text-sm font-semibold text-white hover:bg-slate-700">下载此图</button></div></article>)}</div></section>
}
