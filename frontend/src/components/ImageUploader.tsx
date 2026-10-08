import { useEffect, useMemo, useState } from 'react'
import { useDropzone } from 'react-dropzone'
import { errorMessage, preflightImage } from '../api'

interface Props {
  kind: 'garment' | 'model'
  label: string
  file: File | null
  onChange: (file: File | null) => void
  onError: (message: string) => void
}

export function ImageUploader({ kind, label, file, onChange, onError }: Props) {
  const [checking, setChecking] = useState(false)
  const [valid, setValid] = useState(false)
  const preview = useMemo(() => file ? URL.createObjectURL(file) : '', [file])
  useEffect(() => () => { if (preview) URL.revokeObjectURL(preview) }, [preview])

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    accept: { 'image/jpeg': ['.jpg', '.jpeg'], 'image/png': ['.png'], 'image/webp': ['.webp'] },
    maxFiles: 1,
    maxSize: 10 * 1024 * 1024,
    disabled: checking,
    onDropRejected: () => onError('请选择 10MB 以内的 JPG、PNG 或 BMP 图片。'),
    onDropAccepted: async ([next]) => {
      if (!next) return
      onChange(next)
      setChecking(true)
      setValid(false)
      try {
        await preflightImage(next, kind)
        setValid(true)
      } catch (error) {
        onChange(null)
        onError(`${label}预检未通过：${errorMessage(error)}`)
      } finally {
        setChecking(false)
      }
    },
  })

  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <label className="text-sm font-semibold text-slate-800">{label}</label>
        <span className="text-xs text-slate-400">可选 · 最大 10MB</span>
      </div>
      <div {...getRootProps()} className={`group relative flex min-h-44 cursor-pointer items-center justify-center overflow-hidden rounded-2xl border border-dashed transition ${isDragActive ? 'border-indigo-500 bg-indigo-50' : 'border-slate-300 bg-slate-50 hover:border-indigo-400 hover:bg-indigo-50/40'}`}>
        <input {...getInputProps()} />
        {preview ? (
          <img src={preview} alt={`${label}预览`} className="h-44 w-full object-cover" />
        ) : (
          <div className="px-5 text-center">
            <div className="mx-auto mb-3 grid size-10 place-items-center rounded-xl bg-white text-xl shadow-sm">↥</div>
            <p className="text-sm font-medium text-slate-700">拖拽图片到这里，或点击选择</p>
            <p className="mt-1 text-xs text-slate-400">JPG / PNG / WEBP</p>
          </div>
        )}
        {checking && <div className="absolute inset-0 grid place-items-center bg-white/85 text-sm font-medium text-indigo-700">正在预检图片…</div>}
      </div>
      {valid && <p className="mt-2 text-xs font-medium text-emerald-600">✓ 图片符合要求</p>}
      {file && !checking && <button type="button" onClick={() => { onChange(null); setValid(false) }} className="mt-2 text-xs text-slate-500 hover:text-rose-600">移除图片</button>}
    </div>
  )
}
