export type TaskStatus = 'idle' | 'pending' | 'running' | 'waiting_confirm' | 'completed' | 'failed'

export interface ModelCandidate {
  name?: string
  path?: string
  url?: string
  image?: string
  group?: string
  category?: string
  [key: string]: unknown
}

export interface ConfirmPlan {
  mode?: string
  garment_part?: string
  prompt?: string
  variants?: number
  scenes?: string[]
  colors?: string[]
  angle_preset?: string | null
  image_backend?: string
  model_candidates?: ModelCandidate[]
  actual_cost?: number
  [key: string]: unknown
}

export interface AgentInterrupt {
  type?: 'ask_user' | 'plan_confirm' | string
  question?: string
  message?: string
  reason?: string
  plan?: ConfirmPlan
}

export interface TaskRecord {
  task_id: string
  status: Exclude<TaskStatus, 'idle'>
  state: {
    confirmed_plan?: ConfirmPlan
    model_candidates?: ModelCandidate[]
    image_urls?: Record<string, unknown>
    [key: string]: unknown
  }
  artifacts: string[]
  interrupt?: AgentInterrupt | null
  error?: string | null
  cost: number
  created_at: string
  updated_at: string
}

export interface TaskEvent {
  id: number
  event: string
  status: Exclude<TaskStatus, 'idle'>
  data: { progress?: number; interrupt?: AgentInterrupt; [key: string]: unknown }
  created_at: string
}

export interface GenerateInput {
  description: string
  garmentImage?: File | null
  modelImage?: File | null
}
