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
