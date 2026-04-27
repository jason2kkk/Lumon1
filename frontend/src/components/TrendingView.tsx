import { useState, useEffect, useCallback, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Loader2, TrendingUp, TrendingDown, ExternalLink,
  ChevronDown, AlertTriangle, RefreshCw, Plus, X,
  Minus, ArrowRight, Pin, PinOff,
} from 'lucide-react'
import {
  getTrending, getTrendingHistory, getTrendingDetail,
  getCustomCategories, saveCustomCategories,
  getProductData,
} from '../api/client'
import type { ProductData } from '../api/client'
import type { TrendingCategory, TrendingPost, TrendingSubreddit, TopApp } from '../types'
import { useAppStore } from '../stores/app'
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis,
  Tooltip, CartesianGrid,
} from 'recharts'

function AppIcon({ url, size = 'w-5 h-5', rounded = 'rounded', className = '' }: { url?: string; size?: string; rounded?: string; className?: string }) {
  const [failed, setFailed] = useState(false)
  if (!url || failed) return <div className={`${size} ${rounded} bg-neutral-200 shrink-0 ${className}`} />
  return <img src={url} alt="" className={`${size} ${rounded} shrink-0 ${className}`} onError={() => setFailed(true)} />
}

const OUR_PRODUCTS = [
  {
    key: 'owll_translator',
    name: 'Owll Translator',
    subtitle: 'AI Voice Clone',
    categoryKey: 'translation',
    gradient: 'from-violet-500 to-indigo-500',
    fallbackIcon: '🦉',
  },
  {
    key: 'owll_note',
    name: 'Owll Note',
    subtitle: 'AI Note Taker & Record',
    categoryKey: 'recording',
    gradient: 'from-orange-400 to-rose-500',
    fallbackIcon: '🦉',
  },
  {
    key: 'bible_note',
    name: 'Bible Note',
    subtitle: 'Taker & Recorder',
    categoryKey: 'knowledge',
    gradient: 'from-amber-500 to-yellow-400',
    fallbackIcon: '📖',
  },
]

const CACHE_KEY = 'lumon_trending_cache'
const CACHE_TTL = 24 * 60 * 60 * 1000
const CACHE_VERSION = 3
const PIN_KEY = 'lumon_trending_pinned'

function loadPinned(): Set<string> {
  try {
    const raw = localStorage.getItem(PIN_KEY)
    if (!raw) return new Set()
    return new Set(JSON.parse(raw))
  } catch { return new Set() }
}

function savePinned(pinned: Set<string>) {
  localStorage.setItem(PIN_KEY, JSON.stringify([...pinned]))
}

function fmtMoney(v: number): string {
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}M`
  if (v >= 1_000) return `$${(v / 1_000).toFixed(0)}K`
  return `$${v.toFixed(0)}`
}

function fmtNum(v: number): string {
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`
  if (v >= 1_000) return `${(v / 1_000).toFixed(0)}K`
  return `${v.toFixed(0)}`
}

function loadCache(): TrendingCategory[] | null {
  try {
    const raw = localStorage.getItem(CACHE_KEY)
    if (!raw) return null
    const { data, ts, v } = JSON.parse(raw)
    if (v !== CACHE_VERSION || Date.now() - ts > CACHE_TTL) return null
    return data
  } catch { return null }
}

function saveCache(data: TrendingCategory[]) {
  localStorage.setItem(CACHE_KEY, JSON.stringify({ data, ts: Date.now(), v: CACHE_VERSION }))
}

/* ========== 帖子行（带链接 icon + 中文翻译） ========== */
function PostRow({ post, showSource }: { post: TrendingPost; showSource?: string }) {
  return (
    <div className="flex items-start gap-1.5 text-[11px] py-0.5">
      <a href={post.hn_url || post.url} target="_blank" rel="noopener noreferrer"
        className="shrink-0 mt-0.5 text-muted/50 hover:text-accent transition-colors">
        <ExternalLink size={10} />
      </a>
      <div className="flex-1 min-w-0">
        <a href={post.hn_url || post.url} target="_blank" rel="noopener noreferrer"
          className="text-text/80 hover:text-accent transition-colors line-clamp-1">{post.title}</a>
        {post.title_zh && (
          <p className="text-[10px] text-muted/60 line-clamp-1 mt-0">{post.title_zh}</p>
        )}
      </div>
      <div className="shrink-0 flex items-center gap-1.5 text-[10px] text-muted mt-0.5">
        {showSource && <span className="text-[9px] px-1 py-px rounded bg-bg font-medium">{showSource}</span>}
        <span>▲{post.score}</span>
        <span>💬{post.num_comments}</span>
      </div>
    </div>
  )
}

/* ========== 自定义赛道编辑弹窗 ========== */
interface CategoryEditorProps {
  open: boolean
  onClose: () => void
  onSaved: () => void
}

