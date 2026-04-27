import { useRef, useEffect, useState, useCallback } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Play, RotateCcw, Send,
  Square, XCircle, ArrowDown,
} from 'lucide-react'
import { useAppStore } from '../stores/app'
import { streamSSE, getDebateState, resetDebate as apiResetDebate, streamGenerateReport } from '../api/client'
import ChatMessageComponent from './ChatMessage'
import ConfirmDialog from './ConfirmDialog'
import { ShimmerText } from './animations'
import { HelpButton, DEBATE_HELP } from './HelpDialog'

export default function ChatPanel() {
  const needs = useAppStore((s) => s.needs)
  const selectedNeedIndex = useAppStore((s) => s.selectedNeedIndex)
  const debateStatus = useAppStore((s) => s.debateStatus)
  const messages = useAppStore((s) => s.messages)
  const maxRounds = useAppStore((s) => s.maxRounds)
  const isStreaming = useAppStore((s) => s.isStreaming)
  const errorMessage = useAppStore((s) => s.errorMessage)
  const roleNames = useAppStore((s) => s.roleNames)
  const setDebateStatus = useAppStore((s) => s.setDebateStatus)
  const setDebateRound = useAppStore((s) => s.setDebateRound)
  const addMessage = useAppStore((s) => s.addMessage)
  const appendToLastMessage = useAppStore((s) => s.appendToLastMessage)
  const finalizeLastMessage = useAppStore((s) => s.finalizeLastMessage)
  const clearMessages = useAppStore((s) => s.clearMessages)
  const setFinalReport = useAppStore((s) => s.setFinalReport)
  const resetDebateKeepPost = useAppStore((s) => s.resetDebateKeepPost)
  const setDetailView = useAppStore((s) => s.setDetailView)
  const setIsStreaming = useAppStore((s) => s.setIsStreaming)
  const setErrorMessage = useAppStore((s) => s.setErrorMessage)
  const setActiveView = useAppStore((s) => s.setActiveView)
  const setPendingReportFile = useAppStore((s) => s.setPendingReportFile)

  const [reportProgress, setReportProgress] = useState(0)
  const [reportMsg, setReportMsg] = useState('')
  const [freeTopicTitle, setFreeTopicTitle] = useState<string | null>(null)
  const [confirmAction, setConfirmAction] = useState<{
    title: string; message: string; action: () => void
  } | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  const scrollContainerRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  const userScrolledUpRef = useRef(false)
  const [showScrollBtn, setShowScrollBtn] = useState(false)
  const prevNeedIdxRef = useRef<number | null>(selectedNeedIndex)

  const hasNeed = selectedNeedIndex !== null && needs.length > 0 && selectedNeedIndex < needs.length
  const need = hasNeed ? needs[selectedNeedIndex] : null

  useEffect(() => {
    getDebateState().then((state) => {
      if (state.debate_log.length > 0 && messages.length === 0) {
        const rn = useAppStore.getState().roleNames
        const roleLabels: Record<string, string> = {
          analyst: rn.analyst || '产品经理', critic: rn.critic || '杠精', director: rn.director || '导演', investor: rn.investor || '投资人', human: '你',
        }
        for (const entry of state.debate_log) {
          const role = entry.role as 'analyst' | 'critic' | 'director' | 'human' | 'investor'
          addMessage({
            id: '', role,
            label: roleLabels[role] || role,
            content: entry.content,
          })
        }
        if (state.selected_need_idx !== null) useAppStore.getState().setSelectedNeed(state.selected_need_idx)
        if (state.free_topic_input) setFreeTopicTitle(state.free_topic_input)
        const legacyMap: Record<string, string> = { generating_proposal: 'debate_done', proposal_done: 'debate_done', deep_diving: 'debate_done', deep_dive_done: 'debate_done' }
        const normalizedStatus = legacyMap[state.status] || state.status
        setDebateStatus(normalizedStatus as typeof debateStatus)
        setDebateRound(state.round)
        if (state.final_report) setFinalReport(state.final_report)
        const hasAnalyst = state.debate_log.some((e) => e.role === 'analyst')
        setDetailView({ type: hasAnalyst ? 'analysis' : 'post' })
      }
    }).catch(() => {})
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (prevNeedIdxRef.current !== null && selectedNeedIndex !== null && prevNeedIdxRef.current !== selectedNeedIndex && debateStatus !== 'idle') {
      abortRef.current?.abort()
      abortRef.current = null
      setIsStreaming(false)
      resetDebateKeepPost()
      apiResetDebate().catch(() => {})
    }
    prevNeedIdxRef.current = selectedNeedIndex
  }, [selectedNeedIndex, debateStatus, setIsStreaming, resetDebateKeepPost])

  useEffect(() => {
    if (hasNeed && messages.length === 0 && debateStatus === 'idle') {
      setDetailView({ type: 'post' })
    }
  }, [hasNeed, messages.length, debateStatus, setDetailView])

  // Track user scroll position — only auto-scroll when user is near bottom
  useEffect(() => {
    const el = scrollContainerRef.current
    if (!el) return
    const onScroll = () => {
      const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
      const isUp = distFromBottom > 120
      userScrolledUpRef.current = isUp
      setShowScrollBtn(isUp)
    }
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => el.removeEventListener('scroll', onScroll)
  }, [])

  // Auto-scroll: only on new message count (not every chunk), and only when near bottom
  const msgCountRef = useRef(messages.length)
  const lastStreamIdRef = useRef<string | null>(null)

  useEffect(() => {
    const countChanged = messages.length !== msgCountRef.current
    msgCountRef.current = messages.length

    if (messages.length === 0) return
    const last = messages[messages.length - 1]

    if (countChanged && !userScrolledUpRef.current) {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
    }

    // Auto-show analysis panel when PM's first message starts
    if (last.role === 'analyst' && last.streaming && last.id !== lastStreamIdRef.current) {
      lastStreamIdRef.current = last.id
      const { detailView } = useAppStore.getState()
      if (detailView.type === 'empty' || detailView.type === 'post') {
        setDetailView({ type: 'analysis' })
      }
    }
    if (!last.streaming) {
      lastStreamIdRef.current = null
    }
  }, [messages.length, setDetailView]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleShowPost = useCallback(() => {
    setDetailView({ type: 'post' })
  }, [setDetailView])


  const handleStop = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
    setIsStreaming(false)
    setDebateStatus('debate_done')
  }, [setIsStreaming, setDebateStatus])

  const runDebateStream = useCallback(async (endpoint: string, body: Record<string, unknown>) => {
    setErrorMessage(null)
    setIsStreaming(true)
    clearMessages()
    setDebateStatus('debating')
    setFinalReport(null)

    const controller = new AbortController()
    abortRef.current = controller
    const sig = controller.signal

    await streamSSE(endpoint, body, {
      onMessageStart: (data) => {
        if (sig.aborted) return
        addMessage({ id: '', role: data.role as 'analyst' | 'critic' | 'director', label: data.label, content: '', streaming: true, provider: data.provider as 'claude' | 'gpt' | undefined })
      },
      onChunk: (data) => { if (!sig.aborted) appendToLastMessage(data.text) },
      onMessageEnd: (data) => { if (!sig.aborted) finalizeLastMessage(data.content) },
      onRoundStart: (data) => { if (!sig.aborted) setDebateRound(data.round) },
      onTopicStart: (data) => {
        if (sig.aborted) return
        addMessage({
          id: '', role: 'director', label: '',
          content: '', topicDivider: { index: data.index, title: data.title, total: data.total },
        })
      },
      onDebateEnd: () => {
        if (!sig.aborted) setDebateStatus('debate_done')
      },
      onError: (data) => {
        if (sig.aborted) return
        setErrorMessage(data.message)
        setDebateStatus('debate_done')
      },
    }, controller.signal)

    abortRef.current = null
    setIsStreaming(false)
  }, [clearMessages, setDebateStatus, setFinalReport, addMessage, appendToLastMessage, finalizeLastMessage, setDebateRound, setIsStreaming, setErrorMessage])

  const handleStartDebate = useCallback(async (demo = false) => {
    if (!hasNeed || selectedNeedIndex === null) return
    await runDebateStream('/debate/start', { need_index: selectedNeedIndex, max_rounds: maxRounds, demo })
  }, [hasNeed, selectedNeedIndex, maxRounds, runDebateStream])

  // TODO: 加入讨论功能即将更新 — 以下功能暂时禁用
  // const handleStartFreeDebate = useCallback(async (text: string) => { ... }, [runDebateStream])
  // const handleSendMessage = useCallback(async () => { ... }, [...])

  const handleGenerateReport = useCallback(async () => {
    if (selectedNeedIndex === null) return
    setIsStreaming(true)
    setErrorMessage(null)
    setDebateStatus('generating_report')
    setReportProgress(0)
    setReportMsg('准备生成报告...')

    const controller = new AbortController()
    abortRef.current = controller

    let chunkCount = 0
    let maxProgress = 0
    let lastMsgIdx = -1
    const writingMsgs = [
      '正在撰写报告内容...', '深入分析用户痛点...', '痛点地图生成中...',
      '提取用户原文引述...', '分析场景和用户行为...', '梳理竞品格局...',
      '撰写竞品详情分析...', '构思产品方案...', '细化产品方案与目标用户...',
      '提炼产品定位建议...', '汇总证据来源...', '整理研究局限与后续建议...',
      '正在输出最后的章节...', '校验数据完整性...', '优化表述与措辞...',
      '最后的润色与排版...', '检查报告结构...', '收尾工作进行中...',
      '马上就好...', '即将完成...',
    ]
    const msgThresholds = writingMsgs.map((_, i) => 53 + Math.floor(i * 44 / (writingMsgs.length - 1)))

    await streamGenerateReport(selectedNeedIndex, {
      onProgress: (data) => {
        if (data.progress >= maxProgress) {
          maxProgress = data.progress
          setReportProgress(data.progress)
        }
        setReportMsg(data.message)
      },
      onChunk: () => {
        chunkCount++
        if (chunkCount % 2 === 0) {
          const p = Math.min(52 + Math.floor(46 * chunkCount / (chunkCount + 350)), 98)
          if (p > maxProgress) { maxProgress = p; setReportProgress(p) }
        }
        if (chunkCount === 1) { lastMsgIdx = 0; setReportMsg(writingMsgs[0]) }
        else {
          const nextIdx = lastMsgIdx + 1
          if (nextIdx < writingMsgs.length && maxProgress >= msgThresholds[nextIdx]) {
            lastMsgIdx = nextIdx; setReportMsg(writingMsgs[nextIdx])
          }
        }
      },
      onDone: (data) => {
        setReportProgress(100)
        setReportMsg('报告生成完成！')
        setDebateStatus('done')
        setTimeout(() => {
          if (data?.filename) setPendingReportFile(data.filename)
          setActiveView('reports')
        }, 600)
      },
      onError: (data) => {
        const msg = data.message || '报告生成失败'
        setErrorMessage(msg)
        setDebateStatus('debate_done')
      },
    }, controller.signal)

    abortRef.current = null
    setIsStreaming(false)
  }, [selectedNeedIndex, setDebateStatus, setIsStreaming, setErrorMessage, setActiveView, setPendingReportFile])

  const handleReset = useCallback(() => {
    setConfirmAction({
      title: '重置讨论',
      message: '重置后当前讨论记录将丢失，确认重置？',
      action: async () => {
        setConfirmAction(null)
        abortRef.current?.abort()
        abortRef.current = null
        setIsStreaming(false)
        setErrorMessage(null)
        await new Promise((r) => setTimeout(r, 50))
        setFreeTopicTitle(null)
        resetDebateKeepPost()
        apiResetDebate().catch(() => {})
      },
    })
  }, [resetDebateKeepPost, setIsStreaming, setErrorMessage])

  const statusLabels: Record<string, string> = {
    idle: '等待开始', debating: '讨论中', debate_done: '讨论完成',
    generating_report: '生成报告中', done: '报告已生成',
    generating_proposal: '讨论完成', proposal_done: '讨论完成',
    deep_diving: '讨论完成', deep_dive_done: '讨论完成',
  }

  return (
    <div className="flex flex-col h-full min-w-0">
      {/* Header */}
      <div className="shrink-0 px-5 py-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2.5 min-w-0">
            {need ? (
              <button
                onClick={handleShowPost}
                className="font-semibold text-sm truncate text-left transition-colors hover:text-accent max-md:max-w-[35vw]"
                title="点击查看需求详情"
              >
                {need.need_title}
              </button>
            ) : freeTopicTitle ? (
              <h2 className="font-semibold text-sm truncate">{freeTopicTitle}</h2>
            ) : (
              <h2 className="font-semibold text-sm truncate text-muted">选择需求开始讨论</h2>
            )}
            <span className="max-md:hidden"><HelpButton {...DEBATE_HELP} /></span>
            {(hasNeed || freeTopicTitle) && (
              <span className={`shrink-0 text-[10px] font-medium px-2 py-0.5 rounded-full ${
                debateStatus === 'debating' ? 'bg-blue-50 text-blue-600' :
                debateStatus === 'debate_done' ? 'bg-amber-50 text-amber-600' :
                debateStatus === 'generating_report' ? 'bg-violet-50 text-violet-600' :
                debateStatus === 'done' ? 'bg-violet-50 text-violet-600' :
                'bg-bg text-muted'
              }`}>
                {statusLabels[debateStatus] || debateStatus}
              </span>
            )}
          </div>
          <div className="flex gap-2 shrink-0">
            {debateStatus === 'idle' && hasNeed && (
              <>
                <button onClick={() => handleStartDebate(true)}
                  className="text-[13px] font-medium text-accent/70 hover:text-accent transition-colors whitespace-nowrap">
                  演示模式
                </button>
                <button onClick={() => handleStartDebate(false)}
                  className="flex items-center gap-1.5 bg-accent text-white text-xs font-medium h-8 px-3.5 rounded-xl hover:opacity-90 transition-opacity">
                  <Play size={11} strokeWidth={2.5} /> 开始辩论
                </button>
              </>
            )}

            {(debateStatus === 'debating' ||
              debateStatus === 'generating_report') && (
              <>
                <button onClick={handleStop}
                  className="flex items-center gap-1.5 text-xs font-medium text-signal border border-signal/30 h-8 px-3.5 rounded-xl hover:bg-signal/5 transition-colors">
                  <Square size={9} fill="currentColor" /> 停止
                </button>
                <button onClick={handleReset}
                  className="flex items-center gap-1.5 text-xs text-muted border border-border/50 h-8 px-2.5 rounded-xl hover:border-accent/40 transition-colors">
                  <RotateCcw size={11} />
                </button>
              </>
            )}

            {debateStatus === 'debate_done' && (
              <>
                <button onClick={handleGenerateReport} disabled={isStreaming || selectedNeedIndex === null}
                  className="flex items-center gap-1.5 bg-accent text-white text-xs font-medium h-8 px-3.5 rounded-xl hover:opacity-90 transition-opacity disabled:opacity-50">
                  <img src="/book_2_ai_line.png" alt="" className="w-3 h-3 brightness-0 invert opacity-80" /> 生成报告
                </button>
                <button onClick={handleReset}
                  className="flex items-center gap-1.5 text-xs text-muted border border-border/50 h-8 px-2.5 rounded-xl hover:border-accent/40 transition-colors">
                  <RotateCcw size={11} />
                </button>
              </>
            )}

            {debateStatus === 'done' && (
              <>
                <button onClick={() => setActiveView('reports')}
                  className="flex items-center gap-1.5 text-xs font-medium text-accent border border-border/50 h-8 px-3 rounded-xl hover:border-accent/40 transition-colors">
                  <img src="/book_2_ai_line.png" alt="" className="w-3 h-3 opacity-60" /> 查看报告
                </button>
                <button onClick={handleReset}
                  className="flex items-center gap-1.5 text-xs text-muted border border-border/50 h-8 px-2.5 rounded-xl hover:border-accent/40 transition-colors">
                  <RotateCcw size={11} />
                </button>
              </>
            )}

            {/* 兜底：旧会话状态（proposal_done / deep_dive_done 等） */}
            {debateStatus !== 'idle' && debateStatus !== 'debating' && debateStatus !== 'debate_done' && debateStatus !== 'generating_report' && debateStatus !== 'done' && (
              <button onClick={handleReset}
                className="flex items-center gap-1.5 text-xs text-muted border border-border/50 h-8 px-2.5 rounded-xl hover:border-accent/40 transition-colors">
                <RotateCcw size={11} /> 重置
              </button>
            )}
          </div>
        </div>
      </div>
      <div className="mx-5 h-[1px] bg-border/30" />

      {/* Messages */}
      <div className="relative flex-1 min-h-0">
      <div ref={scrollContainerRef} className="h-full overflow-y-auto scrollbar-auto px-5 py-4">
        {messages.length === 0 && !hasNeed && debateStatus === 'idle' && (
          <div className="h-full flex flex-col items-center justify-center text-center">
            <img src="/group_3_line.png" alt="" className="h-9 w-auto mb-3 opacity-80" />
            <p className="text-sm font-medium mb-1">四角色讨论</p>
            <p className="text-xs text-muted leading-relaxed max-w-[260px] mb-4">
              在「采集」页选择一个需求卡片，点击「开始讨论」进入四角色讨论
            </p>
            <div className="flex flex-wrap items-center justify-center gap-4">
              {[
                roleNames.director || '导演',
                roleNames.analyst || '产品经理',
                roleNames.critic || '杠精',
                roleNames.investor || '投资人',
              ].map((name) => (
                <div key={name} className="border border-border/60 rounded-xl px-5 py-2.5 flex items-center justify-center">
                  <span className="text-xs text-muted font-medium leading-none">{name}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {messages.length === 0 && hasNeed && debateStatus === 'idle' && (
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }}
            className="h-full flex flex-col items-center justify-center text-center">
            <p className="text-sm font-medium mb-1">准备就绪</p>
            <p className="text-xs text-muted">点击「开始辩论」启动四角色讨论</p>
            <p className="text-[11px] text-muted/60 mt-1">需求详情可在右侧面板查看</p>
          </motion.div>
        )}

        <div className="space-y-4">
          {messages.map((msg) => (
            <ChatMessageComponent
              key={msg.id}
              message={msg}
              roleNames={roleNames}
            />
          ))}

          {/* Inline error */}
          {errorMessage && (
            <motion.div
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              className="flex items-start gap-2.5 bg-red-50 border border-red-200 rounded-xl p-3"
            >
              <XCircle size={14} className="text-red-500 mt-0.5 shrink-0" />
              <div className="flex-1 min-w-0">
                <p className="text-xs font-medium text-red-700 mb-0.5">出错了</p>
                <p className="text-[11px] text-red-600 break-all">{errorMessage}</p>
              </div>
              <button onClick={() => setErrorMessage(null)}
                className="text-[11px] text-red-500 hover:text-red-700 shrink-0">
                关闭
              </button>
            </motion.div>
          )}

          {debateStatus === 'generating_report' && (
            <div className="flex flex-col items-center gap-2 text-muted text-xs py-4">
              <div className="flex items-center gap-1.5">
                <img src="/apple_intelligence_line.png" alt="" className="w-3.5 h-3.5" />
                <ShimmerText className="text-xs" shimmerColor="rgba(44,44,44,0.3)" duration={2}>
                  {reportMsg || 'Lumon 正在生成报告...'}
                </ShimmerText>
              </div>
              {reportProgress > 0 && (
                <div className="w-48 h-1 bg-border/30 rounded-full overflow-hidden">
                  <div className="h-full bg-accent/60 rounded-full transition-all duration-500"
                    style={{ width: `${reportProgress}%` }} />
                </div>
              )}
            </div>
          )}
        </div>
        <div ref={bottomRef} />
      </div>

      <AnimatePresence>
        {showScrollBtn && (
          <motion.button
            initial={{ opacity: 0, scale: 0.8 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.8 }}
            transition={{ duration: 0.15 }}
            onClick={() => {
              bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
              userScrolledUpRef.current = false
              setShowScrollBtn(false)
            }}
            className="absolute bottom-3 right-5 z-10 flex items-center gap-1 bg-white/90 backdrop-blur border border-border/40 shadow-sm rounded-full h-7 px-2.5 text-[11px] text-muted hover:text-text hover:shadow transition-all"
          >
            <ArrowDown size={11} />
            <span>最新消息</span>
          </motion.button>
        )}
      </AnimatePresence>
      </div>{/* end relative wrapper */}

      {/* Input bar — 加入讨论功能暂时禁用 */}
      <div className="mx-4 h-[1px] bg-border/30" />
      <div className="shrink-0 px-4 py-2.5">
        <div className="flex gap-2 items-center">
          <div className="relative flex-1">
            <input
              type="text"
              disabled
              placeholder="加入讨论功能即将更新..."
              className="w-full rounded-xl border border-border/50 bg-bg h-10 px-4 text-[13px] placeholder:text-accent/50 focus:outline-none disabled:opacity-60 disabled:cursor-not-allowed transition-all"
            />
          </div>
          <button
            disabled
            className="bg-accent text-white rounded-xl h-10 w-10 flex items-center justify-center disabled:opacity-20 shrink-0"
          >
            <Send size={14} />
          </button>
        </div>
      </div>

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
