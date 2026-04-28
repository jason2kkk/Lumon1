/**
 * 用户画像建模视图
 * 缓存以 need_title 为 key，刷新自动加载 needs + 缓存
 * 性别匹配头像，卡片固定高度可展开（带动画），生成时头像轮播
 */
import React, { useState, useEffect, useRef, useCallback } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { ArrowLeft, MessageSquare, TrendingUp, AlertCircle, ExternalLink, ChevronDown } from 'lucide-react'
import { useAppStore } from '../stores/app'
import { streamGeneratePersonas, getNeeds } from '../api/client'
import type { Persona } from '../types'

type PersonaTab = 'cards' | 'story' | 'compare'

const TAB_OPTIONS: { id: PersonaTab; label: string }[] = [
  { id: 'cards', label: '画像卡片' },
  { id: 'story', label: '一天叙事' },
  { id: 'compare', label: '画像对比' },
]

interface AvatarMeta {
  src: string
  gender: 'male' | 'female'
  tags: string[]
}

const AVATAR_DB: AvatarMeta[] = [
  { src: '/avatars/avatar_1.png', gender: 'female', tags: ['young', 'white', 'blonde', 'casual'] },
  { src: '/avatars/avatar_2.png', gender: 'male', tags: ['young', 'white', 'brown hair', 'casual'] },
  { src: '/avatars/avatar_3.png', gender: 'female', tags: ['middle-aged', 'white', 'professional'] },
  { src: '/avatars/avatar_4.png', gender: 'male', tags: ['senior', 'white', 'gray hair', 'professional'] },
  { src: '/avatars/avatar_5.png', gender: 'female', tags: ['young', 'asian', 'black hair'] },
  { src: '/avatars/avatar_6.png', gender: 'male', tags: ['middle-aged', 'white', 'professional', 'glasses'] },
  { src: '/avatars/avatar_7.png', gender: 'female', tags: ['young', 'white', 'red hair', 'creative'] },
  { src: '/avatars/avatar_8.png', gender: 'male', tags: ['senior', 'white', 'beard'] },
  { src: '/avatars/avatar_9.png', gender: 'female', tags: ['young', 'latina', 'brown hair'] },
  { src: '/avatars/avatar_10.png', gender: 'male', tags: ['middle-aged', 'white', 'professional', 'brown hair'] },
  { src: '/avatars/avatar_11.png', gender: 'female', tags: ['middle-aged', 'white', 'professional', 'blonde'] },
  { src: '/avatars/avatar_12.png', gender: 'male', tags: ['senior', 'white', 'gray hair', 'glasses'] },
  { src: '/avatars/avatar_13.png', gender: 'female', tags: ['young', 'white', 'brown hair', 'casual'] },
  { src: '/avatars/avatar_14.png', gender: 'male', tags: ['young', 'white', 'dark hair', 'casual'] },
  { src: '/avatars/avatar_15.png', gender: 'female', tags: ['senior', 'white', 'gray hair'] },
  { src: '/avatars/avatar_16.png', gender: 'male', tags: ['middle-aged', 'black', 'professional'] },
  { src: '/avatars/avatar_17.png', gender: 'male', tags: ['young', 'black', 'short hair', 'casual'] },
  { src: '/avatars/avatar_18.png', gender: 'female', tags: ['young', 'black', 'curly hair', 'casual'] },
  { src: '/avatars/avatar_19.png', gender: 'male', tags: ['middle-aged', 'black', 'beard', 'professional'] },
  { src: '/avatars/avatar_20.png', gender: 'female', tags: ['middle-aged', 'black', 'professional'] },
  { src: '/avatars/avatar_21.png', gender: 'male', tags: ['young', 'black', 'athletic', 'casual'] },
  { src: '/avatars/avatar_22.png', gender: 'female', tags: ['senior', 'black', 'warm', 'professional'] },
]

const FEMALE_AVATARS = AVATAR_DB.filter(a => a.gender === 'female')
const MALE_AVATARS = AVATAR_DB.filter(a => a.gender === 'male')
const ALL_AVATARS = AVATAR_DB.map(a => a.src)

