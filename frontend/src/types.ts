export interface Post {
  source: string
  title: string
  title_zh?: string
  content: string
  comments: string[]
  url: string
  hn_url: string
  score: number
  num_comments: number
  has_need_signals: boolean
  _post_id?: string
  _engine?: string
}

export interface Quote {
  text: string
  source_url: string
  author: string
  score: number
  platform: string
  context: string
  signal_type: string
}

export interface FemwcDimension {
  score: number
  reasoning: string
}

export interface FemwcResult {
  F: FemwcDimension
  E: FemwcDimension
  M: FemwcDimension
  W: FemwcDimension
  C: FemwcDimension
  total: number
  verdict: string
  summary: string
}

export interface NeedPackage {
  title: string
  description: string
  femwc: FemwcResult
  total_score: number
  quotes: Quote[]
  representative_posts: Post[]
  user_segments: string[]
  existing_solutions: string[]
  signal_summary: string
}

export interface Need {
  need_title: string
  need_description: string
  posts: Post[]
  total_score: number
  total_comments: number
  deep_mine_package?: NeedPackage
}

export interface PersonaQuote {
  text: string
  text_zh: string
  source_url?: string
  context?: string
}

export interface Persona {
  name: string
  avatar_seed: string
  tagline: string
  bio: string
  gender: 'male' | 'female'
  avatar_hint?: string
  demographics: {
    age_range: string
    occupation: string
    location_hint: string
    tech_savviness: 'low' | 'medium' | 'high'
  }
  goals: string[]
  frustrations: string[]
  behaviors: string[]
  tools_used: string[]
  willingness_to_pay: string
  quotes: PersonaQuote[]
  day_in_life: string
  priority_rank: string[]
  switching_trigger: string
  deal_breaker: string
}

export interface EngineStatus {
  engine: string
  preference?: string
  rdt_status?: {
    installed: boolean
    authenticated: boolean
    version: string
    error: string
  }
}

export interface DebateEntry {
  role: 'analyst' | 'critic' | 'director' | 'human' | 'researcher' | 'investor'
  content: string
}

export interface ReportSummary {
  filename: string
  title: string
  created_at: string
  rounds: number
  report_format?: 'json' | 'markdown'
  verdict?: string
  femwc_total?: number | null
  ai_fit?: string
}

export interface FemwcScores {
  F: number
  E: number
  M: number
  W: number
  C: number
  total: number
}

export interface ReportData {
  post?: Post
  need?: Need
  debate_log?: DebateEntry[]
  final_report: string | Record<string, unknown>
  debate_rounds: number
  report_format?: 'json' | 'markdown'
  created_at: string
  feishu?: { url: string; document_id: string }
}

export type DebateStatus =
  | 'idle'
  | 'debating'
  | 'debate_done'
  | 'generating_report'
  | 'done'

export interface ChatMessage {
  id: string
  role: 'analyst' | 'critic' | 'director' | 'human' | 'researcher' | 'investor'
  label: string
  content: string
  streaming?: boolean
  provider?: 'claude' | 'gpt'
  topicDivider?: { index: number; title: string; total: number }
}

// Trending
export interface TrendingPost {
  title: string
  title_zh?: string
  score: number
  num_comments: number
  subreddit?: string
  url: string
  hn_url?: string
  source?: string
}

export interface TrendingSubreddit {
  name: string
  name_zh?: string
  subscribers: number
  active_users: number
  hot_posts: TrendingPost[]
  day_score: number
  day_comments: number
}

export interface TopApp {
  name: string
  icon_url?: string
  publisher?: string
  revenue: number
  revenue_display: string
  downloads: number
  downloads_display: string
  growth_pct?: number | null
  downloads_growth_pct?: number | null
  dau?: number
  dau_display?: string
}

export interface MarketData {
  product_count: number
  revenue_sum: number
  revenue_avg: number
  downloads_sum: number
  revenue_growth_pct: number
  top_apps?: TopApp[]
}

export interface TrendingCategory {
  key: string
  label: string
  label_en?: string
  st_category_id?: number
  custom?: boolean
  subreddits: TrendingSubreddit[]
  hn_posts?: TrendingPost[]
  reddit_score: number
  reddit_comments: number
  hn_score: number
  hn_comments: number
  total_score: number
  total_comments: number
  total_subscribers: number
  heat_index: number
  change_pct?: number
  alert?: 'surge' | 'cool' | null
  market?: MarketData | null
}

export interface TrendingData {
  categories: TrendingCategory[]
  scanned_at: string
  scanning?: boolean
}

export type DetailView =
  | { type: 'empty' }
  | { type: 'post' }
  | { type: 'analysis' }
  | { type: 'message'; id: string }
  | { type: 'report' }
