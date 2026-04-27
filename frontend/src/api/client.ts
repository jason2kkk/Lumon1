import type { Need, ReportSummary, ReportData, DebateEntry, EngineStatus, NeedPackage, TrendingData, TrendingSubreddit, TrendingPost } from '../types'

const BASE = '/api'

const SESSION_KEY = 'lumon_session_id'

function getSessionId(): string {
  let id = localStorage.getItem(SESSION_KEY)
  if (!id) {
    id = crypto.randomUUID()
    localStorage.setItem(SESSION_KEY, id)
  }
  return id
}

export function sessionHeaders(extra?: Record<string, string>): Record<string, string> {
  return { 'X-Session-Id': getSessionId(), ...extra }
}

async function json<T>(url: string, init?: RequestInit): Promise<T> {
  const headers = { ...sessionHeaders(), ...(init?.headers as Record<string, string>) }
  const res = await fetch(BASE + url, { ...init, headers })
  if (!res.ok) {
    const err = await res.text()
    throw new Error(err)
  }
  return res.json()
}

// ---- Config ----

export function getConfigStatus() {
  return json<{ claude_ok: boolean; gpt_ok: boolean; errors: string[] }>('/config/status')
}

export interface ConfigValues {
  CLAUDE_BASE_URL: string
  CLAUDE_API_KEY: string
  CLAUDE_API_KEY_SET: boolean
  CLAUDE_MODEL: string
  GPT_BASE_URL: string
  GPT_API_KEY: string
  GPT_API_KEY_SET: boolean
  GPT_MODEL: string
}

export function getConfigValues() {
  return json<ConfigValues>('/config/values')
}

export function saveConfig(config: Record<string, string>) {
  return json<{ ok: boolean }>('/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config),
  })
}

export function getRoleNames() {
  return json<Record<string, string>>('/config/role-names')
}

export function saveRoleNames(names: Record<string, string>) {
  return json<{ ok: boolean; role_names: Record<string, string> }>('/config/role-names', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(names),
  })
}

export function getGeneralModel() {
  return json<{ model: string }>('/config/general-model')
}

export function setGeneralModel(model: string) {
  return json<{ ok: boolean }>('/config/general-model', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model }),
  })
}

export function getServiceUsage() {
  return json<Record<string, Record<string, unknown>>>('/config/usage')
}

export interface TokenStats {
  claude: { input: number; output: number; calls: number }
  gpt: { input: number; output: number; calls: number }
}

export function getTokenStats() {
  return json<TokenStats>('/config/token-stats')
}

export function resetTokenStats() {
  return json<{ ok: boolean }>('/config/token-stats/reset', { method: 'POST' })
}

export function testConnection(prefix: string, opts?: { base_url?: string; api_key?: string; model?: string }) {
  return json<{ ok: boolean; message: string }>('/config/test', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prefix, ...opts }),
  })
}

// ---- Fetch / Needs ----

export interface FetchParams {
  mode: 'sentence' | 'keywords' | 'open'
  query?: string
  keywords?: string[]
  sources: string[]
  category?: string
  reddit_categories?: string[]
  limit: number
  time_period?: 'month' | '3months' | '6months' | '9months'
  product?: string
  market?: string
  demographics?: string
  segment?: string
  pain_points?: number
  competitors?: string
  demo?: boolean
}

export interface RedditCategory {
  label: string
  subreddits: string[]
}

export function getRedditCategories() {
  return json<{ categories: Record<string, RedditCategory> }>('/reddit-categories')
}

export interface FetchCallbacks {
  onProgress?: (data: { message: string; progress: number }) => void
  onResult?: (data: { needs: Need[]; count: number }) => void
  onError?: (data: { message: string }) => void
  onDone?: () => void
}

