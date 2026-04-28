import { useState, useEffect, useCallback, Component, type ReactNode, type ErrorInfo } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import NavSidebar from './components/NavSidebar'
import FetchView from './components/FetchView'
import ChatPanel from './components/ChatPanel'
import DetailPanel from './components/DetailPanel'
import ReportsView from './components/ReportsView'
import PersonaView from './components/PersonaView'
// import TrendingView from './components/TrendingView'
import SettingsDialog from './components/SettingsDialog'
import WhatsNewModal from './components/WhatsNewModal'
import ResizeHandle from './components/ResizeHandle'
import { useAppStore } from './stores/app'

class ErrorBoundary extends Component<{ children: ReactNode }, { hasError: boolean; error: string }> {
  state = { hasError: false, error: '' }
  static getDerivedStateFromError(error: Error) { return { hasError: true, error: error.message } }
  componentDidCatch(error: Error, info: ErrorInfo) { console.error('[ErrorBoundary]', error, info) }
  render() {
    if (this.state.hasError) {
      return (
        <div className="flex items-center justify-center h-screen bg-bg">
          <div className="text-center p-8 bg-card rounded-2xl shadow-sm max-w-md">
            <p className="text-sm font-semibold mb-2">页面出了点问题</p>
            <p className="text-xs text-muted mb-4 break-all">{this.state.error}</p>
            <button onClick={() => { this.setState({ hasError: false, error: '' }); window.location.reload() }}
              className="text-xs font-medium text-white bg-accent px-4 py-2 rounded-xl hover:opacity-90">
              刷新页面
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}

const MIN_DETAIL = 260
const MAX_DETAIL = 600
const DEFAULT_DETAIL = 380

const viewTransition = {
  initial: { opacity: 0, y: 6 },
  animate: { opacity: 1, y: 0 },
  exit: { opacity: 0, y: -6 },
  transition: { duration: 0.18, ease: 'easeOut' as const },
}

export default function App() {
  const activeView = useAppStore((s) => s.activeView)
  const setActiveView = useAppStore((s) => s.setActiveView)
  const setPrefillFetchQuery = useAppStore((s) => s.setPrefillFetchQuery)
  const setAutoStartFetch = useAppStore((s) => s.setAutoStartFetch)
  const [detailWidth, setDetailWidth] = useState(DEFAULT_DETAIL)
  const [mobileDebateTab, setMobileDebateTab] = useState<'chat' | 'detail'>('chat')

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const q = params.get('q')
    if (q) {
      const trimmed = q.slice(0, 500).trim()
      if (trimmed) {
        setActiveView('fetch')
        setPrefillFetchQuery(trimmed)
        if (params.get('autostart') === '1') {
          setAutoStartFetch(true)
        }
        window.history.replaceState({}, '', window.location.pathname)
      }
    }
  }, [])

  const handleResize = useCallback((delta: number) => {
    setDetailWidth((w) => Math.min(MAX_DETAIL, Math.max(MIN_DETAIL, w - delta)))
  }, [])

  return (
    <ErrorBoundary>
      <div className="flex h-screen overflow-hidden bg-bg p-2 gap-2 max-md:flex-col max-md:p-0 max-md:gap-0 max-md:bg-[#faf8f5]">
        <NavSidebar />

        <AnimatePresence mode="wait">
          {activeView === 'fetch' && (
            <motion.div key="fetch" {...viewTransition}
              className="flex-1 min-w-0 min-h-0 bg-card rounded-3xl overflow-hidden shadow-sm max-md:rounded-none max-md:shadow-none max-md:bg-[#faf8f5]"
            >
              <FetchView />
            </motion.div>
          )}

          {/* 热度雷达模块暂不上线 */}

          {activeView === 'debate' && (
            <motion.div key="debate" {...viewTransition}
              className="flex-1 flex min-w-0 min-h-0 items-stretch max-md:flex-col"
            >
              <div className="hidden max-md:flex items-center gap-1 px-4 pt-3 pb-2 bg-[#faf8f5] shrink-0">
                {(['chat', 'detail'] as const).map((tab) => (
                  <button
                    key={tab}
                    onClick={() => setMobileDebateTab(tab)}
                    className={`px-4 py-1.5 rounded-lg text-sm font-medium transition-colors ${
                      mobileDebateTab === tab ? 'bg-accent/10 text-accent' : 'text-muted'
                    }`}
                  >
                    {tab === 'chat' ? '对话' : '详情'}
                  </button>
                ))}
              </div>
              <div className={`flex-1 min-w-0 min-h-0 bg-card rounded-3xl overflow-hidden shadow-sm max-md:rounded-none max-md:shadow-none max-md:bg-[#faf8f5] ${
                mobileDebateTab === 'detail' ? 'max-md:hidden' : ''
              }`}>
                <ChatPanel />
              </div>
              <ResizeHandle onResize={handleResize} className="max-md:hidden" />
              <div
                style={{ '--detail-w': `${detailWidth}px` } as React.CSSProperties}
                className={`shrink-0 w-[var(--detail-w)] bg-card rounded-3xl overflow-hidden shadow-sm max-md:rounded-none max-md:shadow-none max-md:bg-[#faf8f5] max-md:w-full max-md:flex-1 max-md:min-h-0 ${
                  mobileDebateTab === 'chat' ? 'max-md:hidden' : ''
                }`}
              >
                <DetailPanel />
              </div>
            </motion.div>
          )}

          {activeView === 'reports' && (
            <motion.div key="reports" {...viewTransition}
              className="flex-1 min-w-0 min-h-0 bg-card rounded-3xl overflow-hidden shadow-sm max-md:rounded-none max-md:shadow-none max-md:bg-[#faf8f5]"
            >
              <ReportsView />
            </motion.div>
          )}

          {activeView === 'personas' && (
            <motion.div key="personas" {...viewTransition}
              className="flex-1 min-w-0 min-h-0 bg-card rounded-3xl overflow-hidden shadow-sm max-md:rounded-none max-md:shadow-none max-md:bg-[#faf8f5]"
            >
              <PersonaView />
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      <SettingsDialog />
      <WhatsNewModal />
    </ErrorBoundary>
  )
}
