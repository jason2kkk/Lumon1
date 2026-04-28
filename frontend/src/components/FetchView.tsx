import { useState, useEffect, useRef, useCallback } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Loader2, TrendingUp, MessageSquare,
  ExternalLink, AlertCircle, ChevronDown,
  BarChart3,
  CheckCircle2, Quote,
} from 'lucide-react'
import confetti from 'canvas-confetti'
import { useAppStore } from '../stores/app'
import {
  clearNeeds, getNeeds, getConfigStatus, streamFetchNeeds,
  getRedditCategories, getEngineStatus,
  getFetchStatus, stopFetch, streamGenerateReport,
  getSensorTowerStatus, getReportGenStatus, streamReportGenResume,
} from '../api/client'
import type { FetchParams, RedditCategory } from '../api/client'
import type { Need, FemwcDimension } from '../types'
import ConfirmDialog from './ConfirmDialog'
import { HelpButton, FETCH_HELP } from './HelpDialog'
import { CountUp, ShimmerText, RotatingText, ShineBorder, InteractiveHoverButton } from './animations'

type FetchMode = 'sentence' | 'keywords' | 'open'

const MODE_OPTIONS: { id: FetchMode; label: string; img: string; desc: string }[] = [
  { id: 'sentence', label: '一句话描述', img: '/book_6_ai_line.png', desc: '输入赛道/方向描述，Lumon 深度搜索' },
  { id: 'keywords', label: '关键词挖掘', img: '/signature_2_ai_line.png', desc: '输入多个关键词，精准搜索' },
  { id: 'open', label: '自主发现', img: '/mind_map_line.png', desc: 'Lumon 自动选择高价值方向，发现产品机会' },
]


function _buildHistoryTitle(mode: string, sentence: string, keywordsText: string, needs: Need[]): string {
  if (mode === 'sentence' && sentence.trim()) {
    return sentence.trim()
  }
  if (mode === 'keywords' && keywordsText.trim()) {
    const kws = keywordsText.split(/[,，\s]+/).filter(Boolean).slice(0, 3)
    return kws.join('·') + ' 相关需求'
  }
  if (needs.length > 0) {
    return needs[0].need_title
  }
  return '自主发现需求'
}

