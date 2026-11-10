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

// A recommendation does not always name a player: "roll your transfer" is a
// real answer with no pick. `pick`/`alternative` are null in that case and
// must not be rendered as a pick.
export type DecisionKind = 'captain' | 'transfer' | 'roll' | 'chip' | 'other'

// Issue 17: where an evidence line came from. The backend builds `url` from
// the tool result that produced the stance -- it is already a deep link when
// `start_seconds` is set, so never reconstruct one here.
export interface Citation {
  creator_name: string
  trust_tier: string
  url: string
  start_seconds: number | null
  published_at: string | null
}

// `citation` is null for a purely statistical line (xG, a price move). That
// line is not weaker for it and must render without an empty citation slot.
export interface Evidence {
  text: string
  citation: Citation | null
}

export interface Recommendation {
  decision: DecisionKind
  pick: RecommendationPick | null
  confidence: number
  evidence: Evidence[]
  risks: string[]
  alternative: Alternative | null
}

export interface PlanStep {
  gameweek: number
  decision: DecisionKind
  // For a 'transfer' step this is the player coming IN.
  pick: RecommendationPick | null
  // Issue 25: the player being SOLD. Null for any step that is not a
  // transfer, and for a plan stored before this field existed.
  player_out: RecommendationPick | null
  evidence: Evidence[]
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

// Issue 27: the model talking rather than finalising — asking which player you
// meant, or engaging with an objection. Carries no pick by design: ADR-0004
// says a decision comes from an authoritative lookup, never from prose, so the
// conversational reply is structurally incapable of naming one.
export interface ConversationReply {
  text: string
}

export interface ConversationTurn {
  role: 'user' | 'assistant'
  text: string | null
  // A finalised turn keeps its structure rather than being flattened to prose,
  // so ticket 17's cited evidence still renders inside the transcript.
  recommendation: Recommendation | null
  plan: TransferPlan | null
  created_at: string
}

export interface Conversation {
  team_id: number
  gameweek: number | null
  turns: ConversationTurn[]
}

export type AskResponse = Recommendation | TransferPlan | RememberAck | ConversationReply

export function isRememberAck(value: AskResponse): value is RememberAck {
  return 'remembered' in value
}

export function isTransferPlan(value: AskResponse): value is TransferPlan {
  return 'steps' in value
}

// Checked before the others: a reply has `text` and none of the decision
// fields, so shape alone distinguishes it.
export function isConversationReply(value: AskResponse): value is ConversationReply {
  return 'text' in value && !('decision' in value) && !('steps' in value)
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
  // Issue 20: squad shape. 1-11 is the XI, 12-15 the bench in the order they
  // would come on; null for a manually seeded row that predates ingestion.
  squad_position: number | null
  is_captain: boolean
  is_vice_captain: boolean
  // Issue 20: an AVAILABLE player averaging under 60 minutes a gameweek. Not
  // an injury -- it is the fit player who simply is not being picked, which
  // `status` cannot express.
  rotation_risk: boolean
}

// Issue 20: what the user can spend. `selling_prices_exact` is load-bearing:
// FPL does not publish per-player selling prices without an authenticated
// session, so when it is false a listed price is only an upper bound on what
// selling that player raises.
export interface SquadFinances {
  bank: number | null
  team_value: number | null
  squad_selling_value: number | null
  current_price_total: number | null
  selling_prices_exact: boolean
  note: string | null
}

export interface Team {
  id: number
  name: string
  players: Player[]
  current_gameweek: number | null
  // Issue 20: the gameweek the XI/captain above were read from. Trails
  // current_gameweek in the pre-deadline window, because FPL only publishes
  // picks once a deadline has locked -- so never label these as the lineup
  // for the gameweek being planned.
  squad_gameweek: number | null
  finances: SquadFinances | null
  overall_rank: number | null
  gameweek_points: number | null
  total_points: number | null
  points_on_bench: number | null
  active_chip: string | null
}

export interface Instinct {
  text: string
  logged_at: string
}

// Issue 19: the bandwagon meter. `movement` is the field that matters -- a
// settled majority and a four-day surge produce the same `creators_positive`
// and mean opposite things, so never render the count without it.
export type ConsensusMovement =
  | 'no_coverage'
  | 'new_coverage'
  | 'dropped_off'
  | 'surging'
  | 'cooling'
  | 'settled'
  | 'steady'

export interface StanceCount {
  stance: string
  creators: number
  stances: number
}

export interface ConsensusWindow {
  gameweek: number | null
  creators_covering: number
  creators_positive: number
  creators_negative: number
  creators_neutral: number
  by_stance: StanceCount[]
  creator_names: string[]
  stances: number
}

export interface ConsensusChange {
  covering: number
  positive: number
  negative: number
  neutral: number
}

export interface CreatorConsensus {
  player_name: string
  roster_size: number
  planning_gameweek: number | null
  current: ConsensusWindow
  previous: ConsensusWindow
  change: ConsensusChange
  movement: ConsensusMovement
  summary: string
  instincts: Instinct[]
  instinct_note: string | null
  gameweek_source: string
  undated_stances: number
  note: string | null
}

export interface Instincts {
  notes: Instinct[]
}

// Issue 23: the deadline screen.
//
// Three states rather than a boolean. `unknown` is the honest answer before
// ingestion has run and for anything resting on data FPL does not publish,
// and collapsing it into either of the others makes the screen lie.
// `attention` means "worth your eyes", never "you are wrong" -- the checklist
// reports facts and leaves the decision to the user.
export type ChecklistState = 'ok' | 'attention' | 'unknown'

export interface ChecklistItem {
  key: string
  title: string
  state: ChecklistState
  detail: string
  players: string[]
}

export interface DeadlineCountdown {
  // The gameweek of the next UNPASSED deadline, which is not always the
  // planning gameweek: mid-gameweek, the planning gameweek is one whose
  // deadline has already gone.
  gameweek: number | null
  planning_gameweek: number | null
  deadline: string | null
  seconds_remaining: number | null
  // 'deadlines' | 'first_kickoff' | 'none'. On 'first_kickoff' the countdown
  // runs roughly 90 minutes LONG -- `note` says so and must be shown.
  source: string
  note: string | null
}

export interface PricePrediction {
  player_id: number
  player_name: string
  direction: string
  ownership: number | null
  net_transfers_event: number
  evidence: string
}

export interface PriceChangePredictions {
  rising: PricePrediction[]
  falling: PricePrediction[]
}

export interface DeadlineChecklist {
  target_gameweek: number | null
  squad_gameweek: number | null
  countdown: DeadlineCountdown
  items: ChecklistItem[]
  // The user's own players only, not the global rising/falling lists.
  price_risk: PriceChangePredictions
  attention_count: number
  unknown_count: number
}

// "2d 4h" / "4h 12m" / "12m". Days and hours together, or hours and minutes,
// but never three units -- past a day the minutes are noise, and inside an
// hour they are the whole message. Negative means the deadline has passed
// while the page sat open, which reads as "Deadline passed", not "-3m".
export function countdownLabel(seconds: number | null): string | null {
  if (seconds === null) return null
  if (seconds <= 0) return 'Deadline passed'
  const days = Math.floor(seconds / 86400)
  const hours = Math.floor((seconds % 86400) / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  if (days > 0) return `${days}d ${hours}h`
  if (hours > 0) return `${hours}h ${minutes}m`
  return `${minutes}m`
}

// Under six hours the deadline stops being a date and starts being a clock.
export const URGENT_SECONDS = 6 * 60 * 60

// Issue 25: a Roadmap is a plan the user is FOLLOWING, as opposed to a
// TransferPlan, which is one answer to one question. Versioned and never
// overwritten -- "last week's roadmap said something different" is the whole
// point, so a superseded version stays readable.
export type RoadmapStatus = 'live' | 'completed' | 'superseded'

export interface StepViolation {
  // Indexes the plan's steps AS SUBMITTED. Steps may share a gameweek, so the
  // gameweek alone does not identify one.
  step_index: number
  gameweek: number
  rule: string
  message: string
}

export interface StepCost {
  step_index: number
  gameweek: number
  decision: string
  player_in: string | null
  player_out: string | null
  free_transfers_before: number | null
  is_hit: boolean
  hit_cost: number
  bank_before: number | null
  bank_after: number | null
}

export interface PlanValidation {
  // FPL-legality only. A plan can be legal and still carry violations -- a
  // step aimed at a blank gameweek is the case this distinction exists for.
  legal: boolean
  violations: StepViolation[]
  steps: StepCost[]
  total_hit_cost: number
  transfers_planned: number
  affordability_exact: boolean
  summary: string
  note: string | null
}

export interface FixtureChange {
  club: string
  gameweek: number
  was: number
  now: number
  kind: string
  message: string
}

export interface RoadmapChange {
  step_index: number
  gameweek: number
  // 'elapsed' is not a problem -- the plan simply progressed. Everything else
  // is a reason a remaining step no longer holds.
  kind: 'elapsed' | 'injury' | 'fixture' | 'illegal' | 'price'
  message: string
  player_name: string | null
}

export interface Roadmap {
  id: number
  team_id: number
  version: number
  status: RoadmapStatus
  plan: TransferPlan
  from_gameweek: number | null
  prompt: string | null
  created_at: string
  superseded_at: string | null
  supersede_reason: string | null
}

export interface RoadmapCheckIn {
  roadmap: Roadmap | null
  planning_gameweek: number | null
  elapsed_steps: number[]
  remaining_steps: number[]
  changes: RoadmapChange[]
  fixture_changes: FixtureChange[]
  revalidation: PlanValidation | null
  still_valid: boolean
  summary: string
  note: string | null
}

export type Suggestion = Recommendation | TransferPlan | null

// Headline shown where a player name would otherwise go. Keyed by decision so
// a pick-less answer reads as the decision it is, not as a blank pick.
const DECISION_LABELS: Record<DecisionKind, string> = {
  captain: 'Captain',
  transfer: 'Transfer',
  roll: 'Roll your transfer',
  chip: 'Play a chip',
  other: 'No change',
}

export function decisionLabel(decision: DecisionKind): string {
  return DECISION_LABELS[decision] ?? 'Recommendation'
}

// Issue 17: "12:23" from 743. Hours are only shown when there are any, since
// almost every FPL video is under an hour and "00:12:23" reads as noise.
export function timestampLabel(seconds: number | null): string | null {
  if (seconds === null || seconds <= 0) return null
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  const secs = Math.floor(seconds % 60)
  const pad = (n: number) => String(n).padStart(2, '0')
  return hours > 0 ? `${hours}:${pad(minutes)}:${pad(secs)}` : `${minutes}:${pad(secs)}`
}

// Issue 19: how a movement reads on screen. `tone` drives colour only --
// 'warn' means "this is moving fast, look harder", never "this is bad".
const MOVEMENT_LABELS: Record<ConsensusMovement, { label: string; tone: string }> = {
  surging: { label: 'Bandwagon forming', tone: 'warn' },
  settled: { label: 'Settled view', tone: 'accent' },
  cooling: { label: 'Cooling off', tone: 'warn' },
  new_coverage: { label: 'Newly covered', tone: 'neutral' },
  dropped_off: { label: 'Dropped off', tone: 'neutral' },
  steady: { label: 'No movement', tone: 'neutral' },
  no_coverage: { label: 'No coverage', tone: 'neutral' },
}

export function movementLabel(movement: ConsensusMovement): { label: string; tone: string } {
  return MOVEMENT_LABELS[movement] ?? { label: 'Consensus', tone: 'neutral' }
}

// "+7" / "-3" / "0". Signed on purpose: the change is the point, and an
// unsigned number reads as a level.
export function signed(n: number): string {
  return n > 0 ? `+${n}` : String(n)
}

// Issue 28: what the discourse is on, ranked by movement rather than volume.
export interface AttentionRow {
  player_name: string
  current: ConsensusWindow
  previous: ConsensusWindow
  change: ConsensusChange
  movement: ConsensusMovement
  // Coverage inside the last 48 hours, separated from coverage carried over.
  creators_last_48h: number
  creator_names_last_48h: string[]
  summary: string
}

export interface AttentionBoard {
  planning_gameweek: number | null
  rows: AttentionRow[]
  total_players: number
  returned: number
  roster_size: number
  gameweek_source: string
  note: string | null
}

// Issue 29: a theme is a cluster of related creator content.
//
// Two strings, and the distinction is the point. `title` NAMES the theme and is
// assembled by counting — a term from a closed FPL vocabulary matched literally
// in the creators' words, plus players resolved through stances. `label` QUOTES
// it and is a real chunk of creator text, never generated prose, for the same
// reason ADR-0014 forbids a model-authored citation.
export interface Theme {
  title: string
  label: string
  // Both numbers, never one: `creators` is how many people hold the view,
  // `mentions` is how loudly it was said.
  creators: number
  creator_names: string[]
  mentions: number
  players: string[]
  citation: Citation | null
  is_new: boolean
  first_seen: string | null
}

export interface ThemeBoard {
  planning_gameweek: number | null
  themes: Theme[]
  chunks_considered: number
  threshold: number
  note: string | null
}


// ---------------------------------------------------------------------------
// Refreshing the data.
//
// Ingestion is still on demand (ADR-0002) — nothing here schedules anything.
// What these types carry is the part the app previously could not say: when
// the data on screen was last refreshed, and whether the last attempt worked.

export type IngestionKind = 'fpl' | 'creators'

export type IngestionRunStatus = 'running' | 'succeeded' | 'failed' | 'abandoned'

export interface IngestionRun {
  id: number
  kind: IngestionKind
  status: IngestionRunStatus
  trigger: 'api' | 'cli'
  started_at: string
  finished_at: string | null
  duration_seconds: number | null
  // Whichever pipeline summary this run produced. Untyped on purpose: the two
  // pipelines count different things and each grows a counter per ticket, so
  // it is rendered generically rather than mapped field by field.
  summary: Record<string, unknown> | null
  error: string | null
}

// `source` is the field that stops a stamp from lying:
//   run   — a recorded successful run; the figure is exact.
//   proxy — no run on record, so the data's own newest timestamp is used. A
//           lower bound: it says when data last CHANGED, and a run that
//           legitimately found nothing new would not have moved it.
//   none  — nothing ingested at all. Not the same as old, and must never
//           render as an age.
export interface DataFreshness {
  kind: IngestionKind
  last_success: string | null
  source: 'run' | 'proxy' | 'none'
  age_seconds: number | null
  is_stale: boolean
  note: string | null
}

export interface IngestionStatus {
  runs: IngestionRun[]
  freshness: DataFreshness[]
  running: IngestionKind[]
}

// What each source is called, and what pressing its button actually does.
//
// `billed` is not decoration. The FPL leg is ~100 requests to two public APIs
// and no model calls, so it can be pressed freely; the creator leg bills Grok,
// Claude and Voyage per run. Rendering both the same way is how a user ends up
// spending money by reflex, so the billed one gets a confirmation step.
export const SOURCE_META: Record<
  IngestionKind,
  { label: string; action: string; billed: boolean; blurb: string; expect: string }
> = {
  fpl: {
    label: 'FPL data',
    // Spelled out rather than derived by lowercasing `label`, which turns
    // "FPL" into "fpl".
    action: 'Refresh FPL data',
    billed: false,
    blurb: 'Squad, bank, XI, captain, chips, fixtures, prices, xG/xA, results.',
    expect: 'Free — public APIs only. Usually a minute or two.',
  },
  creators: {
    label: 'Creator content',
    action: 'Refresh creator content',
    billed: true,
    blurb: 'Posts and video transcripts from your trusted creators, and the stances pulled from them.',
    expect: 'Costs money (Grok, Claude, Voyage) and can take tens of minutes.',
  },
}

/**
 * Readable names for the counters an ingestion summary reports.
 *
 * The renderer stays generic — it walks whatever keys the run actually
 * produced — because both pipelines grow a counter per ticket and a
 * hand-written line per pipeline goes stale silently, still looking complete
 * while omitting the new field. This map only improves the WORDING of keys we
 * know about; an unrecognised key still renders, via the underscore
 * transform, which is what preserves that property.
 */
export const SUMMARY_LABELS: Record<string, string> = {
  players_ingested: 'squad players',
  fixtures_ingested: 'fixtures',
  players_pool_ingested: 'players in the pool',
  players_with_xg: 'with xG/xA',
  outcomes_recorded: 'outcomes recorded',
  chips_used: 'chips used',
  minutes_players_fetched: 'players with minutes refreshed',
  gameweeks_of_history: 'gameweeks of history',
  gameweeks_of_calendar: 'gameweek deadlines',
  clubs_ingested: 'clubs',
  creators_processed: 'creators checked',
  content_ingested: 'new posts/videos',
  content_skipped: 'already seen',
  stances_extracted: 'stances',
  chunks_embedded: 'chunks embedded',
  youtube_channels_checked: 'YouTube channels checked',
  youtube_channels_failed: 'YouTube channels unreachable',
  videos_ingested: 'transcripts',
  videos_without_transcript: 'videos without a transcript',
  videos_too_old: 'videos too old',
  transcript_stances_extracted: 'stances from transcripts',
}

export function ageLabel(seconds: number | null): string | null {
  if (seconds === null) return null
  if (seconds < 90) return 'just now'
  if (seconds < 5400) return `${Math.floor(seconds / 60)}m ago`
  if (seconds < 172800) return `${Math.floor(seconds / 3600)}h ago`
  return `${Math.floor(seconds / 86400)}d ago`
}

/**
 * The single word the compact header control shows.
 *
 * Deliberately reports the WORST of the two sources rather than an average or
 * the most recent: the point of a header stamp is to make you open the panel
 * when something needs attention, and a fresh FPL run must not mask creator
 * content that has not been pulled in three weeks.
 */
export function overallFreshness(
  status: IngestionStatus | null,
): { tone: 'ok' | 'warn' | 'unknown' | 'running'; label: string } {
  if (status === null) return { tone: 'unknown', label: 'Data' }
  if (status.running.length > 0) return { tone: 'running', label: 'Refreshing' }

  const failed = status.runs.find((r) => r.status === 'failed' || r.status === 'abandoned')
  if (failed) return { tone: 'warn', label: 'Refresh failed' }

  if (status.freshness.some((f) => f.source === 'none')) {
    return { tone: 'unknown', label: 'Not ingested' }
  }
  if (status.freshness.some((f) => f.is_stale)) return { tone: 'warn', label: 'Data is old' }

  const newest = status.freshness
    .map((f) => f.age_seconds)
    .filter((a): a is number => a !== null)
  const worst = newest.length > 0 ? Math.max(...newest) : null
  return { tone: 'ok', label: ageLabel(worst) ?? 'Data' }
}