function CategoryEditor({ open, onClose, onSaved }: CategoryEditorProps) {
  const [categories, setCategories] = useState<Record<string, { label: string; subreddits: string[]; hn_tags: string[] }>>({})
  const [loading, setLoading] = useState(false)
  const [newKey, setNewKey] = useState('')
  const [newLabel, setNewLabel] = useState('')
  const [newSubs, setNewSubs] = useState('')
  const [newTags, setNewTags] = useState('')

  useEffect(() => {
    if (open) {
      getCustomCategories().then(setCategories).catch(() => {})
    }
  }, [open])

  const handleAdd = () => {
    const key = newKey.trim().replace(/\s+/g, '_').toLowerCase()
    if (!key || !newLabel.trim()) return
    setCategories(prev => ({
      ...prev,
      [key]: {
        label: newLabel.trim(),
        subreddits: newSubs.split(',').map(s => s.trim()).filter(Boolean),
        hn_tags: newTags.split(',').map(s => s.trim()).filter(Boolean),
      },
    }))
    setNewKey(''); setNewLabel(''); setNewSubs(''); setNewTags('')
  }

  const handleRemove = (key: string) => {
    setCategories(prev => {
      const next = { ...prev }
      delete next[key]
      return next
    })
  }

  const handleSave = async () => {
    setLoading(true)
    try {
      await saveCustomCategories(categories)
      onSaved()
      onClose()
    } catch { /* */ }
    setLoading(false)
  }

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30" onClick={onClose}>
      <motion.div
        initial={{ opacity: 0, scale: 0.95 }}
        animate={{ opacity: 1, scale: 1 }}
        exit={{ opacity: 0, scale: 0.95 }}
        className="bg-card rounded-2xl shadow-xl w-[520px] max-h-[80vh] overflow-y-auto p-6"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-base font-semibold">自定义赛道管理</h3>
          <button onClick={onClose} className="text-muted hover:text-text"><X size={16} /></button>
        </div>

        {Object.entries(categories).length > 0 && (
          <div className="space-y-2 mb-4">
            {Object.entries(categories).map(([key, cat]) => (
              <div key={key} className="flex items-center gap-2 bg-bg/60 rounded-xl px-3 py-2">
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium">{cat.label}</p>
                  <p className="text-[11px] text-muted truncate">
                    Reddit: {cat.subreddits.join(', ')} | HN: {cat.hn_tags.join(', ')}
                  </p>
                </div>
                <button onClick={() => handleRemove(key)} className="text-muted hover:text-signal shrink-0">
                  <Minus size={14} />
                </button>
              </div>
            ))}
          </div>
        )}

        <div className="border border-border/30 rounded-xl p-3 space-y-2 mb-4">
          <p className="text-xs font-medium text-muted">添加新赛道</p>
          <div className="grid grid-cols-2 gap-2">
            <input value={newKey} onChange={e => setNewKey(e.target.value)}
              placeholder="赛道 key（英文）" className="text-xs px-2.5 py-1.5 bg-bg rounded-lg border border-border/30" />
            <input value={newLabel} onChange={e => setNewLabel(e.target.value)}
              placeholder="显示名称（中文）" className="text-xs px-2.5 py-1.5 bg-bg rounded-lg border border-border/30" />
          </div>
          <input value={newSubs} onChange={e => setNewSubs(e.target.value)}
            placeholder="子版块（逗号分隔）如 ChatGPT, LocalLLaMA" className="w-full text-xs px-2.5 py-1.5 bg-bg rounded-lg border border-border/30" />
          <input value={newTags} onChange={e => setNewTags(e.target.value)}
            placeholder="HN 标签（逗号分隔）如 AI, LLM, GPT" className="w-full text-xs px-2.5 py-1.5 bg-bg rounded-lg border border-border/30" />
          <button onClick={handleAdd} disabled={!newKey.trim() || !newLabel.trim()}
            className="flex items-center gap-1 text-xs text-accent hover:underline disabled:opacity-40">
            <Plus size={12} /> 添加
          </button>
        </div>

        <div className="flex justify-end gap-2">
          <button onClick={onClose} className="text-xs px-4 py-1.5 text-muted hover:text-text">取消</button>
          <button onClick={handleSave} disabled={loading}
            className="text-xs px-4 py-1.5 bg-accent text-white rounded-lg hover:bg-accent/90 disabled:opacity-50">
            {loading ? '保存中...' : '保存'}
          </button>
        </div>
      </motion.div>
    </div>
  )
}