function inferAgeGroup(hint: string): 'young' | 'middle-aged' | 'senior' | null {
  const h = hint.toLowerCase()
  if (/\b(young|20s|early 20|late 20|early 30|college|student|intern)\b/.test(h)) return 'young'
  if (/\b(middle.?aged?|30s|40s|mid.?career|experienced)\b/.test(h)) return 'middle-aged'
  if (/\b(senior|elder|old|50s|60s|retired|gray|grey|aging)\b/.test(h)) return 'senior'
  const ageMatch = h.match(/\b(\d{2})\b/)
  if (ageMatch) {
    const age = parseInt(ageMatch[1])
    if (age < 35) return 'young'
    if (age < 55) return 'middle-aged'
    return 'senior'
  }
  return null
}

function scoreAvatarMatch(avatar: AvatarMeta, hint: string): number {
  if (!hint) return 0
  const h = hint.toLowerCase()
  let score = 0

  for (const tag of avatar.tags) {
    if (h.includes(tag)) score += 1
  }

  const hintAge = inferAgeGroup(hint)
  if (hintAge) {
    if (avatar.tags.includes(hintAge)) {
      score += 4
    } else {
      score -= 3
    }
  }

  const raceTerms = ['white', 'black', 'asian', 'latina', 'hispanic']
  for (const race of raceTerms) {
    if (h.includes(race) && avatar.tags.includes(race)) score += 3
    if (h.includes(race) && !avatar.tags.includes(race)) score -= 2
  }

  return score
}

const _usedAvatars = new Map<string, Set<string>>()

function getAvatarForPersona(index: number, needTitle: string, gender?: string, avatarHint?: string): string {
  const key = needTitle.trim().toLowerCase()
  if (!_usedAvatars.has(key)) _usedAvatars.set(key, new Set())
  const used = _usedAvatars.get(key)!

  const pool = gender === 'female' ? FEMALE_AVATARS : gender === 'male' ? MALE_AVATARS : AVATAR_DB
  const available = pool.filter(a => !used.has(a.src))
  const candidates = available.length > 0 ? available : pool

  if (avatarHint) {
    const scored = candidates.map(a => ({ a, score: scoreAvatarMatch(a, avatarHint) }))
    scored.sort((a, b) => b.score - a.score)
    if (scored[0].score > 0) {
      used.add(scored[0].a.src)
      return scored[0].a.src
    }
  }

  let hash = 0
  for (let i = 0; i < needTitle.length; i++) hash = ((hash << 5) - hash + needTitle.charCodeAt(i)) | 0
  const pick = candidates[((Math.abs(hash) + index) % candidates.length)]
  used.add(pick.src)
  return pick.src
}

const CACHE_KEY = 'lumon_persona_cache_v2'

function cacheKey(title: string): string {
  return title.trim().toLowerCase()
}

function loadCachedPersonas(title: string): Persona[] | null {
  try {
    const raw = localStorage.getItem(CACHE_KEY)
    if (!raw) return null
    const cache = JSON.parse(raw) as Record<string, Persona[]>
    return cache[cacheKey(title)] || null
  } catch { return null }
}

function saveCachedPersonas(title: string, personas: Persona[]) {
  try {
    const raw = localStorage.getItem(CACHE_KEY)
    const cache = raw ? JSON.parse(raw) as Record<string, Persona[]> : {}
    cache[cacheKey(title)] = personas
    localStorage.setItem(CACHE_KEY, JSON.stringify(cache))
  } catch { /* ignore */ }
}

function parseTimeline(text: string): { time: string; content: string }[] {
  const lines = text.split('\n').filter(l => l.trim())
  const entries: { time: string; content: string }[] = []
  const timeRegex = /^[\s]*(?:\*\*)?(\d{1,2}[:.：]\d{2}(?:\s*[-–—]\s*\d{1,2}[:.：]\d{2})?(?:\s*[AP]M)?|早上|上午|中午|下午|傍晚|晚上|深夜|凌晨|早晨|午后|夜晚|清晨)(?:\*\*)?[\s]*[:\-–—|](.+)/i
  for (const line of lines) {
    const m = line.match(timeRegex)
    if (m) {
      entries.push({ time: m[1].replace(/\*\*/g, '').trim(), content: m[2].trim() })
    } else if (entries.length > 0) {
      entries[entries.length - 1].content += '\n' + line.trim()
    } else {
      entries.push({ time: '', content: line.trim() })
    }
  }
  return entries
}

function extractFirstName(name: string): string {
  return name.split(/[,，]/)[0].trim() || name
}

