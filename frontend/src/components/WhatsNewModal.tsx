import { useState, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { useAppStore } from '../stores/app'

const CURRENT_VERSION = '1.1.0'
const STORAGE_KEY = 'lumon_whats_new_seen'

interface UpdateSlide {
  subtitle: string
}

const SLIDES: UpdateSlide[] = [
  { subtitle: '全面适配移动端，随时随地查看研究报告和需求分析。' },
  { subtitle: '新增「画像建模」功能，基于真实用户发言建模用户画像。' },
  { subtitle: '优化多项交互体验，修复已知问题，使用更流畅。' },
]

export default function WhatsNewModal() {
  const storeVisible = useAppStore((s) => s.whatsNewVisible)
  const setStoreVisible = useAppStore((s) => s.setWhatsNewVisible)

  const [localVisible, setLocalVisible] = useState(false)
  const [slide, setSlide] = useState(0)

  useEffect(() => {
    try {
      const seen = localStorage.getItem(STORAGE_KEY)
      if (seen !== CURRENT_VERSION) {
        setLocalVisible(true)
        setStoreVisible(true)
      }
    } catch {
      setLocalVisible(true)
      setStoreVisible(true)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (storeVisible && !localVisible) {
      setSlide(0)
      setLocalVisible(true)
    }
  }, [storeVisible, localVisible])

  const visible = localVisible

  const handleDismiss = () => {
    setLocalVisible(false)
    setStoreVisible(false)
    try { localStorage.setItem(STORAGE_KEY, CURRENT_VERSION) } catch { /* */ }
  }

  const handleNext = () => {
    if (slide < SLIDES.length - 1) {
      setSlide(s => s + 1)
    } else {
      handleDismiss()
    }
  }

  return (
    <AnimatePresence>
      {visible && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.2 }}
          className="fixed inset-0 z-[60] flex items-center justify-center"
        >
          <div className="absolute inset-0 bg-black/30 backdrop-blur-[2px]" onClick={handleDismiss} />

          <motion.div
            initial={{ scale: 0.92, opacity: 0, y: 20 }}
            animate={{ scale: 1, opacity: 1, y: 0 }}
            exit={{ scale: 0.95, opacity: 0, y: 10 }}
            transition={{ duration: 0.3, ease: [0.22, 1, 0.36, 1] }}
            className="relative w-[420px] max-md:w-[90vw] bg-white rounded-[28px] shadow-2xl overflow-hidden"
          >
            <div className="w-full overflow-hidden">
              <img
                src="/new1.png"
                alt=""
                className="w-full h-auto block"
                onError={(e) => {
                  const target = e.currentTarget
                  target.style.display = 'none'
                  if (target.parentElement) {
                    target.parentElement.style.background = 'linear-gradient(135deg, #6366f1 0%, #3b82f6 50%, #06b6d4 100%)'
                    target.parentElement.style.aspectRatio = '16/9'
                  }
                }}
              />
            </div>

            <div className="px-6 pt-4 pb-6">
              <div className="flex justify-center gap-1.5 mb-4">
                {SLIDES.map((_, i) => (
                  <button
                    key={i}
                    onClick={() => setSlide(i)}
                    className={`h-2 rounded-full transition-all duration-300 ${
                      i === slide ? 'bg-zinc-800 w-5' : 'bg-zinc-200 w-2'
                    }`}
                  />
                ))}
              </div>

              <div className="text-center min-h-[68px] flex flex-col justify-center">
                <h2 className="text-lg font-bold text-zinc-900 mb-2">What&apos;s New?</h2>
                <AnimatePresence mode="wait">
                  <motion.p
                    key={slide}
                    initial={{ opacity: 0, y: 8 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -8 }}
                    transition={{ duration: 0.2 }}
                    className="text-[14px] text-zinc-600 leading-relaxed"
                  >
                    {SLIDES[slide].subtitle}
                  </motion.p>
                </AnimatePresence>
              </div>

              <button
                onClick={handleNext}
                className="relative w-full mt-5 bg-zinc-900 text-white text-[14px] font-semibold py-3 rounded-full overflow-hidden transition-colors hover:bg-zinc-800 active:bg-black"
              >
                <span className="relative z-10">{slide < SLIDES.length - 1 ? 'Next' : '开始使用'}</span>
                <span className="absolute top-0 bottom-0 left-0 w-[200%] animate-[sweepLight_2.5s_ease-in-out_infinite] bg-gradient-to-r from-transparent via-white/30 to-transparent" />
              </button>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
