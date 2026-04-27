"""
prompts/search.py — 搜索相关 Prompt

包含：搜索规划、批次相关性检查、深挖查询生成、自主发现模式
"""

SEARCH_PLANNING_PROMPT = """你是需求挖掘专家。用户想探索某个方向的产品机会。
请按四类角度生成英文搜索词矩阵和推荐 Reddit 社区。

## 用户输入
{user_input}

{research_context}

输出 JSON（不加代码块标记）：
{{
  "problem_queries": ["痛点搜索词1", "痛点搜索词2", "..."],
  "solution_queries": ["方案搜索词1", "方案搜索词2", "..."],
  "competitor_queries": ["竞品搜索词1", "竞品搜索词2", "..."],
  "platform_queries": ["平台定向搜索词1", "..."],
  "discovery_queries": ["自然语言搜索句1", "自然语言搜索句2", "..."],
  "subreddits": ["subreddit1", "subreddit2", "..."],
  "known_competitors": ["竞品名1", "竞品名2"],
  "reasoning": "搜索策略说明（中文，1-2句）"
}}

=== 四类搜索词角度（按优先级排序） ===

**1. problem_queries（痛点角度，最优先执行）** — 8-12 条
抓取用户的挫败感、放弃行为、迁移行为。这是 ROI 最高的搜索方向。
模板：`[topic] frustrated`, `[topic] hate`, `[topic] struggle`, `[topic] impossible`,
      `[topic] workaround`, `[topic] gave up`, `[topic] switched from`, `[topic] broken`,
      `[topic] wish`, `[topic] annoying`, `[topic] alternative`
- 每条 2-4 个英文单词，尽量精准
- 优先用具体场景/行为词，避免太宽泛

**2. solution_queries（方案寻求角度）** — 5-8 条
抓取用户主动寻找解决方案的表达。
模板：`best [topic] app`, `[topic] recommendation reddit`, `[topic] alternative`,
      `how do you handle [topic]`, `[topic] hack`, `best [topic] 2026`

**3. competitor_queries（竞品定向角度）** — 5-8 条
针对已知或推测的竞品进行定向搜索。
模板：`[competitor] review reddit`, `[competitor] vs`, `[competitor] problems`,
      `[competitor] alternative`, `switched from [competitor]`
- 必须猜测该领域 3-5 个可能的竞品名，为每个生成 1-2 条搜索词

**4. platform_queries（平台定向角度）** — 4-6 条
显式指向 Reddit 的搜索词（用于 Web 搜索引擎，禁止使用 site: 操作符）。
模板：`reddit [topic] frustrated`, `reddit [topic] recommend`,
      `r/[subreddit] [topic]`, `reddit [topic] switched from`

=== discovery_queries — Web 语义搜索（完整自然语言句子） ===
15-20 个完整英文句子，模拟用户会说的话。ROI 极高。
覆盖：痛点叙述、方案探索、竞品对比、场景描述、用户旅程。

✅ "frustrated with translating conversations with my partner"
✅ "best real-time translation app for couples who speak different languages"
✅ "I wish I could talk to my wife without Google Translate"
✅ "I gave up trying to use [competitor] because..."
✅ "anyone else struggle with [specific scenario]"

=== subreddits ===
12-20 个最相关的 Reddit 社区名（不带 r/）：
- 核心垂直社区占 50%（直接讨论该领域的社区）
- 泛用户社区占 30%（如 AskReddit, NoStupidQuestions, LifeProTips）
- 周边话题社区占 20%（相邻但不完全相同的领域）

=== 关键规则 ===
- 即使用户输入是中文，所有搜索词必须是英文
- 禁止使用 site: 操作符（Web 搜索引擎不可靠支持）
- known_competitors 填入该领域你知道的 3-5 个竞品名（英文）
- 如果提供了目标市场/用户画像/竞品信息，搜索词和社区选择要针对性适配"""