export async function streamFetchNeeds(
  params: FetchParams,
  callbacks: FetchCallbacks,
  signal?: AbortSignal,
) {
  let res: Response
  try {
    res = await fetch(BASE + '/fetch', {
      method: 'POST',
      headers: sessionHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(params),
      signal,
    })
  } catch (err) {
    if (signal?.aborted) return
    callbacks.onError?.({ message: String(err) })
    return
  }

  if (!res.ok || !res.body) {
    const errText = await res.text().catch(() => `HTTP ${res.status}`)
    callbacks.onError?.({ message: errText })
    return
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  let gotDone = false
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      let currentEvent = ''
      for (const line of lines) {
        if (line.startsWith('event: ')) {
          currentEvent = line.slice(7).trim()
        } else if (line.startsWith('data: ')) {
          try {
            const data = JSON.parse(line.slice(6))
            switch (currentEvent) {
              case 'fetch_progress':
                callbacks.onProgress?.(data)
                break
              case 'fetch_result':
                callbacks.onResult?.(data)
                break
              case 'error':
                callbacks.onError?.(data)
                break
              case 'done':
                gotDone = true
                callbacks.onDone?.()
                break
            }
          } catch { /* skip malformed */ }
          currentEvent = ''
        }
      }
    }
  } catch (err) {
    if (!signal?.aborted) {
      callbacks.onError?.({ message: String(err) })
    }
  }
  // Stream ended without a 'done' SSE event (e.g. connection dropped by proxy)
  if (!gotDone && !signal?.aborted) {
    callbacks.onDone?.()
  }
}

export function getNeeds() {
  return json<{ needs: Need[] }>('/needs')
}

export function clearNeeds() {
  return json<{ ok: boolean }>('/needs', { method: 'DELETE' })
}

export function syncNeeds(needs: Need[]) {
  return json<{ ok: boolean; count: number }>('/needs', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ needs }),
  })
}

export interface FetchJobStatus {
  active: boolean
  progress: number
  history: string[]
  error: string
  needs: Need[] | null
  engine: string
}

export function getFetchStatus() {
  return json<FetchJobStatus>('/fetch/status')
}

export function stopFetch() {
  return json<{ ok: boolean }>('/fetch/stop', { method: 'POST' })
}

export function translateText(text: string) {
  return json<{ translation: string }>('/translate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  })
}

// ---- Engine Status ----

export function getEngineStatus(force = false) {
  return json<EngineStatus>(`/engine-status${force ? '?force=true' : ''}`)
}

export function getEnginePreference() {
  return json<{ preference: string }>('/engine-preference')
}

export function setEnginePreference(preference: string) {
  return json<{ ok: boolean; preference: string }>('/engine-preference', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ preference }),
  })
}

export function getWebSearchEngine() {
  return json<{ engine: string }>('/web-search-engine')
}

export function setWebSearchEngine(engine: string) {
  return json<{ ok: boolean; engine: string }>('/web-search-engine', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ engine }),
  })
}

export function testWebSearch(engine: string) {
  return json<{ ok: boolean; message: string }>('/web-search-test', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ engine }),
  })
}

// ---- Trending ----

export function getTrending(refresh = false) {
  return json<TrendingData>(`/trending${refresh ? '?refresh=true' : ''}`)
}

export function getTrendingHistory(category = '', days = 7) {
  const params = new URLSearchParams()
  if (category) params.set('category', category)
  params.set('days', String(days))
  return json<{ category?: string; series: Record<string, unknown>[] }>(`/trending/history?${params}`)
}

export function getTrendingDetail(category: string) {
  return json<{
    key: string; label: string
    subreddits: TrendingSubreddit[]
    hn_posts: TrendingPost[]
    keywords: string[]
  }>(`/trending/detail/${category}`)
}

export function getCustomCategories() {
  return json<Record<string, { label: string; subreddits: string[]; hn_tags: string[]; custom?: boolean }>>('/trending/custom-categories')
}

export function saveCustomCategories(categories: Record<string, unknown>) {
  return json<{ ok: boolean }>('/trending/custom-categories', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ categories }),
  })
}

// ---- Product Data ----

export interface ProductPeer {
  name: string
  icon_url: string
  publisher: string
  revenue: number
  revenue_display: string
  downloads: number
  downloads_display: string
  growth_pct: number | null
  downloads_growth_pct: number | null
  dau: number
  dau_display: string
  rank?: number
  is_ours?: boolean
}