/* ========== 市场数据卡片（含可展开 Top5） ========== */
function MarketDataCard({ market }: { market: NonNullable<TrendingCategory['market']> }) {
  const [expanded, setExpanded] = useState(false)
  const topApps = (market as any).top_apps as TopApp[] | undefined

  return (
    <div className="bg-bg/50 rounded-xl p-4">
      <h4 className="text-xs font-semibold mb-3 text-muted flex items-center gap-1.5">
        <span className="w-4 h-4 bg-emerald-500 text-white rounded text-[10px] flex items-center justify-center font-bold">$</span>
        市场数据（SensorTower）
      </h4>
      <div className="grid grid-cols-4 gap-3">
        <div className="text-center">
          <p className="text-[18px] font-bold text-emerald-600">{fmtMoney(market.revenue_sum)}</p>
          <p className="text-[10px] text-muted mt-0.5">月收入</p>
        </div>
        <div className="text-center">
          <p className="text-[18px] font-bold text-blue-500">{fmtNum(market.downloads_sum)}</p>
          <p className="text-[10px] text-muted mt-0.5">月下载</p>
        </div>
        <div className="text-center">
          <p className={`text-[18px] font-bold ${market.revenue_growth_pct >= 0 ? 'text-emerald-600' : 'text-red-500'}`}>
            {market.revenue_growth_pct >= 0 ? '+' : ''}{market.revenue_growth_pct}%
          </p>
          <p className="text-[10px] text-muted mt-0.5">收入增长</p>
        </div>
        <div className="text-center">
          <p className="text-[18px] font-bold text-text">{market.product_count}</p>
          <p className="text-[10px] text-muted mt-0.5">产品数</p>
        </div>
      </div>

      {topApps && topApps.length > 0 && (
        <>
          <button
            onClick={() => setExpanded(!expanded)}
            className="w-full mt-3 pt-2 border-t border-border/20 flex items-center justify-center gap-1 text-[10px] text-muted hover:text-accent transition-colors cursor-pointer"
          >
            {expanded ? '收起' : `查看 Top ${topApps.length} 产品`}
            <ChevronDown size={10} className={`transition-transform ${expanded ? 'rotate-180' : ''}`} />
          </button>
          <AnimatePresence>
            {expanded && (
              <motion.div
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: 'auto', opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                transition={{ duration: 0.2 }}
                className="overflow-hidden"
              >
                {(() => {
                  const hasDau = topApps.some(a => a.dau)
                  return (
                    <div className="mt-2 text-[11px]">
                      <div className="flex items-center py-2 border-b border-border/20 text-muted/70 font-medium">
                        <span className="w-6 shrink-0">#</span>
                        <span className="w-[180px] shrink-0">产品</span>
                        <span className="flex-1 text-center">月收入</span>
                        <span className="flex-1 text-center">月下载</span>
                        {hasDau && <span className="flex-1 text-center">DAU</span>}
                        <span className="flex-1 text-center pr-3">增长</span>
                      </div>
                      {topApps.map((app, i) => (
                        <div key={i} className="flex items-center py-2.5 border-b border-border/10 last:border-0">
                          <span className="w-6 shrink-0 text-muted/50">{i + 1}</span>
                          <div className="w-[180px] shrink-0 flex items-center gap-1.5 min-w-0 pr-2">
                            <AppIcon url={app.icon_url} />
                            <div className="min-w-0 flex-1">
                              <p className="font-medium text-text truncate">{app.name || '-'}</p>
                              {app.publisher && <p className="text-[9px] text-muted/50 truncate">{app.publisher}</p>}
                            </div>
                          </div>
                          <span className="flex-1 text-center text-text font-medium whitespace-nowrap">{app.revenue_display}</span>
                          <span className="flex-1 text-center text-text whitespace-nowrap">
                            {app.downloads_display}
                            {app.downloads_growth_pct != null && (
                              <span className={`ml-0.5 text-[9px] ${app.downloads_growth_pct >= 0 ? 'text-emerald-500' : 'text-red-400'}`}>
                                {app.downloads_growth_pct >= 0 ? '↑' : '↓'}{Math.abs(app.downloads_growth_pct)}%
                              </span>
                            )}
                          </span>
                          {hasDau && <span className="flex-1 text-center text-text whitespace-nowrap">{app.dau_display || '-'}</span>}
                          <span className="flex-1 text-center pr-3 whitespace-nowrap">
                            {app.growth_pct != null ? (
                              <span className={app.growth_pct >= 0 ? 'text-emerald-600' : 'text-red-500'}>
                                {app.growth_pct >= 0 ? '+' : ''}{app.growth_pct}%
                              </span>
                            ) : <span className="text-muted/40">-</span>}
                          </span>
                        </div>
                      ))}
                    </div>
                  )
                })()}
              </motion.div>
            )}
          </AnimatePresence>
        </>
      )}

      {(!topApps || topApps.length === 0) && (
        <div className="mt-2 pt-2 border-t border-border/20 text-[9px] text-muted text-center">
          Top {market.product_count} 产品 · 平均月收入 {fmtMoney(market.revenue_avg)}
        </div>
      )}
    </div>
  )
}

const _productDataCache: Record<string, ProductData> = {}