function renderBoldText(text: string): React.ReactNode[] {
  const parts = text.split(/(\*\*[^*]+\*\*)/)
  return parts.map((part, i) => {
    if (part.startsWith('**') && part.endsWith('**')) {
      return <strong key={i} className="font-semibold text-text/95">{part.slice(2, -2)}</strong>
    }
    return <span key={i}>{part}</span>
  })
}

function AvatarCycler() {
  const [idx, setIdx] = useState(0)
  useEffect(() => {
    const timer = setInterval(() => setIdx(i => (i + 1) % ALL_AVATARS.length), 1200)
    return () => clearInterval(timer)
  }, [])
  return (
    <div className="w-16 h-16 rounded-2xl overflow-hidden bg-accent/5 flex items-center justify-center relative">
      <AnimatePresence mode="wait">
        <motion.img
          key={idx}
          src={ALL_AVATARS[idx]}
          alt=""
          className="w-12 h-12 object-cover rounded-full"
          initial={{ opacity: 0, scale: 0.8 }}
          animate={{ opacity: 1, scale: 1 }}
          exit={{ opacity: 0, scale: 0.8 }}
          transition={{ duration: 0.3 }}
        />
      </AnimatePresence>
    </div>
  )
}

const GENDER_LABEL: Record<string, string> = { male: '男', female: '女' }