export interface ProductData {
  product: {
    name: string
    icon_url: string
    publisher: string
    revenue: number
    revenue_display: string
    downloads: number
    downloads_display: string
    dau: number
    dau_display: string
    growth_pct: number | null
    downloads_growth_pct: number | null
  } | null
  peers: ProductPeer[]
}

export function getProductData(productKey: string) {
  return json<ProductData>(`/trending/product/${productKey}`)
}

// ---- Deep Mine ----

export interface DeepMineCallbacks {
  onProgress?: (data: { message: string; progress: number }) => void
  onResult?: (data: { package: NeedPackage; need_index: number }) => void
  onError?: (data: { message: string }) => void
  onDone?: () => void
}

export async function streamDeepMine(
  needIndex: number,
  callbacks: DeepMineCallbacks,
  signal?: AbortSignal,
) {
  let res: Response
  try {
    res = await fetch(BASE + '/deep-mine', {
      method: 'POST',
      headers: sessionHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ need_index: needIndex }),
      signal,
    })
  } catch (err) {
    if (signal?.aborted) return
    callbacks.onError?.({ message: String(err) })
    return
  }

  if (!res.ok || !res.body) {
    const errText = await res.text().catch(() => `HTTP ${res.status}`)
    callbacks.onError?.({ message: errText })
    return
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let _gotDone = false

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      let currentEvent = ''
      for (const line of lines) {
        if (line.startsWith('event: ')) {
          currentEvent = line.slice(7).trim()
        } else if (line.startsWith('data: ')) {
          try {
            const data = JSON.parse(line.slice(6))
            switch (currentEvent) {
              case 'fetch_progress':
                callbacks.onProgress?.(data)
                break
              case 'deep_mine_result':
                _gotDone = true
                callbacks.onResult?.(data)
                break
              case 'error':
                _gotDone = true
                callbacks.onError?.(data)
                break
              case 'done':
                _gotDone = true
                callbacks.onDone?.()
                break
            }
          } catch { /* skip malformed */ }
          currentEvent = ''
        }
      }
    }
  } catch (err) {
    if (!signal?.aborted) {
      callbacks.onError?.({ message: String(err) })
      _gotDone = true
    }
  }
  if (!_gotDone && !signal?.aborted) {
    callbacks.onError?.({ message: '网络连接中断，请刷新页面重试' })
  }
}

// ---- Debate ----

export function getDebateState() {
  return json<{
    status: string
    round: number
    max_rounds: number
    debate_log: DebateEntry[]
    selected_need_idx: number | null
    final_report: string | null
    product_proposal: string | null
    free_topic_input?: string | null
  }>('/debate/state')
}

export function resetDebate() {
  return json<{ ok: boolean }>('/debate/reset', { method: 'POST' })
}

export interface SSECallbacks {
  onMessageStart?: (data: { role: string; label: string; provider?: string }) => void
  onChunk?: (data: { text: string }) => void
  onMessageEnd?: (data: { role: string; content: string }) => void
  onRoundStart?: (data: { round: number }) => void
  onDebateEnd?: (data: { reason: string; rounds: number }) => void
  onReportEnd?: (data: { report: string; filename: string }) => void
  onProposalEnd?: (data: { proposal: string }) => void
  onSearchProgress?: (data: { query: string; result_count: number; total_results: number; total_queries: number }) => void
  onDeepDiveEnd?: () => void
  onTopicList?: (data: { topics: { title: string; question: string }[] }) => void
  onTopicStart?: (data: { index: number; title: string; total: number }) => void
  onTopicEnd?: (data: { index: number; title: string; summary: string }) => void
  onError?: (data: { message: string }) => void
  onDone?: () => void
}