/* ========== 产品详情右栏 ========== */
function ProductDetailPanel({ productKey, productConfig }: {
  productKey: string
  productConfig: typeof OUR_PRODUCTS[number]
}) {
  const [data, setData] = useState<ProductData | null>(_productDataCache[productKey] ?? null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (_productDataCache[productKey]) {
      setData(_productDataCache[productKey])
      return
    }
    setLoading(true)
    getProductData(productKey)
      .then(d => {
        if (d) _productDataCache[productKey] = d
        setData(d)
      })
      .catch(() => setData(null))
      .finally(() => setLoading(false))
  }, [productKey])

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20 gap-2 text-sm text-muted">
        <Loader2 size={18} className="animate-spin" /> 正在加载产品数据...
      </div>
    )
  }

  const product = data?.product
  const peers = data?.peers ?? []
  const hasDau = peers.some(p => p.dau)

  return (
    <div className="space-y-4">
      {/* 产品信息卡 */}
      <div className="bg-bg/50 rounded-xl p-5">
        <div className="flex items-center gap-3 mb-4">
          <AppIcon url={product?.icon_url} size="w-12 h-12" rounded="rounded-xl" className="shadow-sm" />
          <div>
            <h3 className="text-base font-semibold text-text">{product?.name || productConfig.name}</h3>
            <p className="text-[11px] text-muted">{product?.publisher || productConfig.subtitle}</p>
          </div>
        </div>

        {product && (
          <div className="grid grid-cols-4 gap-3">
            <div className="text-center">
              <p className="text-[18px] font-bold text-emerald-600">{product.revenue_display}</p>
              <p className="text-[10px] text-muted mt-0.5">月收入</p>
            </div>
            <div className="text-center">
              <p className="text-[18px] font-bold text-blue-500">{product.downloads_display}</p>
              <p className="text-[10px] text-muted mt-0.5">月下载</p>
            </div>
            <div className="text-center">
              <p className="text-[18px] font-bold text-text">{product.dau_display}</p>
              <p className="text-[10px] text-muted mt-0.5">DAU</p>
            </div>
            <div className="text-center">
              <p className={`text-[18px] font-bold ${(product.growth_pct ?? 0) >= 0 ? 'text-emerald-600' : 'text-red-500'}`}>
                {product.growth_pct != null ? `${product.growth_pct >= 0 ? '+' : ''}${product.growth_pct}%` : '-'}
              </p>
              <p className="text-[10px] text-muted mt-0.5">收入增长</p>
            </div>
          </div>
        )}
        {!product && (
          <p className="text-xs text-muted text-center py-4">
            未获取到产品数据，请确认 st-cli 已连接
          </p>
        )}
      </div>

      {/* 同量级竞品排名 */}
      {peers.length > 0 && (
        <div className="bg-bg/50 rounded-xl p-4">
          <h4 className="text-xs font-semibold mb-3 text-muted flex items-center gap-1.5">
            <span className="w-4 h-4 bg-blue-500 text-white rounded text-[10px] flex items-center justify-center font-bold">⚡</span>
            同量级竞品排名（按收入排序）
          </h4>
          <div className="text-[11px]">
            <div className="flex items-center py-2 border-b border-border/20 text-muted/70 font-medium">
              <span className="w-6 shrink-0">#</span>
              <span className="w-[180px] shrink-0">产品</span>
              <span className="flex-1 text-center">月收入</span>
              <span className="flex-1 text-center">月下载</span>
              {hasDau && <span className="flex-1 text-center">DAU</span>}
              <span className="flex-1 text-center pr-3">增长</span>
            </div>
            {peers.map((peer, i) => (
              <div key={i} className={`flex items-center py-2.5 border-b border-border/10 last:border-0 ${
                peer.is_ours ? 'bg-accent/5 rounded-lg -mx-1 px-1 border-accent/20' : ''
              }`}>
                <span className={`w-6 shrink-0 ${peer.is_ours ? 'text-accent font-bold' : 'text-muted/50'}`}>
                  {peer.rank ?? (i + 1)}
                </span>
                <div className="w-[180px] shrink-0 flex items-center gap-1.5 min-w-0 pr-2">
                  <AppIcon url={peer.icon_url} size="w-5 h-5" className={peer.is_ours ? 'ring-1.5 ring-accent/40' : ''} />
                  <div className="min-w-0 flex-1">
                    <p className={`font-medium truncate ${peer.is_ours ? 'text-accent' : 'text-text'}`}>
                      {peer.name || '-'}
                      {peer.is_ours && <span className="ml-1 text-[8px] bg-accent/15 text-accent px-1 py-px rounded">我们</span>}
                    </p>
                    {peer.publisher && <p className="text-[9px] text-muted/50 truncate">{peer.publisher}</p>}
                  </div>
                </div>
                <span className={`flex-1 text-center font-medium whitespace-nowrap ${peer.is_ours ? 'text-accent' : 'text-text'}`}>{peer.revenue_display}</span>
                <span className={`flex-1 text-center whitespace-nowrap ${peer.is_ours ? 'text-accent' : 'text-text'}`}>
                  {peer.downloads_display}
                  {peer.downloads_growth_pct != null && (
                    <span className={`ml-0.5 text-[9px] ${peer.downloads_growth_pct >= 0 ? 'text-emerald-500' : 'text-red-400'}`}>
                      {peer.downloads_growth_pct >= 0 ? '↑' : '↓'}{Math.abs(peer.downloads_growth_pct)}%
                    </span>
                  )}
                </span>
                {hasDau && <span className={`flex-1 text-center whitespace-nowrap ${peer.is_ours ? 'text-accent' : 'text-text'}`}>{peer.dau_display || '-'}</span>}
                <span className="flex-1 text-center pr-3 whitespace-nowrap">
                  {peer.growth_pct != null ? (
                    <span className={peer.growth_pct >= 0 ? 'text-emerald-600' : 'text-red-500'}>
                      {peer.growth_pct >= 0 ? '+' : ''}{peer.growth_pct}%
                    </span>
                  ) : <span className="text-muted/40">-</span>}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {peers.length === 0 && !loading && (
        <div className="bg-bg/50 rounded-xl p-4 text-center">
          <p className="text-xs text-muted">暂无竞品数据，请确认 st-cli 已连接</p>
        </div>
      )}
    </div>
  )
}

/* ========== 赛道详情右栏 ========== */
interface DetailPanelProps {
  category: TrendingCategory
  historyData: Record<string, unknown>[]
  historyLoading: boolean
}

const DETAIL_CACHE_KEY = 'lumon_trending_detail_cache'

function _loadDetailCache(): Record<string, { subreddits: TrendingSubreddit[]; hn_posts: TrendingPost[] }> {
  try {
    const raw = sessionStorage.getItem(DETAIL_CACHE_KEY)
    if (raw) return JSON.parse(raw)
  } catch { /* */ }
  return {}
}

function _saveDetailCache(cache: Record<string, { subreddits: TrendingSubreddit[]; hn_posts: TrendingPost[] }>) {
  try {
    sessionStorage.setItem(DETAIL_CACHE_KEY, JSON.stringify(cache))
  } catch { /* quota exceeded */ }
}

const detailCacheRef: Record<string, { subreddits: TrendingSubreddit[]; hn_posts: TrendingPost[] }> = _loadDetailCache()

function TrendingDetailPanel({ category, historyData, historyLoading }: DetailPanelProps) {
  const [detailData, setDetailData] = useState<{
    subreddits: TrendingSubreddit[]
    hn_posts: TrendingPost[]
  } | null>(detailCacheRef[category.key] ?? null)
  const [detailLoading, setDetailLoading] = useState(false)

  useEffect(() => {
    const cached = detailCacheRef[category.key]
    const hasTranslations = cached?.subreddits?.some(s => s.name_zh) ||
      cached?.hn_posts?.some(p => p.title_zh)
    if (cached && hasTranslations) {
      setDetailData(cached)
      return
    }
    setDetailLoading(true)
    getTrendingDetail(category.key)
      .then(d => {
        detailCacheRef[category.key] = d
        _saveDetailCache(detailCacheRef)
        setDetailData(d)
      })
      .catch(() => setDetailData(null))
      .finally(() => setDetailLoading(false))
  }, [category.key])

  const subs = detailData?.subreddits ?? category.subreddits
  const hnPosts = detailData?.hn_posts ?? category.hn_posts ?? []

  return (
    <div className="space-y-4">
      {/* 趋势图 */}
      <div className="bg-bg/50 rounded-xl p-4">
        <h4 className="text-xs font-semibold mb-3 text-muted">热度趋势（近 7 天）</h4>
        {historyLoading ? (
          <div className="flex items-center justify-center py-8 text-muted">
            <Loader2 size={16} className="animate-spin" />
          </div>
        ) : historyData.length > 1 ? (
          <ResponsiveContainer width="100%" height={160}>
            <LineChart data={historyData}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(0,0,0,0.06)" />
              <XAxis dataKey="date" tick={{ fontSize: 10 }} tickFormatter={v => v?.slice(5) || ''} />
              <YAxis tick={{ fontSize: 10 }} width={40} />
              <Tooltip
                contentStyle={{ fontSize: 11, borderRadius: 8, border: '1px solid rgba(0,0,0,0.1)' }}
                labelFormatter={v => `日期: ${v}`}
              />
              <Line type="monotone" dataKey="heat_index" stroke="#f97316" strokeWidth={2} dot={{ r: 3 }} name="热度指数" />
            </LineChart>
          </ResponsiveContainer>
        ) : (
          <p className="text-xs text-muted text-center py-6">历史数据不足，至少需要 2 天数据</p>
        )}
      </div>

      {/* 市场数据（SensorTower） */}
      {category.market && <MarketDataCard market={category.market} />}

      {/* Reddit 子版块 */}
      {subs.length > 0 && (
        <div className="bg-bg/50 rounded-xl p-4">
          <h4 className="text-xs font-semibold mb-2 text-muted flex items-center gap-1.5">
            <span className="w-4 h-4 bg-orange-500 text-white rounded text-[10px] flex items-center justify-center font-bold">R</span>
            Reddit 子版块
            {detailLoading && <Loader2 size={10} className="animate-spin text-muted/40 ml-1" />}
          </h4>
          <div className="space-y-2">
            {subs.map(sub => (
              <div key={sub.name} className="bg-card rounded-xl px-3 py-2">
                <div className="flex items-center justify-between mb-1">
                  <span className="text-xs font-semibold">
                    r/{sub.name}
                    {sub.name_zh && <span className="text-muted/60 font-normal ml-1.5">({sub.name_zh})</span>}
                  </span>
                </div>
                {sub.hot_posts.length > 0 && (
                  <div className="space-y-0.5">
                    {sub.hot_posts.slice(0, 5).map((post, pi) => <PostRow key={pi} post={post} />)}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* HN 热帖 */}
      {hnPosts.length > 0 && (
        <div className="bg-bg/50 rounded-xl p-4">
          <h4 className="text-xs font-semibold mb-2 text-muted flex items-center gap-1.5">
            <span className="w-4 h-4 bg-orange-500 text-white rounded text-[10px] flex items-center justify-center font-bold">Y</span>
            HackerNews 热帖
          </h4>
          <div className="space-y-1">
            {hnPosts.map((post, i) => <PostRow key={i} post={post} />)}
          </div>
        </div>
      )}

    </div>
  )
}

/* ========== 赛道卡片组件 ========== */
function CategoryCard({ cat, rank, isSelected, isPinned, maxHeat, onSelect }: {
  cat: TrendingCategory; rank: number; isSelected: boolean; isPinned: boolean; maxHeat: number; onSelect: () => void
}) {
  const pct = cat.change_pct ?? 0
  return (
    <motion.button
      onClick={onSelect}
      whileTap={{ scale: 0.99 }}
      className={`w-full text-left rounded-3xl border transition-all px-3.5 py-2.5 ${
        isSelected
          ? 'bg-accent/5 border-accent/30 shadow-sm'
          : 'bg-card border-border/60 hover:border-accent/30 hover:shadow-sm'
      }`}
    >
      <div className="flex items-center gap-2.5">
        <div className={`w-6 h-6 rounded-lg flex items-center justify-center text-[11px] font-bold shrink-0 ${
          isPinned ? 'bg-amber-100 text-amber-700' : rank < 3 ? 'bg-neutral-100 text-text' : 'bg-bg text-muted'
        }`}>
          {isPinned ? <Pin size={10} /> : rank + 1}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5">
            <p className="text-[13px] font-semibold truncate">{cat.label}</p>
            {cat.label_en && <span className="text-[9px] text-muted/60 truncate">{cat.label_en}</span>}
            {cat.alert === 'surge' && <span className="text-[8px] px-1 py-px bg-red-50 text-red-500 rounded animate-pulse">暴涨</span>}
            {cat.alert === 'cool' && <span className="text-[8px] px-1 py-px bg-blue-50 text-blue-500 rounded">冷却</span>}
          </div>
          <div className="flex items-center gap-2 mt-0.5 text-[9px] text-muted">
            <span>Reddit ▲{cat.reddit_score} 💬{cat.reddit_comments}</span>
            {(cat.hn_score ?? 0) > 0 && <span>HN ▲{cat.hn_score} 💬{cat.hn_comments}</span>}
          </div>
          {cat.market && (
            <div className="flex items-center gap-3 mt-1.5 text-[9px] text-text/60">
              <span>月收入 {fmtMoney(cat.market.revenue_sum)}</span>
              <span>月下载 {fmtNum(cat.market.downloads_sum)}</span>
              <span className={cat.market.revenue_growth_pct >= 0 ? 'text-emerald-600' : 'text-red-500'}>
                月收入 {cat.market.revenue_growth_pct >= 0 ? '+' : ''}{cat.market.revenue_growth_pct}%
              </span>
            </div>
          )}
          <div className="flex items-center gap-2 mt-1.5">
            <div className="flex-1 h-1 bg-black/[0.04] rounded-full overflow-hidden">
              <div className="h-full bg-gradient-to-r from-orange-400 to-orange-500 rounded-full transition-all"
                style={{ width: `${Math.min(100, (cat.heat_index / maxHeat) * 100)}%` }} />
            </div>
            <span className="text-[10px] text-muted/50 tabular-nums w-10 text-right">{cat.heat_index}</span>
          </div>
        </div>
        <div className="shrink-0 flex flex-col items-end gap-0.5">
          {pct !== 0 ? (
            <span className={`text-[10px] font-semibold flex items-center gap-0.5 ${
              pct > 0 ? 'text-emerald-600' : 'text-red-500'
            }`}>
              {pct > 0 ? <TrendingUp size={10} /> : <TrendingDown size={10} />}
              {pct > 0 ? '+' : ''}{pct}%
            </span>
          ) : (
            <span className="text-[10px] text-muted">—</span>
          )}
          <ChevronDown size={10} className={`text-muted/40 transition-transform ${isSelected ? 'rotate-180' : ''}`} />
        </div>
      </div>
    </motion.button>
  )
}

/* ========== 主组件 ========== */
export default function TrendingView() {
  const { setActiveView, setPrefillFetchQuery } = useAppStore()
  const [data, setData] = useState<TrendingCategory[]>([])
  const [scanning, setScanning] = useState(false)
  const [scannedAt, setScannedAt] = useState('')
  const [selectedKey, setSelectedKey] = useState<string | null>(null)
  const [selectedProduct, setSelectedProduct] = useState<string | null>(null)
  const [historyData, setHistoryData] = useState<Record<string, unknown>[]>([])
  const [historyLoading, setHistoryLoading] = useState(false)
  const [editorOpen, setEditorOpen] = useState(false)
  const [pinnedKeys, setPinnedKeys] = useState<Set<string>>(loadPinned)
  const [productIcons, setProductIcons] = useState<Record<string, string>>(() => {
    try { const s = localStorage.getItem('lumon_product_icons'); return s ? JSON.parse(s) : {} } catch { return {} }
  })

  const togglePin = useCallback((key: string) => {
    setPinnedKeys(prev => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      savePinned(next)
      return next
    })
  }, [])

  const selectedCat = !selectedProduct ? (data.find(c => c.key === selectedKey) ?? null) : null
  const selectedProdConfig = OUR_PRODUCTS.find(p => p.key === selectedProduct) ?? null
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    OUR_PRODUCTS.forEach(prod => {
      if (productIcons[prod.key]) return
      getProductData(prod.key).then(d => {
        if (d?.product?.icon_url) {
          setProductIcons(prev => {
            const next = { ...prev, [prod.key]: d.product!.icon_url }
            try { localStorage.setItem('lumon_product_icons', JSON.stringify(next)) } catch { /* */ }
            return next
          })
        }
      }).catch(() => {})
    })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const doLoad = useCallback(async (refresh = false) => {
    if (!refresh) {
      const cached = loadCache()
      if (cached && cached.length > 0) {
        setData(cached)
      }
    } else {
      for (const k of Object.keys(detailCacheRef)) delete detailCacheRef[k]
      try { sessionStorage.removeItem(DETAIL_CACHE_KEY) } catch { /* */ }
    }
    try {
      const result = await getTrending(refresh)
      if (result.categories?.length > 0) {
        setData(result.categories)
        setScannedAt(result.scanned_at || '')
        if (!result.scanning) {
          saveCache(result.categories)
        }
      }
      setScanning(!!result.scanning)
      return !!result.scanning
    } catch {
      setScanning(false)
      return false
    }
  }, [])

  useEffect(() => {
    let alive = true
    const run = async () => {
      const isScanning = await doLoad()
      if (isScanning && alive) {
        const poll = async () => {
          if (!alive) return
          try {
            const result = await getTrending(false)
            if (result.categories?.length > 0) {
              setData(result.categories)
              setScannedAt(result.scanned_at || '')
              if (!result.scanning) {
                saveCache(result.categories)
              }
            }
            if (result.scanning && alive) {
              pollRef.current = setTimeout(poll, 5000)
            } else {
              setScanning(false)
            }
          } catch {
            setScanning(false)
          }
        }
        pollRef.current = setTimeout(poll, 5000)
      }
    }
    run()
    return () => {
      alive = false
      if (pollRef.current) clearTimeout(pollRef.current)
    }
  }, [doLoad])

  useEffect(() => {
    if (!selectedKey && data.length > 0) {
      setSelectedKey(data[0].key)
    }
  }, [data, selectedKey])

  useEffect(() => {
    if (!selectedKey) return
    setHistoryLoading(true)
    getTrendingHistory(selectedKey, 7)
      .then(res => setHistoryData(res.series))
      .catch(() => setHistoryData([]))
      .finally(() => setHistoryLoading(false))
  }, [selectedKey])

  const alerts = data.filter(c => c.alert)

  const { setAutoStartFetch } = useAppStore()
  const handleStartFetch = (label: string) => {
    setPrefillFetchQuery(label)
    setAutoStartFetch(true)
    setActiveView('fetch')
  }

  const maxHeat = data[0]?.heat_index || 1

  return (
    <div className="flex flex-col h-full">
      {/* 顶栏 */}
      <div className="shrink-0 flex items-center justify-between px-6 h-[52px] border-b border-border/20">
        <div className="flex items-center gap-3">
          <img src="/fire_line.png" alt="" className="w-5 h-5" />
          <h2 className="text-[15px] font-semibold">市场热度雷达</h2>
          {scannedAt && !scanning && (
            <span className="text-[10px] text-muted">
              更新于 {new Date(scannedAt).toLocaleString('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => {
              setScanning(true)
              doLoad(true).then(isScanning => {
                if (isScanning) {
                  const poll = async () => {
                    try {
                      const result = await getTrending(false)
                      if (result.categories?.length > 0) {
                        setData(result.categories)
                        setScannedAt(result.scanned_at || '')
                        if (!result.scanning) saveCache(result.categories)
                      }
                      if (result.scanning) {
                        pollRef.current = setTimeout(poll, 5000)
                      } else {
                        setScanning(false)
                      }
                    } catch { setScanning(false) }
                  }
                  pollRef.current = setTimeout(poll, 5000)
                } else { setScanning(false) }
              })
            }} disabled={scanning}
            className="flex items-center gap-1 text-[11px] text-accent hover:underline disabled:opacity-50 px-2.5 py-1 rounded-lg hover:bg-accent/5">
            <RefreshCw size={12} className={scanning ? 'animate-spin' : ''} /> {scanning ? '扫描中...' : '刷新'}
          </button>
        </div>
      </div>

      {/* 异常告警横幅 */}
      <AnimatePresence>
        {alerts.length > 0 && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="shrink-0 px-6 overflow-hidden"
          >
            <div className="flex items-center gap-2 bg-orange-50 border border-orange-200/50 rounded-xl px-4 py-2.5 mt-2">
              <AlertTriangle size={14} className="text-orange-500 shrink-0" />
              <p className="text-xs text-orange-700 flex-1">
                {alerts.map(c => (
                  <span key={c.key} className="mr-3">
                    <b>{c.label}</b> {c.alert === 'surge' ? '热度异常暴涨' : '热度异常冷却'}
                    {c.change_pct ? ` (${c.change_pct > 0 ? '+' : ''}${c.change_pct}%)` : ''}
                  </span>
                ))}
              </p>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* 主体：左右栏 */}
      <div className="flex-1 flex min-h-0">
        {/* 左栏：赛道排行榜 */}
        <div className="w-[340px] shrink-0 border-r border-border/15 overflow-y-auto scrollbar-auto p-4">
          {/* 监控产品入口 */}
          <div className="mb-3">
            <p className="text-[10px] text-muted font-medium mb-2 px-0.5">监控产品</p>
            <div className="flex gap-2">
              {OUR_PRODUCTS.map(prod => {
                const isActive = selectedProduct === prod.key
                const iconUrl = productIcons[prod.key]
                return (
                  <button
                    key={prod.key}
                    onClick={() => {
                      if (isActive) {
                        setSelectedProduct(null)
                      } else {
                        setSelectedProduct(prod.key)
                        setSelectedKey(null)
                      }
                    }}
                    className={`flex-1 min-w-0 rounded-2xl border p-2 transition-all text-left group cursor-pointer ${
                      isActive
                        ? 'border-accent/40 bg-accent/5 shadow-sm'
                        : 'border-border/50 bg-card hover:border-accent/30 hover:shadow-sm'
                    }`}
                  >
                    {iconUrl ? (
                      <img src={iconUrl} alt="" className="w-7 h-7 rounded-lg mb-1.5 shadow-sm" />
                    ) : (
                      <div className={`w-7 h-7 rounded-lg bg-gradient-to-br ${prod.gradient} flex items-center justify-center text-sm mb-1.5 shadow-sm`}>
                        {prod.fallbackIcon}
                      </div>
                    )}
                    <p className="text-[11px] font-semibold text-text truncate leading-tight">{prod.name}</p>
                    <p className="text-[9px] text-muted/60 truncate leading-tight mt-0.5">{prod.subtitle}</p>
                  </button>
                )
              })}
            </div>
          </div>

          {data.length === 0 ? (
            <div className="flex items-center justify-center py-16 gap-2 text-sm text-muted">
              <Loader2 size={18} className="animate-spin" /> 正在加载品类列表...
            </div>
          ) : (
            <>
              {/* 置顶赛道区域 */}
              <AnimatePresence>
                {data.some(c => pinnedKeys.has(c.key)) && (
                  <motion.div
                    initial={{ height: 0, opacity: 0 }}
                    animate={{ height: 'auto', opacity: 1 }}
                    exit={{ height: 0, opacity: 0 }}
                    transition={{ duration: 0.3, ease: 'easeInOut' }}
                    className="overflow-hidden mb-3"
                  >
                    <div className="bg-[#f3f3f1] rounded-2xl p-2.5">
                      <p className="text-[10px] text-muted font-medium mb-1.5 px-1 flex items-center gap-1"><Pin size={9} /> 置顶关注</p>
                      <div className="space-y-1.5">
                        <AnimatePresence initial={false}>
                          {data.filter(c => pinnedKeys.has(c.key)).map(cat => (
                            <motion.div
                              key={cat.key}
                              initial={{ opacity: 0, y: -20, height: 0 }}
                              animate={{ opacity: 1, y: 0, height: 'auto' }}
                              exit={{ opacity: 0, y: -20, height: 0 }}
                              transition={{ duration: 0.25, ease: 'easeInOut' }}
                            >
                              <CategoryCard cat={cat} rank={data.indexOf(cat)} isSelected={selectedKey === cat.key}
                                isPinned maxHeat={maxHeat} onSelect={() => { setSelectedProduct(null); setSelectedKey(selectedKey === cat.key ? null : cat.key) }} />
                            </motion.div>
                          ))}
                        </AnimatePresence>
                      </div>
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
              {/* 排行榜 */}
              <div className="space-y-1.5">
                <AnimatePresence initial={false}>
                  {data.filter(c => !pinnedKeys.has(c.key)).map(cat => {
                    const rank = data.indexOf(cat)
                    return (
                      <motion.div
                        key={cat.key}
                        layout
                        initial={{ opacity: 0, y: -16 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0, y: -16, height: 0 }}
                        transition={{ duration: 0.25, ease: 'easeInOut' }}
                      >
                        <CategoryCard cat={cat} rank={rank} isSelected={selectedKey === cat.key}
                          isPinned={false} maxHeat={maxHeat} onSelect={() => { setSelectedProduct(null); setSelectedKey(selectedKey === cat.key ? null : cat.key) }} />
                      </motion.div>
                    )
                  })}
                </AnimatePresence>
              </div>
            </>
          )}
        </div>

        {/* 右栏：赛道详情 / 产品详情 */}
        <div className="flex-1 overflow-y-auto scrollbar-auto p-4">
          <AnimatePresence mode="wait">
            {selectedProduct && selectedProdConfig ? (
              <motion.div
                key={`prod-${selectedProduct}`}
                initial={{ opacity: 0, x: 12 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: -12 }}
                transition={{ duration: 0.15 }}
              >
                <div className="flex items-center gap-2 mb-4">
                  <h3 className="text-base font-semibold">{selectedProdConfig.name}</h3>
                  <span className="text-[10px] text-muted/60 font-medium px-2 py-0.5 rounded-full bg-bg border border-border/30">竞品监控</span>
                  <div className="flex-1" />
                  <button
                    onClick={() => handleStartFetch(selectedProdConfig.name)}
                    className="group relative inline-flex items-center gap-1.5 text-[11px] font-semibold px-3 py-1.5 rounded-lg bg-neutral-900 text-white transition-all hover:scale-90 active:scale-75"
                  >
                    <span className="pointer-events-none absolute -inset-[5px] rounded-[10px] border-[2.5px] border-dashed border-transparent transition-colors group-hover:border-neutral-900/50" style={{ borderSpacing: '8px' }} />
                    <ArrowRight size={12} />
                    一键挖掘
                  </button>
                </div>
                <ProductDetailPanel productKey={selectedProduct} productConfig={selectedProdConfig} />
              </motion.div>
            ) : selectedCat ? (
              <motion.div
                key={selectedCat.key}
                initial={{ opacity: 0, x: 12 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: -12 }}
                transition={{ duration: 0.15 }}
              >
                <div className="flex items-center gap-2 mb-4">
                  <h3 className="text-base font-semibold">{selectedCat.label}</h3>
                  <button
                    onClick={() => togglePin(selectedCat.key)}
                    title={pinnedKeys.has(selectedCat.key) ? '取消置顶' : '置顶关注'}
                    className={`flex items-center gap-1 text-[10px] font-medium px-2 py-1 rounded-lg border transition-all ${
                      pinnedKeys.has(selectedCat.key)
                        ? 'bg-amber-50 border-amber-200 text-amber-700 hover:bg-amber-100'
                        : 'border-border/40 text-muted hover:border-amber-300 hover:text-amber-600'
                    }`}
                  >
                    {pinnedKeys.has(selectedCat.key) ? <PinOff size={10} /> : <Pin size={10} />}
                    {pinnedKeys.has(selectedCat.key) ? '取消置顶' : '置顶'}
                  </button>
                  {(selectedCat.change_pct ?? 0) !== 0 && (
                    <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${
                      (selectedCat.change_pct ?? 0) > 0 ? 'bg-emerald-100 text-emerald-700' : 'bg-red-100 text-red-600'
                    }`}>
                      {(selectedCat.change_pct ?? 0) > 0 ? '↑' : '↓'}{Math.abs(selectedCat.change_pct ?? 0)}%
                    </span>
                  )}
                  <div className="flex-1" />
                    <button
                      onClick={() => handleStartFetch(selectedCat.label)}
                      className="group relative inline-flex items-center gap-1.5 text-[11px] font-semibold px-3 py-1.5 rounded-lg bg-neutral-900 text-white transition-all hover:scale-90 active:scale-75"
                    >
                      <span className="pointer-events-none absolute -inset-[5px] rounded-[10px] border-[2.5px] border-dashed border-transparent transition-colors group-hover:border-neutral-900/50" style={{ borderSpacing: '8px' }} />
                      <ArrowRight size={12} />
                      一键挖掘
                    </button>
                </div>
                <TrendingDetailPanel
                  category={selectedCat}
                  historyData={historyData}
                  historyLoading={historyLoading}
                />
              </motion.div>
            ) : (
              <motion.div
                key="empty"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                className="flex flex-col items-center justify-center h-full text-muted"
              >
                <img src="/fire_line.png" alt="" className="w-12 h-12 mb-4" />
                <p className="text-sm">点击左侧赛道查看详情和趋势</p>
                <p className="text-xs mt-1 text-muted/60">包含 Reddit + HackerNews 综合热度数据</p>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>

      {/* 自定义赛道编辑器 */}
      <AnimatePresence>
        {editorOpen && (
          <CategoryEditor open={editorOpen} onClose={() => setEditorOpen(false)} onSaved={() => doLoad(true)} />
        )}
      </AnimatePresence>
    </div>
  )
}