function PersonaCard({ persona, index, needTitle }: { persona: Persona; index: number; needTitle: string }) {
  const [expanded, setExpanded] = useState(false)
  const p = persona
  const firstName = extractFirstName(p.name)

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.1, duration: 0.4, ease: [0.22, 1, 0.36, 1] }}
      className="bg-[#fafaf9] rounded-2xl border border-border/40 p-5 flex flex-col"
    >
      <div className="flex gap-3 mb-3">
        <img src={getAvatarForPersona(index, needTitle, p.gender, p.avatar_hint)} alt="" className="w-11 h-11 rounded-full object-cover shrink-0" />
        <div className="min-w-0 flex-1">
          <h3 className="text-[13px] font-semibold leading-snug mb-1.5">{firstName}</h3>
          <div className="flex flex-wrap gap-1">
            {p.gender && <span className="text-[9px] font-medium px-1.5 py-0.5 rounded-md bg-bg border border-border/30">{GENDER_LABEL[p.gender] || p.gender}</span>}
            <span className="text-[9px] font-medium px-1.5 py-0.5 rounded-md bg-accent/8 text-accent">{p.demographics.age_range}</span>
            <span className="text-[9px] font-medium px-1.5 py-0.5 rounded-md bg-bg border border-border/30">{p.demographics.occupation}</span>
            <span className="text-[9px] font-medium px-1.5 py-0.5 rounded-md bg-bg border border-border/30">{p.demographics.location_hint}</span>
          </div>
        </div>
      </div>

      <motion.div
        animate={{ height: expanded ? 'auto' : 220 }}
        initial={false}
        transition={{ duration: 0.35, ease: [0.25, 0.1, 0.25, 1] }}
        className="overflow-hidden relative"
      >
        <div className="flex flex-col gap-3">
          {(p.bio || p.tagline) && (
            <p className="text-[11px] text-text/70 leading-relaxed italic">{p.bio || p.tagline}</p>
          )}

          <div>
            <p className="text-[10px] font-semibold text-muted mb-1.5">核心痛点</p>
            <ul className="space-y-1">
              {p.frustrations.slice(0, 3).map((f, fi) => (
                <li key={fi} className="text-[11px] text-text/80 leading-relaxed flex gap-1.5">
                  <span className="text-signal shrink-0 mt-0.5">•</span><span>{f}</span>
                </li>
              ))}
            </ul>
          </div>

          <div>
            <p className="text-[10px] font-semibold text-muted mb-1.5">核心需求</p>
            <ul className="space-y-1">
              {p.goals.slice(0, 3).map((g, gi) => (
                <li key={gi} className="text-[11px] text-text/80 leading-relaxed flex gap-1.5">
                  <span className="text-emerald-500 shrink-0 mt-0.5">•</span><span>{g}</span>
                </li>
              ))}
            </ul>
          </div>

          {p.quotes.length > 0 && (
            <div>
              <p className="text-[10px] font-semibold text-muted mb-1.5">原文发言</p>
              <div className="space-y-2">
                {p.quotes.slice(0, 3).map((q, qi) => (
                  <div key={qi} className="bg-bg rounded-lg px-3 py-2.5 border-l-2 border-accent/30">
                    <p className="text-[11px] italic text-text/75 leading-relaxed">"{q.text}"</p>
                    {q.text_zh && <p className="text-[10px] text-muted leading-relaxed mt-1">{q.text_zh}</p>}
                    {q.source_url && (
                      <a href={q.source_url} target="_blank" rel="noopener noreferrer"
                        className="inline-flex items-center gap-0.5 text-[9px] text-accent/70 hover:text-accent mt-1.5 transition-colors">
                        <ExternalLink size={8} /> 原帖链接
                      </a>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {!expanded && (
          <div className="absolute bottom-0 left-0 right-0 h-10 bg-gradient-to-t from-[#fafaf9] to-transparent pointer-events-none" />
        )}
      </motion.div>

      <button
        onClick={() => setExpanded(!expanded)}
        className="mt-2 flex items-center justify-center gap-1 text-[10px] text-muted hover:text-accent transition-colors py-1"
      >
        <motion.div animate={{ rotate: expanded ? 180 : 0 }} transition={{ duration: 0.25 }}>
          <ChevronDown size={12} />
        </motion.div>
        {expanded ? '收起' : '展开全部'}
      </button>
    </motion.div>
  )
}

export default function PersonaView() {
  const needs = useAppStore((s) => s.needs)
  const setNeeds = useAppStore((s) => s.setNeeds)
  const personaNeedIndex = useAppStore((s) => s.personaNeedIndex)
  const setPersonaNeedIndex = useAppStore((s) => s.setPersonaNeedIndex)

  const [activeTab, setActiveTab] = useState<PersonaTab>('cards')
  const [generating, setGenerating] = useState(false)
  const [realProgress, setRealProgress] = useState(0)
  const [smoothProgress, setSmoothProgress] = useState(0)
  const [progressMsg, setProgressMsg] = useState('')
  const [error, setError] = useState('')
  const [personas, setPersonas] = useState<Persona[]>([])
  const [storyIdx, setStoryIdx] = useState(0)
  const abortRef = useRef<AbortController | null>(null)
  const smoothRef = useRef({ real: 0, display: 0, lastUpdate: Date.now() })

  const selectedNeed = personaNeedIndex !== null ? needs[personaNeedIndex] : null
  const selectedTitle = selectedNeed?.need_title ?? ''

  // Load needs from backend on mount if empty (handles page refresh)
  useEffect(() => {
    if (needs.length === 0) {
      getNeeds().then((r) => {
        if (r.needs && r.needs.length > 0) setNeeds(r.needs)
      }).catch(() => {})
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (!generating) {
      smoothRef.current = { real: 0, display: 0, lastUpdate: Date.now() }
      setSmoothProgress(personas.length > 0 ? 100 : 0)
      return
    }
    const timer = setInterval(() => {
      const s = smoothRef.current
      if (s.display >= 98) return
      const gap = s.real - s.display
      let increment: number
      if (gap > 1) {
        increment = Math.max(gap * 0.15, 0.5)
      } else {
        const speed = s.display < 30 ? 0.5 : s.display < 60 ? 0.3 : s.display < 85 ? 0.15 : 0.06
        increment = speed
        const ceiling = Math.min(s.real + 8, 98)
        if (s.display + increment > ceiling) increment = Math.max(ceiling - s.display, 0)
      }
      if (increment > 0.05) {
        s.display = Math.min(s.display + increment, 100)
        setSmoothProgress(Math.round(s.display * 10) / 10)
      }
    }, 800)
    return () => clearInterval(timer)
  }, [generating, personas.length])

  useEffect(() => {
    if (realProgress !== smoothRef.current.real) {
      smoothRef.current.real = realProgress
      smoothRef.current.lastUpdate = Date.now()
      if (realProgress >= smoothRef.current.display) {
        smoothRef.current.display = realProgress
        setSmoothProgress(realProgress)
      }
    }
  }, [realProgress])

  useEffect(() => {
    if (personaNeedIndex !== null && selectedNeed && personas.length === 0 && !generating) {
      const cached = loadCachedPersonas(selectedNeed.need_title)
      if (cached && cached.length > 0) {
        setPersonas(cached)
      } else {
        handleGenerate(personaNeedIndex)
      }
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleGenerate = useCallback((needIdx: number) => {
    setGenerating(true)
    setRealProgress(0)
    setSmoothProgress(0)
    smoothRef.current = { real: 0, display: 0, lastUpdate: Date.now() }
    setProgressMsg('准备中...')
    setError('')
    setPersonas([])

    abortRef.current = new AbortController()
    const title = needs[needIdx]?.need_title ?? ''

    streamGeneratePersonas(needIdx, {
      onProgress: (data) => {
        setRealProgress(data.progress)
        setProgressMsg(data.message)
      },
      onDone: (data) => {
        const result = data.personas as Persona[]
        setPersonas(result)
        setRealProgress(100)
        setSmoothProgress(100)
        setProgressMsg('画像建模完成！')
        setGenerating(false)
        saveCachedPersonas(title, result)
      },
      onError: (data) => {
        setError(data.message || '画像生成失败')
        setGenerating(false)
      },
    }, abortRef.current.signal)
  }, [needs])

  const handleSelectNeed = (idx: number) => {
    setPersonaNeedIndex(idx)
    const title = needs[idx]?.need_title ?? ''
    const cached = loadCachedPersonas(title)
    if (cached && cached.length > 0) {
      setPersonas(cached)
    } else {
      handleGenerate(idx)
    }
  }

  const handleBack = () => {
    abortRef.current?.abort()
    setPersonaNeedIndex(null)
    setPersonas([])
    setGenerating(false)
    setError('')
  }

  // ===== 需求选择列表 =====
  if (!selectedNeed) {
    return (
      <div className="h-full flex flex-col">
        <div className="shrink-0 px-6 max-md:px-4 pt-5 max-md:pt-3 pb-2">
          <h1 className="text-base font-bold">画像建模</h1>
          <p className="text-xs max-md:text-[10px] text-muted">基于真实用户发言，建模典型用户画像</p>
        </div>
        <div className="flex-1 overflow-y-auto scrollbar-auto">
          <div className="px-6 max-md:px-4 py-4">
            {needs.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-16 text-center">
                <img src="/group_2_line.png" alt="" className="w-10 h-10 opacity-20 mb-3" />
                <p className="text-sm font-medium mb-1">暂无需求数据</p>
                <p className="text-xs text-muted">请先在「采集需求」中完成挖掘，再来生成用户画像</p>
              </div>
            ) : (
              <div className="space-y-3">
                <p className="text-xs text-muted">
                  共 <span className="font-semibold text-text">{needs.length}</span> 个需求主题可用于画像建模，选择一个主题开始：
                </p>
                <div className="grid grid-cols-2 max-md:grid-cols-1 gap-3">
                  {needs.map((need, i) => {
                    const cached = loadCachedPersonas(need.need_title)
                    return (
                      <button
                        key={i}
                        onClick={() => handleSelectNeed(i)}
                        className="text-left bg-[#fafaf9] rounded-2xl border border-border/40 p-4 hover:border-accent/25 hover:shadow-sm transition-all"
                      >
                        <div className="flex items-center gap-2 mb-2">
                          <div className="w-6 h-6 bg-accent/8 rounded-lg flex items-center justify-center shrink-0">
                            <img src="/group_2_line.png" alt="" className="w-3 h-3 opacity-70" />
                          </div>
                          <h3 className="text-[13px] font-semibold leading-snug flex-1 min-w-0 line-clamp-1">{need.need_title}</h3>
                          {cached && cached.length > 0 && (
                            <span className="text-[9px] font-medium px-1.5 py-0.5 rounded-full bg-emerald-50 text-emerald-600 shrink-0">
                              {cached.length} 个画像
                            </span>
                          )}
                        </div>
                        <p className="text-[11px] text-muted leading-relaxed line-clamp-2 mb-2">{need.need_description}</p>
                        <div className="flex items-center gap-2.5 text-[10px] text-muted">
                          <span className="flex items-center gap-1"><MessageSquare size={10} /> {need.posts.length}</span>
                          <span className="flex items-center gap-1"><TrendingUp size={10} /> {need.total_score}</span>
                          <span className="ml-auto text-accent font-medium">{cached ? '查看画像' : '生成画像'} →</span>
                        </div>
                      </button>
                    )
                  })}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    )
  }

  // ===== 画像详情 =====
  return (
    <div className="h-full flex flex-col">
      <div className="shrink-0 px-6 max-md:px-4 pt-5 max-md:pt-3 pb-4">
        <div className="flex items-center gap-2 mb-2">
          <button onClick={handleBack}
            className="flex items-center gap-1 text-[11px] text-muted hover:text-text transition-colors">
            <ArrowLeft size={12} /> 返回
          </button>
          {!generating && personas.length > 0 && (
            <button onClick={() => handleGenerate(personaNeedIndex!)}
              className="ml-auto text-[11px] text-accent hover:underline">重新生成</button>
          )}
        </div>
        <h1 className="text-base font-bold line-clamp-1 mb-4">{selectedNeed.need_title}</h1>

        {!generating && personas.length > 0 && (
          <div className="flex items-center gap-1.5">
            {TAB_OPTIONS.map(({ id, label }) => (
              <button
                key={id}
                onClick={() => setActiveTab(id)}
                className={`text-xs font-medium h-7 px-3 rounded-lg transition-all ${
                  activeTab === id
                    ? 'bg-accent text-white'
                    : 'text-muted hover:text-text bg-bg ring-1 ring-border/40'
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="flex-1 overflow-y-auto scrollbar-auto">
        <div className="px-6 max-md:px-4 py-4">

          {generating && (
            <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}
              className="flex flex-col items-center justify-center py-16 text-center">
              <AvatarCycler />
              <p className="text-sm font-semibold mb-2 mt-4">正在建模用户画像</p>
              <p className="text-xs text-muted mb-4 max-w-xs">{progressMsg}</p>
              <div className="w-64 h-1.5 bg-black/[0.06] rounded-full overflow-hidden">
                <motion.div className="h-full bg-accent rounded-full"
                  animate={{ width: `${smoothProgress}%` }} transition={{ duration: 0.6, ease: 'easeOut' }} />
              </div>
              <span className="text-[11px] text-muted mt-2 tabular-nums">{Math.round(smoothProgress)}%</span>
            </motion.div>
          )}

          {!generating && error && (
            <div className="max-w-md mx-auto mt-8">
              <div className="flex items-start gap-3 px-5 py-4 bg-signal/5 border border-signal/20 rounded-xl">
                <AlertCircle size={18} className="text-signal shrink-0 mt-0.5" />
                <div>
                  <p className="text-[13px] font-semibold text-signal mb-1">画像生成失败</p>
                  <p className="text-xs text-signal/80 leading-relaxed">{error}</p>
                </div>
              </div>
              <button onClick={() => handleGenerate(personaNeedIndex!)}
                className="mt-3 text-xs font-medium text-accent hover:underline">重新生成</button>
            </div>
          )}

          {/* ===== Tab 1: 画像卡片 ===== */}
          {!generating && !error && personas.length > 0 && activeTab === 'cards' && (
            <div className="grid grid-cols-2 max-md:grid-cols-1 gap-4">
              {personas.map((p, pi) => (
                <PersonaCard key={pi} persona={p} index={pi} needTitle={selectedTitle} />
              ))}
            </div>
          )}

          {/* ===== Tab 2: 一天叙事 ===== */}
          {!generating && !error && personas.length > 0 && activeTab === 'story' && (
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.3 }}>
              <div className="flex items-center gap-2 mb-5">
                {personas.map((p, pi) => (
                  <button key={pi} onClick={() => setStoryIdx(pi)}
                    className={`flex items-center gap-2 text-[11px] font-medium h-8 px-3 rounded-lg transition-all ${
                      storyIdx === pi
                        ? 'bg-accent text-white'
                        : 'text-muted hover:text-text bg-bg ring-1 ring-border/40'
                    }`}
                  >
                    <img src={getAvatarForPersona(pi, selectedTitle, p.gender, p.avatar_hint)} alt=""
                      className={`w-5 h-5 rounded-full object-cover shrink-0 ${storyIdx === pi ? 'ring-1 ring-white/40' : ''}`} />
                    {extractFirstName(p.name)}
                  </button>
                ))}
              </div>

              {personas[storyIdx] && (() => {
                const p = personas[storyIdx]
                const timeline = parseTimeline(p.day_in_life)
                return (
                  <div className="bg-[#fafaf9] rounded-2xl border border-border/40 p-6">
                    <div className="flex items-center gap-3 mb-5">
                      <img src={getAvatarForPersona(storyIdx, selectedTitle, p.gender, p.avatar_hint)} alt="" className="w-12 h-12 rounded-full object-cover shrink-0" />
                      <div>
                        <h3 className="text-sm font-semibold">{extractFirstName(p.name)}</h3>
                        <p className="text-[11px] text-muted">{p.bio || p.tagline}</p>
                      </div>
                    </div>

                    <div className="relative ml-3">
                      <div className="absolute left-0 top-2 bottom-2 w-[2px] bg-border/30 rounded-full" />
                      <div className="space-y-5">
                        {timeline.map((entry, ei) => (
                          <motion.div key={ei}
                            initial={{ opacity: 0, x: -10 }}
                            animate={{ opacity: 1, x: 0 }}
                            transition={{ delay: ei * 0.08, duration: 0.3 }}
                            className="relative pl-6"
                          >
                            <div className="absolute left-[-3px] top-1.5 w-2 h-2 rounded-full bg-accent ring-2 ring-[#fafaf9]" />
                            {entry.time && (
                              <span className="text-[10px] font-bold text-accent">{entry.time}</span>
                            )}
                            <p className="text-[12px] text-text/85 leading-[1.7] mt-0.5 whitespace-pre-line">{renderBoldText(entry.content)}</p>
                          </motion.div>
                        ))}
                      </div>
                    </div>

                    {p.quotes.length > 0 && (
                      <div className="mt-6 pt-4 border-t border-border/25">
                        <p className="text-[10px] font-semibold text-muted mb-2">相关原文发言</p>
                        <div className="space-y-2">
                          {p.quotes.map((q, qi) => (
                            <div key={qi} className="bg-bg rounded-lg px-4 py-2.5 border-l-2 border-accent/30">
                              <p className="text-[12px] italic text-text/70 leading-relaxed">"{q.text}"</p>
                              {q.text_zh && <p className="text-[10px] text-muted mt-1">{q.text_zh}</p>}
                              {q.source_url && (
                                <a href={q.source_url} target="_blank" rel="noopener noreferrer"
                                  className="inline-flex items-center gap-0.5 text-[9px] text-accent/70 hover:text-accent mt-1 transition-colors">
                                  <ExternalLink size={8} /> 原帖链接
                                </a>
                              )}
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                )
              })()}
            </motion.div>
          )}

          {/* ===== Tab 3: 画像对比 ===== */}
          {!generating && !error && personas.length > 0 && activeTab === 'compare' && (
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.3 }}>
              <div className="overflow-x-auto">
                <table className="w-full text-[11px] border-collapse min-w-[500px]">
                  <thead>
                    <tr className="border-b border-border/30">
                      <th className="text-left py-2 pr-3 text-muted font-medium w-24">维度</th>
                      {personas.map((p, pi) => (
                        <th key={pi} className="text-left py-2 px-2 font-semibold">
                          <div className="flex items-center gap-2">
                            <img src={getAvatarForPersona(pi, selectedTitle, p.gender, p.avatar_hint)} alt="" className="w-6 h-6 rounded-full object-cover shrink-0" />
                            <span className="truncate">{extractFirstName(p.name)}</span>
                          </div>
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {[
                      { label: '人设', render: (p: Persona) => p.bio || p.tagline },
                      { label: '年龄/职业', render: (p: Persona) => `${p.demographics.age_range} · ${p.demographics.occupation}` },
                      { label: '核心痛点', render: (p: Persona) => p.frustrations.slice(0, 2).join('；') },
                      { label: '核心需求', render: (p: Persona) => p.goals.slice(0, 2).join('；') },
                      { label: '换产品触发', render: (p: Persona) => p.switching_trigger },
                      { label: '不可接受', render: (p: Persona) => p.deal_breaker },
                    ].map((row, ri) => (
                      <tr key={ri} className="border-b border-border/20">
                        <td className="py-2.5 pr-3 text-muted font-medium align-top whitespace-nowrap">{row.label}</td>
                        {personas.map((p, pi) => (
                          <td key={pi} className="py-2.5 px-2 text-text/80 leading-relaxed align-top">{row.render(p)}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </motion.div>
          )}

        </div>
      </div>
    </div>
  )
}