BATCH_RELEVANCE_PROMPT = """以下是一批搜索结果（含标题和内容摘要）。逐条判断每条与目标主题「{topic}」是否相关。

## 帖子列表
{titles_json}

输出 JSON（不加代码块标记）：
{{
  "keep_indices": [0, 2, 4],
  "discard_indices": [1, 3],
  "reason": "一句话说明判断依据"
}}

规则：
- 逐条判断，综合 title 和 snippet 内容来判断
- keep_indices 里放相关帖子的 idx，discard_indices 里放跑题的 idx
- 「跑题」= 帖子讨论的实际内容与目标主题无关（即使标题中含有部分关键词）
- 典型跑题：Android/iOS 系统更新评测、编程开发教程、与主题无关的产品评测、纯新闻转载
- 保留：直接讨论目标主题痛点的帖子、竞品体验、替代方案讨论、用户使用场景描述
- 如果 snippet 为空，仅根据标题判断，此时可适当宽松"""


QUICK_RELEVANCE_PROMPT = """以下是一批搜索结果中排名前 5 的帖子标题。快速判断这批结果与目标主题「{topic}」的相关性。

## 帖子标题
{titles_text}

输出 JSON（不加代码块标记）：
{{
  "off_topic_count": 0,
  "verdict": "keep" 或 "discard",
  "reason": "一句话说明"
}}

规则：
- 逐条判断每个标题是否与目标主题相关
- off_topic_count = 跑题的标题数量
- 如果 off_topic_count >= 3（5条中有3条以上跑题），verdict 必须为 "discard"
- 如果 off_topic_count < 3，verdict 为 "keep"
- 「跑题」= 帖子讨论的实际主题与目标主题无关（即使标题含部分关键词）
- 典型跑题：Android/iOS 系统更新、编程教程、无关领域评测、纯新闻
- 快速判断，不要过度思考"""


DEEP_MINING_QUERY_PROMPT = """基于以下已发现的需求方向和帖子内容，生成补充搜索词，用于更深度的挖掘。

## 需求方向
{need_title}
{need_description}

## 已有帖子关键内容
{posts_summary}

输出 JSON（不加代码块标记）：
{{
  "search_queries": ["补充搜索词，英文，10-15条"],
  "subreddits": ["补充 subreddit，5-8个"],
  "competitor_names": ["帖子中提到的竞品/工具名"],
  "focus_areas": ["需要深挖的具体方向"]
}}

要求：
- 搜索词要比阶段 A 更具体、更有针对性
- 重点挖掘：用户的具体 workaround、竞品体验、付费行为
- 包含竞品名关键词（如有提到的话）
- 包含用户描述的具体场景关键词"""


AUTO_DISCOVER_PROMPT = """你是需求挖掘专家。现在进入「自主发现」模式，你需要从以下高价值 Reddit 板块中选择 3-5 个最有潜力的方向进行挖掘。

## 可选板块分类
{categories_json}

{category_constraint}

## 任务
分析这些板块，判断哪些方向最可能存在未被充分解决的用户需求和产品机会。
然后为每个方向生成搜索词。

输出 JSON（不加代码块标记）：
{{
  "selected_directions": [
    {{
      "category": "板块分类key",
      "direction": "具体的挖掘方向（中文，1句话）",
      "reasoning": "为什么选择这个方向（中文，1句话）",
      "search_queries": ["英文搜索词，4-6条"],
      "subreddits": ["目标subreddit，2-3个"]
    }}
  ],
  "total_reasoning": "整体选择策略说明（中文，1-2句）"
}}

要求：
- 选择 3-5 个方向，优先选择用户痛点密集、产品机会明确的领域
- 每个方向的搜索词要覆盖痛点、workaround、竞品不满等角度
- 避免太宽泛的方向（如"提升效率"），要具体到可落地的产品场景
- 只关注能通过 App/软件/AI 解决的需求方向，忽略需要硬件或实物的方向"""