export async function streamSSE(
  url: string,
  body: unknown,
  callbacks: SSECallbacks,
  signal?: AbortSignal,
) {
  let res: Response
  try {
    res = await fetch(BASE + url, {
      method: 'POST',
      headers: sessionHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(body),
      signal,
    })
  } catch (err) {
    if (signal?.aborted) return
    callbacks.onError?.({ message: String(err) })
    return
  }

  if (!res.ok || !res.body) {
    callbacks.onError?.({ message: `HTTP ${res.status}` })
    return
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let gotTerminal = false

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      let currentEvent = ''
      for (const line of lines) {
        if (line.startsWith('event: ')) {
          currentEvent = line.slice(7).trim()
        } else if (line.startsWith('data: ')) {
          const raw = line.slice(6)
          try {
            const data = JSON.parse(raw)
            switch (currentEvent) {
              case 'message_start':
                callbacks.onMessageStart?.(data)
                break
              case 'chunk':
                callbacks.onChunk?.(data)
                break
              case 'message_end':
                callbacks.onMessageEnd?.(data)
                break
              case 'round_start':
                callbacks.onRoundStart?.(data)
                break
              case 'debate_end':
                gotTerminal = true
                callbacks.onDebateEnd?.(data)
                break
              case 'report_start':
                break
              case 'report_end':
                gotTerminal = true
                callbacks.onReportEnd?.(data)
                break
              case 'proposal_start':
                break
              case 'proposal_end':
                gotTerminal = true
                callbacks.onProposalEnd?.(data)
                break
              case 'search_progress':
                callbacks.onSearchProgress?.(data)
                break
              case 'deep_dive_end':
                gotTerminal = true
                callbacks.onDeepDiveEnd?.()
                break
              case 'topic_list':
                callbacks.onTopicList?.(data)
                break
              case 'topic_start':
                callbacks.onTopicStart?.(data)
                break
              case 'topic_end':
                callbacks.onTopicEnd?.(data)
                break
              case 'error':
                gotTerminal = true
                callbacks.onError?.(data)
                break
              case 'done':
                gotTerminal = true
                callbacks.onDone?.()
                break
            }
          } catch {
            // skip malformed JSON lines
          }
          currentEvent = ''
        }
      }
    }
  } catch (err) {
    if (!signal?.aborted) {
      callbacks.onError?.({ message: String(err) })
      gotTerminal = true
    }
  }
  if (!gotTerminal && !signal?.aborted) {
    callbacks.onError?.({ message: '网络连接中断，请刷新页面重试' })
  }
}

// ---- Direct Report Generation ----

export interface DirectReportCallbacks {
  onProgress?: (data: { message: string; progress: number }) => void
  onChunk?: (data: { text: string }) => void
  onDone?: (data: { report: string; filename: string }) => void
  onError?: (data: { message: string }) => void
}

export async function streamGenerateReport(
  needIndex: number,
  callbacks: DirectReportCallbacks,
  signal?: AbortSignal,
  options?: { demo?: boolean },
) {
  let res: Response
  try {
    res = await fetch(BASE + '/generate-report', {
      method: 'POST',
      headers: sessionHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ need_index: needIndex, ...(options?.demo ? { demo: true } : {}) }),
      signal,
    })
  } catch (err) {
    if (signal?.aborted) return
    callbacks.onError?.({ message: String(err) })
    return
  }

  if (!res.ok || !res.body) {
    const errText = await res.text().catch(() => `HTTP ${res.status}`)
    callbacks.onError?.({ message: errText })
    return
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let _gotDone = false

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      let currentEvent = ''
      for (const line of lines) {
        if (line.startsWith('event: ')) {
          currentEvent = line.slice(7).trim()
        } else if (line.startsWith('data: ')) {
          try {
            const data = JSON.parse(line.slice(6))
            switch (currentEvent) {
              case 'report_progress':
                callbacks.onProgress?.(data)
                break
              case 'report_chunk':
                callbacks.onChunk?.(data)
                break
              case 'report_done':
                _gotDone = true
                callbacks.onDone?.(data)
                break
              case 'error':
                _gotDone = true
                callbacks.onError?.(data)
                break
            }
          } catch { /* skip malformed */ }
          currentEvent = ''
        }
      }
    }
    if (!signal?.aborted && !_gotDone) {
      const status = await getReportGenStatus().catch(() => null)
      if (status?.done && status.filename) {
        callbacks.onDone?.({ report: '', filename: status.filename })
      } else if (status?.active) {
        // still running in background, don't error - caller should reconnect
      } else {
        callbacks.onError?.({ message: '报告生成中断，请重试。反复失败请检查网络或 API 配置' })
      }
    }
  } catch (err) {
    if (!signal?.aborted) {
      callbacks.onError?.({ message: String(err) })
    }
  }
}

