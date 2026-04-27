import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { X } from 'lucide-react'

interface HelpStep {
  title: string
  description: string
}

interface HelpDialogProps {
  title: string
  steps: HelpStep[]
}

export function HelpButton({ title, steps }: HelpDialogProps) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="w-6 h-6 rounded-full flex items-center justify-center opacity-40 hover:opacity-80 transition-opacity"
        title={`了解${title}的工作流程`}
      >
        <img src="/question_line.png" alt="" className="w-3.5 h-3.5" />
      </button>
      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm"
            onClick={(e) => { if (e.target === e.currentTarget) setOpen(false) }}
          >
            <motion.div
              initial={{ opacity: 0, scale: 0.95, y: 10 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.95, y: 10 }}
              transition={{ duration: 0.2 }}
              className="bg-card rounded-3xl shadow-2xl border border-border/30 w-full max-w-md max-md:max-w-[calc(100vw-2rem)] max-h-[80vh] overflow-hidden flex flex-col"
            >
              <div className="flex items-center justify-between px-6 pt-5 pb-3">
                <div className="flex items-center gap-2">
                  <img src="/question_line.png" alt="" className="w-4 h-4 opacity-60" />
                  <h2 className="text-[15px] font-bold text-text">{title}</h2>
                </div>
                <button
                  onClick={() => setOpen(false)}
                  className="w-7 h-7 rounded-lg flex items-center justify-center text-muted hover:text-text hover:bg-bg transition-colors"
                >
                  <X size={14} />
                </button>
              </div>

              <div className="flex-1 overflow-y-auto px-6 pb-6">
                <div className="space-y-4">
                  {steps.map((step, i) => (
                    <div key={i} className="flex gap-3">
                      <div className="shrink-0 w-6 h-6 rounded-full bg-accent/10 flex items-center justify-center text-[11px] font-bold text-accent mt-0.5">
                        {i + 1}
                      </div>
                      <div className="flex-1 min-w-0">
                        <h3 className="text-[13px] font-semibold text-text mb-1">{step.title}</h3>
                        <p className="text-[12px] text-muted leading-relaxed whitespace-pre-line">{step.description}</p>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  )
}

// 各模块的帮助内容
export const FETCH_HELP: HelpDialogProps = {
  title: '需求挖掘 · 工作流程',
  steps: [
    {
      title: '输入需求关键词',
      description: '用户输入自然语言描述或标签（如"meal planning app"），系统理解语义意图。',
    },
    {
      title: '智能生成搜索策略',
      description: 'AI 分析输入后自动生成多组搜索关键词，覆盖不同表达方式和相关话题，最大化搜索覆盖面。',
    },
    {
      title: 'Web Search 发现帖子',
      description: '通过搜索引擎在 Reddit 等社区中定位高相关性的讨论帖链接，支持时间范围过滤。',
    },
    {
      title: '深度爬取内容',
      description: '使用 rdt-cli 爬取每个帖子的完整内容和评论，获取真实用户声音和上下文。',
    },
    {
      title: '相关性过滤与聚类',
      description: 'AI 对每个帖子进行相关性打分，过滤噪音内容。然后将高相关帖子按主题聚类，形成需求卡片。',
    },
    {
      title: '需求卡片生成',
      description: '每个需求卡片包含：主题标题、核心痛点摘要、相关帖子列表、用户原文引用。可直接用于生成报告或发起讨论。',
    },
  ],
}

export const DEBATE_HELP: HelpDialogProps = {
  title: '需求讨论 · 工作流程',
  steps: [
    {
      title: '四个 AI 角色',
      description: '导演：控制讨论节奏，拆分话题，做阶段总结\n产品经理：需求的坚定推动者，用帖子证据和场景分析来论证\n杠精：专业质疑者，挑战每一个假设和论点\n投资人：从商业视角评估，关注市场规模和变现能力',
    },
    {
      title: '上下文注入',
      description: '系统将挖掘到的帖子内容处理成结构化摘要（痛点、场景、用户画像），注入到每个角色的上下文中，确保讨论基于真实数据。',
    },
    {
      title: '话题驱动的讨论',
      description: '导演先分析帖子，拆出 2-3 个核心话题（如痛点真实性、竞品差异化、付费意愿）。每个话题下，产品经理和杠精进行多轮辩论。',
    },
    {
      title: '实时上下文同步',
      description: '每个角色发言时，能看到之前所有角色的完整对话记录。上下文是实时同步的，确保讨论连贯和有针对性。',
    },
    {
      title: '导演总结与裁决',
      description: '每个话题结束后导演做小结，所有话题讨论完毕后给出最终判断：值不值得做、最大风险是什么、建议的切入点。',
    },
  ],
}

export const REPORT_HELP: HelpDialogProps = {
  title: '报告生成 · 工作流程',
  steps: [
    {
      title: '数据来源',
      description: '报告基于三类数据：\n· 挖掘到的 Reddit 帖子和评论（用户真实声音）\n· AI 联网搜索的竞品信息（App Store、官网、定价）\n· 帖子的聚类分析结果（痛点分布、场景归纳）',
    },
    {
      title: '痛点地图生成',
      description: 'AI 分析所有帖子，提取用户痛点并按强度排序。每个痛点附带原文引用、出现频率、用户情绪强度评分。',
    },
    {
      title: '竞品联网搜索',
      description: 'AI 自动生成竞品搜索词，通过 Web Search（GPT/Claude/Tavily）联网搜索真实竞品信息，获取定价、评分、用户评价等数据。',
    },
    {
      title: '场景与用户行为分析',
      description: '从帖子中提取用户的真实使用场景、触发时刻、当前替代方案，构建完整的用户行为画像。',
    },
    {
      title: '产品方案生成',
      description: '基于痛点和竞品分析，AI 生成 3 个具体可落地的产品方案。每个方案包含目标用户、核心功能、产品形态和可行性依据。',
    },
    {
      title: '报告输出与评价',
      description: '最终报告包含完整的研究结论。每个产品方案可通过「POC 产品评价」功能，基于红毛丹准入准则进行 AI 评审。',
    },
  ],
}
