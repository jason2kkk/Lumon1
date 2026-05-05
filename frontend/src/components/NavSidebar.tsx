import { motion, AnimatePresence } from 'framer-motion'
import { ChevronDown, X } from 'lucide-react'
import { useAppStore, type ActiveView } from '../stores/app'
import { useState, useEffect, useRef, useCallback } from 'react'
import { GradientText } from './animations'
import { getOnlineStats, type OnlineStats } from '../api/client'

const NAV_ITEMS: { id: ActiveView; img: string; label: string }[] = [
  // { id: 'trending', img: '/fire_line.png', label: '热度雷达' },
  { id: 'fetch', img: '/search_2_ai_line.png', label: '采集需求' },
  { id: 'debate', img: '/chat_4_ai_line.png', label: '讨论需求' },
  { id: 'reports', img: '/book_2_ai_line.png', label: '报告中心' },
  { id: 'personas', img: '/group_2_line.png', label: '画像建模' },
]

export default function NavSidebar() {
  const {
    activeView, setActiveView, setShowSettingsDialog,
    fetchHistory, activeFetchHistoryId,
    setActiveFetchHistory, removeFetchHistory,
  } = useAppStore()
  const [historyCollapsed, setHistoryCollapsed] = useState(false)
  const [hoveredHistoryId, setHoveredHistoryId] = useState<string | null>(null)
  const [stats, setStats] = useState<OnlineStats | null>(null)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const checkAppVersion = useAppStore((s) => s.checkAppVersion)

  const fetchStats = useCallback(() => {
    getOnlineStats().then((data) => {
      setStats(data)
      if (data.app_version) checkAppVersion(data.app_version)
    }).catch(() => {})
  }, [checkAppVersion])

  useEffect(() => {
    fetchStats()
    timerRef.current = setInterval(fetchStats, 15000)
    return () => { if (timerRef.current) clearInterval(timerRef.current) }
  }, [fetchStats])

  return (
    <>
    <div className="w-[210px] bg-card rounded-3xl flex flex-col pt-5 pb-3 shrink-0 shadow-sm max-md:hidden">
      {/* Logo + brand */}
      <div className="flex items-center gap-3 px-5 mb-6">
        <div className="w-11 h-11 rounded-xl overflow-hidden shadow-sm shrink-0">
          <img src="/logo.png" alt="Logo" className="w-full h-full object-cover" />
        </div>
        <span style={{ fontFamily: "'Sora', sans-serif" }}>
          <GradientText
            className="text-[20px] tracking-tight"
            colors={['#2c2c2c', '#6b6b6b', '#2c2c2c']}
            animationSpeed={6}
          >
            Lumon
          </GradientText>
        </span>
      </div>

      {/* Main nav */}
      <nav className="px-3 space-y-1">
        {NAV_ITEMS.map(({ id, img, label }) => {
          const isActive = activeView === id
          return (
            <button
              key={id}
              onClick={() => { setActiveFetchHistory(null); setActiveView(id) }}
              className={`relative w-full flex items-center gap-3 px-3.5 h-11 rounded-xl transition-all text-[14px] ${
                isActive
                  ? 'font-semibold text-accent'
                  : 'text-text/50 hover:text-text/80 hover:bg-bg/60'
              }`}
            >
              {isActive && (
                <motion.div
                  layoutId="nav-active"
                  className="absolute inset-0 bg-accent/8 rounded-xl"
                  transition={{ type: 'spring', stiffness: 400, damping: 30 }}
                />
              )}
              <img
                src={img}
                alt=""
                className={`relative z-10 w-5 h-5 transition-opacity ${
                  isActive ? 'opacity-90' : 'opacity-40'
                }`}
              />
              <span className="relative z-10">{label}</span>
            </button>
          )
        })}
      </nav>

      {/* Fetch history */}
      {fetchHistory.length > 0 && (
        <div className="mt-5 px-3 flex-1 min-h-0 flex flex-col">
          <button
            onClick={() => setHistoryCollapsed(!historyCollapsed)}
            className="flex items-center gap-1.5 px-3.5 py-1.5 text-[12px] text-muted font-medium hover:text-text/70 transition-colors"
          >
            <ChevronDown size={11} className={`transition-transform duration-200 ${historyCollapsed ? '-rotate-90' : ''}`} />
            需求历史
            <span className="ml-auto text-[10px] text-muted/50 font-normal tabular-nums">{fetchHistory.length}</span>
          </button>
          <AnimatePresence>
            {!historyCollapsed && (
              <motion.div
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: 'auto', opacity: 1, overflow: 'visible' }}
                exit={{ height: 0, opacity: 0, overflow: 'hidden' }}
                transition={{ duration: 0.2, ease: 'easeInOut' }}
                style={{ overflow: 'hidden' }}
              >
                <div className="overflow-y-auto scrollbar-auto space-y-0.5 pr-0.5" style={{ maxHeight: '125px' }}>
                  {fetchHistory.map((item) => {
                    const isActive = activeFetchHistoryId === item.id && activeView === 'fetch'
                    const isHovered = hoveredHistoryId === item.id
                    return (
                      <div
                        key={item.id}
                        onMouseEnter={() => setHoveredHistoryId(item.id)}
                        onMouseLeave={() => setHoveredHistoryId(null)}
                        className={`w-full flex items-center gap-2.5 px-3.5 h-[34px] rounded-xl text-left transition-all cursor-pointer group relative ${
                          isActive
                            ? 'bg-accent/8 text-accent'
                            : isHovered
                              ? 'bg-bg/50 text-text/80'
                              : 'text-text/60'
                        }`}
                        onClick={() => {
                          setActiveFetchHistory(item.id)
                          setActiveView('fetch')
                        }}
                      >
                        <img src="/blockquote_line.png" alt="" className={`w-3 h-3 shrink-0 ${isActive ? 'opacity-80' : 'opacity-30'}`} />
                        <span className="flex-1 min-w-0 text-[12px] leading-snug truncate pr-5">{item.title}</span>
                        <AnimatePresence>
                          {(isHovered || isActive) && (
                            <motion.button
                              initial={{ opacity: 0, scale: 0.8 }}
                              animate={{ opacity: 1, scale: 1 }}
                              exit={{ opacity: 0, scale: 0.8 }}
                              transition={{ duration: 0.12 }}
                              onClick={(e) => {
                                e.stopPropagation()
                                removeFetchHistory(item.id)
                              }}
                              className="absolute right-2 top-1/2 -translate-y-1/2 w-5 h-5 rounded-md flex items-center justify-center text-muted/50 hover:text-signal hover:bg-signal/10 transition-colors"
                            >
                              <X size={11} />
                            </motion.button>
                          )}
                        </AnimatePresence>
                      </div>
                    )
                  })}
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      )}

      {/* Spacer */}
      <div className="flex-1" />

      {/* Online stats */}
      {stats && (
        <div className="px-5 mb-5 space-y-1">
          <div className="flex items-center gap-2 text-[11px] text-muted/60">
            <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${stats.online > 0 ? 'bg-emerald-400 animate-[gentlePulse_1.5s_ease-in-out_infinite]' : 'bg-emerald-400/50'}`} />
            <span>在线 <span className="text-text/70 font-medium tabular-nums">{stats.online}</span> 人</span>
          </div>
          <div className="flex items-center gap-2 text-[11px] text-muted/60">
            <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${stats.mining > 0 ? 'bg-blue-400 animate-[gentlePulse_1.5s_ease-in-out_infinite]' : 'bg-blue-300/50'}`} />
            <span>挖掘中 <span className="text-text/70 font-medium tabular-nums">{stats.mining}</span> 人</span>
          </div>
          <div className="flex items-center gap-2 text-[11px] text-muted/60">
            <span className="w-1.5 h-1.5 rounded-full bg-purple-400 shrink-0" />
            <span>已挖掘 <span className="text-text/70 font-medium tabular-nums">{stats.needs}</span> 个需求</span>
          </div>
        </div>
      )}

      {/* Settings - pushed to bottom */}
      <div className="px-3">
        <div className="mx-2 mb-3 h-[1.5px] bg-border/35 rounded-full" />
        <button
          onClick={() => setShowSettingsDialog(true)}
          className="w-full flex items-center gap-3 px-3.5 h-11 rounded-xl text-[14px] text-text/50 hover:text-text/80 hover:bg-bg/60 transition-all"
        >
          <img src="/settings_1_line.png" alt="" className="w-5 h-5 opacity-40" />
          <span>设置</span>
        </button>
      </div>
    </div>

    {/* Mobile bottom tab bar */}
    <nav
      className="md:hidden bg-[#faf8f5] border-t border-border/20 flex items-center shrink-0"
      style={{ paddingBottom: 'env(safe-area-inset-bottom, 0px)' }}
    >
      {NAV_ITEMS.map(({ id, img, label }) => {
        const isActive = activeView === id
        return (
          <button
            key={id}
            onClick={() => { setActiveFetchHistory(null); setActiveView(id) }}
            className={`flex-1 flex flex-col items-center gap-0.5 pt-2 pb-1.5 transition-colors ${
              isActive ? 'text-accent' : 'text-muted'
            }`}
          >
            <img src={img} alt="" className={`w-5 h-5 ${isActive ? 'opacity-90' : 'opacity-40'}`} />
            <span className={`text-[10px] leading-tight ${isActive ? 'font-semibold' : ''}`}>{label}</span>
          </button>
        )
      })}
      <button
        onClick={() => setShowSettingsDialog(true)}
        className="flex-1 flex flex-col items-center gap-0.5 pt-2 pb-1.5 text-muted transition-colors"
      >
        <img src="/settings_1_line.png" alt="" className="w-5 h-5 opacity-40" />
        <span className="text-[10px] leading-tight">设置</span>
      </button>
    </nav>
    </>
  )
}