export function getReportGenStatus() {
  return json<{ active: boolean; need_index: number; progress: number; message: string; error: string; done: boolean; filename: string; chunk_count: number }>('/report-gen/status')
}

export function streamReportGenResume(
  callbacks: DirectReportCallbacks,
  signal?: AbortSignal,
) {
  const url = BASE + '/report-gen/stream'
  fetch(url, { headers: sessionHeaders(), signal })
    .then(async (res) => {
      if (!res.ok || !res.body) {
        callbacks.onError?.({ message: `HTTP ${res.status}` })
        return
      }
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let _gotDone = false
      try {
        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true })
          const lines = buffer.split('\n')
          buffer = lines.pop() || ''
          let currentEvent = ''
          for (const line of lines) {
            if (line.startsWith('event: ')) {
              currentEvent = line.slice(7).trim()
            } else if (line.startsWith('data: ')) {
              try {
                const data = JSON.parse(line.slice(6))
                switch (currentEvent) {
                  case 'report_progress': callbacks.onProgress?.(data); break
                  case 'report_chunk': callbacks.onChunk?.(data); break
                  case 'report_done': _gotDone = true; callbacks.onDone?.(data); break
                  case 'error': _gotDone = true; callbacks.onError?.(data); break
                }
              } catch { /* skip */ }
              currentEvent = ''
            }
          }
        }
        if (!signal?.aborted && !_gotDone) {
          const status = await getReportGenStatus().catch(() => null)
          if (status?.done && status.filename) {
            callbacks.onDone?.({ report: '', filename: status.filename })
          }
        }
      } catch { /* ignore */ }
    })
    .catch(() => {})
}

// ---- Reports ----

export function listReports() {
  return json<{ reports: ReportSummary[] }>('/reports')
}

export function getReport(filename: string) {
  return json<ReportData>(`/reports/${filename}`)
}

export async function deleteReport(filename: string) {
  const res = await fetch(BASE + '/reports/' + encodeURIComponent(filename), { method: 'DELETE', headers: sessionHeaders() })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function exportToFeishu(filename: string) {
  const res = await fetch(BASE + '/reports/' + encodeURIComponent(filename) + '/export-feishu', { method: 'POST', headers: sessionHeaders() })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: '导出失败' }))
    throw new Error(err.detail || '导出失败')
  }
  return res.json() as Promise<{ ok: boolean; url: string; document_id: string }>
}

export async function getFeishuStatus() {
  return json<{ configured: boolean }>('/config/feishu-status')
}

export async function getSensorTowerStatus() {
  return json<{ installed: boolean; available: boolean; api_ok: boolean; error: string }>('/config/st-status')
}

// ---- Online Stats ----

export interface OnlineStats {
  online: number
  mining: number
  needs: number
}

export function getOnlineStats() {
  return json<OnlineStats>('/online-stats')
}

// ---- POC 评价 ----

export interface PocEvalInput {
  idea_name: string
  idea_brief: string
  target_users: string
  pain_points: string
  simple_product: string
}

export interface PocEvalDimension {
  verdict: boolean
  description?: string
  reason: string
  suggestion: string
}

export interface PocEvalResult {
  id: string
  timestamp: string
  input: PocEvalInput
  evaluation: {
    clear_users: PocEvalDimension
    real_needs: PocEvalDimension
    simple_product: PocEvalDimension
    overall_verdict: string
    summary: string
  }
}

export interface OpportunityPoint {
  title: string
  description: string
  target_users: string
  pain_points: string
  features: string[]
  simple_product: string
  eval_id?: string
}

export function extractOpportunities(reportContent: string | Record<string, unknown>, reportFilename?: string) {
  return json<{ opportunities: OpportunityPoint[] }>('/poc-evaluate/extract-opportunities', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ report_content: reportContent, report_filename: reportFilename || '' }),
  })
}

export function runPocEvaluation(input: PocEvalInput & { report_filename?: string; opportunity_index?: number }) {
  return json<PocEvalResult>('/poc-evaluate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
}

export function getPocEvalResult(evalId: string) {
  return json<PocEvalResult>(`/poc-evaluate/${evalId}`)
}