export default function FetchView() {
  const {
    needs, setNeeds, selectedNeedIndex, setSelectedNeed,
    setActiveView, resetDebate, debateStatus,
    configReady, setConfigReady, setShowSettingsDialog,
    addFetchHistory, loadFetchHistory,
    activeFetchHistoryId, fetchHistory, setActiveFetchHistory,
    dataSources: sources, loadDataSources,
    reportGenIdx, reportGenProgress, reportGenMsg,
    setReportGenIdx, setReportGenProgress, setReportGenMsg,
    fetchLoading: loading, fetchProgress: progress,
    fetchProgressHistory: progressHistory, fetchError: error,
    fetchDone, needsEpoch,
    setFetchLoading: setLoading, setFetchProgress: setProgress,
    setFetchProgressHistory: setProgressHistory,
    appendFetchProgressHistory,
    setFetchError: setError, setFetchDone,
    resetFetchProgress,
    prefillFetchQuery, setPrefillFetchQuery,
    autoStartFetch, setAutoStartFetch,
  } = useAppStore()

  const typewriterTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const typewriterActiveRef = useRef(false)
  const [pendingAutoFetch, setPendingAutoFetch] = useState(false)

  useEffect(() => {
    if (!prefillFetchQuery) return
    const fullText = prefillFetchQuery
    const shouldAutoStart = autoStartFetch
    setPrefillFetchQuery(null)
    setAutoStartFetch(false)

    // 取消上一轮未完成的打字动画
    typewriterActiveRef.current = false
    if (typewriterTimerRef.current) {
      clearTimeout(typewriterTimerRef.current)
      typewriterTimerRef.current = null
    }

    setMode('sentence')
    setSentence('')
    typewriterActiveRef.current = true

    let i = 0
    const tick = () => {
      if (!typewriterActiveRef.current) return
      i++
      setSentence(fullText.slice(0, i))
      if (i < fullText.length) {
        typewriterTimerRef.current = setTimeout(tick, 50)
      } else {
        typewriterActiveRef.current = false
        typewriterTimerRef.current = null
        if (shouldAutoStart) {
          // 打字完毕后停顿 600ms，再触发自动挖掘
          typewriterTimerRef.current = setTimeout(() => {
            typewriterTimerRef.current = null
            setPendingAutoFetch(true)
          }, 600)
        }
      }
    }
    typewriterTimerRef.current = setTimeout(tick, 300)
    // 不设 cleanup return — refs 跨 StrictMode 双重执行存活，靠 typewriterActiveRef 控制停止
  }, [prefillFetchQuery, setPrefillFetchQuery, autoStartFetch, setAutoStartFetch])

  const isViewingHistory = activeFetchHistoryId !== null
  const activeHistoryItem = isViewingHistory
    ? fetchHistory.find(h => h.id === activeFetchHistoryId)
    : null

  const [mode, setMode] = useState<FetchMode>('sentence')
  const [sentence, setSentence] = useState('')
  const [keywordsText, setKeywordsText] = useState('')
  const [category, setCategory] = useState('ask')
  const [limit] = useState(70)
  const [timePeriod, setTimePeriod] = useState<'month' | '3months' | '6months' | '9months'>('6months')
  const [product, setProduct] = useState('')
  const [market, setMarket] = useState('')
  const [demographics, setDemographics] = useState('')
  const [segment, setSegment] = useState('')
  const [competitors, setCompetitors] = useState('')
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [tagsExpanded, setTagsExpanded] = useState(false)
  const [openTimePeriod, setOpenTimePeriod] = useState(false)
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null)
  const [progressCollapsed, setProgressCollapsed] = useState(() => fetchDone)
  const [progressDismissed, setProgressDismissed] = useState(false)
  const [smoothProgress, setSmoothProgress] = useState(0)
  const smoothRef = useRef({ real: 0, display: 0, lastUpdate: Date.now() })
  const [estimatedSeconds, setEstimatedSeconds] = useState(0)
  const [elapsedSeconds, setElapsedSeconds] = useState(0)
  const startTimeRef = useRef<number>(0)
  const abortRef = useRef<AbortController | null>(null)
  const fetchingRef = useRef(false)
  const historyAddedRef = useRef(false)
  const progressScrollRef = useRef<HTMLDivElement | null>(null)
  const userScrolledUpRef = useRef(false)

  const [redditCategories, setRedditCategories] = useState<Record<string, RedditCategory>>({})
  const [selectedRedditCats, setSelectedRedditCats] = useState<string[]>([])
  const [engineName, setEngineName] = useState<string>('')
  const [stConnected, setStConnected] = useState<boolean | null>(null)
  const reportGenAbort = useRef<AbortController | null>(null)
  const demoModeRef = useRef(false)

  const _reportEasterEggs = [
    '正在努力输出报告，请稍等',
    '报告正在精雕细琢中',
    '数据已就位，正在转化为洞察',
    '好的分析需要一点耐心',
    '正在把用户的吐槽变成商业机会',
    '从噪声中提炼信号，从信号中发现机会',
    '快了快了，不要催',
  ]
  const [reportSubMsg, setReportSubMsg] = useState('')
  useEffect(() => {
    if (reportGenIdx === null) { setReportSubMsg(''); return }
    let idx = 0
    setReportSubMsg(_reportEasterEggs[0])
    const timer = setInterval(() => {
      idx = (idx + 1) % _reportEasterEggs.length
      setReportSubMsg(_reportEasterEggs[idx])
    }, 6000)
    return () => clearInterval(timer)
  }, [reportGenIdx])

  const fireSideCannons = useCallback(() => {
    const colors = ['#26ccff', '#a25afd', '#ff5e7e', '#88ff5a', '#fcff42', '#ffa62d']
    const base = { colors, gravity: 0.9, ticks: 300, disableForReducedMotion: true }
    confetti({ ...base, particleCount: 200, angle: 55, spread: 70, origin: { x: 0, y: 0.55 }, startVelocity: 55 })
    confetti({ ...base, particleCount: 200, angle: 125, spread: 70, origin: { x: 1, y: 0.55 }, startVelocity: 55 })
  }, [])

  useEffect(() => {
    if (progress !== smoothRef.current.real) {
      smoothRef.current.real = progress
      smoothRef.current.lastUpdate = Date.now()
      if (progress >= smoothRef.current.display) {
        smoothRef.current.display = progress
        setSmoothProgress(progress)
      }
    }
  }, [progress])

  useEffect(() => {
    const el = progressScrollRef.current
    if (!el || !loading) return
    if (!userScrolledUpRef.current) {
      el.scrollTop = el.scrollHeight
    }
  }, [progressHistory.length, loading])

  useEffect(() => {
    if (!loading) {
      userScrolledUpRef.current = false
      smoothRef.current = { real: 0, display: 0, lastUpdate: Date.now() }
      setSmoothProgress(fetchDone ? 100 : 0)
      return
    }
    const timer = setInterval(() => {
      const s = smoothRef.current
      if (s.display >= 98) return
      const elapsed = (Date.now() - s.lastUpdate) / 1000
      const gap = s.real - s.display
      let increment: number
      if (gap > 1) {
        increment = Math.max(gap * 0.15, 0.5)
      } else {
        const speed = s.display < 30 ? 0.6 : s.display < 60 ? 0.35 : s.display < 85 ? 0.2 : 0.08
        increment = speed * Math.min(elapsed, 2)
        const ceiling = Math.min(s.real + 8, 98)
        if (s.display + increment > ceiling) {
          increment = Math.max(ceiling - s.display, 0)
        }
      }
      if (increment > 0.05) {
        s.display = Math.min(s.display + increment, 100)
        setSmoothProgress(Math.round(s.display * 10) / 10)
      }
    }, 800)
    return () => clearInterval(timer)
  }, [loading, fetchDone])

  const { confettiFired, setConfettiFired } = useAppStore()
  const confettiTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => {
    if (fetchDone && needs.length > 0 && !confettiFired) {
      setConfettiFired(true)
      confettiTimerRef.current = setTimeout(() => fireSideCannons(), 1000)
    }
  }, [fetchDone, needs.length, confettiFired, setConfettiFired, fireSideCannons])

  useEffect(() => {
    if (!loading) {
      setElapsedSeconds(0)
      return
    }
    const tick = setInterval(() => {
      setElapsedSeconds(Math.floor((Date.now() - startTimeRef.current) / 1000))
    }, 1000)
    return () => clearInterval(tick)
  }, [loading])

  const refreshEngineStatus = () => {
    if (fetchingRef.current) return
    getEngineStatus()
      .then((s) => setEngineName(s.engine || ''))
      .catch(() => setEngineName(''))
    getSensorTowerStatus()
      .then((s) => setStConnected(s.available))
      .catch(() => setStConnected(false))
  }

  const loadRoleNames = useAppStore((s) => s.loadRoleNames)

  useEffect(() => {
    loadFetchHistory()
    loadDataSources()
    loadRoleNames()
    getRedditCategories()
      .then((r) => setRedditCategories(r.categories))
      .catch(() => {})
    refreshEngineStatus()
    const enginePoll = setInterval(refreshEngineStatus, 30_000)

    // 如果当前不在查看历史且 needs 为空，尝试从后端加载
    if (!activeFetchHistoryId && needs.length === 0) {
      getNeeds().then((r) => {
        if (r.needs && r.needs.length > 0) setNeeds(r.needs)
      }).catch(() => {})
    }

    // Resume active fetch job after page refresh
    getFetchStatus().then((status) => {
      if (status.active) {
        fetchingRef.current = true
        setLoading(true)
        setProgress(status.progress)
        setProgressHistory(status.history)
        // Start polling for updates
        const poll = setInterval(async () => {
          try {
            const s = await getFetchStatus()
            setProgress(s.progress)
            setProgressHistory(s.history)
            if (s.error) {
              setError(s.error)
              setLoading(false)
              fetchingRef.current = false
              clearInterval(poll)
            }
            if (s.needs) {
              setNeeds(s.needs)
              setFetchDone(true)
              setLoading(false)
              fetchingRef.current = false
              clearInterval(poll)
              setTimeout(() => setProgressCollapsed(true), 2000)
              if (s.needs.length > 0 && !historyAddedRef.current) {
                historyAddedRef.current = true
                const historyTitle = _buildHistoryTitle(mode, sentence, keywordsText, s.needs)
                addFetchHistory({
                  id: `fetch-${Date.now()}`,
                  title: historyTitle,
                  mode,
                  query: mode === 'sentence' ? sentence : mode === 'keywords' ? keywordsText : '自主发现',
                  needs: s.needs,
                  createdAt: Date.now(),
                })
              }
            }
            if (!s.active && !s.needs && !s.error) {
              setLoading(false)
              fetchingRef.current = false
              clearInterval(poll)
            }
          } catch {
            clearInterval(poll)
            setLoading(false)
            fetchingRef.current = false
          }
        }, 500)
      }
    }).catch(() => {})

    // Resume active report generation after page refresh (only if still running)
    getReportGenStatus().then((rStatus) => {
      if (rStatus.active) {
        const resumeIdx = rStatus.need_index >= 0 ? rStatus.need_index : -1
        setReportGenIdx(resumeIdx)
        setReportGenProgress(rStatus.progress)
        setReportGenMsg(rStatus.message || '报告生成中...')
        const abortCtrl = new AbortController()
        reportGenAbort.current = abortCtrl
        streamReportGenResume({
          onProgress: (data) => {
            setReportGenProgress(data.progress)
            setReportGenMsg(data.message)
          },
          onChunk: () => {},
          onDone: (data) => {
            setReportGenProgress(100)
            setReportGenMsg('报告生成完成！')
            setTimeout(() => {
              setReportGenIdx(null)
              if (data?.filename) {
                useAppStore.getState().setPendingReportFile(data.filename)
              }
              setActiveView('reports')
            }, 600)
          },
          onError: (data) => {
            setReportGenMsg(`出错了：${data.message || '报告生成失败'}`)
            setReportGenProgress(0)
            setTimeout(() => setReportGenIdx(null), 4000)
          },
        }, abortCtrl.signal)
      }
    }).catch(() => {})

    return () => clearInterval(enginePoll)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const toggleRedditCat = (key: string) => {
    setSelectedRedditCats(prev =>
      prev.includes(key) ? [] : [key]
    )
  }

  const [confirmAction, setConfirmAction] = useState<{
    title: string; message: string; action: () => void
  } | null>(null)

  useEffect(() => {
    if (configReady === null) {
      getConfigStatus().then((s) => {
        setConfigReady((s as Record<string, unknown>).ready as boolean ?? (s.claude_ok || s.gpt_ok))
      }).catch(() => setConfigReady(false))
    }
  }, [configReady, setConfigReady])

  const hasActiveDebate = debateStatus !== 'idle'

  const canFetch = () => {
    if (sources.length === 0) return false
    if (mode === 'sentence' && !sentence.trim()) return false
    if (mode === 'keywords' && !keywordsText.trim()) return false
    return true
  }

  const [fetchHint, setFetchHint] = useState('')

  const handleFetch = () => {
    if (loading || fetchingRef.current) return
    if (!canFetch()) {
      if (mode === 'sentence' && !sentence.trim()) {
        setFetchHint('请输入你想挖掘的需求方向')
      } else if (mode === 'keywords' && !keywordsText.trim()) {
        setFetchHint('请输入关键词')
      } else if (sources.length === 0) {
        setFetchHint('请选择至少一个数据源')
      }
      setTimeout(() => setFetchHint(''), 3000)
      return
    }
    setFetchHint('')
    if (hasActiveDebate) {
      setConfirmAction({
        title: '当前有进行中的讨论',
        message: '重新采集将清除当前讨论记录，确认继续？',
        action: doFetch,
      })
      return
    }
    doFetch()
  }

  useEffect(() => {
    if (pendingAutoFetch && sentence.trim() && !loading && !fetchingRef.current && !typewriterActiveRef.current) {
      setPendingAutoFetch(false)
      handleFetch()
    }
  }, [pendingAutoFetch])

  const doFetch = async (options?: { demo?: boolean }) => {
    const isDemo = options?.demo ?? false
    demoModeRef.current = isDemo
    setConfirmAction(null)

    if (abortRef.current) {
      abortRef.current.abort()
      abortRef.current = null
    }
    try {
      await stopFetch()
      await new Promise(r => setTimeout(r, 300))
    } catch { /* 无活跃任务时忽略 */ }

    fetchingRef.current = true
    historyAddedRef.current = false
    setNeeds([])
    resetDebate()
    setExpandedIdx(null)
    setLoading(true)
    setProgress(0)
    setProgressHistory([])
    setProgressCollapsed(false)
    setProgressDismissed(false)
    setFetchDone(false)
    setConfettiFired(false)
    setError('')

    startTimeRef.current = Date.now()
    setElapsedSeconds(0)

    if (isDemo) {
      setEstimatedSeconds(22)
    } else {
      const srcCount = sources.length
      const hasTavily = mode !== 'open'
      // 质量筛选已合并到聚类Step1，省去独立筛选~100s；聚类拆两步但Step2并发，总计~80s
      const est = 30 + (hasTavily ? 150 : 0) + srcCount * 35 + 80
      setEstimatedSeconds(est)
    }

    const params: FetchParams = isDemo
      ? { mode: 'sentence', query: '海量照片整理与回忆管理的用户痛点', sources: ['reddit', 'hackernews'], limit: 30, demo: true }
      : { mode, sources, limit, time_period: timePeriod }

    if (!isDemo) {
      if (product.trim()) params.product = product.trim()
      if (market.trim()) params.market = market.trim()
      if (demographics.trim()) params.demographics = demographics.trim()
      if (segment.trim()) params.segment = segment.trim()
      if (competitors.trim()) params.competitors = competitors.trim()

      if (mode === 'sentence') {
        params.query = sentence.trim()
      } else if (mode === 'keywords') {
        params.keywords = keywordsText.split(/[,，、\s]+/).filter(Boolean)
      } else {
        params.category = category
      }

      if (sources.includes('reddit') && selectedRedditCats.length > 0) {
        params.reddit_categories = selectedRedditCats
      }
    }

    abortRef.current = new AbortController()

    streamFetchNeeds(params, {
      onProgress: (data) => {
        setProgress(data.progress)
        appendFetchProgressHistory(data.message)
      },
      onResult: (data) => {
        setNeeds(data.needs)
        setFetchDone(true)
        resetDebate()
        if (!isDemo && data.needs.length > 0 && !historyAddedRef.current) {
          historyAddedRef.current = true
          const historyTitle = _buildHistoryTitle(mode, sentence, keywordsText, data.needs)
          addFetchHistory({
            id: `fetch-${Date.now()}`,
            title: historyTitle,
            mode,
            query: mode === 'sentence' ? sentence : mode === 'keywords' ? keywordsText : '自主发现',
            needs: data.needs,
            createdAt: Date.now(),
          })
        }
      },
      onError: (data) => {
        const msg = data.message || ''
        let displayMsg = msg
        try {
          const parsed = JSON.parse(msg)
          if (parsed.detail) displayMsg = parsed.detail
        } catch { /* 非 JSON，直接使用原文 */ }
        setError(displayMsg)
        setLoading(false)
        fetchingRef.current = false
      },
      onDone: () => {
        setLoading(false)
        fetchingRef.current = false
        setTimeout(() => setProgressCollapsed(true), 3000)
        // Recovery: if SSE stream ended but needs weren't received (e.g. Cloudflare dropped the final event),
        // fetch results from the backend API as a fallback.
        setTimeout(() => {
          const currentNeeds = useAppStore.getState().needs
          if (currentNeeds.length === 0) {
            getFetchStatus().then((s) => {
              if (s.needs && s.needs.length > 0) {
                setNeeds(s.needs)
                setFetchDone(true)
                if (!historyAddedRef.current) {
                  historyAddedRef.current = true
                  const historyTitle = _buildHistoryTitle(mode, sentence, keywordsText, s.needs)
                  addFetchHistory({
                    id: `fetch-${Date.now()}`,
                    title: historyTitle,
                    mode,
                    query: mode === 'sentence' ? sentence : mode === 'keywords' ? keywordsText : '自主发现',
                    needs: s.needs,
                    createdAt: Date.now(),
                  })
                }
              }
            }).catch(() => {})
          }
        }, 500)
      },
    }, abortRef.current.signal)
  }

  const handleAbort = () => {
    // 取消打字动画和待触发的自动挖掘
    typewriterActiveRef.current = false
    if (typewriterTimerRef.current) {
      clearTimeout(typewriterTimerRef.current)
      typewriterTimerRef.current = null
    }
    setPendingAutoFetch(false)

    abortRef.current?.abort()
    abortRef.current = null
    resetFetchProgress()
    fetchingRef.current = false
    setEstimatedSeconds(0)
    setElapsedSeconds(0)
    stopFetch().catch(() => {})
    setSmoothProgress(0)
  }

  const handleClear = () => {
    setConfirmAction({
      title: '确认清空',
      message: '清空后所有需求数据和当前讨论将丢失，确认清空？',
      action: doClear,
    })
  }

  const doClear = async () => {
    setConfirmAction(null)
    await clearNeeds()
    setNeeds([])
    resetDebate()
    useAppStore.getState().setActiveFetchHistory(null)
  }

  const handleSelectAndDebate = (idx: number) => {
    if (hasActiveDebate && selectedNeedIndex !== idx) {
      setConfirmAction({
        title: '切换讨论需求',
        message: '当前有进行中的讨论，切换需求将丢失讨论记录，确认切换？',
        action: () => { setConfirmAction(null); doSelectAndDebate(idx) },
      })
      return
    }
    doSelectAndDebate(idx)
  }

  const doSelectAndDebate = (idx: number) => {
    if (selectedNeedIndex !== idx) resetDebate()
    setSelectedNeed(idx)
    setActiveView('debate')
  }

  const handleGenerateReport = (idx: number) => {
    setReportGenIdx(idx)
    setReportGenProgress(0)
    setReportGenMsg('准备生成报告...')
    reportGenAbort.current = new AbortController()

    const isDemo = demoModeRef.current
    let chunkCount = 0
    let maxProgress = 0
    let lastMsgIdx = -1
    const _writingMsgs = [
      '正在撰写报告内容...',
      '深入分析用户痛点...',
      '痛点地图生成中...',
      '提取用户原文引述...',
      '分析场景和用户行为...',
      '梳理竞品格局...',
      '撰写竞品详情分析...',
      '构思产品方案...',
      '细化产品方案与目标用户...',
      '提炼产品定位建议...',
      '汇总证据来源...',
      '整理研究局限与后续建议...',
      '正在输出最后的章节...',
      '校验数据完整性...',
      '优化表述与措辞...',
      '最后的润色与排版...',
      '检查报告结构...',
      '收尾工作进行中...',
      '马上就好...',
      '即将完成...',
    ]
    // 文案切换的进度阈值，均匀分布在 53-97 之间
    const _msgThresholds = _writingMsgs.map((_, i) => 53 + Math.floor(i * 44 / (_writingMsgs.length - 1)))
    streamGenerateReport(idx, {
      onProgress: (data) => {
        if (data.progress >= maxProgress) {
          maxProgress = data.progress
          setReportGenProgress(data.progress)
        }
        setReportGenMsg(data.message)
      },
      onChunk: () => {
        chunkCount++
        if (chunkCount % 2 === 0) {
          // 双曲线：永远不会真正停住，始终缓慢增长
          const p = Math.min(52 + Math.floor(46 * chunkCount / (chunkCount + 350)), 98)
          if (p > maxProgress) {
            maxProgress = p
            setReportGenProgress(p)
          }
        }
        // 文案跟随进度阈值切换，而非固定 chunk 间隔
        if (chunkCount === 1) {
          lastMsgIdx = 0
          setReportGenMsg(_writingMsgs[0])
        } else {
          const nextIdx = lastMsgIdx + 1
          if (nextIdx < _writingMsgs.length && maxProgress >= _msgThresholds[nextIdx]) {
            lastMsgIdx = nextIdx
            setReportGenMsg(_writingMsgs[nextIdx])
          }
        }
      },
      onDone: (data) => {
        setReportGenProgress(100)
        setReportGenMsg('报告生成完成！')
        setTimeout(() => {
          setReportGenIdx(null)
          if (data?.filename) {
            useAppStore.getState().setPendingReportFile(data.filename)
          }
          setActiveView('reports')
        }, 600)
      },
      onError: (data) => {
        const msg = data.message || '报告生成失败'
        setReportGenMsg(`出错了：${msg}`)
        setReportGenProgress(0)
        console.error('[ReportGen] error:', msg)
        setTimeout(() => setReportGenIdx(null), 4000)
      },
    }, reportGenAbort.current.signal, isDemo ? { demo: true } : undefined)
  }

  const isDebatingNeed = (idx: number) => selectedNeedIndex === idx && hasActiveDebate

  return (
    <div className="h-full flex flex-col">
      {!isViewingHistory && configReady === false && (
        <div className="shrink-0 bg-amber-50 border-b border-amber-200 px-6 max-md:px-4 py-2.5 flex items-center gap-3">
          <AlertCircle size={14} className="text-amber-500 shrink-0" />
          <p className="text-xs text-amber-700 flex-1">模型未配置，请先前往「设置」完成 API 配置</p>
          <button onClick={() => setShowSettingsDialog(true)}
            className="flex items-center gap-1.5 text-xs font-medium text-amber-700 border border-amber-300 h-8 px-3 rounded-xl hover:bg-amber-100 transition-colors shrink-0">
            <img src="/settings_1_line.png" alt="" className="w-3 h-3 opacity-60" /> 前往设置
          </button>
        </div>
      )}

      {/* Mobile history strip */}
      {fetchHistory.length > 0 && (
        <div className="md:hidden shrink-0 px-4 pt-3 pb-1">
          <div className="flex items-center gap-1.5 overflow-x-auto pb-1">
            <button
              onClick={() => setActiveFetchHistory(null)}
              className={`shrink-0 text-[11px] font-medium px-3 py-1.5 rounded-full transition-all ${
                !activeFetchHistoryId ? 'bg-accent/10 text-accent' : 'text-muted border border-border/50'
              }`}
            >
              当前
            </button>
            {fetchHistory.map((item) => (
              <button
                key={item.id}
                onClick={() => setActiveFetchHistory(item.id)}
                className={`shrink-0 text-[11px] font-medium px-3 py-1.5 rounded-full transition-all truncate max-w-[140px] ${
                  activeFetchHistoryId === item.id ? 'bg-accent/10 text-accent' : 'text-muted border border-border/50'
                }`}
              >
                {item.title}
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="shrink-0 px-6 max-md:px-4 pt-5 max-md:pt-3 pb-2">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5">
              <h1 className="text-base font-bold break-words">
                {isViewingHistory
                  ? ((activeHistoryItem?.query && activeHistoryItem.query !== '自主发现')
                      ? activeHistoryItem.query
                      : (needs.length > 0 ? needs[0].need_title : (activeHistoryItem?.title || '需求历史')))
                  : '需求挖掘'}
              </h1>
              {!isViewingHistory && <HelpButton {...FETCH_HELP} />}
            </div>
            <p className="text-xs max-md:text-[10px] text-muted">
              {isViewingHistory
                ? `${activeHistoryItem?.needs.length || 0} 个需求主题 · ${activeHistoryItem?.query || ''}`
                : '从海外社区采集帖子，挖掘真实需求'}
            </p>
          </div>
          {!isViewingHistory && (
            <div className="flex items-center gap-1.5 shrink-0">
              {!loading && (
                <button onClick={() => doFetch({ demo: true })}
                  className="max-md:hidden text-[13px] font-medium text-accent/70 hover:text-accent transition-colors">
                  演示模式
                </button>
              )}
              {!loading && (
                <div className="relative md:hidden">
                  <button onClick={handleFetch}
                    className="relative cursor-pointer animate-rainbow inline-flex items-center gap-1.5 text-[13px] font-semibold h-9 px-5 rounded-xl transition-all select-none border-0 text-white bg-[linear-gradient(#121213,#121213),linear-gradient(#121213_50%,rgba(18,18,19,0.6)_80%,rgba(18,18,19,0)),linear-gradient(90deg,hsl(var(--color-1)),hsl(var(--color-5)),hsl(var(--color-3)),hsl(var(--color-4)),hsl(var(--color-2)))] bg-[length:200%] [background-clip:padding-box,border-box,border-box] [background-origin:border-box] [border:2px_solid_transparent] before:absolute before:bottom-[-20%] before:left-1/2 before:z-0 before:h-1/5 before:w-3/5 before:-translate-x-1/2 before:animate-rainbow before:bg-[linear-gradient(90deg,hsl(var(--color-1)),hsl(var(--color-5)),hsl(var(--color-3)),hsl(var(--color-4)),hsl(var(--color-2)))] before:bg-[length:200%] before:[filter:blur(0.75rem)] hover:scale-105 active:scale-95">
                    <img src="/search_2_ai_line.png" alt="" className="relative z-10 w-3.5 h-3.5 brightness-0 invert" />
                    <span className="relative z-10">开始挖掘</span>
                  </button>
                  {fetchHint && (
                    <div className="absolute top-full right-0 mt-2 px-3 py-1.5 rounded-lg bg-neutral-900 text-white text-[11px] whitespace-nowrap shadow-lg z-50">
                      {fetchHint}
                    </div>
                  )}
                </div>
              )}
              {loading && (
                <button onClick={handleAbort}
                  className="inline-flex md:hidden items-center gap-1.5 text-[13px] font-semibold h-9 px-5 rounded-xl bg-signal/10 text-signal border-x-2 border-t-2 border-b-[5px] border-signal/25 active:border-b-2 whitespace-nowrap">
                  <img src="/hand_line.png" alt="" className="w-4 h-4" style={{ filter: 'brightness(0) saturate(100%) invert(24%) sepia(79%) saturate(1834%) hue-rotate(345deg) brightness(89%) contrast(97%)' }} /> 停止挖掘
                </button>
              )}
              {engineName && (
                <div className={`max-md:hidden flex items-center gap-1.5 text-[10px] font-medium px-2.5 py-1 rounded-xl border ${
                  engineName === 'rdt-cli'
                    ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
                    : 'border-border/40 bg-bg text-muted'
                }`}>
                  <span className={`w-1.5 h-1.5 rounded-full ${
                    engineName === 'rdt-cli' ? 'bg-emerald-500' : 'bg-muted'
                  }`} />
                  rdt-cli
                </div>
              )}
              {stConnected !== null && (
                <div className={`max-md:hidden flex items-center gap-1.5 text-[10px] font-medium px-2.5 py-1 rounded-xl border ${
                  stConnected
                    ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
                    : 'border-border/40 bg-bg text-muted'
                }`}>
                  <span className={`w-1.5 h-1.5 rounded-full ${
                    stConnected ? 'bg-emerald-500' : 'bg-muted'
                  }`} />
                  st-cli
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Scrollable area: mode selector, inputs, progress, needs list */}
      <div className="flex-1 overflow-y-auto scrollbar-auto">

      {reportGenIdx === -1 && (
        <div className="shrink-0 mx-6 max-md:mx-4 mt-3 mb-2 bg-accent/5 border border-accent/20 rounded-xl px-4 py-3 space-y-1.5">
          <div className="flex items-center justify-between min-w-0">
            <div className="flex items-center gap-1.5 min-w-0">
              <Loader2 size={12} className="text-accent animate-spin flex-shrink-0" />
              <span className="text-[12px] text-accent font-medium truncate">{reportGenMsg || '报告生成中...'}</span>
            </div>
            <button
              onClick={() => { reportGenAbort.current?.abort(); setReportGenIdx(null) }}
              className="shrink-0 text-[11px] text-signal border border-signal/30 h-6 px-2 rounded-md hover:bg-signal/5"
            >停止</button>
          </div>
          <div className="flex items-center gap-2">
            <div className="flex-1 h-1.5 bg-black/[0.06] rounded-full overflow-hidden">
              <motion.div className="h-full bg-accent rounded-full" animate={{ width: `${reportGenProgress}%` }} transition={{ duration: 1.5, ease: 'easeOut' }} />
            </div>
            <span className="text-[11px] font-medium text-foreground tabular-nums w-8 text-right flex-shrink-0">{reportGenProgress}%</span>
          </div>
        </div>
      )}

      {/* Mode selector + action button (hidden when viewing history) */}
      {!isViewingHistory && <div className="shrink-0 px-6 max-md:px-4 py-2.5">
        {!loading && (
          <button onClick={() => doFetch({ demo: true })}
            className="hidden max-md:inline-flex text-[11px] font-medium text-accent/70 hover:text-accent transition-colors mb-1.5">
            演示模式
          </button>
        )}
        <div className="flex items-center gap-1.5 max-md:flex-wrap max-md:gap-2">
          {MODE_OPTIONS.map(({ id, label, img }) => (
            <button
              key={id}
              onClick={() => { if (!loading) setMode(id) }}
              className={`flex items-center gap-1.5 text-xs font-medium h-8 px-3.5 rounded-xl transition-all whitespace-nowrap shrink-0 ${
                mode === id
                  ? 'bg-accent text-white shadow-sm'
                  : 'bg-bg text-muted hover:text-text ring-1 ring-border/40'
              }`}
            >
              <img src={img} alt="" className={`w-3.5 h-3.5 ${mode === id ? 'brightness-0 invert' : 'opacity-50'}`} />
              {label}
            </button>
          ))}
          <div className="flex-1 max-md:hidden" />
          {!loading ? (
            <div className="relative flex items-center gap-2 max-md:hidden">
              <button onClick={handleFetch}
                className="relative cursor-pointer animate-rainbow inline-flex items-center justify-center gap-1.5 text-[13px] font-semibold h-9 px-5 rounded-xl transition-all select-none border-0 text-white bg-[linear-gradient(#121213,#121213),linear-gradient(#121213_50%,rgba(18,18,19,0.6)_80%,rgba(18,18,19,0)),linear-gradient(90deg,hsl(var(--color-1)),hsl(var(--color-5)),hsl(var(--color-3)),hsl(var(--color-4)),hsl(var(--color-2)))] bg-[length:200%] [background-clip:padding-box,border-box,border-box] [background-origin:border-box] [border:2px_solid_transparent] before:absolute before:bottom-[-20%] before:left-1/2 before:z-0 before:h-1/5 before:w-3/5 before:-translate-x-1/2 before:animate-rainbow before:bg-[linear-gradient(90deg,hsl(var(--color-1)),hsl(var(--color-5)),hsl(var(--color-3)),hsl(var(--color-4)),hsl(var(--color-2)))] before:bg-[length:200%] before:[filter:blur(0.75rem)] hover:scale-105 active:scale-95">
                <img src="/search_2_ai_line.png" alt="" className="relative z-10 w-3.5 h-3.5 brightness-0 invert" />
                <span className="relative z-10">开始挖掘</span>
              </button>
              {fetchHint && (
                <div className="absolute top-full right-0 mt-2 px-3 py-1.5 rounded-lg bg-neutral-900 text-white text-[11px] whitespace-nowrap shadow-lg z-50 animate-in fade-in slide-in-from-top-1">
                  {fetchHint}
                </div>
              )}
            </div>
          ) : (
            <button onClick={handleAbort}
              className="max-md:hidden inline-flex items-center justify-center gap-1.5 text-[13px] font-semibold h-9 px-5 rounded-xl transition-all select-none origin-bottom bg-signal/10 text-signal border-x-2 border-t-2 border-b-[5px] border-signal/25 hover:bg-signal/20 active:border-b-2 active:scale-y-[0.97] whitespace-nowrap">
              <img src="/hand_line.png" alt="" className="w-4 h-4" style={{ filter: 'brightness(0) saturate(100%) invert(24%) sepia(79%) saturate(1834%) hue-rotate(345deg) brightness(89%) contrast(97%)' }} /> 停止挖掘
            </button>
          )}
        </div>
      </div>}

      {/* Mode-specific inputs + controls (hidden when viewing history or mining) */}
      {!isViewingHistory && !loading && <div className="shrink-0 px-6 max-md:px-4 py-3">
        <div>
          {/* Text input — sentence/keywords share same element; grid-rows for smooth collapse */}
          <div
            className="grid transition-[grid-template-rows,opacity,margin] duration-200 ease-in-out"
            style={{
              gridTemplateRows: mode === 'sentence' || mode === 'keywords' ? '1fr' : '0fr',
              opacity: mode === 'sentence' || mode === 'keywords' ? 1 : 0,
              marginBottom: mode === 'sentence' || mode === 'keywords' ? 12 : 0,
            }}
          >
            <div className="overflow-hidden">
              <div className="pb-0.5">
                <label className="block text-[11px] text-muted mb-1.5 font-medium">
                  {mode === 'sentence' ? '描述你想挖掘的需求方向' : '输入关键词（逗号或空格分隔）'}
                </label>
                <input
                  type="text"
                  value={mode === 'sentence' ? sentence : keywordsText}
                  onChange={(e) => mode === 'sentence' ? setSentence(e.target.value) : setKeywordsText(e.target.value)}
                  placeholder={mode === 'sentence'
                    ? '例如：照片拍了一堆，但没有整理成有意义的记录'
                    : '例如：AI 写作，笔记工具，日程管理，习惯养成'}
                  disabled={loading}
                  className="w-full rounded-xl border border-border/50 bg-bg h-10 px-3.5 text-[13px] placeholder:text-muted/40 focus:outline-none disabled:opacity-50"
                  onKeyDown={(e) => { if (e.key === 'Enter') handleFetch() }}
                />
              </div>
            </div>
          </div>

          {/* HN category — only in open mode, grid-rows collapse */}
          <div
            className="grid transition-[grid-template-rows,opacity,margin] duration-200 ease-in-out"
            style={{
              gridTemplateRows: mode === 'open' && sources.includes('hackernews') ? '1fr' : '0fr',
              opacity: mode === 'open' && sources.includes('hackernews') ? 1 : 0,
              marginBottom: mode === 'open' && sources.includes('hackernews') ? 12 : 0,
            }}
          >
            <div className="overflow-hidden">
              <div className="w-32 pb-0.5">
                <label className="block text-[11px] text-muted mb-1.5 font-medium">HN 分类</label>
                <div className="relative">
                  <select value={category} onChange={(e) => setCategory(e.target.value)} disabled={loading}
                    className="w-full appearance-none rounded-xl border border-border/50 bg-bg h-9 pl-3 pr-8 text-[13px] focus:outline-none focus:ring-2 focus:ring-accent/15 disabled:opacity-50 transition-shadow cursor-pointer">
                    {['ask', 'top', 'show', 'new'].map((c) => <option key={c}>{c}</option>)}
                  </select>
                  <ChevronDown size={13} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted/50 pointer-events-none" />
                </div>
              </div>
            </div>
          </div>

          {/* Reddit categories — show in sentence & open modes */}
          {sources.includes('reddit') && Object.keys(redditCategories).length > 0 && (
            <div
              className="grid transition-[grid-template-rows,opacity,margin] duration-200 ease-in-out"
              style={{
                gridTemplateRows: (mode === 'sentence' || mode === 'open') ? '1fr' : '0fr',
                opacity: (mode === 'sentence' || mode === 'open') ? 1 : 0,
                marginBottom: (mode === 'sentence' || mode === 'open') ? 12 : 0,
              }}
            >
              <div className="overflow-hidden">
                <div className="pb-1">
                  <label className="block text-[11px] text-muted mb-1.5 font-medium">
                    {mode === 'open' ? '限定赛道（可选，不选则自动规划）' : 'Reddit 赛道（可选，不选则自动规划）'}
                  </label>
                  <div className={`flex flex-wrap gap-1.5 p-px max-md:overflow-hidden max-md:transition-[max-height] max-md:duration-300 max-md:ease-in-out ${!tagsExpanded ? 'max-md:max-h-[64px]' : 'max-md:max-h-[500px]'}`}>
                    {Object.entries(redditCategories).map(([key, cat]) => (
                      <button
                        key={key}
                        onClick={() => { if (!loading) toggleRedditCat(key) }}
                        className={`text-[11px] font-medium px-2.5 py-1 rounded-xl transition-all border ${
                          selectedRedditCats.includes(key)
                            ? 'bg-accent/8 text-accent border-accent/40'
                            : 'text-muted/70 border-border hover:border-accent/30 hover:text-text'
                        }`}
                      >
                        {cat.label}
                      </button>
                    ))}
                  </div>
                  <button
                    onClick={() => setTagsExpanded(!tagsExpanded)}
                    className="hidden max-md:inline-flex items-center gap-1 text-[10px] text-accent font-medium mt-1"
                  >
                    <ChevronDown size={10} className={`transition-transform duration-200 ${tagsExpanded ? 'rotate-180' : ''}`} />
                    {tagsExpanded ? '收起' : '展开更多赛道'}
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* 挖掘参数 - collapsible */}
          <div>
            <button
              onClick={() => setShowAdvanced(!showAdvanced)}
              className="flex items-center gap-1.5 text-[11px] text-muted hover:text-text/80 transition-colors py-0.5"
            >
              <ChevronDown size={10} className={`transition-transform duration-200 ${showAdvanced ? 'rotate-180' : ''}`} />
              挖掘参数（可选）
            </button>

            <AnimatePresence>
              {showAdvanced && (
                <motion.div
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: 'auto', opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  transition={{ duration: 0.2, ease: 'easeInOut' }}
                  className="overflow-hidden"
                >
                  <div className="grid grid-cols-2 max-md:grid-cols-1 gap-x-4 gap-y-3 pt-3">
                    <div>
                      <label className="block text-[11px] text-muted mb-1.5 font-medium">时间范围</label>
                      <div className="relative">
                        <button
                          type="button"
                          onClick={() => !loading && setOpenTimePeriod(!openTimePeriod)}
                          onBlur={() => setTimeout(() => setOpenTimePeriod(false), 150)}
                          disabled={loading}
                          className="w-full flex items-center justify-between rounded-xl border border-border/50 bg-bg h-9 pl-3 pr-7 text-[13px] focus:outline-none focus:ring-2 focus:ring-accent/15 disabled:opacity-50 transition-shadow cursor-pointer text-left"
                        >
                          <span>{{ month: '过去 1 个月', '3months': '过去 3 个月', '6months': '过去 6 个月', '9months': '过去 9 个月' }[timePeriod]}</span>
                        </button>
                        <ChevronDown size={13} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted/50 pointer-events-none" />
                        {openTimePeriod && (
                          <div className="absolute left-0 top-full mt-1 bg-card border border-border/60 rounded-xl shadow-lg z-50 overflow-hidden w-full">
                            {([
                              { value: 'month', label: '过去 1 个月' },
                              { value: '3months', label: '过去 3 个月' },
                              { value: '6months', label: '过去 6 个月' },
                              { value: '9months', label: '过去 9 个月' },
                            ] as const).map((opt) => (
                              <button
                                key={opt.value}
                                type="button"
                                onMouseDown={(e) => {
                                  e.preventDefault()
                                  setTimePeriod(opt.value)
                                  setOpenTimePeriod(false)
                                }}
                                className={`w-full text-left px-3 py-2 text-[13px] hover:bg-accent/8 transition-colors ${
                                  timePeriod === opt.value ? 'text-accent font-medium bg-accent/5' : 'text-text'
                                }`}
                              >
                                {opt.label}
                              </button>
                            ))}
                          </div>
                        )}
                      </div>
                    </div>
                    <div>
                      <label className="block text-[11px] text-muted mb-1.5 font-medium">现有产品</label>
                      <input type="text" value={product} onChange={(e) => setProduct(e.target.value)} disabled={loading}
                        placeholder="如已有产品可输入名称" className="w-full rounded-xl border border-border/50 bg-bg h-9 px-3 text-[13px] placeholder:text-muted/40 focus:outline-none focus:ring-2 focus:ring-accent/15 disabled:opacity-50 transition-shadow" />
                    </div>
                    <div>
                      <label className="block text-[11px] text-muted mb-1.5 font-medium">目标市场</label>
                      <input type="text" value={market} onChange={(e) => setMarket(e.target.value)} disabled={loading}
                        placeholder="例如：美国、英国、东南亚" className="w-full rounded-xl border border-border/50 bg-bg h-9 px-3 text-[13px] placeholder:text-muted/40 focus:outline-none focus:ring-2 focus:ring-accent/15 disabled:opacity-50 transition-shadow" />
                    </div>
                    <div>
                      <label className="block text-[11px] text-muted mb-1.5 font-medium">目标用户画像</label>
                      <input type="text" value={demographics} onChange={(e) => setDemographics(e.target.value)} disabled={loading}
                        placeholder="例如：25-40 岁女性白领" className="w-full rounded-xl border border-border/50 bg-bg h-9 px-3 text-[13px] placeholder:text-muted/40 focus:outline-none focus:ring-2 focus:ring-accent/15 disabled:opacity-50 transition-shadow" />
                    </div>
                    <div>
                      <label className="block text-[11px] text-muted mb-1.5 font-medium">行为/情境细分</label>
                      <input type="text" value={segment} onChange={(e) => setSegment(e.target.value)} disabled={loading}
                        placeholder="例如：异地恋情侣、跨国家庭" className="w-full rounded-xl border border-border/50 bg-bg h-9 px-3 text-[13px] placeholder:text-muted/40 focus:outline-none focus:ring-2 focus:ring-accent/15 disabled:opacity-50 transition-shadow" />
                    </div>
                    <div>
                      <label className="block text-[11px] text-muted mb-1.5 font-medium">已知竞品</label>
                      <input type="text" value={competitors} onChange={(e) => setCompetitors(e.target.value)} disabled={loading}
                        placeholder="例如：Notion、Obsidian、Roam" className="w-full rounded-xl border border-border/50 bg-bg h-9 px-3 text-[13px] placeholder:text-muted/40 focus:outline-none focus:ring-2 focus:ring-accent/15 disabled:opacity-50 transition-shadow" />
                    </div>
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        </div>
      </div>}
      {!isViewingHistory && !loading && <div className="mx-6 max-md:mx-4 h-[1px] bg-border/30" />}

      {/* Progress indicator (hidden when viewing history) */}
      <AnimatePresence>
        {!isViewingHistory && (loading || (progressHistory.length > 1 && !progressDismissed)) && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.25, ease: 'easeInOut' }}
            className="shrink-0 overflow-hidden"
          >
            <div className="mx-6 max-md:mx-4 my-2.5 bg-[#f5f5f5] rounded-3xl p-4 relative overflow-hidden">
              {loading && (
                <ShineBorder
                  shineColor={["#ff6b6b", "#feca57", "#48dbfb", "#ff9ff3", "#54a0ff", "#5f27cd"]}
                  duration={8}
                  borderWidth={2}
                />
              )}
              <div
                className={`flex items-center gap-3 ${progressCollapsed && !loading ? 'cursor-pointer' : ''}`}
                onClick={() => { if (!loading && progressCollapsed) setProgressCollapsed(false) }}
              >
                {!loading && fetchDone ? (
                  <div className="w-5 h-5 rounded-full bg-emerald-500 flex items-center justify-center shrink-0">
                    <svg width="10" height="10" viewBox="0 0 8 8" fill="none"><path d="M1.5 4L3 5.5L6.5 2" stroke="white" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>
                  </div>
                ) : (
                  <img src="/vibe_coding_line.png" alt="" className="w-5 h-5 shrink-0 opacity-80" />
                )}
                {progressCollapsed && !loading ? (
                  <>
                    <p className="flex-1 text-xs text-text/70 line-clamp-1">
                      {progressHistory.slice(-2).join(' · ')}
                    </p>
                    <button onClick={(e) => { e.stopPropagation(); setProgressCollapsed(false) }}
                      className="text-[11px] text-accent hover:underline shrink-0">展开</button>
                    <button onClick={(e) => { e.stopPropagation(); setProgressDismissed(true) }}
                      className="text-[11px] text-muted/40 hover:text-signal shrink-0 ml-1">✕</button>
                  </>
                ) : (
                  <>
                    <div className="flex-1">
                      <div className="h-1.5 bg-black/[0.06] rounded-full overflow-hidden">
                        <motion.div
                          className={`h-full rounded-full ${loading ? 'bg-accent' : fetchDone ? 'bg-emerald-500' : 'bg-accent'}`}
                          initial={{ width: 0 }}
                          animate={{ width: `${smoothProgress}%` }}
                          transition={{ duration: 0.6, ease: 'easeOut' }}
                        />
                      </div>
                    </div>
                    <span className={`text-xs font-semibold shrink-0 w-10 text-right tabular-nums ${loading ? 'text-accent' : fetchDone ? 'text-emerald-600' : 'text-accent'}`}>
                      <CountUp to={Math.round(smoothProgress)} duration={0.6} />%
                    </span>
                    {!loading && (
                      <>
                        <button onClick={() => setProgressCollapsed(true)}
                          className="text-[11px] text-muted hover:text-accent shrink-0">收起</button>
                        <button onClick={() => setProgressDismissed(true)}
                          className="text-[11px] text-muted/40 hover:text-signal shrink-0 ml-0.5">✕</button>
                      </>
                    )}
                  </>
                )}
              </div>

              <AnimatePresence initial={false}>
                {!progressCollapsed && (
                  <motion.div
                    key="progress-messages"
                    initial={{ height: 0, opacity: 0 }}
                    animate={{ height: 'auto', opacity: 1 }}
                    exit={{ height: 0, opacity: 0 }}
                    transition={{ duration: 0.35, ease: [0.25, 0.1, 0.25, 1] }}
                    className="overflow-hidden"
                  >
                    <div className="max-h-[130px] overflow-y-auto scrollbar-auto flex flex-col gap-1 mt-3"
                      ref={progressScrollRef}
                      onScroll={(e) => {
                        const el = e.currentTarget
                        const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40
                        userScrolledUpRef.current = !nearBottom
                      }}
                    >
                      <AnimatePresence initial={false}>
                        {progressHistory.map((msg, i) => {
                          const isCurrent = i === progressHistory.length - 1
                          const remaining = Math.max(estimatedSeconds - elapsedSeconds, 0)
                          const showCountdown = isCurrent && loading && estimatedSeconds > 0
                          return (
                            <motion.div
                              key={`${i}-${msg}`}
                              initial={{ opacity: 0, y: 8, height: 0 }}
                              animate={{ opacity: 1, y: 0, height: 'auto' }}
                              transition={{ duration: 0.4, ease: [0.25, 0.1, 0.25, 1] }}
                              className={`text-xs flex items-center gap-2 ${
                                isCurrent ? 'text-text/80 font-medium' : 'text-muted/60'
                              }`}
                            >
                              <span className={`inline-block w-1.5 h-1.5 rounded-full shrink-0 ${
                                isCurrent && loading ? 'bg-accent' : isCurrent && fetchDone ? 'bg-emerald-500' : isCurrent ? 'bg-accent' : 'bg-muted/30'
                              }`} />
                              <span className="flex-1 min-w-0">
                                {isCurrent && loading ? (
                                  <ShimmerText className="text-xs" shimmerColor="rgba(44,44,44,0.3)" duration={2}>
                                    {msg}
                                  </ShimmerText>
                                ) : msg}
                              </span>
                              {showCountdown && (
                                <span className="text-[10px] text-muted/50 tabular-nums shrink-0">
                                  预计 {String(Math.floor(remaining / 60)).padStart(2, '0')}:{String(remaining % 60).padStart(2, '0')}
                                </span>
                              )}
                            </motion.div>
                          )
                        })}
                      </AnimatePresence>
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>


            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Needs list or empty state */}
      <div className="px-6 max-md:px-4 py-4">
        {needs.length === 0 && !loading ? (
          <div className="flex flex-col items-center justify-center py-10 text-center">
            {error ? (
              <div className="max-w-md mx-auto">
                <div className="flex items-start gap-3 px-5 py-4 bg-signal/5 border border-signal/20 rounded-xl text-left">
                  <AlertCircle size={18} className="text-signal shrink-0 mt-0.5" />
                  <div>
                    <p className="text-[13px] font-semibold text-signal mb-1">挖掘未成功</p>
                    <p className="text-xs text-signal/80 leading-relaxed">{error}</p>
                  </div>
                </div>
              </div>
            ) : (
              <>
                <img src="/logo.png" alt="" className="w-14 h-14 rounded-2xl mb-3" />
                <p className="text-sm font-medium mb-1">暂无需求数据</p>
                <p className="text-xs text-muted flex items-center gap-1">
                  试试
                  <RotatingText
                    texts={['一句话描述挖掘', '关键词搜索', '自主发现模式']}
                    rotationInterval={2500}
                    className="text-accent font-medium"
                    staggerDuration={0.02}
                  />
                </p>
              </>
            )}
          </div>
        ) : (
          <>
            {needs.length > 0 && (
              <>
                <div className="flex items-center justify-between mb-4">
                  <p className="text-xs text-muted">
                    共发现 <CountUp to={needs.length} duration={0.8} className="font-semibold text-text" /> 个需求主题 · <CountUp to={needs.reduce((s, n) => s + n.posts.length, 0)} duration={1} className="font-semibold text-text" /> 个相关帖子
                  </p>
                  {!loading && (
                    <button onClick={handleClear}
                      className="flex items-center gap-1.5 text-[11px] text-muted border border-border/40 h-7 px-3 rounded-lg hover:border-signal/40 hover:text-signal transition-colors">
                      <img src="/delete_2_line.png" alt="" className="w-3 h-3 opacity-50" /> 清空
                    </button>
                  )}
                </div>
                <div className="grid grid-cols-2 max-md:grid-cols-1 gap-3 items-start">
                  {needs.map((need, i) => {
                    const debating = isDebatingNeed(i)
                    const isExpanded = expandedIdx === i
                    const isSelected = selectedNeedIndex === i
                    return (
                      <motion.div
                        key={`card-${needsEpoch}-${i}`}
                        initial={{ opacity: 0, y: 20, scale: 0.96 }}
                        animate={{ opacity: 1, y: 0, scale: 1 }}
                        transition={{ delay: 0.15 + i * 0.07, duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
                        onClick={() => setExpandedIdx(isExpanded ? null : i)}
                        className={`bg-[#fafaf9] rounded-3xl border overflow-hidden flex flex-col cursor-pointer ${
                          isSelected ? 'border-accent/40 shadow-md ring-1 ring-accent/10' : 'border-border/40 hover:border-accent/25 hover:shadow-sm'
                        } ${isExpanded ? 'ring-1 ring-accent/20' : ''}`}
                      >
                        <div className="p-4 flex flex-col shrink-0">
                          <div className="flex items-center gap-2 mb-2">
                            <div className="w-6 h-6 bg-accent/8 rounded-lg flex items-center justify-center shrink-0">
                              <img src="/blockquote_line.png" alt="" className="w-3 h-3 opacity-70" />
                            </div>
                            <h3 className={`text-[13px] font-semibold leading-snug flex-1 min-w-0 ${isExpanded ? '' : 'line-clamp-1'}`}>{need.need_title}</h3>
                            {debating && (
                              <span className="shrink-0 text-[10px] font-medium px-1.5 py-0.5 rounded-full bg-blue-50 text-blue-600">讨论中</span>
                            )}
                            {!debating && isSelected && debateStatus === 'done' && (
                              <span className="shrink-0 text-[10px] font-medium px-1.5 py-0.5 rounded-full bg-emerald-50 text-emerald-600">已完成</span>
                            )}
                            {need.deep_mine_package && (
                              <span className="flex items-center gap-1 text-[10px] font-medium px-1.5 py-0.5 rounded-full bg-emerald-50 text-emerald-600 shrink-0">
                                <CheckCircle2 size={10} />
                                {need.deep_mine_package.femwc?.total?.toFixed(2) || '—'}分
                              </span>
                            )}
                          </div>

                          <p className={`text-[11px] text-muted leading-relaxed mb-3 mt-1 ${isExpanded ? '' : 'line-clamp-3'}`}>{need.need_description}</p>

                          <div className="flex items-center gap-2.5 text-[10px] text-muted mb-3">
                            <span className="flex items-center gap-1"><MessageSquare size={10} /> {need.posts.length}</span>
                            <span className="flex items-center gap-1"><TrendingUp size={10} /> {need.total_score}</span>
                            {(() => {
                              const srcs = [...new Set(need.posts.map(p => p.source.startsWith('reddit') ? 'Reddit' : 'HN'))]
                              return srcs.map(s => (
                                <span key={s} className="px-1 py-0.5 rounded text-[9px] bg-white font-medium border border-border/30">{s}</span>
                              ))
                            })()}
                          </div>

                          <div className="flex items-center gap-1.5" onClick={(e) => e.stopPropagation()}>
                            {reportGenIdx !== i ? (
                              <button
                                onClick={() => handleGenerateReport(i)}
                                disabled={reportGenIdx !== null}
                                className="flex items-center gap-1 text-[11px] font-medium text-white bg-accent h-7 px-3 rounded-lg hover:opacity-90 transition-opacity disabled:opacity-50"
                              >
                                <img src="/book_2_ai_line.png" alt="" className="w-3.5 h-3.5 opacity-90 brightness-0 invert" /> 生成报告
                              </button>
                            ) : (
                              <button
                                onClick={() => { reportGenAbort.current?.abort(); setReportGenIdx(null) }}
                                className="flex items-center gap-1 text-[11px] font-medium text-signal border border-signal/30 h-7 px-3 rounded-lg"
                              >
                                <Loader2 size={10} className="animate-spin" /> 生成中
                              </button>
                            )}
                            <InteractiveHoverButton
                              onClick={() => handleSelectAndDebate(i)}
                              icon={<img src="/chat_4_ai_line.png" alt="" className="w-3.5 h-3.5 opacity-90" />}
                              hoverIcon={<img src="/chat_4_ai_line.png" alt="" className="w-3.5 h-3.5 brightness-0 invert" />}
                            >
                              {debating ? '继续讨论' : '讨论'}
                            </InteractiveHoverButton>
                            <InteractiveHoverButton
                              onClick={() => { useAppStore.getState().setPersonaNeedIndex(i); setActiveView('personas') }}
                              icon={<img src="/group_2_line.png" alt="" className="w-3.5 h-3.5 opacity-90" />}
                              hoverIcon={<img src="/group_2_line.png" alt="" className="w-3.5 h-3.5 brightness-0 invert" />}
                            >
                              画像
                            </InteractiveHoverButton>
                            <div className="flex-1" />
                            <ChevronDown size={13} className={`text-muted transition-transform duration-200 ${isExpanded ? 'rotate-180' : ''}`} />
                          </div>
                        </div>

                        <div
                          className="grid transition-[grid-template-rows,opacity] duration-300 ease-in-out"
                          style={{
                            gridTemplateRows: reportGenIdx === i ? '1fr' : '0fr',
                            opacity: reportGenIdx === i ? 1 : 0,
                          }}
                        >
                          <div className="overflow-hidden">
                            <div className="mx-4 h-[1px] bg-border/25" />
                            <div className="px-4 pb-3 pt-2 space-y-1.5">
                              <div className="flex items-center gap-1.5 min-w-0">
                                <img src="/vibe_coding_line.png" alt="" className="w-3 h-3 opacity-60 flex-shrink-0" />
                                <ShimmerText className="text-[11px] text-accent font-medium truncate" shimmerColor="rgba(44,44,44,0.3)" duration={2.5}>
                                  {reportGenMsg}<span className="inline-block w-[1.2em] text-left animate-[dotPulse_1.4s_ease-in-out_infinite]">...</span>
                                </ShimmerText>
                                {reportSubMsg && (
                                  <span className="text-[11px] text-muted/40 truncate ml-1">{reportSubMsg}</span>
                                )}
                              </div>
                              <div className="flex items-center gap-2">
                                <div className="flex-1 h-1.5 bg-black/[0.06] rounded-full overflow-hidden">
                                  <motion.div
                                    className="h-full bg-accent rounded-full"
                                    animate={{ width: `${reportGenProgress}%` }}
                                    transition={{ duration: 1.5, ease: 'easeOut' }}
                                  />
                                </div>
                                <span className="text-[11px] font-medium text-foreground tabular-nums w-8 text-right flex-shrink-0">{reportGenProgress}%</span>
                              </div>
                            </div>
                          </div>
                        </div>

                        {/* Expanded detail inside card */}
                        <AnimatePresence>
                          {isExpanded && (
                            <motion.div
                              initial={{ height: 0, opacity: 0 }}
                              animate={{ height: 'auto', opacity: 1 }}
                              exit={{ height: 0, opacity: 0 }}
                              transition={{ duration: 0.25, ease: 'easeInOut' }}
                              className="overflow-hidden"
                              onClick={(e) => e.stopPropagation()}
                            >
                              <div className="mx-4 h-[1px] bg-border/25" />
                              <div className="px-4 pb-4 pt-3 max-h-[320px] overflow-y-auto scrollbar-auto">
                                {need.deep_mine_package?.femwc && (
                                  <div className="mb-3">
                                    <div className="flex items-center gap-2 mb-2">
                                      <BarChart3 size={13} className="text-accent" />
                                      <span className="text-[11px] font-semibold">FEMWC 评分</span>
                                      <span className="text-[11px] font-bold text-accent ml-auto">{need.deep_mine_package.femwc.total?.toFixed(2)} 分</span>
                                    </div>
                                    <div className="grid grid-cols-5 max-md:grid-cols-3 gap-1.5">
                                      {(['F', 'E', 'M', 'W', 'C'] as const).map((dim) => {
                                        const d = need.deep_mine_package!.femwc[dim] as FemwcDimension
                                        const labels: Record<string, string> = { F: '频率', E: '情感', M: '市场', W: '付费', C: '竞争' }
                                        const sc = d?.score || 0
                                        return (
                                          <div key={dim} className="bg-bg rounded-xl p-2 text-center">
                                            <p className="text-[10px] text-muted mb-0.5">{labels[dim]}</p>
                                            <p className="text-sm font-bold">{sc}</p>
                                            <div className="h-1 bg-black/[0.06] rounded-full mt-1"><div className="h-full bg-accent rounded-full" style={{ width: `${sc * 20}%` }} /></div>
                                          </div>
                                        )
                                      })}
                                    </div>
                                    <p className="text-[11px] text-muted mt-1.5">{need.deep_mine_package.femwc.verdict} — {need.deep_mine_package.femwc.summary}</p>
                                  </div>
                                )}
                                {need.deep_mine_package?.quotes && need.deep_mine_package.quotes.length > 0 && (
                                  <div className="mb-3">
                                    <div className="flex items-center gap-2 mb-2">
                                      <Quote size={13} className="text-violet-500" />
                                      <span className="text-[11px] font-semibold">原文摘录（{need.deep_mine_package.quotes.length}条）</span>
                                    </div>
                                    <div className="space-y-1.5">
                                      {need.deep_mine_package.quotes.slice(0, 6).map((q, qi) => (
                                        <div key={qi} className="bg-bg rounded-xl px-3 py-2">
                                          <p className="text-[12px] italic text-text/80 leading-relaxed mb-1">"{q.text.slice(0, 200)}{q.text.length > 200 ? '...' : ''}"</p>
                                          <div className="flex items-center gap-2 text-[10px] text-muted">
                                            <span className="px-1.5 py-0.5 rounded bg-card text-[9px] font-medium">
                                              {q.signal_type === 'pain' ? '痛点' : q.signal_type === 'workaround' ? '临时方案' : q.signal_type === 'willingness_to_pay' ? '付费信号' : q.signal_type === 'competitor_complaint' ? '竞品不满' : q.signal_type === 'journey' ? '用户旅程' : q.signal_type}
                                            </span>
                                            {q.score > 0 && <span>▲{q.score}</span>}
                                            {q.source_url && <a href={q.source_url} target="_blank" rel="noopener noreferrer" className="text-accent hover:underline">来源</a>}
                                          </div>
                                        </div>
                                      ))}
                                    </div>
                                  </div>
                                )}
                                <div className="space-y-2">
                                  {need.posts.map((post, pi) => (
                                    <div key={pi} className="bg-bg rounded-xl px-3 py-2.5">
                                      <div className="flex items-start gap-2">
                                        <div className="flex-1 min-w-0">
                                          <p className="text-[12px] font-medium leading-snug mb-0.5">{post.title}</p>
                                          {post.title_zh && <p className="text-[11px] text-muted leading-snug mb-1">{post.title_zh}</p>}
                                          <div className="flex items-center gap-2.5 text-[10px] text-muted">
                                            <span>▲ {post.score}</span>
                                            <span>💬 {post.num_comments}</span>
                                            <span className="px-1 py-0.5 rounded text-[9px] bg-card font-medium">{post.source.startsWith('reddit') ? 'Reddit' : 'HN'}</span>
                                            {post.has_need_signals && <span className="px-1 py-0.5 rounded-full text-[9px] font-medium bg-signal/10 text-signal">需求信号</span>}
                                          </div>
                                        </div>
                                        {post.url && <a href={post.url} target="_blank" rel="noopener noreferrer" className="shrink-0 text-muted hover:text-accent transition-colors"><ExternalLink size={11} /></a>}
                                      </div>
                                    </div>
                                  ))}
                                </div>
                              </div>
                            </motion.div>
                          )}
                        </AnimatePresence>
                      </motion.div>
                    )
                  })}
                </div>
              </>
            )}
          </>
        )}
      </div>

      </div>{/* end scrollable area */}

      <ConfirmDialog
        open={confirmAction !== null}
        title={confirmAction?.title || ''}
        message={confirmAction?.message || ''}
        onConfirm={() => confirmAction?.action()}
        onCancel={() => setConfirmAction(null)}
      />

    </div>
  )
}
