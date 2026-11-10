// Mirrors fpl_coach.models / fpl_coach.api.routes response shapes.

export interface RecommendationPick {
  player_id: number
  player_name: string
}

export interface Alternative {
  player_id: number
  player_name: string
  reason: string
}

export interface Recommendation {
  pick: RecommendationPick
  confidence: number
  evidence: string[]
  risks: string[]
  alternative: Alternative
}

export interface PlanStep {
  gameweek: number
  pick: RecommendationPick
  evidence: string[]
  risks: string[]
}

export interface TransferPlan {
  horizon: string
  confidence: number
  steps: PlanStep[]
}

export interface RememberAck {
  remembered: string
}

export type AskResponse = Recommendation | TransferPlan | RememberAck

export function isRememberAck(value: AskResponse): value is RememberAck {
  return 'remembered' in value
}

export function isTransferPlan(value: AskResponse): value is TransferPlan {
  return 'steps' in value
}

export interface Player {
  id: number
  name: string
  position: string
  club: string
  price: number
  ownership: number | null
  form: number | null
  status: string | null
  news: string | null
  xg: number | null
  xa: number | null
}

export interface Team {
  id: number
  name: string
  players: Player[]
  current_gameweek: number | null
}

export interface Instinct {
  text: string
  logged_at: string
}

export interface Instincts {
  notes: Instinct[]
}

export type Suggestion = Recommendation | TransferPlan | null
