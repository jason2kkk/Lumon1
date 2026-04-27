/**
 * DecryptedText — 解密风格文字动画
 * 基于 React Bits (MIT) 简化版，适配 framer-motion
 * https://reactbits.dev/text-animations/decrypted-text
 */
import { useEffect, useState, useRef, useMemo, useCallback } from 'react'

interface DecryptedTextProps {
  text: string
  speed?: number
  maxIterations?: number
  sequential?: boolean
  revealDirection?: 'start' | 'end' | 'center'
  characters?: string
  className?: string
  encryptedClassName?: string
  parentClassName?: string
  animateOn?: 'view' | 'hover'
}

export default function DecryptedText({
  text,
  speed = 50,
  maxIterations = 10,
  sequential = false,
  revealDirection = 'start',
  characters = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz!@#$%^&*()_+',
  className = '',
  encryptedClassName = '',
  parentClassName = '',
  animateOn = 'view',
}: DecryptedTextProps) {
  const [displayText, setDisplayText] = useState(text)
  const [isAnimating, setIsAnimating] = useState(false)
  const [revealedIndices, setRevealedIndices] = useState<Set<number>>(new Set())
  const [hasAnimated, setHasAnimated] = useState(false)
  const containerRef = useRef<HTMLSpanElement>(null)
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const availableChars = useMemo(() => characters.split(''), [characters])

  const shuffleText = useCallback(
    (originalText: string, currentRevealed: Set<number>) => {
      return originalText
        .split('')
        .map((char, i) => {
          if (char === ' ') return ' '
          if (currentRevealed.has(i)) return originalText[i]
          return availableChars[Math.floor(Math.random() * availableChars.length)]
        })
        .join('')
    },
    [availableChars],
  )

  const triggerDecrypt = useCallback(() => {
    setRevealedIndices(new Set())
    setIsAnimating(true)
  }, [])

  // 进入视口时触发
  useEffect(() => {
    if (animateOn !== 'view') return
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting && !hasAnimated) {
            triggerDecrypt()
            setHasAnimated(true)
          }
        })
      },
      { threshold: 0.1 },
    )
    if (containerRef.current) observer.observe(containerRef.current)
    return () => observer.disconnect()
  }, [animateOn, hasAnimated, triggerDecrypt])

  // 动画主循环
  useEffect(() => {
    if (!isAnimating) return

    let currentIteration = 0

    const getNextIndex = (revealed: Set<number>): number => {
      const len = text.length
      if (revealDirection === 'start') return revealed.size
      if (revealDirection === 'end') return len - 1 - revealed.size
      const mid = Math.floor(len / 2)
      const offset = Math.floor(revealed.size / 2)
      return revealed.size % 2 === 0 ? mid + offset : mid - offset - 1
    }

    intervalRef.current = setInterval(() => {
      setRevealedIndices((prev) => {
        if (sequential) {
          if (prev.size < text.length) {
            const nextIdx = getNextIndex(prev)
            const next = new Set(prev)
            next.add(nextIdx)
            setDisplayText(shuffleText(text, next))
            return next
          } else {
            clearInterval(intervalRef.current ?? undefined)
            setIsAnimating(false)
            return prev
          }
        } else {
          setDisplayText(shuffleText(text, prev))
          currentIteration++
          if (currentIteration >= maxIterations) {
            clearInterval(intervalRef.current ?? undefined)
            setIsAnimating(false)
            setDisplayText(text)
          }
          return prev
        }
      })
    }, speed)
    return () => clearInterval(intervalRef.current ?? undefined)
  }, [isAnimating, text, speed, maxIterations, sequential, revealDirection, shuffleText])

  const hoverProps =
    animateOn === 'hover'
      ? {
          onMouseEnter: () => {
            if (!isAnimating) {
              setRevealedIndices(new Set())
              setIsAnimating(true)
            }
          },
          onMouseLeave: () => {
            clearInterval(intervalRef.current ?? undefined)
            setIsAnimating(false)
            setDisplayText(text)
          },
        }
      : {}

  return (
    <span ref={containerRef} className={parentClassName} {...hoverProps}>
      <span className="sr-only">{displayText}</span>
      <span aria-hidden>
        {displayText.split('').map((char, i) => {
          const isRevealed = revealedIndices.has(i) || (!isAnimating && !hasAnimated) || (!isAnimating && hasAnimated)
          return (
            <span key={i} className={isRevealed ? className : `${className} ${encryptedClassName}`}>
              {char}
            </span>
          )
        })}
      </span>
    </span>
  )
}
