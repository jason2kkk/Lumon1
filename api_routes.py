"""
api_routes.py — FastAPI 路由定义

职责：REST 端点（采集、帖子、报告、配置）+ SSE 端点（辩论流式、报告生成流式）
"""

import json
import os
import re as _re_tag
import threading
import time as _time
from datetime import datetime
from pathlib import Path
from typing import Generator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from llm_client import (
    check_config, get_config_values, estimate_debate_cost, test_connection,
    reset_clients, set_runtime_config, get_provider_config,
    call_claude_stream, call_gpt_stream, call_claude, call_gpt,
    call_for_role, call_for_role_stream, get_role_model_config,
    get_token_stats, reset_token_stats,
    call_llm, call_llm_stream, check_llm_available, check_role_models_available,
    get_general_model, set_general_model,
    set_thread_session, get_thread_session, clear_thread_session,
)
from session_context import get_session, SessionContext, cleanup_expired_sessions, _sessions, _sessions_lock
import httpx
from scrapers import search_hackernews, fetch_hackernews, REDDIT_CATEGORIES, TRACK_CATEGORIES, hard_filter, HN_ALGOLIA_BASE
from rdt_client import get_reddit_fetcher, init_reddit_fetcher, fetch_subreddit_info, fetch_subreddit_hot
from quote_extractor import extract_quotes, score_femwc, build_need_package
from debate import (
    generate_final_report, generate_product_proposal,
    prepare_initial_messages, prepare_critic_messages,
    prepare_analyst_reply, prepare_critic_reply,
    prepare_director_conclude, prepare_human_inject,
    prepare_deep_dive_messages,
    prepare_topic_analysis, prepare_topic_pm, prepare_topic_critic,
    prepare_topic_pm_counter, prepare_topic_wrap, prepare_final_verdict,
    prepare_human_inject_topic, is_structural_feedback,
    format_topic_exchanges,
    prepare_free_topic_analysis, prepare_free_topic_pm, prepare_free_topic_critic,
    prepare_topic_critic_followup, prepare_free_topic_critic_followup,
    prepare_investor_bg, prepare_investor_final,
    prepare_free_investor_bg, prepare_free_investor_final,
    _format_need_posts_compact,
)
from prompts import (
    CLUSTERING_PROMPT, CLUSTERING_STEP1_PROMPT, CLUSTERING_STEP2_PROMPT,
    SEARCH_PLANNING_PROMPT, POST_FILTER_PROMPT,
    BATCH_RELEVANCE_PROMPT, DEEP_MINING_QUERY_PROMPT, AUTO_DISCOVER_PROMPT,
    DIRECT_REPORT_PROMPT, SIGNAL_EXTRACTION_PROMPT,
    QUICK_RELEVANCE_PROMPT,
    POC_EVAL_PROMPT,
)
from web_search import (
    search_competitors,
    discover_reddit_urls,
    discover_hn_urls,
    gpt_discover_reddit_urls,
    claude_discover_reddit_urls,
    investor_competitor_web_context,
)
from st_client import check_available as st_check_available

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data" / "reports"
CACHE_DIR = ROOT / "data" / "cache"
DATA_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)

def _safe_json_write(path: Path, data, **kwargs):
    """原子写入 JSON：先写 .tmp 再 rename，防止 crash 损坏文件。"""
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, **kwargs)
    tmp.replace(path)

# ---- 全局统计计数器（只增不减，持久化到磁盘） ----
_GLOBAL_STATS_FILE = ROOT / "data" / "global_stats.json"
_global_stats_lock = threading.Lock()

def _increment_global_needs(count: int):
    """挖掘完成时调用，把本次新增的需求卡片数累加到全局计数器。"""
    if count <= 0:
        return
    with _global_stats_lock:
        stats = {"total_needs": 0}
        if _GLOBAL_STATS_FILE.exists():
            try:
                stats = json.loads(_GLOBAL_STATS_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        stats["total_needs"] = stats.get("total_needs", 0) + count
        _safe_json_write(_GLOBAL_STATS_FILE, stats)

def _read_global_needs_count() -> int:
    if _GLOBAL_STATS_FILE.exists():
        try:
            return json.loads(_GLOBAL_STATS_FILE.read_text(encoding="utf-8")).get("total_needs", 0)
        except Exception:
            pass
    return 0

# 启动时清理过期 session，并启动定期清理定时器
cleanup_expired_sessions()


def _schedule_session_cleanup():
    """每 10 分钟执行一次过期 session 清理。"""
    cleanup_expired_sessions()
    _timer = threading.Timer(600, _schedule_session_cleanup)
    _timer.daemon = True
    _timer.start()


_cleanup_timer = threading.Timer(600, _schedule_session_cleanup)
_cleanup_timer.daemon = True
_cleanup_timer.start()


def _check_cli_available(sources: list[str]) -> tuple[bool, str]:
    """预检数据源 CLI 可用性（rdt-cli / st-cli），返回 (ok, err_msg)。"""
    import asyncio as _aio

    if "reddit" in sources:
        loop = _aio.new_event_loop()
        try:
            from rdt_client import get_reddit_fetcher
            fetcher = get_reddit_fetcher()
            info = loop.run_until_complete(fetcher.rdt.check_available())
            if not (info.get("installed") and info.get("authenticated")):
                err = info.get("error", "")
                if not info.get("installed"):
                    return False, "rdt-cli 未安装，请联系管理员配置"
                return False, f"rdt-cli 未认证：{err}" if err else "rdt-cli 未认证，请联系管理员执行 rdt login"
        except Exception as e:
            return False, f"rdt-cli 检测失败：{str(e)[:80]}"
        finally:
            loop.close()

    return True, ""


def _check_web_search_available(ctx: SessionContext) -> tuple[bool, str]:
    """检测当前选择的 WebSearch 引擎是否已配置且工具可用。"""
    import os
    engine = ctx.web_search_engine
    if engine == "tavily":
        key = ctx._runtime_config.get("TAVILY_API_KEY") or os.getenv("TAVILY_API_KEY", "")
        if not key:
            return False, "Tavily API Key 未配置，请前往「设置」填写，或切换 WebSearch 为 GPT"
        try:
            from web_search import _get_tavily_client
            client = _get_tavily_client()
            r = client.search(query="test", search_depth="basic", max_results=1, include_answer=False)
            if r is None or r.get("results") is None:
                return False, "Tavily API Key 无效或已过期，请前往「设置」检查，或切换 WebSearch 为 GPT"
        except ValueError:
            return False, "Tavily API Key 未配置，请前往「设置」填写，或切换 WebSearch 为 GPT"
        except Exception as e:
            return False, f"Tavily 连接失败（{str(e)[:60]}），请检查 Key 或切换 WebSearch 为 GPT"
    elif engine in ("gpt", "claude"):
        label = "GPT" if engine == "gpt" else "Claude"
        prefix = "GPT" if engine == "gpt" else "CLAUDE"
        alt = "GPT 或 Tavily" if engine == "claude" else "Claude 或 Tavily"
        cfg = ctx.get_config(prefix)
        if not cfg.get("api_key"):
            return False, f"{label} API Key 未配置，请前往「设置」填写，或切换 WebSearch 为 {alt}"
        try:
            from openai import OpenAI
            from web_search import _test_web_search_support
            client = OpenAI(base_url=cfg["base_url"], api_key=cfg["api_key"])
            supported = _test_web_search_support(client, cfg["model"], label)
            if not supported:
                return False, f"{label} 中转站不支持联网搜索，请切换 WebSearch 为 {alt}"
        except Exception as e:
            return False, f"{label} WebSearch 不可用（{str(e)[:60]}），请切换为 {alt}"
    return True, ""


def _get_session(request: Request) -> SessionContext:
    """从请求 header 中提取 session_id 并获取对应的 SessionContext。"""
    sid = request.headers.get("x-session-id", "default")
    ctx = get_session(sid)
    set_thread_session(ctx)
    return ctx


def _normalize_need_dict(n: object) -> dict:
    """保证每条 need 含 posts 列表，避免不完整缓存导致前端崩溃。"""
    if not isinstance(n, dict):
        return {"need_title": "未命名需求", "need_description": "", "posts": []}
    posts = n.get("posts")
    if not isinstance(posts, list):
        posts = []
    out = dict(n)
    out["need_title"] = str(out.get("need_title") or "未命名需求")
    out["need_description"] = str(out.get("need_description") or "")
    out["posts"] = posts
    return out


def _normalize_needs_list(raw: object) -> list[dict]:
    if not isinstance(raw, list):
        return []
    return [_normalize_need_dict(x) for x in raw]

ENV_PATH = ROOT / ".env"


def _safe_path(base_dir: Path, filename: str) -> Path:
    """校验文件路径不超出 base_dir，防止 ../ 穿越攻击。"""
    resolved = (base_dir / filename).resolve()
    if not str(resolved).startswith(str(base_dir.resolve())):
        raise HTTPException(status_code=400, detail="非法文件名")
    return resolved


router = APIRouter(prefix="/api")


def _friendly_error(e: Exception) -> str:
    """Convert raw API exceptions to user-friendly Chinese messages."""
    msg = str(e)
    low = msg.lower()
    if "429" in msg or "rate" in low or "cooldown" in low or "cooling" in low:
        return "请求太频繁，请等 1-2 分钟再试"
    if "503" in msg or "no available" in low or "service unavailable" in low:
        return "模型额度暂时不足，请等几分钟再试"
    if "403" in msg or "no access" in low:
        return "模型访问被拒，请前往「设置」检查 API Key 权限"
    if "401" in msg or "unauthorized" in low:
        return "API Key 无效，请前往「设置」更新"
    if "timeout" in low or "timed out" in low:
        return "模型响应超时，请再试一次"
    if "connection" in low:
        return "网络连接失败，请检查网络或 API 地址配置"
    if "stream" in low or "codex" in low:
        return "模型输出中断，请重试。反复失败请前往「设置」检查中转站状态"
    if "500" in msg or "server" in low or "internal" in low:
        return "模型服务异常，请稍后重试"
    return f"出错了：{msg[:120]}"


def _log_sse_error(tag: str, e: Exception, ctx: "SessionContext | None" = None):
    """统一 SSE 流异常日志：打印 tag、session_id、堆栈。"""
    import traceback as _tb
    sid = ctx.session_id if ctx else "?"
    print(f"[{tag}] ERROR session={sid}: {e}\n{_tb.format_exc()}")


# ============================================================
# Clustering: group posts into needs
# ============================================================

def _fix_unescaped_quotes(s: str) -> str:
    """修复 LLM 在 JSON 字符串值中输出的未转义 ASCII 双引号。

    例如 "名为"大都会"的" → "名为「大都会」的"
    策略：逐字符扫描，跟踪是否在字符串值内部，
    遇到字符串内部不该出现的裸引号时替换为中文书名号。
    """
    result = []
    i = 0
    in_string = False
    n = len(s)
    while i < n:
        ch = s[i]
        if ch == '\\' and in_string and i + 1 < n:
            result.append(ch)
            result.append(s[i + 1])
            i += 2
            continue
        if ch == '"':
            if not in_string:
                in_string = True
                result.append(ch)
            else:
                after = s[i + 1:i + 10].lstrip() if i + 1 < n else ""
                if after and after[0] in (',', '}', ']', ':'):
                    in_string = False
                    result.append(ch)
                elif i + 1 >= n:
                    result.append(ch)
                else:
                    result.append('「')
                    j = s.find('"', i + 1)
                    if j != -1 and j < i + 60:
                        result.append(s[i + 1:j])
                        result.append('」')
                        i = j + 1
                        continue
                    else:
                        pass
        else:
            result.append(ch)
        i += 1
    return ''.join(result)


def _parse_json_from_text(text: str):
    """Extract JSON from LLM response text, with truncation-aware repair."""
    if not text:
        return None
    text = text.strip()
    import re
    # 先剥离 <think>/<thinking> 标签，避免标签内的 [ ] { } 干扰 JSON 定位
    text = re.sub(r'<think(?:ing)?[\s\S]*?</think(?:ing)?>', '', text, flags=re.IGNORECASE).strip()
    # 处理未闭合的 <think> 标签（模型输出被截断时可能只有开头没有结尾）
    text = re.sub(r'<think(?:ing)?[^>]*>[\s\S]*$', '', text, flags=re.IGNORECASE).strip() if '<think' in text.lower() else text
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r'```(?:json)?\s*\n?([\s\S]*?)\n?\s*```', text)
    if m:
        inner = m.group(1).strip()
        try:
            return json.loads(inner)
        except json.JSONDecodeError:
            fixed = _fix_unescaped_quotes(inner)
            try:
                return json.loads(fixed)
            except Exception:
                pass
            repaired = _repair_truncated_json(inner)
            if repaired is not None:
                return repaired
    first_bracket = text.find('[')
    last_bracket = text.rfind(']')
    if first_bracket != -1 and last_bracket > first_bracket:
        sub = text[first_bracket:last_bracket + 1]
        try:
            return json.loads(sub)
        except json.JSONDecodeError:
            fixed = _fix_unescaped_quotes(sub)
            try:
                return json.loads(fixed)
            except Exception:
                pass
    first_brace = text.find('{')
    last_brace = text.rfind('}')
    if first_brace != -1 and last_brace > first_brace:
        sub = text[first_brace:last_brace + 1]
        try:
            return json.loads(sub)
        except json.JSONDecodeError:
            fixed = _fix_unescaped_quotes(sub)
            try:
                return json.loads(fixed)
            except Exception:
                pass
    start = first_bracket if first_bracket != -1 else first_brace
    if start != -1:
        repaired = _repair_truncated_json(text[start:])
        if repaired is not None:
            return repaired
    return None


def _build_research_context(req: "FetchRequest") -> str:
    """Build extra context block from optional research parameters."""
    parts: list[str] = []
    period_labels = {"month": "过去1个月", "3months": "过去3个月", "6months": "过去6个月", "9months": "过去9个月"}
    parts.append(f"时间范围：{period_labels.get(req.time_period, '过去6个月')}")
    if req.product:
        parts.append(f"现有产品：{req.product}")
    if req.market:
        parts.append(f"目标市场：{req.market}")
    if req.demographics:
        parts.append(f"目标用户画像：{req.demographics}")
    if req.segment:
        parts.append(f"用户行为/情境细分：{req.segment}")
    if req.competitors:
        parts.append(f"已知竞品：{req.competitors}")
    if req.pain_points and req.pain_points != 10:
        parts.append(f"目标痛点数量：{req.pain_points}")
    if not parts:
        return ""
    return "## 研究参数\n" + "\n".join(f"- {p}" for p in parts)


def _plan_search(user_input: str, req: "FetchRequest | None" = None) -> dict | None:
    """Ask Claude to generate search queries and subreddits from user input.

    新版返回结构支持四分类搜索矩阵（向后兼容 search_queries 字段）。
    """
    research_context = _build_research_context(req) if req else ""
    prompt_text = SEARCH_PLANNING_PROMPT.format(
        user_input=user_input,
        research_context=research_context,
    )
    messages = [{"role": "user", "content": prompt_text}]
    try:
        response = call_llm(messages)
        result = _parse_json_from_text(response)
        if result and isinstance(result, dict):
            # 四分类合并为统一 search_queries（按优先级排序：痛点 > 方案 > 竞品 > 平台）
            if "problem_queries" in result and "search_queries" not in result:
                merged = []
                merged.extend(result.get("problem_queries", []))
                merged.extend(result.get("solution_queries", []))
                merged.extend(result.get("competitor_queries", []))
                merged.extend(result.get("platform_queries", []))
                result["search_queries"] = merged
            print(f"[SearchPlan] queries={len(result.get('search_queries', []))}, "
                  f"discovery={len(result.get('discovery_queries', []))}, "
                  f"subreddits={result.get('subreddits')}, "
                  f"competitors={result.get('known_competitors', [])}")
            return result
    except Exception as e:
        print(f"[SearchPlan] LLM call failed: {e}")
    return None


def _quick_relevance_check(posts: list[dict], topic: str) -> bool:
    """快速检查一批搜索结果的相关性：取 top 5 标题，>=3 跑题则丢弃整批。

    Returns: True = 保留, False = 丢弃整批
    """
    if len(posts) < 3:
        return True

    top5 = sorted(posts, key=lambda p: p.get("score", 0), reverse=True)[:5]
    titles_text = "\n".join(f"{i+1}. {p.get('title', '(无标题)')}" for i, p in enumerate(top5))

    prompt = QUICK_RELEVANCE_PROMPT.format(topic=topic, titles_text=titles_text)
    try:
        resp = call_llm([{"role": "user", "content": prompt}])
        result = _parse_json_from_text(resp)
        if result and isinstance(result, dict):
            verdict = result.get("verdict", "keep")
            off_topic = result.get("off_topic_count", 0)
            reason = result.get("reason", "")
            print(f"[QuickCheck] off_topic={off_topic}, verdict={verdict}: {reason}")
            return verdict != "discard"
    except Exception as e:
        print(f"[QuickCheck] LLM error, keeping batch: {e}")
    return True


def _batch_relevance_check(posts: list[dict], topic: str) -> list[dict]:
    """Check batches of posts for relevance. Per-post granularity with content."""
    if len(posts) <= 3:
        return posts

    kept: list[dict] = []
    batch_size = 8

    for i in range(0, len(posts), batch_size):
        batch = posts[i:i + batch_size]
        titles = [
            {"idx": j, "title": p["title"], "snippet": (p.get("content", "") or "")[:150]}
            for j, p in enumerate(batch)
        ]
        prompt = BATCH_RELEVANCE_PROMPT.format(
            topic=topic,
            titles_json=json.dumps(titles, ensure_ascii=False),
        )
        try:
            resp = call_llm([{"role": "user", "content": prompt}])
            result = _parse_json_from_text(resp)
            if result and isinstance(result, dict):
                keep_indices = set(result.get("keep_indices", []))
                discard_indices = set(result.get("discard_indices", []))
                if keep_indices:
                    for j, p in enumerate(batch):
                        if j in keep_indices:
                            kept.append(p)
                    discarded = len(discard_indices)
                    if discarded:
                        print(f"[BatchCheck] batch {i//batch_size+1}: kept {len(keep_indices)}, discarded {discarded}: {result.get('reason', '')}")
                    continue
        except Exception as e:
            print(f"[BatchCheck] LLM error, keeping batch: {e}")

        kept.extend(batch)

    print(f"[BatchCheck] {len(posts)} → {len(kept)} posts after relevance check")
    return kept


def _filter_posts(posts: list[dict], topic: str = "") -> list[dict]:
    """Ask Claude to filter out posts with no product opportunity."""
    if len(posts) <= 3:
        return posts

    posts_summary = []
    for i, p in enumerate(posts):
        posts_summary.append({
            "idx": i,
            "title": p["title"],
            "content": (p.get("content", "") or "")[:600],
            "score": p.get("score", 0),
            "num_comments": p.get("num_comments", 0),
            "top_comments": [c[:200] for c in p.get("comments", [])[:5]],
        })

    prompt_text = POST_FILTER_PROMPT.format(
        topic=topic or "（未指定）",
        posts_json=json.dumps(posts_summary, ensure_ascii=False, indent=2),
    )
    messages = [{"role": "user", "content": prompt_text}]

    try:
        response = call_llm(messages, max_tokens=4096)
        result = _parse_json_from_text(response)
        if result and isinstance(result, dict):
            keep = result.get("keep_indices", [])
            removed = result.get("removed_reasons", {})
            if keep:
                filtered = [posts[i] for i in keep if 0 <= i < len(posts)]
                print(f"[Filter] {len(posts)} → {len(filtered)} posts. "
                      f"Removed {len(removed)}: {list(removed.values())[:3]}")
                return filtered if filtered else posts
    except Exception as e:
        print(f"[Filter] LLM call failed: {e}")
    return posts


def _repair_truncated_json(text: str):
    """Try to salvage a truncated JSON array by closing open braces/brackets."""
    import re
    text = text.strip()
    if not text.startswith("["):
        start = text.find("[")
        if start == -1:
            return None
        text = text[start:]

    opens = 0
    open_sq = 0
    in_str = False
    escape = False
    for ch in text:
        if escape:
            escape = False
            continue
        if ch == '\\':
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == '{':
            opens += 1
        elif ch == '}':
            opens -= 1
        elif ch == '[':
            open_sq += 1
        elif ch == ']':
            open_sq -= 1

    if opens == 0 and open_sq == 0:
        try:
            return json.loads(text)
        except Exception:
            return None

    patched = text.rstrip().rstrip(',')
    if in_str:
        patched += '"'
    patched += '}'  * max(opens, 0)
    patched += ']' * max(open_sq, 0)
    try:
        result = json.loads(patched)
        print(f"[Clustering] Repaired truncated JSON ({opens} braces, {open_sq} brackets, in_str={in_str})")
        return result
    except Exception:
        pass

    last_complete = text.rfind('},')
    if last_complete > 0:
        attempt = text[:last_complete + 1] + ']' * max(open_sq, 1)
        try:
            result = json.loads(attempt)
            print(f"[Clustering] Salvaged partial JSON (truncated last entry)")
            return result
        except Exception:
            pass

    last_obj_end = text.rfind('}')
    if last_obj_end > 0:
        attempt = text[:last_obj_end + 1] + ']' * max(open_sq, 1)
        try:
            result = json.loads(attempt)
            print(f"[Clustering] Salvaged JSON (cut at last complete object)")
            return result
        except Exception:
            pass

    return None


def _cluster_posts_into_needs(posts: list[dict], topic: str = "") -> list[dict]:
    """两步聚类：Step1 过滤+粗分组（只输出索引），Step2 逐组生成标题/描述（可并发）。"""
    ctx = get_thread_session()
    _emit = ctx.fetch_emit if ctx else (lambda msg, prog: None)
    _lock = ctx.fetch_lock if ctx else threading.Lock()
    _job = ctx.fetch_job if ctx else {}
    # 帖子过多时截取 top 70（按 score 排序），避免 prompt 过大
    if len(posts) > 70:
        sorted_posts = sorted(posts, key=lambda p: p.get("score", 0), reverse=True)[:70]
        idx_map = {id(sp): i for i, sp in enumerate(posts)}
        reindexed = []
        for sp in sorted_posts:
            orig_idx = idx_map.get(id(sp), 0)
            reindexed.append((orig_idx, sp))
        reindexed.sort(key=lambda x: x[0])
        working_posts = [sp for _, sp in reindexed]
        print(f"[Clustering] 帖子过多（{len(posts)}），截取 top 70 进入聚类")
    else:
        working_posts = posts

    posts_summary = []
    many_posts = len(working_posts) > 20
    for i, p in enumerate(working_posts):
        entry = {
            "idx": i,
            "title": p["title"],
            "content": (p.get("content", "") or "")[:250 if many_posts else 400],
            "score": p.get("score", 0),
            "num_comments": p.get("num_comments", 0),
        }
        comments = p.get("comments", [])
        if comments:
            entry["top_comments"] = [c[:100 if many_posts else 150] for c in comments[:2 if many_posts else 3]]
        posts_summary.append(entry)

    json_indent = None if many_posts else 2
    posts_json_str = json.dumps(posts_summary, ensure_ascii=False, indent=json_indent)

    # ── Step 1: 过滤 + 粗分组（只输出索引，JSON 极小，几乎不会解析失败） ──
    _emit("分析帖子关联性，过滤 + 粗分组...", 80)
    step1_prompt = CLUSTERING_STEP1_PROMPT.format(
        topic=topic or "（未指定）",
        posts_json=posts_json_str,
    )
    step1_messages = [
        {"role": "system", "content": "直接输出纯 JSON 对象，不要添加代码块标记或多余文字。"},
        {"role": "user", "content": step1_prompt},
    ]
    print(f"[Clustering Step1] {len(working_posts)} posts, prompt ~{len(step1_prompt)} chars")

    grouping = None
    for attempt in range(3):
        try:
            response = call_llm(step1_messages, max_tokens=2048)
            grouping = _parse_json_from_text(response)
            if grouping and isinstance(grouping, dict) and "groups" in grouping:
                break
            print(f"[Clustering Step1] attempt {attempt+1} 格式不对: {str(response)[:200]}")
            if attempt < 2:
                _emit(f"分组解析失败，正在重试（{attempt+1}/3）...", 82)
            grouping = None
        except Exception as e:
            print(f"[Clustering Step1] attempt {attempt+1} failed: {e}")
            if attempt < 2:
                _emit(f"分组模型调用失败，正在重试...", 82)
                import time; time.sleep(2)

    if not grouping or not isinstance(grouping.get("groups"), list):
        _emit("分组未成功，尝试轻量聚类...", 86)
        with _lock:
            _job["clustering_fallback"] = True
        return _fallback_needs(posts, topic=topic)

    groups = grouping["groups"]
    skipped = set(grouping.get("skipped", []))
    valid_groups = [g for g in groups if isinstance(g, list) and len(g) > 0]
    print(f"[Clustering Step1] 完成：{len(valid_groups)} 组，跳过 {len(skipped)} 帖子")

    if not valid_groups:
        with _lock:
            _job["clustering_fallback"] = True
        return _fallback_needs(posts, topic=topic)

    # ── Step 2: 逐组生成标题/描述/翻译（并发调用） ──
    _emit(f"为 {len(valid_groups)} 个需求组生成标题和描述...", 85)

    import concurrent.futures

    def _name_one_group(group_indices: list[int]) -> dict | None:
        """为一个组生成 need_title / need_description / title_translations。"""
        if ctx:
            set_thread_session(ctx)
        group_posts = []
        for idx in group_indices:
            if 0 <= idx < len(working_posts):
                p = working_posts[idx]
                group_posts.append({
                    "idx": idx,
                    "title": p["title"],
                    "content": (p.get("content", "") or "")[:500],
                    "score": p.get("score", 0),
                    "num_comments": p.get("num_comments", 0),
                    "top_comments": [c[:200] for c in p.get("comments", [])[:3]],
                })
        if not group_posts:
            return None
        prompt = CLUSTERING_STEP2_PROMPT.format(
            topic=topic or "（未指定）",
            group_posts_json=json.dumps(group_posts, ensure_ascii=False, indent=2),
        )
        msgs = [
            {"role": "system", "content": "直接输出纯 JSON 对象，不要添加代码块标记或多余文字。"},
            {"role": "user", "content": prompt},
        ]
        for att in range(2):
            try:
                resp = call_llm(msgs, max_tokens=2048)
                result = _parse_json_from_text(resp)
                if result and isinstance(result, dict) and "need_title" in result:
                    result["_indices"] = group_indices
                    return result
            except Exception as e:
                print(f"[Clustering Step2] group {group_indices[:3]}... attempt {att+1} failed: {e}")
        return {"need_title": "未命名需求", "need_description": "", "title_translations": {}, "_indices": group_indices}

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(valid_groups), 4)) as pool:
        futures = [pool.submit(_name_one_group, g) for g in valid_groups]
        step2_results = [f.result() for f in futures]

    # ── 组装最终 needs ──
    needs = []
    for r in step2_results:
        if r is None:
            continue
        indices = r.get("_indices", [])
        translations = r.get("title_translations", {})
        need_posts = []
        for idx in indices:
            if 0 <= idx < len(working_posts):
                post = dict(working_posts[idx])
                post["title_zh"] = translations.get(str(idx), "")
                need_posts.append(post)
        if need_posts:
            needs.append({
                "need_title": r.get("need_title", "未命名需求"),
                "need_description": r.get("need_description", ""),
                "posts": need_posts,
                "total_score": sum(p.get("score", 0) for p in need_posts),
                "total_comments": sum(p.get("num_comments", 0) for p in need_posts),
            })

    if not needs:
        with _lock:
            _job["clustering_fallback"] = True
        return _fallback_needs(posts, topic=topic)
    print(f"[Clustering] 两步聚类完成：{len(needs)} 个需求组")
    return needs


def _fallback_needs(posts: list[dict], topic: str = "") -> list[dict]:
    """Fallback: 聚类失败时用更简化的 prompt 做轻量聚类，避免机械分组。"""
    if not posts:
        return []
    valid = [p for p in posts if p.get("title")]
    if not valid:
        return []

    titles_block = "\n".join(f"{i}: {p['title']}" for i, p in enumerate(valid))
    fallback_prompt = (
        f"将以下帖子标题按语义分成 3-6 组，围绕研究主题「{topic or '未指定'}」聚类。\n"
        "输出 JSON（不加代码块标记），格式：\n"
        '[{"need_title":"中文需求名(5-15字)","need_description":"中文描述(1-2句)","indices":[0,1,3],'
        '"translations":{"0":"标题中文翻译","1":"标题中文翻译"}}]\n\n'
        f"帖子列表：\n{titles_block}"
    )
    try:
        resp = call_llm(
            [{"role": "system", "content": "你是需求分析专家，直接输出纯 JSON 数组。"},
             {"role": "user", "content": fallback_prompt}],
            max_tokens=4096,
        )
        groups = _parse_json_from_text(resp)
        if groups and isinstance(groups, list) and len(groups) >= 1:
            needs = []
            for g in groups:
                indices = g.get("indices", [])
                translations = g.get("translations", {})
                chunk = []
                for idx in indices:
                    if 0 <= idx < len(valid):
                        post = dict(valid[idx])
                        post["title_zh"] = translations.get(str(idx), "")
                        chunk.append(post)
                if chunk:
                    needs.append({
                        "need_title": g.get("need_title", "未命名需求"),
                        "need_description": g.get("need_description", ""),
                        "posts": chunk,
                        "total_score": sum(p.get("score", 0) for p in chunk),
                        "total_comments": sum(p.get("num_comments", 0) for p in chunk),
                    })
            if needs:
                print(f"[Fallback] 轻量聚类成功: {len(needs)} 组")
                return needs
    except Exception as e:
        print(f"[Fallback] 轻量聚类也失败: {e}")

    # 最终兜底：翻译标题后按热度分组
    print("[Fallback] 使用最终兜底（按热度分组）")
    translations_map: dict[int, str] = {}
    try:
        t_prompt = (
            "将以下英文帖子标题翻译为中文，输出 JSON（不加代码块标记）。"
            '格式：{"0": "中文翻译", "1": "中文翻译", ...}\n\n'
            + "\n".join(f"{i}: {t}" for i, t in enumerate([p["title"] for p in valid]))
        )
        t_resp = call_llm([{"role": "user", "content": t_prompt}])
        parsed = _parse_json_from_text(t_resp)
        if parsed and isinstance(parsed, dict):
            translations_map = {int(k): v for k, v in parsed.items()}
    except Exception as e:
        print(f"[Fallback] Translation failed: {e}")

    for i, p in enumerate(valid):
        p["title_zh"] = translations_map.get(i, "")

    chunk_size = max(len(valid) // 5, 3)
    needs = []
    for start in range(0, len(valid), chunk_size):
        chunk = valid[start:start + chunk_size]
        if not chunk:
            continue
        top_title = chunk[0].get("title_zh") or chunk[0]["title"]
        if len(top_title) > 20:
            top_title = top_title[:20] + "…"
        needs.append({
            "need_title": f"{top_title} 等相关需求",
            "need_description": f"包含 {len(chunk)} 个相关帖子，按热度排序（聚类降级模式）",
            "posts": chunk,
            "total_score": sum(p.get("score", 0) for p in chunk),
            "total_comments": sum(p.get("num_comments", 0) for p in chunk),
        })
    return needs


# ============================================================
# Debate state — now per-session via SessionContext
# ============================================================
# ctx.debate_state, _save_debate_cache, _load_debate_cache, _reset_debate
# are replaced by ctx.debate_state, ctx.save_debate_cache(), ctx.reset_debate()

# ============================================================
# SSE helpers
# ============================================================

def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _provider_for_role(role: str, ctx: SessionContext | None = None) -> str:
    """返回角色对应的模型提供商（claude/gpt）。"""
    if ctx:
        return ctx.get_role_model_config().get(role, "claude")
    thread_ctx = get_thread_session()
    if thread_ctx:
        return thread_ctx.get_role_model_config().get(role, "claude")
    return get_role_model_config().get(role, "claude")

# ============================================================
# Pydantic models
# ============================================================

class FetchRequest(BaseModel):
    mode: str = "open"           # "sentence" | "keywords" | "open"
    query: str = ""              # for sentence mode
    keywords: list[str] = []     # for keywords mode
    sources: list[str] = ["hackernews"]  # ["hackernews", "reddit"]
    category: str = "top"        # for open mode HN category
    reddit_categories: list[str] = []  # selected reddit board categories
    limit: int = 70
    time_period: str = "6months"  # "month" | "3months" | "6months" | "9months"
    product: str = ""             # existing product name (optional)
    market: str = ""              # target market/region (optional)
    demographics: str = ""        # target user demographics (optional)
    segment: str = ""             # behavioral/situational segment (optional)
    pain_points: int = 10         # number of pain points to deep-dive
    competitors: str = ""         # known competitors, comma-separated (optional)
    demo: bool = False             # demo mode: use cached data, fake progress


class ConfigSaveRequest(BaseModel):
    CLAUDE_BASE_URL: str = ""
    CLAUDE_API_KEY: str = ""
    CLAUDE_MODEL: str = ""
    GPT_BASE_URL: str = ""
    GPT_API_KEY: str = ""
    GPT_MODEL: str = ""
    TAVILY_API_KEY: str = ""
    FEISHU_APP_ID: str = ""
    FEISHU_APP_SECRET: str = ""


class TestConnectionRequest(BaseModel):
    prefix: str
    base_url: str = ""
    api_key: str = ""
    model: str = ""


class StartDebateRequest(BaseModel):
    need_index: int
    max_rounds: int = 5
    demo: bool = False


class StartFreeDebateRequest(BaseModel):
    user_input: str
    max_rounds: int = 5


class HumanMessageRequest(BaseModel):
    text: str
    target: str = "analyst"  # "analyst" | "critic"


class TranslateRequest(BaseModel):
    text: str

# ============================================================
# Config routes
# ============================================================

@router.get("/config/status")
def config_status(request: Request):
    ctx = _get_session(request)
    return ctx.check_config()


@router.get("/config/values")
def config_values(request: Request):
    ctx = _get_session(request)
    return ctx.get_config_values()


@router.post("/config")
def save_config(req: ConfigSaveRequest, request: Request):
    ctx = _get_session(request)
    config = req.model_dump()
    ctx.save_config(config)
    return {"ok": True}


@router.post("/config/test")
def test_config(req: TestConnectionRequest, request: Request):
    ctx = _get_session(request)
    override = {}
    if req.base_url:
        override["base_url"] = req.base_url
    if req.api_key:
        override["api_key"] = req.api_key
    if req.model:
        override["model"] = req.model
    ok, msg = ctx.test_connection(req.prefix, override=override)
    return {"ok": ok, "message": msg}


@router.get("/config/role-models")
def get_role_models(request: Request):
    ctx = _get_session(request)
    return ctx.get_role_model_config()


@router.post("/config/role-models")
def save_role_models(mapping: dict, request: Request):
    ctx = _get_session(request)
    ctx.set_role_model_config(mapping)
    return {"ok": True}


@router.get("/config/general-model")
def get_general_model_api(request: Request):
    ctx = _get_session(request)
    return {"model": ctx.get_general_model()}


@router.post("/config/general-model")
def save_general_model_api(body: dict, request: Request):
    ctx = _get_session(request)
    model = body.get("model", "claude")
    ctx.set_general_model(model)
    return {"ok": True}


@router.get("/config/usage")
def get_service_usage(request: Request):
    ctx = _get_session(request)
    import httpx

    result: dict[str, dict] = {}

    for prefix, label in [("CLAUDE", "claude"), ("GPT", "gpt")]:
        cfg = ctx.get_config(prefix)
        base_url = cfg["base_url"]
        api_key = cfg["api_key"]
        if not base_url or not api_key:
            continue
        base = base_url.rstrip("/")
        billing_urls = [
            f"{base}/dashboard/billing/credit_grants",
            f"{base}/v1/dashboard/billing/credit_grants",
        ]
        for url in billing_urls:
            try:
                resp = httpx.get(
                    url,
                    headers={"Authorization": f"Bearer {api_key}"},
                    timeout=8,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    balance = data.get("total_available", data.get("balance", None))
                    if balance is not None:
                        result[label] = {"balance_usd": round(float(balance), 2)}
                        break
                    total_granted = data.get("total_granted", 0)
                    total_used = data.get("total_used", 0)
                    if total_granted:
                        result[label] = {"balance_usd": round(float(total_granted) - float(total_used), 2)}
                        break
            except Exception:
                pass

    return result


@router.get("/config/token-stats")
def get_token_stats_route(request: Request):
    ctx = _get_session(request)
    return ctx.get_token_stats()


@router.post("/config/token-stats/reset")
def reset_token_stats_route(request: Request):
    ctx = _get_session(request)
    ctx.reset_token_stats()
    return {"ok": True}


@router.get("/config/role-names")
def get_role_names(request: Request):
    ctx = _get_session(request)
    return ctx.role_names


@router.post("/config/role-names")
def save_role_names(mapping: dict, request: Request):
    ctx = _get_session(request)
    ctx.save_role_names(mapping)
    return {"ok": True, "role_names": ctx.role_names}


@router.get("/reddit-categories")
def reddit_categories():
    return {"categories": REDDIT_CATEGORIES}

# ============================================================
# Fetch routes (returns needs)
# ============================================================

def _run_fetch_job(ctx: SessionContext, req_dict: dict):
    """Run the fetch job in a background thread. All progress goes to ctx.fetch_job."""
    import asyncio as _aio
    from web_search import reset_tavily_counter, get_tavily_credit_count

    set_thread_session(ctx)

    _loop = _aio.new_event_loop()
    _aio.set_event_loop(_loop)
    def _run(coro):
        return _loop.run_until_complete(coro)

    req = FetchRequest(**req_dict)
    reset_tavily_counter()

    # ===== 预检：LLM 可用性 =====
    if not req.demo:
        ctx.fetch_emit("正在检测模型可用性...", 1)
        llm_ok, llm_err = check_llm_available()
        if not llm_ok:
            model_name = "GPT" if ctx._general_model == "gpt" else "Claude"
            err_msg = f"{model_name} 模型不可用，请前往「设置」检查配置"
            ctx.fetch_emit(err_msg, 100)
            with ctx.fetch_lock:
                ctx.fetch_job["error"] = err_msg
                ctx.fetch_job["active"] = False
            return

        # ===== 预检：数据源 CLI 可用性 =====
        if "reddit" in req.sources:
            ctx.fetch_emit("正在检测 rdt-cli 连接状态...", 1)
            cli_ok, cli_err = _check_cli_available(req.sources)
            if not cli_ok:
                ctx.fetch_emit(cli_err, 100)
                with ctx.fetch_lock:
                    ctx.fetch_job["error"] = cli_err
                    ctx.fetch_job["active"] = False
                return

        # ===== 预检：WebSearch 引擎可用性 =====
        ws_engine = ctx.web_search_engine
        ws_label = {"gpt": "GPT", "tavily": "Tavily", "claude": "Claude"}.get(ws_engine, ws_engine)
        ctx.fetch_emit(f"正在检测 WebSearch（{ws_label}）...", 1)
        ws_ok, ws_err = _check_web_search_available(ctx)
        if not ws_ok:
            ctx.fetch_emit(ws_err, 100)
            with ctx.fetch_lock:
                ctx.fetch_job["error"] = ws_err
                ctx.fetch_job["active"] = False
            return

    _t_total_start = _time.time()
    _timing: dict[str, float] = {}

    def _t_start(phase: str):
        _timing[f"_{phase}_start"] = _time.time()

    def _t_end(phase: str):
        start_key = f"_{phase}_start"
        if start_key in _timing:
            _timing[phase] = round(_time.time() - _timing[start_key], 1)
            del _timing[start_key]

    try:
        # ===== 演示模式：读缓存 + 模拟进度 =====
        if req.demo:
            import time as _time_mod
            _DEMO_DIR = ROOT / "data" / "demo"
            _demo_needs_path = _DEMO_DIR / "demo_needs.json"
            if not _demo_needs_path.exists():
                ctx.fetch_emit("演示数据不存在，请先准备 data/demo/demo_needs.json", 100)
                with ctx.fetch_lock:
                    ctx.fetch_job["error"] = "演示数据文件不存在"
                return

            with open(_demo_needs_path, "r", encoding="utf-8") as f:
                demo_needs = json.load(f)

            _DEMO_STEPS = [
                ("初始化需求识别引擎...", 2, 0.8),
                ("加载痛点检测模型...", 5, 0.7),
                ("建立社区数据管线...", 8, 0.6),
                ("校准信号过滤阈值...", 10, 0.6),
                ("规划搜索策略...", 14, 1.2),
                ("搜索策略：聚焦海量照片整理、回忆管理和智能检索的用户痛点", 18, 0.8),
                ("锁定 10 条搜索词、5 个社区", 22, 0.6),
                ("启动多源采集调度器...", 25, 0.8),
                ("GPT WebSearch 正在发现高质量 Reddit 帖子...", 30, 1.5),
                ("WebSearch 发现 8 个帖子，rdt read 并发提取全文+评论...", 35, 1.2),
                ("已提取 8/8 个帖子全文", 40, 0.8),
                ("正在补充搜索 Reddit...", 45, 1.2),
                ("Reddit 搜索完成：共 22 个帖子", 50, 0.6),
                ("正在补充搜索 HackerNews...", 55, 1.0),
                ("语义去重与排序...", 60, 0.8),
                ("开始质量筛选（30 个帖子）...", 65, 0.8),
                ("硬性门槛过滤：30 → 14 个帖子", 70, 0.6),
                ("并发拉取 10 个帖子的深层评论...", 75, 1.2),
                ("Lumon 正在统一筛选帖子（相关性 + 产品机会）...", 80, 1.0),
                ("质量筛选完成：14 → 8 个有效帖子", 83, 0.6),
                ("分析帖子关联性...", 86, 0.8),
                ("聚类为需求主题...", 90, 1.2),
                (f"产出 {len(demo_needs)} 个需求主题，整理结构...", 94, 0.8),
                ("评估产品机会...", 97, 0.6),
            ]
            for msg, prog, delay in _DEMO_STEPS:
                if ctx.fetch_is_stopped(): return
                ctx.fetch_emit(msg, prog)
                _time_mod.sleep(delay)

            total_posts = sum(len(n.get("posts", [])) for n in demo_needs)
            ctx.fetch_emit(f"挖掘完成！发现 {len(demo_needs)} 个需求主题，共 {total_posts} 个帖子", 100)
            ctx.fetch_emit("⏱ 总用时 20s — 演示模式", 100)

            _safe_json_write(ctx.needs_cache, demo_needs, indent=2)
            _increment_global_needs(len(demo_needs))
            ctx.reset_debate()

            with ctx.fetch_lock:
                ctx.fetch_job["needs"] = demo_needs
                ctx.fetch_job["timing"] = {"total": 10.0, "phases": {}}
            return

        all_posts: list[dict] = []
        source_names = {"hackernews": "HackerNews", "reddit": "Reddit"}

        fetcher = get_reddit_fetcher()
        try:
            engine_info = _run(init_reddit_fetcher())
            detected_engine = engine_info.get("engine", "unknown")
            print(f"[Fetch] init_reddit_fetcher result: {engine_info}")
        except Exception as e:
            import traceback as _tb_init
            print(f"[Fetch] init_reddit_fetcher EXCEPTION: {e}\n{_tb_init.format_exc()}")
            detected_engine = "unknown"

        engine_name = detected_engine if detected_engine != "unknown" else "rdt-cli"

        if engine_name == "rdt-cli":
            ctx.fetch_emit("Reddit 引擎: rdt-cli", 2)
        elif engine_name == "none":
            ctx.fetch_emit("Reddit 引擎: rdt-cli 未认证，请在设置 > CLI 连接中检查", 2)
        else:
            ctx.fetch_emit(f"Reddit 引擎: {engine_name}（未知状态）", 2)

        fetcher._active_engine = engine_name

        with ctx.fetch_lock:
            ctx.fetch_job["engine"] = engine_name

        if ctx.fetch_is_stopped(): return

        import time as _time_mod
        _TICK = 0.4

        def _emit_slow(msg: str, prog: int, delay: float = _TICK):
            if ctx.fetch_is_stopped(): return
            ctx.fetch_emit(msg, prog)
            _time_mod.sleep(delay)

        _emit_slow("初始化需求识别引擎...", 2)
        _emit_slow("加载痛点检测模型...", 3)
        _emit_slow("建立社区数据管线...", 3)
        _emit_slow("校准信号过滤阈值...", 4)
        _emit_slow("连接数据源...", 4)

        search_queries: list[str] = []
        discovery_queries: list[str] = []
        subreddits: list[str] = []
        topic_for_check = ""

        known_competitors: list[str] = []

        if req.mode in ("sentence", "keywords"):
            user_input = req.query if req.mode == "sentence" else ", ".join(req.keywords)
            topic_for_check = user_input
            _emit_slow("规划搜索策略...", 5)
            if ctx.fetch_is_stopped(): return
            _t_start("search_planning")
            plan = _plan_search(user_input, req)
            _t_end("search_planning")
            if plan:
                search_queries = plan.get("search_queries", [])
                discovery_queries = plan.get("discovery_queries", [])
                subreddits = plan.get("subreddits", [])
                known_competitors = plan.get("known_competitors", [])
                reasoning = plan.get("reasoning", "")
                ctx.fetch_emit(f"搜索策略：{reasoning}", 10)
            else:
                search_queries = [req.query] if req.mode == "sentence" else [k.strip() for k in req.keywords if k.strip()]
                ctx.fetch_emit("使用原始输入进行搜索...", 13)

            # 用户选了 Reddit 子板块分类时，将其 subreddit 注入（优先于 LLM 自动规划）
            if req.reddit_categories:
                user_subs: list[str] = []
                for cat_key in req.reddit_categories:
                    cat = REDDIT_CATEGORIES.get(cat_key, {})
                    user_subs.extend(cat.get("subreddits", []))
                if user_subs:
                    subreddits = list(dict.fromkeys(user_subs + subreddits))
                    ctx.fetch_emit(f"使用指定板块 + LLM 推荐，共 {len(subreddits)} 个社区", 12)

            _emit_slow(f"锁定 {len(search_queries)} 条搜索词、{len(subreddits)} 个社区", 13)
            _emit_slow("启动多源采集调度器...", 14)
        else:
            ctx.fetch_emit("Lumon 正在分析高价值挖掘方向...", 5)
            if ctx.fetch_is_stopped(): return
            categories_json = json.dumps(
                {k: {"label": v["label"], "subreddits": v["subreddits"][:5]}
                 for k, v in REDDIT_CATEGORIES.items()},
                ensure_ascii=False, indent=2,
            )
            category_constraint = ""
            if req.reddit_categories:
                category_constraint = f"用户已选择以下板块：{', '.join(req.reddit_categories)}，请在这些板块范围内发现方向。"
            discover_prompt = AUTO_DISCOVER_PROMPT.format(
                categories_json=categories_json,
                category_constraint=category_constraint,
            )
            try:
                discover_resp = call_llm([{"role": "user", "content": discover_prompt}])
                discover_plan = _parse_json_from_text(discover_resp)
                if discover_plan and isinstance(discover_plan, dict):
                    directions = discover_plan.get("selected_directions", [])
                    total_reason = discover_plan.get("total_reasoning", "")
                    ctx.fetch_emit(f"发现 {len(directions)} 个方向：{total_reason}", 12)
                    for d in directions:
                        search_queries.extend(d.get("search_queries", []))
                        subreddits.extend(d.get("subreddits", []))
                        topic_for_check = d.get("direction", topic_for_check)
                    _emit_slow(f"锁定 {len(subreddits)} 个社区、{len(search_queries)} 条搜索词", 13)
                    _emit_slow("启动多源采集调度器...", 14)
                else:
                    print(f"[AutoDiscover] JSON parse failed. Raw: {discover_resp[:500]}")
                    ctx.fetch_emit("自主发现规划失败，使用热门板块浏览...", 10)
            except Exception as e:
                print(f"[AutoDiscover] LLM error: {e}")
                ctx.fetch_emit("开始开放式挖掘...", 10)

            if not search_queries and not subreddits:
                _default_open_queries = [
                    "I wish there was", "anyone built", "looking for tool",
                    "frustrated with", "need a solution",
                ]
                if req.reddit_categories:
                    for cat_key in req.reddit_categories:
                        cat = REDDIT_CATEGORIES.get(cat_key, {})
                        subreddits.extend(cat.get("subreddits", [])[:5])
                else:
                    _all_subs: list[str] = []
                    for _v in REDDIT_CATEGORIES.values():
                        _all_subs.extend(_v.get("subreddits", [])[:2])
                    subreddits = _all_subs[:20]
                search_queries = _default_open_queries
                ctx.fetch_emit(f"使用默认高价值板块（{len(subreddits)} 个）和 {len(search_queries)} 条搜索词", 14)

        if ctx.fetch_is_stopped(): return

        _time_map_local = {"month": "month", "3months": "year", "6months": "year", "9months": "all"}
        rdt_time_filter = _time_map_local.get(req.time_period, "year")

        # ========== Phase A: WebSearch 精准 URL 发现 ==========
        _t_start("websearch_discovery")
        # 根据用户设置选择搜索引擎：gpt / tavily / claude
        # 核心机制：通过 Web 语义搜索发现最相关的 Reddit 帖子，
        # 然后用 rdt read 提取全文和深层评论。同时动态发现新 subreddit。
        discovered_subs: set[str] = set()

        if "reddit" in req.sources and topic_for_check and search_queries:
            ws_engine = ctx.web_search_engine
            ws_engine_label = {"gpt": "GPT", "tavily": "Tavily", "claude": "Claude"}.get(ws_engine, ws_engine)
            ctx.fetch_emit(f"{ws_engine_label} WebSearch 正在发现高质量 Reddit 帖子...", 15)
            try:
                if ws_engine == "gpt":
                    discovered, new_subs = gpt_discover_reddit_urls(
                        topic=topic_for_check,
                        search_queries=search_queries[:10],
                        subreddits=subreddits[:6] if subreddits else None,
                        discovery_queries=discovery_queries if discovery_queries else None,
                        progress_callback=lambda msg: ctx.fetch_emit(msg, 18),
                    )
                    discovered_subs.update(new_subs)
                elif ws_engine == "claude":
                    discovered, new_subs = claude_discover_reddit_urls(
                        topic=topic_for_check,
                        search_queries=search_queries[:10],
                        subreddits=subreddits[:6] if subreddits else None,
                        discovery_queries=discovery_queries if discovery_queries else None,
                        progress_callback=lambda msg: ctx.fetch_emit(msg, 18),
                    )
                    discovered_subs.update(new_subs)
                else:
                    # Tavily
                    discovered = discover_reddit_urls(
                        topic=topic_for_check,
                        search_queries=search_queries[:6],
                        subreddits=subreddits[:4] if subreddits else None,
                        discovery_queries=discovery_queries if discovery_queries else None,
                        progress_callback=lambda msg: ctx.fetch_emit(msg, 18),
                    )
                    import re as _re
                    _sub_pat = _re.compile(r'reddit\.com/r/(\w+)')
                    for d in discovered:
                        m = _sub_pat.search(d.get("url", ""))
                        if m:
                            discovered_subs.add(m.group(1))

                if discovered:
                    ctx.fetch_emit(f"WebSearch 发现 {len(discovered)} 个帖子，rdt read 并发提取全文+评论...", 20)
                    ws_posts = []
                    disc_batch = discovered[:45]

                    import asyncio as _aio_ws
                    _WS_READ_BATCH = 5
                    for batch_start in range(0, len(disc_batch), _WS_READ_BATCH):
                        if ctx.fetch_is_stopped(): return
                        batch = disc_batch[batch_start:batch_start + _WS_READ_BATCH]

                        async def _read_one(disc_item):
                            try:
                                return await fetcher.read_post(disc_item["post_id"])
                            except Exception as e:
                                print(f"[WebSearch→rdt read] {disc_item['post_id']} failed: {e}")
                                return None

                        results = _run(_aio_ws.gather(*[_read_one(d) for d in batch]))
                        for detail in results:
                            if detail and detail.get("title"):
                                detail["_discovery_source"] = "websearch"
                                ws_posts.append(detail)
                                src = detail.get("source", "")
                                if src.startswith("reddit/"):
                                    discovered_subs.add(src.split("/", 1)[1])
                        ctx.fetch_emit(f"已提取 {len(ws_posts)}/{len(disc_batch)} 个帖子全文", 20 + int(10 * (batch_start + len(batch)) / len(disc_batch)))

                    if ws_posts:
                        all_posts.extend(ws_posts)
                        ctx.fetch_emit(f"WebSearch 贡献 {len(ws_posts)} 个高质量帖子（含深层评论）", 30)
                    else:
                        ctx.fetch_emit("WebSearch 未提取到有效帖子", 30)
                else:
                    ctx.fetch_emit("WebSearch 未发现相关帖子", 30)
            except Exception as e:
                import traceback as _tb_ws
                print(f"[WebSearch {ws_engine}] ERROR: {e}\n{_tb_ws.format_exc()}")
                ctx.fetch_emit(f"WebSearch 跳过: {str(e)[:60]}", 30)

        _t_end("websearch_discovery")

        # 动态 subreddit 合并（最多追加 5 个新发现的 sub）
        original_sub_set = set(subreddits)
        new_subs_to_add = [s for s in discovered_subs if s not in original_sub_set][:8]
        if new_subs_to_add:
            subreddits.extend(new_subs_to_add)
            ctx.fetch_emit(f"动态发现 {len(new_subs_to_add)} 个新 subreddit: {', '.join(new_subs_to_add)}", 31)

        if ctx.fetch_is_stopped(): return

        # ========== Phase B: rdt search / HN 关键词补充 ==========
        print(f"[Fetch] Phase B start: sources={req.sources}, queries={search_queries[:5]}, subs={subreddits[:10]}, all_posts_from_A={len(all_posts)}, engine={engine_name}")
        _t_start("keyword_search")
        total_sources = len(req.sources)
        per_source = max(req.limit // total_sources, 15) if total_sources else req.limit

        src_done = 0
        for src in req.sources:
            if ctx.fetch_is_stopped(): return
            src_label = source_names.get(src, src)
            base_progress = 32 + src_done * 15
            ctx.fetch_emit(f"正在补充搜索 {src_label}...", base_progress)

            try:
                if src == "hackernews":
                    if req.mode == "open":
                        posts = fetch_hackernews(req.category, per_source)
                    else:
                        hn_posts: list[dict] = []
                        # HN Algolia 支持语义搜索，同时用短词和自然语言查询
                        hn_queries = list(search_queries[:8])
                        if discovery_queries:
                            hn_queries.extend(discovery_queries[:6])
                        per_hn_q = max(per_source // max(len(hn_queries), 1), 5)
                        for q in hn_queries:
                            if ctx.fetch_is_stopped(): return
                            hn_posts.extend(search_hackernews(q, per_hn_q, time_period=req.time_period))
                        posts = hn_posts
                elif src == "reddit":
                    target_subs: list[str] = []
                    if req.reddit_categories:
                        for cat_key in req.reddit_categories:
                            cat = REDDIT_CATEGORIES.get(cat_key, {})
                            target_subs.extend(cat.get("subreddits", []))
                    elif subreddits:
                        target_subs = subreddits[:20]

                    reddit_posts: list[dict] = []
                    queries = search_queries[:10] if search_queries else []

                    if target_subs:
                        import asyncio as _aio_b
                        per_sub = max(per_source // max(len(target_subs[:20]), 1), 5)
                        _ADAPTIVE_THRESHOLD = 60
                        _SUFFICIENT_THRESHOLD = 100
                        _INITIAL_SUBS = 7

                        async def _search_one_sub(sub_name: str, q_list: list[str]):
                            """并发搜索单个 sub 的多个 query"""
                            results: list[dict] = []
                            for q in q_list:
                                try:
                                    sp = await fetcher.search(
                                        query=q, subreddit=sub_name,
                                        sort="top" if not q else "relevance",
                                        time_filter=rdt_time_filter,
                                        limit=per_sub,
                                    )
                                    results.extend(sp)
                                    if sp:
                                        print(f"[Reddit] {sub_name}/{q[:30]}: {len(sp)} posts")
                                except Exception as e:
                                    print(f"[Reddit] {sub_name}/{q[:30]} FAILED: {e}")
                            return sub_name, results

                        subs_to_search = target_subs[:15]
                        phase1_subs = subs_to_search[:_INITIAL_SUBS]
                        phase2_subs = subs_to_search[_INITIAL_SUBS:]
                        full_queries = queries[:6] if queries else [""]

                        # Phase B-1: 前 5 个 sub 并发搜索
                        ctx.fetch_emit(f"并发搜索前 {len(phase1_subs)} 个核心 subreddit...", base_progress + 2)
                        phase1_results = _run(_aio_b.gather(*[
                            _search_one_sub(s, full_queries) for s in phase1_subs
                        ]))
                        for sub_name, sub_batch in phase1_results:
                            if sub_batch:
                                reddit_posts.extend(sub_batch)
                                for sp in sub_batch:
                                    sp_src = sp.get("source", "")
                                    if sp_src.startswith("reddit/"):
                                        new_sub = sp_src.split("/", 1)[1]
                                        if new_sub not in original_sub_set and new_sub not in set(subs_to_search):
                                            discovered_subs.add(new_sub)
                        ctx.fetch_emit(f"核心 sub 搜索完成：{len(reddit_posts)} 个帖子", base_progress + 7)

                        # Phase B-2: 自适应搜索剩余 sub
                        if ctx.fetch_is_stopped(): return
                        if len(reddit_posts) >= _SUFFICIENT_THRESHOLD:
                            ctx.fetch_emit(f"已采集 {len(reddit_posts)} 个帖子（充足），跳过剩余 {len(phase2_subs)} 个 sub", base_progress + 10)
                        elif phase2_subs:
                            reduced_queries = full_queries[:3] if len(reddit_posts) >= _ADAPTIVE_THRESHOLD else full_queries
                            mode_label = "精简" if len(reduced_queries) < len(full_queries) else "完整"
                            ctx.fetch_emit(f"已采集 {len(reddit_posts)} 个帖子，{mode_label}模式搜索剩余 {len(phase2_subs)} 个 sub...", base_progress + 8)

                            _SUB_BATCH_SIZE = 5
                            for sb_start in range(0, len(phase2_subs), _SUB_BATCH_SIZE):
                                if ctx.fetch_is_stopped(): return
                                if len(reddit_posts) >= _SUFFICIENT_THRESHOLD:
                                    ctx.fetch_emit(f"已采集 {len(reddit_posts)} 个帖子（充足），停止搜索", base_progress + 12)
                                    break
                                sb = phase2_subs[sb_start:sb_start + _SUB_BATCH_SIZE]
                                batch_results = _run(_aio_b.gather(*[
                                    _search_one_sub(s, reduced_queries) for s in sb
                                ]))
                                for sub_name, sub_batch in batch_results:
                                    if sub_batch:
                                        reddit_posts.extend(sub_batch)
                                        for sp in sub_batch:
                                            sp_src = sp.get("source", "")
                                            if sp_src.startswith("reddit/"):
                                                new_sub = sp_src.split("/", 1)[1]
                                                if new_sub not in original_sub_set and new_sub not in set(subs_to_search):
                                                    discovered_subs.add(new_sub)
                                ctx.fetch_emit(f"补充搜索：累计 {len(reddit_posts)} 个帖子", base_progress + 10 + int(5 * (sb_start + len(sb)) / len(phase2_subs)))

                        ctx.fetch_emit(f"Reddit 搜索完成：共 {len(reddit_posts)} 个帖子", base_progress + 15)
                    elif req.mode == "open":
                        reddit_posts = _run(fetcher.search("", sort="top", time_filter=rdt_time_filter, limit=per_source))
                    else:
                        import asyncio as _aio_fb
                        fallback_subs = subreddits[:8] if subreddits else []
                        if fallback_subs:
                            per_fb = max(per_source // max(len(fallback_subs), 1), 5)
                            async def _fb_search(fb_sub, q):
                                try:
                                    return await fetcher.search(q, subreddit=fb_sub, sort="relevance", time_filter=rdt_time_filter, limit=per_fb)
                                except Exception:
                                    return []
                            tasks = [_fb_search(fb_sub, q) for fb_sub in fallback_subs for q in queries[:3]]
                            fb_results = _run(_aio_fb.gather(*tasks))
                            for r in fb_results:
                                reddit_posts.extend(r)
                        else:
                            for q in queries[:5]:
                                if ctx.fetch_is_stopped(): return
                                sub_posts = _run(fetcher.search(q, sort="relevance", time_filter=rdt_time_filter, limit=per_source // max(len(queries[:5]), 1)))
                                reddit_posts.extend(sub_posts)
                    posts = reddit_posts
                else:
                    posts = []

                all_posts.extend(posts)
                print(f"[Fetch] {src_label}: {len(posts)} posts collected")
                ctx.fetch_emit(f"{src_label}: 已发现 {len(posts)} 个帖子", base_progress + 18)
            except Exception as e:
                print(f"[Fetch] {src_label} ERROR: {e}")
                ctx.fetch_emit(f"{src_label} 采集出错: {str(e)[:80]}", base_progress + 18)
            src_done += 1

        if ctx.fetch_is_stopped(): return

        _t_end("keyword_search")
        if ctx.fetch_is_stopped(): return

        # 自动扩展：初始采集不足时，用 discovery_queries 做更广泛搜索
        if len(all_posts) < 35 and engine_name == "rdt-cli" and req.mode != "open":
            expand_queries = []
            if discovery_queries:
                for dq in discovery_queries[:6]:
                    words = dq.split()
                    if len(words) >= 3:
                        expand_queries.append(" ".join(words[:3]))
            if not expand_queries and search_queries:
                expand_queries = search_queries[5:10]
            expand_subs = subreddits[12:20] if len(subreddits) > 12 else subreddits[:5]

            if expand_queries and expand_subs:
                ctx.fetch_emit(f"初始数据不足（{len(all_posts)} 条），正在扩展搜索...", 46)
                for exp_sub in expand_subs[:6]:
                    if ctx.fetch_is_stopped(): return
                    for eq in expand_queries[:4]:
                        try:
                            exp_posts = _run(fetcher.search(
                                query=eq, subreddit=exp_sub,
                                sort="relevance", time_filter=rdt_time_filter,
                                limit=15,
                            ))
                            all_posts.extend(exp_posts)
                        except Exception:
                            pass
                ctx.fetch_emit(f"扩展搜索后：共 {len(all_posts)} 条帖子", 48)

        if ctx.fetch_is_stopped(): return

        seen_titles: set[str] = set()
        seen_content: set[str] = set()
        deduped: list[dict] = []
        for p in all_posts:
            title_key = p["title"].lower().strip()
            content_key = (p.get("content", "") or "")[:120].lower().strip()
            if title_key in seen_titles:
                continue
            if content_key and len(content_key) > 50 and content_key in seen_content:
                continue
            seen_titles.add(title_key)
            if content_key and len(content_key) > 50:
                seen_content.add(content_key)
            deduped.append(p)
        # 先做精确时间范围过滤，再截取 top N，避免超时帖子占用名额
        _period_days = {"month": 30, "3months": 90, "6months": 183, "9months": 270}
        max_age_days = _period_days.get(req.time_period, 183)
        cutoff_ts = _time.time() - max_age_days * 86400
        before_time_filter = len(deduped)
        deduped = [p for p in deduped if (p.get("created_utc") or 0) == 0 or p["created_utc"] >= cutoff_ts]
        dropped = before_time_filter - len(deduped)
        if dropped:
            print(f"[TimeFilter] {req.time_period}（{max_age_days}天） → 移除 {dropped} 条超时帖子")
            ctx.fetch_emit(f"时间范围过滤：移除 {dropped} 条超出 {req.time_period} 范围的帖子", 52)

        deduped.sort(key=lambda p: p["score"], reverse=True)
        deduped = deduped[:req.limit]

        raw_count = len(deduped)
        if raw_count == 0:
            _src_hints = []
            if "reddit" in req.sources:
                if engine_name == "none":
                    _src_hints.append("rdt-cli 未认证 → 前往「设置 → CLI 连接」检查")
                else:
                    _src_hints.append("Reddit 未搜到结果，试试更换关键词或扩大时间范围")
            if "hackernews" in req.sources:
                _src_hints.append("HackerNews 未返回结果")
            _hint = "；".join(_src_hints) if _src_hints else "所选数据源均未返回结果，请更换关键词或检查网络"
            print(f"[Fetch] 0 posts. engine={engine_name}, sources={req.sources}, queries={search_queries[:5]}, subs={subreddits[:5]}")
            ctx.fetch_emit(f"未采集到帖子：{_hint}", 100)
            with ctx.fetch_lock:
                ctx.fetch_job["error"] = f"未采集到帖子：{_hint}"
            return

        ctx.fetch_emit(f"采集完成，共 {raw_count} 个帖子", 52)
        _emit_slow("语义去重与排序...", 53)
        _emit_slow(f"开始质量筛选（{raw_count} 个帖子）...", 55)

        if req.mode == "open":
            hard_filtered = [p for p in deduped if p.get("score", 0) >= 2 or p.get("num_comments", 0) >= 2]
            if len(hard_filtered) < 5:
                hard_filtered = deduped
        else:
            hard_filtered = [p for p in deduped if hard_filter(p)]
            if len(hard_filtered) < 3:
                hard_filtered = deduped
        ctx.fetch_emit(f"硬性门槛过滤：{raw_count} → {len(hard_filtered)} 个帖子", 60)

        if ctx.fetch_is_stopped(): return

        # 评论充实：并发拉取高分帖子的完整评论（2-3 层深度）
        _t_start("comment_enrichment")
        if engine_name == "rdt-cli" and hard_filtered:
            enrichable = [p for p in hard_filtered
                          if p.get("_post_id") and len(p.get("comments") or []) < 3]
            enrichable.sort(key=lambda p: p.get("score", 0), reverse=True)
            enrich_limit = min(len(enrichable), 30)
            if enrich_limit > 0:
                import asyncio as _aio_enrich
                _ENRICH_BATCH = 3
                _ENRICH_COMMENT_TARGET = 120
                ctx.fetch_emit(f"拉取 {enrich_limit} 个帖子的深层评论...", 62)
                enriched_count = 0
                total_comments_collected = 0
                consecutive_empty_batches = 0

                for batch_start in range(0, enrich_limit, _ENRICH_BATCH):
                    if ctx.fetch_is_stopped(): return
                    if total_comments_collected >= _ENRICH_COMMENT_TARGET and enriched_count >= 8:
                        ctx.fetch_emit(f"评论充实已足够（{enriched_count} 帖 / {total_comments_collected} 条评论），停止", 64)
                        break
                    if consecutive_empty_batches >= 2:
                        ctx.fetch_emit(f"连续 {consecutive_empty_batches} 批无结果，跳过剩余评论充实", 64)
                        break
                    batch = enrichable[batch_start:batch_start + _ENRICH_BATCH]

                    async def _enrich_one(post):
                        try:
                            return post, await fetcher.read_post(post["_post_id"])
                        except Exception as e:
                            print(f"[Enrich] read_post {post.get('_post_id')} failed: {e}")
                            return post, None

                    results = _run(_aio_enrich.gather(*[_enrich_one(p) for p in batch]))
                    prev_enriched = enriched_count
                    for post_ref, detail in results:
                        if detail and detail.get("comments"):
                            post_ref["comments"] = detail["comments"][:35]
                            if detail.get("content") and len(detail["content"]) > len(post_ref.get("content", "")):
                                post_ref["content"] = detail["content"]
                            enriched_count += 1
                            total_comments_collected += len(post_ref["comments"])
                    if enriched_count > prev_enriched:
                        consecutive_empty_batches = 0
                        ctx.fetch_emit(f"评论充实进度：{enriched_count} 帖 / {total_comments_collected} 条评论", 62 + int(3 * (batch_start + len(batch)) / enrich_limit))
                    else:
                        consecutive_empty_batches += 1

                ctx.fetch_emit(f"评论充实完成：{enriched_count}/{enrich_limit} 帖，共 {total_comments_collected} 条评论", 65)

        _t_end("comment_enrichment")
        if ctx.fetch_is_stopped(): return

        # 过滤已合并到两步聚类的 Step1 中，不再单独调用 _filter_posts
        filtered = hard_filtered
        ctx.fetch_emit(f"共 {len(filtered)} 个帖子进入聚类（过滤 + 分组一步完成）", 75)

        if ctx.fetch_is_stopped(): return

        if not filtered:
            ctx.fetch_emit("未采集到有效帖子，请尝试更换关键词或数据源", 100)
            with ctx.fetch_lock:
                ctx.fetch_job["error"] = "未采集到有效帖子，请尝试更换关键词、扩大时间范围或切换数据源"
            return

        _t_start("clustering")
        needs = _cluster_posts_into_needs(filtered, topic=topic_for_check)
        _t_end("clustering")

        # 把用户原始搜索主题注入每个 need，报告生成时用它做主题锚定
        for n in needs:
            n["original_topic"] = topic_for_check

        valid_needs = [n for n in needs if n.get("posts") and len(n["posts"]) > 0]

        if not valid_needs:
            err_msg = f"采集到 {len(filtered)} 个帖子但未归纳出需求主题，建议更换关键词、选更具体的赛道或扩大时间范围"
            ctx.fetch_emit(err_msg, 100)
            with ctx.fetch_lock:
                ctx.fetch_job["error"] = err_msg
            return

        _safe_json_write(ctx.needs_cache, valid_needs, indent=2)
        _increment_global_needs(len(valid_needs))
        ctx.reset_debate()

        total_posts = sum(len(n["posts"]) for n in valid_needs)
        _emit_slow(f"产出 {len(valid_needs)} 个需求主题，整理结构...", 92)
        _emit_slow("评估产品机会...", 95)
        tavily_credits = get_tavily_credit_count()
        total_elapsed = round(_time.time() - _t_total_start, 1)

        def _fmt_duration(secs: float) -> str:
            s = int(secs)
            if s < 60:
                return f"{s}s"
            return f"{s // 60}m{s % 60:02d}s"

        phase_labels = {
            "search_planning": "搜索规划",
            "websearch_discovery": "WebSearch发现",
            "keyword_search": "关键词搜索",
            "comment_enrichment": "评论充实",
            "quality_filter": "质量筛选",
            "clustering": "需求聚类",
        }
        timing_parts = []
        for key in ["search_planning", "websearch_discovery", "keyword_search", "comment_enrichment", "quality_filter", "clustering"]:
            if key in _timing:
                timing_parts.append(f"{phase_labels.get(key, key)} {_fmt_duration(_timing[key])}")
        timing_str = " | ".join(timing_parts)

        ctx.fetch_emit(f"挖掘完成！发现 {len(valid_needs)} 个需求主题，共 {total_posts} 个帖子", 100)
        ctx.fetch_emit(f"⏱ 总用时 {_fmt_duration(total_elapsed)} — {timing_str}", 100)
        print(f"[Fetch Job Done] Total: {_fmt_duration(total_elapsed)} | {timing_str} | Tavily credits: {tavily_credits}")

        with ctx.fetch_lock:
            ctx.fetch_job["needs"] = valid_needs
            ctx.fetch_job["timing"] = {"total": total_elapsed, "phases": dict(_timing)}

    except Exception as e:
        _log_sse_error("Fetch", e, ctx)
        with ctx.fetch_lock:
            ctx.fetch_job["error"] = _friendly_error(e)
    finally:
        fetcher = get_reddit_fetcher()
        fetcher.force_engine = None
        with ctx.fetch_lock:
            ctx.fetch_job["active"] = False
        try:
            _loop.close()
        except Exception:
            pass


@router.post("/fetch")
def fetch_posts(req: FetchRequest, request: Request):
    ctx = _get_session(request)
    with ctx.fetch_lock:
        if ctx.fetch_job["active"]:
            if ctx.fetch_job["stop_requested"]:
                ctx.fetch_job["active"] = False
            else:
                raise HTTPException(status_code=409, detail="已有挖掘任务进行中")
        ctx.fetch_job.update({
            "active": True,
            "stop_requested": False,
            "progress": 0,
            "history": ["准备开始挖掘...", "正在连接数据源..."],
            "error": "",
            "needs": None,
            "engine": "",
            "clustering_fallback": False,
        })
    if ctx.fetch_thread and ctx.fetch_thread.is_alive():
        ctx.fetch_thread.join(timeout=5)
    if ctx.needs_cache.exists():
        try:
            ctx.needs_cache.unlink()
        except Exception:
            pass

    t = threading.Thread(target=_run_fetch_job, args=(ctx, req.model_dump()), daemon=True)
    ctx.fetch_thread = t
    t.start()

    def event_stream() -> Generator[str, None, None]:
        sent_idx = 0
        while True:
            _time.sleep(0.3)
            with ctx.fetch_lock:
                active = ctx.fetch_job["active"]
                stopped = ctx.fetch_job["stop_requested"]
                history = list(ctx.fetch_job["history"])
                progress = ctx.fetch_job["progress"]
                error = ctx.fetch_job["error"]
                needs = ctx.fetch_job["needs"]

            if stopped and not needs and not error:
                yield _sse("error", {"message": "挖掘已停止"})
                yield _sse("done", {})
                return

            new_messages = history[sent_idx:]
            for msg in new_messages:
                yield _sse("fetch_progress", {"message": msg, "progress": progress})
            sent_idx = len(history)

            if error:
                yield _sse("error", {"message": error})
                yield _sse("done", {})
                return

            if needs is not None:
                yield _sse("fetch_result", {
                    "needs": needs,
                    "count": len(needs),
                    "engine": ctx.fetch_job.get("engine", ""),
                    "timing": ctx.fetch_job.get("timing"),
                })
                yield _sse("done", {})
                return

            if not active:
                yield _sse("done", {})
                return

            # Keepalive: prevent Cloudflare / reverse proxy idle timeout
            if not new_messages:
                yield ": keepalive\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/fetch/status")
def fetch_status(request: Request):
    ctx = _get_session(request)
    with ctx.fetch_lock:
        return {
            "active": ctx.fetch_job["active"],
            "progress": ctx.fetch_job["progress"],
            "history": list(ctx.fetch_job["history"]),
            "error": ctx.fetch_job["error"],
            "needs": ctx.fetch_job["needs"],
            "engine": ctx.fetch_job.get("engine", ""),
            "timing": ctx.fetch_job.get("timing"),
        }


@router.post("/fetch/stop")
def fetch_stop(request: Request):
    ctx = _get_session(request)
    with ctx.fetch_lock:
        ctx.fetch_job["stop_requested"] = True
    return {"ok": True}


@router.get("/needs")
def get_needs(request: Request):
    ctx = _get_session(request)
    with ctx.fetch_lock:
        if ctx.fetch_job["active"]:
            return {"needs": []}
    if ctx.needs_cache.exists():
        try:
            with open(ctx.needs_cache, "r", encoding="utf-8") as f:
                raw = json.load(f)
            return {"needs": _normalize_needs_list(raw)}
        except Exception:
            pass
    return {"needs": []}


class SyncNeedsRequest(BaseModel):
    needs: list


@router.put("/needs")
def sync_needs(req: SyncNeedsRequest, request: Request):
    ctx = _get_session(request)
    normalized = _normalize_needs_list(req.needs)
    _safe_json_write(ctx.needs_cache, normalized, indent=2)
    print(f"[SyncNeeds] 已同步 {len(normalized)} 个需求到缓存")
    return {"ok": True, "count": len(normalized)}


@router.delete("/needs")
def clear_needs(request: Request):
    ctx = _get_session(request)
    ctx.reset_debate()
    if ctx.needs_cache.exists():
        ctx.needs_cache.unlink()
    return {"ok": True}


@router.post("/translate")
def translate_text(req: TranslateRequest, request: Request):
    """Translate English text to Chinese using LLM."""
    ctx = _get_session(request)
    try:
        messages = [
            {"role": "user", "content": (
                "将以下英文内容翻译为中文，只输出翻译结果，不要添加任何解释：\n\n"
                + req.text[:3000]
            )},
        ]
        translation = call_llm(messages)
        return {"translation": translation.strip()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ============================================================
# Debate routes
# ============================================================

@router.get("/debate/state")
def debate_state(request: Request):
    ctx = _get_session(request)
    return {
        "status": ctx.debate_state["status"],
        "round": ctx.debate_state["round"],
        "max_rounds": ctx.debate_state["max_rounds"],
        "debate_log": ctx.debate_state["debate_log"],
        "selected_need_idx": ctx.debate_state.get("selected_need_idx"),
        "final_report": ctx.debate_state["final_report"],
        "product_proposal": ctx.debate_state.get("product_proposal"),
        "topics": ctx.debate_state.get("topics", []),
        "current_topic_idx": ctx.debate_state.get("current_topic_idx", -1),
        "topic_conclusions": ctx.debate_state.get("topic_conclusions", []),
        "free_topic_input": ctx.debate_state.get("free_topic_input"),
    }


@router.post("/debate/reset")
def debate_reset(request: Request):
    ctx = _get_session(request)
    ctx.reset_debate()
    return {"ok": True}


def _stream_role(role: str, messages: list[dict]) -> tuple[str, list[str]]:
    """Select the correct streaming function for a role based on config."""
    return call_for_role_stream(role, messages)


@router.post("/debate/start")
def start_debate(req: StartDebateRequest, request: Request):
    ctx = _get_session(request)
    if ctx.debate_state["status"] == "debating" and not req.demo:
        raise HTTPException(status_code=409, detail="已有讨论进行中")

    needs_data = get_needs(request)["needs"]
    if req.need_index < 0 or req.need_index >= len(needs_data):
        raise HTTPException(status_code=404, detail="Need not found")

    need = needs_data[req.need_index]

    # ===== 演示模式：从缓存回放讨论 =====
    if req.demo:
        import time as _t
        _DEMO_DEBATE_PATH = ROOT / "data" / "demo" / "demo_debate.json"
        if not _DEMO_DEBATE_PATH.exists():
            def _err():
                yield _sse("error", {"message": "演示讨论数据不存在，请先准备 data/demo/demo_debate.json"})
            return StreamingResponse(_err(), media_type="text/event-stream")

        demo_msgs = json.loads(_DEMO_DEBATE_PATH.read_text(encoding="utf-8"))
        ctx.debate_state["status"] = "debating"
        ctx.debate_state["selected_need_idx"] = req.need_index
        ctx.debate_state["debate_log"] = []
        ctx.debate_state["round"] = 0

        def _demo_stream():
            _first_analyst = True
            for item in demo_msgs:
                evt = item.get("event")
                if evt == "topic_start":
                    yield _sse("topic_start", item.get("data", {}))
                    _t.sleep(0.3)
                elif evt == "round_start":
                    yield _sse("round_start", item.get("data", {}))
                    _t.sleep(0.2)
                elif evt == "message":
                    role = item.get("role", "director")
                    label = item.get("label", "")
                    content = item.get("content", "")
                    provider = item.get("provider", "claude")
                    is_first_pm = role == "analyst" and _first_analyst

                    yield _sse("message_start", {"role": role, "label": label, "provider": provider})

                    if is_first_pm:
                        think_start = content.find("<think>")
                        think_end = content.find("</think>")
                        for idx, ch in enumerate(content):
                            yield _sse("chunk", {"text": ch})
                            in_think = think_start != -1 and think_start <= idx <= (think_end + 7 if think_end != -1 else len(content))
                            _t.sleep(0.002 if in_think else 0.008)
                    else:
                        _t.sleep(0.15)
                        yield _sse("chunk", {"text": content})

                    yield _sse("message_end", {"role": role, "content": content})
                    ctx.debate_state["debate_log"].append({"role": role, "content": content})
                    if is_first_pm:
                        ctx.debate_state["analysis_result"] = content
                        _first_analyst = False
                    _t.sleep(0.6)
            yield _sse("debate_end", {})
            ctx.debate_state["status"] = "debate_done"

        return StreamingResponse(_demo_stream(), media_type="text/event-stream")

    def _parse_topics_json(text: str) -> list[dict]:
        """从 LLM 输出中解析话题 JSON 数组。"""
        import re as _re
        cleaned = _re.sub(r'<think>[\s\S]*?</think>', '', text, flags=_re.IGNORECASE).strip()
        cleaned = _re.sub(r'```(?:json)?\s*', '', cleaned).strip()
        cleaned = _re.sub(r'```\s*$', '', cleaned).strip()
        start = cleaned.find('[')
        end = cleaned.rfind(']')
        if start != -1 and end != -1:
            cleaned = cleaned[start:end+1]
        return json.loads(cleaned)

    def event_stream() -> Generator[str, None, None]:
        set_thread_session(ctx)
        # 预检：讨论使用的角色模型可用性
        role_ok, role_err = check_role_models_available()
        if not role_ok:
            yield _sse("error", {"message": f"角色模型不可用，请前往「设置 → 角色模型分配」检查配置"})
            return

        ctx.debate_state["status"] = "debating"
        ctx.debate_state["selected_need_idx"] = req.need_index
        ctx.debate_state["debate_log"] = []
        ctx.debate_state["round"] = 0
        ctx.debate_state["topics"] = []
        ctx.debate_state["current_topic_idx"] = -1
        ctx.debate_state["topic_conclusions"] = []
        ctx.debate_state["current_topic_exchanges"] = []

        _analyst_label = ctx.role_names.get("analyst", "产品经理")
        _critic_label = ctx.role_names.get("critic", "杠精")
        _director_label = ctx.role_names.get("director", "导演")

        try:
            import time as _time

            # ── Phase 1: 导演即时开场白（模板，不调 LLM） ──
            instant_opening = f"好，我来安排{_analyst_label}和{_critic_label}讨论「{need['need_title']}」。让我先看看帖子，拆几个核心话题出来。"
            yield _sse("message_start", {"role": "director", "label": _director_label, "provider": _provider_for_role("director")})
            for _ch in instant_opening:
                yield _sse("chunk", {"text": _ch})
                _time.sleep(0.02)
            yield _sse("message_end", {"role": "director", "content": instant_opening})
            ctx.debate_state["debate_log"].append({"role": "director", "content": instant_opening})

            # ── Phase 2: 导演拆话题（LLM 调用前先显示占位） ──
            yield _sse("message_start", {"role": "director", "label": _director_label, "provider": _provider_for_role("director")})

            print("[Debate] Phase 2: Director analyzing topics...")
            topic_msgs = prepare_topic_analysis(need)
            topic_raw = call_for_role("director", topic_msgs, max_tokens=2000)

            topics = []
            try:
                topics = _parse_topics_json(topic_raw)
            except Exception as parse_err:
                print(f"[Debate] Topic parse failed: {parse_err}, raw={topic_raw[:300]}")
                topics = [
                    {"title": "痛点真实性", "question": "帖子里这些人是真痛还是嘴上说说？"},
                    {"title": "付费意愿", "question": "有用户愿意为这个方向掏钱吗？"},
                    {"title": "竞品差异化", "question": "已有方案这么多，凭什么我们能做？"},
                ]

            topics = [t for t in topics if isinstance(t, dict) and "title" in t and "question" in t][:3]
            if not topics:
                topics = [
                    {"title": "需求验证", "question": "这个需求到底有多少用户有？"},
                    {"title": "可行性", "question": "App/AI 能解决这个问题吗？"},
                ]

            ctx.debate_state["topics"] = topics
            print(f"[Debate] Parsed {len(topics)} topics: {[t['title'] for t in topics]}")

            topic_intro = "我拆了 {} 个话题：{}。一个个来聊。".format(
                len(topics),
                "、".join(t["title"] for t in topics),
            )
            for _ch in topic_intro:
                yield _sse("chunk", {"text": _ch})
                _time.sleep(0.02)
            yield _sse("message_end", {"role": "director", "content": topic_intro})
            ctx.debate_state["debate_log"].append({"role": "director", "content": topic_intro})

            yield _sse("topic_list", {"topics": topics})

            # ── 启动投资人后台并行分析 ──
            _posts_compact = _format_need_posts_compact(need)
            _post_count = len(need.get("posts", []))
            _investor_bg_result = {"text": "", "error": ""}

            def _run_investor_bg():
                set_thread_session(ctx)
                try:
                    _cr = investor_competitor_web_context(
                        need_title=need["need_title"],
                        need_description=need.get("need_description", "") or "",
                        posts_compact=_posts_compact,
                        web_search_engine=ctx.web_search_engine,
                    )
                    _msgs = prepare_investor_bg(need, _posts_compact, _post_count, competitor_research=_cr)
                    _investor_bg_result["text"] = call_for_role("investor", _msgs)
                    print(f"[Debate] Investor BG analysis done ({len(_investor_bg_result['text'])} chars)")
                except Exception as _e:
                    _investor_bg_result["error"] = str(_e)[:200]
                    print(f"[Debate] Investor BG analysis failed: {_e}")
                finally:
                    clear_thread_session()

            _investor_thread = threading.Thread(target=_run_investor_bg, daemon=True)
            _investor_thread.start()
            print("[Debate] Investor BG analysis started in background")

            # ── Phase 3: 逐话题讨论 ──
            conclusions: list[dict] = []

            for t_idx, topic in enumerate(topics):
                ctx.debate_state["current_topic_idx"] = t_idx
                ctx.debate_state["current_topic_exchanges"] = []
                ctx.debate_state["round"] = t_idx + 1
                topic_exchanges: list[dict] = []

                yield _sse("round_start", {"round": t_idx + 1})
                yield _sse("topic_start", {"index": t_idx, "title": topic["title"], "total": len(topics)})

                # 导演提问（模板，逐字输出）
                director_q = f"话题 {t_idx+1}：{topic['title']}。{topic['question']}"
                yield _sse("message_start", {"role": "director", "label": _director_label, "provider": _provider_for_role("director")})
                for _ch in director_q:
                    yield _sse("chunk", {"text": _ch})
                    _time.sleep(0.02)
                yield _sse("message_end", {"role": "director", "content": director_q})
                ctx.debate_state["debate_log"].append({"role": "director", "content": director_q})

                # PM 表态（流式）
                is_first = (t_idx == 0)
                pm_msgs = prepare_topic_pm(need, topic, "", conclusions, is_first=is_first)
                yield _sse("message_start", {"role": "analyst", "label": _analyst_label, "provider": _provider_for_role("analyst")})
                pm_parts: list[str] = []
                for chunk in call_for_role_stream("analyst", pm_msgs):
                    pm_parts.append(chunk)
                    yield _sse("chunk", {"text": chunk})
                pm_resp = "".join(pm_parts)
                ctx.debate_state["debate_log"].append({"role": "analyst", "content": pm_resp})
                if is_first:
                    ctx.debate_state["analysis_result"] = pm_resp
                yield _sse("message_end", {"role": "analyst", "content": pm_resp})
                topic_exchanges.append({"role": "analyst", "content": pm_resp})

                # 杠精回应（流式，含反馈分级）
                critic_msgs = prepare_topic_critic(need, topic, pm_resp, conclusions)
                yield _sse("message_start", {"role": "critic", "label": _critic_label, "provider": _provider_for_role("critic")})
                critic_parts: list[str] = []
                for chunk in call_for_role_stream("critic", critic_msgs):
                    critic_parts.append(chunk)
                    yield _sse("chunk", {"text": chunk})
                critic_resp = "".join(critic_parts)
                _structural = is_structural_feedback(critic_resp)
                critic_clean = _re_tag.sub(r'\[(STRUCTURAL|MINOR)\]\s*', '', critic_resp).strip()
                ctx.debate_state["debate_log"].append({"role": "critic", "content": critic_clean})
                yield _sse("message_end", {"role": "critic", "content": critic_clean})
                topic_exchanges.append({"role": "critic", "content": critic_clean})

                # ── 第二轮：PM 反击（无论 STRUCTURAL / MINOR 都做） ──
                print(f"[Debate] Topic {t_idx+1} '{topic['title']}': {'STRUCTURAL' if _structural else 'MINOR'} feedback → PM counter")
                counter_msgs = prepare_topic_pm_counter(need, topic, pm_resp, critic_clean, conclusions)
                yield _sse("message_start", {"role": "analyst", "label": _analyst_label, "provider": _provider_for_role("analyst")})
                counter_parts: list[str] = []
                for chunk in call_for_role_stream("analyst", counter_msgs):
                    counter_parts.append(chunk)
                    yield _sse("chunk", {"text": chunk})
                counter_resp = "".join(counter_parts)
                ctx.debate_state["debate_log"].append({"role": "analyst", "content": counter_resp})
                yield _sse("message_end", {"role": "analyst", "content": counter_resp})
                topic_exchanges.append({"role": "analyst", "content": counter_resp})

                # ── 第二轮：杠精跟进 ──
                followup_msgs = prepare_topic_critic_followup(need, topic, critic_clean, counter_resp, conclusions)
                yield _sse("message_start", {"role": "critic", "label": _critic_label, "provider": _provider_for_role("critic")})
                followup_parts: list[str] = []
                for chunk in call_for_role_stream("critic", followup_msgs):
                    followup_parts.append(chunk)
                    yield _sse("chunk", {"text": chunk})
                followup_resp = "".join(followup_parts)
                followup_clean = _re_tag.sub(r'\[(STRUCTURAL|MINOR)\]\s*', '', followup_resp).strip()
                ctx.debate_state["debate_log"].append({"role": "critic", "content": followup_clean})
                yield _sse("message_end", {"role": "critic", "content": followup_clean})
                topic_exchanges.append({"role": "critic", "content": followup_clean})

                ctx.debate_state["current_topic_exchanges"] = topic_exchanges

                # 导演话题小结（流式）
                wrap_msgs = prepare_topic_wrap(topic, topic_exchanges, conclusions)
                yield _sse("message_start", {"role": "director", "label": _director_label, "provider": _provider_for_role("director")})
                wrap_parts: list[str] = []
                for chunk in call_for_role_stream("director", wrap_msgs):
                    wrap_parts.append(chunk)
                    yield _sse("chunk", {"text": chunk})
                wrap_resp = "".join(wrap_parts)
                ctx.debate_state["debate_log"].append({"role": "director", "content": wrap_resp})
                yield _sse("message_end", {"role": "director", "content": wrap_resp})

                conclusion = {"title": topic["title"], "summary": wrap_resp.strip()}
                conclusions.append(conclusion)
                ctx.debate_state["topic_conclusions"] = list(conclusions)

                yield _sse("topic_end", {"index": t_idx, "title": topic["title"], "summary": wrap_resp.strip()})
                print(f"[Debate] Topic {t_idx+1}/{len(topics)} '{topic['title']}' done")

            # ── Phase 4a: 等待投资人后台分析完成 ──
            _investor_label = ctx.role_names.get("investor", "投资人")
            _investor_thread.join(timeout=120)
            if _investor_thread.is_alive():
                print("[Debate] Investor BG analysis timed out after 120s")
                _investor_bg_result["error"] = "分析超时"

            # ── Phase 4b: 投资人结合讨论结论，流式输出最终商业分析 ──
            investor_resp = ""
            try:
                print("[Debate] Phase 4b: Investor final analysis")
                investor_final_msgs = prepare_investor_final(need, conclusions, _investor_bg_result["text"])
                yield _sse("message_start", {"role": "investor", "label": _investor_label, "provider": _provider_for_role("investor")})
                investor_parts: list[str] = []
                for chunk in call_for_role_stream("investor", investor_final_msgs):
                    investor_parts.append(chunk)
                    yield _sse("chunk", {"text": chunk})
                investor_resp = "".join(investor_parts)
                ctx.debate_state["debate_log"].append({"role": "investor", "content": investor_resp})
                yield _sse("message_end", {"role": "investor", "content": investor_resp})
            except Exception as inv_err:
                print(f"[Debate] Investor final analysis failed: {inv_err}")
                _err_text = f"投资人分析暂时不可用（{_friendly_error(inv_err)}），导演将直接判决。"
                yield _sse("message_end", {"role": "investor", "content": _err_text})
                ctx.debate_state["debate_log"].append({"role": "investor", "content": _err_text})

            # ── Phase 4c: 导演最终判决（综合产品讨论 + 投资人分析） ──
            print("[Debate] Phase 4c: Director final verdict")
            verdict_msgs = prepare_final_verdict(need, conclusions, investor_resp)
            yield _sse("message_start", {"role": "director", "label": _director_label, "provider": _provider_for_role("director")})
            verdict_parts: list[str] = []
            for chunk in call_for_role_stream("director", verdict_msgs):
                verdict_parts.append(chunk)
                yield _sse("chunk", {"text": chunk})
            verdict = "".join(verdict_parts)
            ctx.debate_state["debate_log"].append({"role": "director", "content": verdict})
            yield _sse("message_end", {"role": "director", "content": verdict})

            ctx.debate_state["status"] = "debate_done"
            ctx.debate_state["current_topic_idx"] = -1
            ctx.save_debate_cache()

            print(f"[Debate] Finished: {len(topics)} topics, {len(ctx.debate_state['debate_log'])} messages")
            yield _sse("debate_end", {"reason": "director_verdict", "topics": len(topics), "messages": len(ctx.debate_state["debate_log"])})

        except Exception as e:
            import traceback
            traceback.print_exc()
            ctx.debate_state["status"] = "debate_done"
            ctx.save_debate_cache()
            yield _sse("error", {"message": _friendly_error(e)})
            yield _sse("debate_end", {"reason": "error"})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/debate/start-free")
def start_free_debate(req: StartFreeDebateRequest, request: Request):
    """自由话题模式：用户输入一句话/话题，三角色直接讨论。"""
    ctx = _get_session(request)
    if ctx.debate_state["status"] == "debating":
        raise HTTPException(status_code=409, detail="已有讨论进行中")

    user_input = req.user_input.strip()
    if not user_input:
        raise HTTPException(status_code=400, detail="话题不能为空")

    def _parse_free_topics_json(text: str) -> list[dict]:
        import re as _re
        cleaned = _re.sub(r'<think>[\s\S]*?</think>', '', text).strip()
        cleaned = cleaned.strip('`').strip()
        if cleaned.startswith('json'):
            cleaned = cleaned[4:].strip()
        return json.loads(cleaned)

    def event_stream():
        set_thread_session(ctx)
        import time as _time

        # 预检：讨论使用的角色模型可用性
        role_ok, role_err = check_role_models_available()
        if not role_ok:
            yield _sse("error", {"message": f"角色模型不可用，请前往「设置 → 角色模型分配」检查配置"})
            return

        ctx.reset_debate()
        ctx.debate_state["status"] = "debating"
        ctx.debate_state["selected_need_idx"] = None
        ctx.debate_state["free_topic_input"] = user_input

        names = ctx.role_names
        _director_label = names.get("director", "导演")
        _analyst_label = names.get("analyst", "产品经理")
        _critic_label = names.get("critic", "杠精")

        try:
            # Phase 1: 导演开场
            instant_opening = f"好，我来安排讨论「{user_input}」。让我先拆几个核心话题出来。"
            yield _sse("message_start", {"role": "director", "label": _director_label, "provider": _provider_for_role("director")})
            for _ch in instant_opening:
                yield _sse("chunk", {"text": _ch})
                _time.sleep(0.02)
            yield _sse("message_end", {"role": "director", "content": instant_opening})
            ctx.debate_state["debate_log"].append({"role": "director", "content": instant_opening})

            # Phase 2: 导演拆话题
            yield _sse("message_start", {"role": "director", "label": _director_label, "provider": _provider_for_role("director")})
            print("[FreeDeb] Phase 2: Director analyzing topics...")
            topic_msgs = prepare_free_topic_analysis(user_input)
            topic_raw = call_for_role("director", topic_msgs, max_tokens=2000)

            topics = []
            try:
                topics = _parse_free_topics_json(topic_raw)
            except Exception as parse_err:
                print(f"[FreeDeb] Topic parse failed: {parse_err}, raw={topic_raw[:300]}")
                topics = [
                    {"title": "需求真伪", "question": "这个需求是真的还是伪需求？"},
                    {"title": "谁会买单", "question": "什么人愿意为这个掏钱？"},
                    {"title": "凭什么你做", "question": "已有方案那么多，凭什么我们能做？"},
                ]

            topics = [t for t in topics if isinstance(t, dict) and "title" in t and "question" in t][:3]
            if not topics:
                topics = [
                    {"title": "需求验证", "question": "这个需求到底有多少人有？"},
                    {"title": "可行性", "question": "做成产品的话能落地吗？"},
                ]

            ctx.debate_state["topics"] = topics
            print(f"[FreeDeb] Parsed {len(topics)} topics: {[t['title'] for t in topics]}")

            topic_intro = "我拆了 {} 个话题：{}。一个个来聊。".format(
                len(topics),
                "、".join(t["title"] for t in topics),
            )
            for _ch in topic_intro:
                yield _sse("chunk", {"text": _ch})
                _time.sleep(0.02)
            yield _sse("message_end", {"role": "director", "content": topic_intro})
            ctx.debate_state["debate_log"].append({"role": "director", "content": topic_intro})

            yield _sse("topic_list", {"topics": topics})

            # ── 启动投资人后台并行分析（自由话题模式）──
            _investor_bg_result_free = {"text": "", "error": ""}

            def _run_investor_bg_free():
                set_thread_session(ctx)
                try:
                    _cr_f = investor_competitor_web_context(
                        user_input=user_input,
                        web_search_engine=ctx.web_search_engine,
                    )
                    _msgs_f = prepare_free_investor_bg(user_input, competitor_research=_cr_f)
                    _investor_bg_result_free["text"] = call_for_role("investor", _msgs_f)
                    print(f"[FreeDeb] Investor BG analysis done ({len(_investor_bg_result_free['text'])} chars)")
                except Exception as _e:
                    _investor_bg_result_free["error"] = str(_e)[:200]
                    print(f"[FreeDeb] Investor BG analysis failed: {_e}")
                finally:
                    clear_thread_session()

            _investor_thread_free = threading.Thread(target=_run_investor_bg_free, daemon=True)
            _investor_thread_free.start()
            print("[FreeDeb] Investor BG analysis started in background")

            # Phase 3: 逐话题讨论
            conclusions: list[dict] = []

            for t_idx, topic in enumerate(topics):
                ctx.debate_state["current_topic_idx"] = t_idx
                ctx.debate_state["current_topic_exchanges"] = []
                ctx.debate_state["round"] = t_idx + 1
                topic_exchanges: list[dict] = []

                yield _sse("round_start", {"round": t_idx + 1})
                yield _sse("topic_start", {"index": t_idx, "title": topic["title"], "total": len(topics)})

                director_q = f"话题 {t_idx+1}：{topic['title']}。{topic['question']}"
                yield _sse("message_start", {"role": "director", "label": _director_label, "provider": _provider_for_role("director")})
                for _ch in director_q:
                    yield _sse("chunk", {"text": _ch})
                    _time.sleep(0.02)
                yield _sse("message_end", {"role": "director", "content": director_q})
                ctx.debate_state["debate_log"].append({"role": "director", "content": director_q})

                # PM（自由话题模式）
                is_first = (t_idx == 0)
                pm_msgs = prepare_free_topic_pm(user_input, topic, conclusions, is_first=is_first)
                yield _sse("message_start", {"role": "analyst", "label": _analyst_label, "provider": _provider_for_role("analyst")})
                pm_parts: list[str] = []
                for chunk in call_for_role_stream("analyst", pm_msgs):
                    pm_parts.append(chunk)
                    yield _sse("chunk", {"text": chunk})
                pm_resp = "".join(pm_parts)
                ctx.debate_state["debate_log"].append({"role": "analyst", "content": pm_resp})
                yield _sse("message_end", {"role": "analyst", "content": pm_resp})
                topic_exchanges.append({"role": "analyst", "content": pm_resp})

                # 杠精（自由话题模式）
                critic_msgs = prepare_free_topic_critic(user_input, topic, pm_resp, conclusions)
                yield _sse("message_start", {"role": "critic", "label": _critic_label, "provider": _provider_for_role("critic")})
                critic_parts: list[str] = []
                for chunk in call_for_role_stream("critic", critic_msgs):
                    critic_parts.append(chunk)
                    yield _sse("chunk", {"text": chunk})
                critic_resp = "".join(critic_parts)
                _structural = is_structural_feedback(critic_resp)
                critic_clean = _re_tag.sub(r'\[(STRUCTURAL|MINOR)\]\s*', '', critic_resp).strip()
                ctx.debate_state["debate_log"].append({"role": "critic", "content": critic_clean})
                yield _sse("message_end", {"role": "critic", "content": critic_clean})
                topic_exchanges.append({"role": "critic", "content": critic_clean})

                # ── 第二轮：PM 反击（无论 STRUCTURAL / MINOR 都做） ──
                print(f"[FreeDeb] Topic {t_idx+1} '{topic['title']}': {'STRUCTURAL' if _structural else 'MINOR'} → PM counter")
                counter_msgs = prepare_topic_pm_counter(
                    {"need_title": user_input}, topic, pm_resp, critic_clean, conclusions
                )
                yield _sse("message_start", {"role": "analyst", "label": _analyst_label, "provider": _provider_for_role("analyst")})
                counter_parts: list[str] = []
                for chunk in call_for_role_stream("analyst", counter_msgs):
                    counter_parts.append(chunk)
                    yield _sse("chunk", {"text": chunk})
                counter_resp = "".join(counter_parts)
                ctx.debate_state["debate_log"].append({"role": "analyst", "content": counter_resp})
                yield _sse("message_end", {"role": "analyst", "content": counter_resp})
                topic_exchanges.append({"role": "analyst", "content": counter_resp})

                # ── 第二轮：杠精跟进 ──
                followup_msgs = prepare_free_topic_critic_followup(user_input, topic, critic_clean, counter_resp, conclusions)
                yield _sse("message_start", {"role": "critic", "label": _critic_label, "provider": _provider_for_role("critic")})
                followup_parts: list[str] = []
                for chunk in call_for_role_stream("critic", followup_msgs):
                    followup_parts.append(chunk)
                    yield _sse("chunk", {"text": chunk})
                followup_resp = "".join(followup_parts)
                followup_clean = _re_tag.sub(r'\[(STRUCTURAL|MINOR)\]\s*', '', followup_resp).strip()
                ctx.debate_state["debate_log"].append({"role": "critic", "content": followup_clean})
                yield _sse("message_end", {"role": "critic", "content": followup_clean})
                topic_exchanges.append({"role": "critic", "content": followup_clean})

                ctx.debate_state["current_topic_exchanges"] = topic_exchanges

                wrap_msgs = prepare_topic_wrap(topic, topic_exchanges, conclusions)
                yield _sse("message_start", {"role": "director", "label": _director_label, "provider": _provider_for_role("director")})
                wrap_parts: list[str] = []
                for chunk in call_for_role_stream("director", wrap_msgs):
                    wrap_parts.append(chunk)
                    yield _sse("chunk", {"text": chunk})
                wrap_resp = "".join(wrap_parts)
                ctx.debate_state["debate_log"].append({"role": "director", "content": wrap_resp})
                yield _sse("message_end", {"role": "director", "content": wrap_resp})

                conclusion = {"title": topic["title"], "summary": wrap_resp.strip()}
                conclusions.append(conclusion)
                ctx.debate_state["topic_conclusions"] = list(conclusions)

                yield _sse("topic_end", {"index": t_idx, "title": topic["title"], "summary": wrap_resp.strip()})
                print(f"[FreeDeb] Topic {t_idx+1}/{len(topics)} '{topic['title']}' done")

            # ── Phase 4a: 等待投资人后台分析完成 ──
            _investor_label_free = names.get("investor", "投资人")
            _investor_thread_free.join(timeout=120)
            if _investor_thread_free.is_alive():
                print("[FreeDeb] Investor BG analysis timed out after 120s")
                _investor_bg_result_free["error"] = "分析超时"

            # ── Phase 4b: 投资人最终商业分析（流式）──
            investor_resp = ""
            try:
                print("[FreeDeb] Phase 4b: Investor final analysis")
                investor_final_msgs = prepare_free_investor_final(user_input, conclusions, _investor_bg_result_free["text"])
                yield _sse("message_start", {"role": "investor", "label": _investor_label_free, "provider": _provider_for_role("investor")})
                investor_parts: list[str] = []
                for chunk in call_for_role_stream("investor", investor_final_msgs):
                    investor_parts.append(chunk)
                    yield _sse("chunk", {"text": chunk})
                investor_resp = "".join(investor_parts)
                ctx.debate_state["debate_log"].append({"role": "investor", "content": investor_resp})
                yield _sse("message_end", {"role": "investor", "content": investor_resp})
            except Exception as inv_err:
                print(f"[FreeDeb] Investor final analysis failed: {inv_err}")
                _err_text = f"投资人分析暂时不可用（{_friendly_error(inv_err)}），导演将直接判决。"
                yield _sse("message_end", {"role": "investor", "content": _err_text})
                ctx.debate_state["debate_log"].append({"role": "investor", "content": _err_text})

            # ── Phase 4c: 导演最终判决 ──
            print("[FreeDeb] Phase 4c: Director final verdict")
            verdict_msgs = prepare_final_verdict({"need_title": user_input}, conclusions, investor_resp)
            yield _sse("message_start", {"role": "director", "label": _director_label, "provider": _provider_for_role("director")})
            verdict_parts: list[str] = []
            for chunk in call_for_role_stream("director", verdict_msgs):
                verdict_parts.append(chunk)
                yield _sse("chunk", {"text": chunk})
            verdict = "".join(verdict_parts)
            ctx.debate_state["debate_log"].append({"role": "director", "content": verdict})
            yield _sse("message_end", {"role": "director", "content": verdict})

            ctx.debate_state["status"] = "debate_done"
            ctx.debate_state["current_topic_idx"] = -1
            ctx.save_debate_cache()

            print(f"[FreeDeb] Finished: {len(topics)} topics, {len(ctx.debate_state['debate_log'])} messages")
            yield _sse("debate_end", {"reason": "director_verdict", "topics": len(topics), "messages": len(ctx.debate_state["debate_log"])})

        except Exception as e:
            import traceback
            traceback.print_exc()
            ctx.debate_state["status"] = "debate_done"
            ctx.save_debate_cache()
            yield _sse("error", {"message": _friendly_error(e)})
            yield _sse("debate_end", {"reason": "error"})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/debate/message")
def human_message(req: HumanMessageRequest, request: Request):
    ctx = _get_session(request)
    if not ctx.debate_state["debate_log"]:
        raise HTTPException(status_code=400, detail="No active debate")

    free_topic_input = ctx.debate_state.get("free_topic_input")
    if free_topic_input:
        need = {"need_title": free_topic_input, "posts": []}
    else:
        needs_data = get_needs(request)["needs"]
        idx = ctx.debate_state.get("selected_need_idx")
        if idx is None or idx < 0 or idx >= len(needs_data):
            raise HTTPException(status_code=400, detail="No need selected")
        need = needs_data[idx]

    ctx.debate_state["debate_log"].append({"role": "human", "content": req.text})

    topics = ctx.debate_state.get("topics", [])
    current_topic_idx = ctx.debate_state.get("current_topic_idx", -1)
    current_topic = topics[current_topic_idx] if 0 <= current_topic_idx < len(topics) else {"title": "讨论", "question": ""}
    topic_exchanges = ctx.debate_state.get("current_topic_exchanges", [])

    def event_stream() -> Generator[str, None, None]:
        set_thread_session(ctx)
        try:
            target = req.target
            role_label = ctx.role_names.get(target, "杠精" if target == "critic" else "产品经理")
            msgs = prepare_human_inject_topic(need, current_topic, topic_exchanges, req.text, target)

            yield _sse("message_start", {"role": target, "label": role_label, "provider": _provider_for_role(target)})
            parts: list[str] = []
            for chunk in call_for_role_stream(target, msgs):
                parts.append(chunk)
                yield _sse("chunk", {"text": chunk})
            resp = "".join(parts)
            ctx.debate_state["debate_log"].append({"role": target, "content": resp})
            yield _sse("message_end", {"role": target, "content": resp})

            ctx.save_debate_cache()
            yield _sse("done", {})

        except Exception as e:
            _log_sse_error("HumanMessage", e, ctx)
            yield _sse("error", {"message": _friendly_error(e)})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/debate/report")
def generate_report(request: Request):
    ctx = _get_session(request)
    needs_data = get_needs(request)["needs"]
    idx = ctx.debate_state.get("selected_need_idx")
    if idx is not None and 0 <= idx < len(needs_data):
        need = needs_data[idx]
    elif ctx.debate_state.get("free_topic_input"):
        need = {"need_title": ctx.debate_state["free_topic_input"], "need_description": "", "posts": []}
    else:
        raise HTTPException(status_code=400, detail="No need selected")
    debate_log = ctx.debate_state["debate_log"]
    claude_msgs = ctx.debate_state["analyst_messages"]

    def event_stream() -> Generator[str, None, None]:
        set_thread_session(ctx)
        try:
            ctx.debate_state["status"] = "generating_report"
            yield _sse("report_start", {})

            deep_dive_data = ctx.debate_state.get("deep_dive_analysis", "")

            # 限制 debate_log 体积：最多取最后 20 条，并截断过长的单条消息
            trimmed_log = debate_log[-20:] if len(debate_log) > 20 else debate_log
            report = generate_final_report(need, trimmed_log, claude_msgs, deep_dive_data)

            ctx.debate_state["final_report"] = report
            ctx.debate_state["status"] = "done"

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            slug = need["need_title"][:30].replace(" ", "_").replace("/", "-")
            filename = f"{timestamp}_{slug}.json"
            report_data = {
                "need": need,
                "debate_log": debate_log,
                "product_proposal": ctx.debate_state.get("product_proposal", ""),
                "deep_dive_analysis": deep_dive_data,
                "final_report": report,
                "debate_rounds": ctx.debate_state["round"],
                "created_at": datetime.now().isoformat(),
            }
            with open(ctx.reports_dir / filename, "w", encoding="utf-8") as f:
                json.dump(report_data, f, ensure_ascii=False, indent=2)

            ctx.save_debate_cache()
            yield _sse("report_end", {"report": report, "filename": filename})

        except Exception as e:
            import traceback
            traceback.print_exc()
            ctx.debate_state["status"] = "debate_done"
            ctx.save_debate_cache()
            yield _sse("error", {"message": _friendly_error(e)})

    return StreamingResponse(event_stream(), media_type="text/event-stream")

# ============================================================
# 直接生成报告（无需辩论）
# ============================================================

class DirectReportRequest(BaseModel):
    need_index: int
    demo: bool = False

@router.post("/generate-report")
def generate_report_direct(req: DirectReportRequest, request: Request):
    ctx = _get_session(request)
    needs_data = get_needs(request)["needs"]
    if req.need_index < 0 or req.need_index >= len(needs_data):
        raise HTTPException(status_code=400, detail="无效的需求索引")

    need = needs_data[req.need_index]

    # ===== 演示模式：读缓存报告 + 模拟生成过程 =====
    if req.demo:
        _DEMO_DIR = ROOT / "data" / "demo"
        _demo_report_path = _DEMO_DIR / "demo_report.json"
        if not _demo_report_path.exists():
            def _err_stream():
                yield _sse("error", {"message": "演示报告数据不存在"})
            return StreamingResponse(_err_stream(), media_type="text/event-stream")

        with open(_demo_report_path, "r", encoding="utf-8") as f:
            demo_report_data = json.load(f)
        demo_report_text = demo_report_data.get("final_report", "")

        def _demo_report_stream():
            import time as _time_mod
            _DEMO_REPORT_STEPS = [
                ("正在整理帖子数据...", 5),
                ("Lumon 正在并行执行：信号提炼 + 竞品搜索...", 10),
                ("信号提炼完成，分析核心痛点...", 20),
                ("竞品搜索完成：发现 5 个相关产品", 30),
                ("正在查询竞品市场数据...", 40),
                ("Lumon 正在生成研究报告...", 50),
            ]
            for msg, prog in _DEMO_REPORT_STEPS:
                yield _sse("report_progress", {"progress": prog, "message": msg})
                _time_mod.sleep(0.5)

            chunk_size = max(len(demo_report_text) // 30, 50)
            for i in range(0, len(demo_report_text), chunk_size):
                chunk = demo_report_text[i:i + chunk_size]
                yield _sse("report_chunk", {"text": chunk})
                _time_mod.sleep(0.15)

            yield _sse("report_progress", {"progress": 100, "message": "报告生成完成！"})
            yield _sse("report_done", {"report": demo_report_text, "filename": "demo_report.json"})

        return StreamingResponse(_demo_report_stream(), media_type="text/event-stream")

    def _format_posts_detail(need: dict) -> str:
        lines = []
        for i, post in enumerate(need.get("posts", []), 1):
            lines.append(f"### 帖子 {i}: {post.get('title', '')}")
            lines.append(f"- 来源: {post.get('source', 'unknown')}")
            lines.append(f"- 赞数: {post.get('score', 0)} | 评论数: {post.get('num_comments', 0)}")
            lines.append(f"- URL: {post.get('url', '')}")
            content = post.get("content", "")
            if content:
                lines.append(f"- 内容: {content[:1500]}")
            comments = post.get("comments", [])
            if comments:
                lines.append("- 评论:")
                for c in comments[:8]:
                    lines.append(f"  > {c[:500]}")
            lines.append("")
        return "\n".join(lines)


    def _parse_signal_result(raw: str) -> dict | None:
        """解析信号提炼的 JSON 结果，失败返回 None。"""
        import re as _re_sig
        try:
            return json.loads(raw)
        except Exception:
            m = _re_sig.search(r'\{[\s\S]*\}', raw)
            if m:
                try:
                    return json.loads(m.group())
                except Exception:
                    pass
        return None

    def _build_filtered_posts_summary(
        posts: list[dict], signal_json: dict | None, need_title: str
    ) -> tuple[str, int, int, int]:
        """根据信号提炼结果对帖子分级，生成分层摘要。

        返回 (summary_text, relevant_count, total_quotes, total_sources)
        """
        post_relevance: dict[str, str] = {}
        if signal_json:
            for sig in signal_json.get("extracted_signals", []):
                title = sig.get("post_title", "").strip()
                rel = sig.get("relevance", "低")
                if title:
                    post_relevance[title.lower()] = rel

        high_posts: list[dict] = []
        mid_posts: list[dict] = []
        low_posts: list[dict] = []

        for p in posts:
            title_key = p.get("title", "").strip().lower()
            rel = post_relevance.get(title_key, "中")
            if rel == "高":
                high_posts.append(p)
            elif rel in ("中", "中高"):
                mid_posts.append(p)
            else:
                low_posts.append(p)

        # 没有信号分类结果时，全部视为中相关
        if not signal_json:
            mid_posts = posts
            high_posts = []
            low_posts = []

        high_posts = high_posts[:15]
        mid_posts = mid_posts[:20]
        low_posts = low_posts[:10]

        lines: list[str] = []
        source_set: set[str] = set()

        if high_posts:
            lines.append(f"## 高相关帖子（{len(high_posts)} 个）\n")
            for i, p in enumerate(high_posts, 1):
                src = p.get("source", "").split("/")[0] if "/" in p.get("source", "") else p.get("source", "unknown")
                source_set.add(src)
                lines.append(f"### [{i}] {p.get('title', '')}")
                lines.append(f"- 来源: {p.get('source', '')} | 赞: {p.get('score', 0)} | 评论: {p.get('num_comments', 0)} | URL: {p.get('url', '')}")
                content = p.get("content", "")
                if content:
                    lines.append(f"- 内容: {content[:800]}")
                comments = p.get("comments", [])
                if comments:
                    lines.append("- 评论:")
                    for c in comments[:5]:
                        lines.append(f"  > {c[:300]}")
                lines.append("")

        if mid_posts:
            lines.append(f"\n## 中相关帖子（{len(mid_posts)} 个）\n")
            for i, p in enumerate(mid_posts, 1):
                src = p.get("source", "").split("/")[0] if "/" in p.get("source", "") else p.get("source", "unknown")
                source_set.add(src)
                lines.append(f"[{i}] {p.get('title', '')} | {p.get('source', '')} | 赞: {p.get('score', 0)} | URL: {p.get('url', '')}")
                content = p.get("content", "")
                if content:
                    lines.append(f"  摘要: {content[:300]}")

        if low_posts:
            lines.append(f"\n## 低相关帖子（{len(low_posts)} 个）\n")
            for p in low_posts:
                lines.append(f"- {p.get('title', '')}（{p.get('source', '')}）")

        relevant_count = len(high_posts) + len(mid_posts)
        total_quotes = 0
        if signal_json:
            for sig in signal_json.get("extracted_signals", []):
                if sig.get("relevance") in ("高", "中", "中高"):
                    total_quotes += len(sig.get("verbatim_quotes", []))

        return "\n".join(lines), relevant_count, total_quotes, len(source_set)

    def _report_emit(progress: int, message: str):
        with ctx.report_lock:
            ctx.report_job["progress"] = progress
            ctx.report_job["message"] = message

    def _report_chunk(text: str):
        with ctx.report_lock:
            ctx.report_job["chunks"].append(text)

    def _run_report_bg():
        set_thread_session(ctx)
        try:
            _report_emit(5, "正在整理帖子数据...")

            all_posts = need.get("posts", [])
            full_posts_summary = _format_posts_detail(need)
            post_count = len(all_posts)

            deep_dive_data = ""
            dmp = need.get("deep_mine_package")
            if dmp:
                deep_dive_data = json.dumps(dmp, ensure_ascii=False, indent=2)

            original_topic = need.get("original_topic", "")
            report_title = original_topic if original_topic else need.get("need_title", "")
            report_desc = need.get("need_description", "")
            if original_topic and original_topic != need.get("need_title", ""):
                report_desc = f"用户研究方向：{original_topic}。聚类子主题：{need.get('need_title', '')}。{report_desc}"

            _report_emit(10, "Lumon 正在并行执行：信号提炼 + 竞品搜索...")

            import concurrent.futures as _cf

            signal_prompt = SIGNAL_EXTRACTION_PROMPT \
                .replace("{need_title}", report_title) \
                .replace("{need_description}", report_desc) \
                .replace("{posts_summary}", full_posts_summary)
            signal_messages = [
                {"role": "system", "content": f"你是一位资深用户研究分析师。你的任务是深入理解「{report_title}」这个需求，然后从帖子数据中精准提炼出与之相关的信号。只输出 JSON，不要多余内容。"},
                {"role": "user", "content": signal_prompt},
            ]

            def _run_signal_extraction():
                set_thread_session(ctx)
                chunks = []
                for chunk in call_llm_stream(signal_messages):
                    chunks.append(chunk)
                return "".join(chunks)

            _comp_state = {"msgs": [], "failed": False}
            def _comp_progress(msg):
                _comp_state["msgs"].append(msg)
                if msg.startswith("⚠️"):
                    _comp_state["failed"] = True

            def _run_competitor_search():
                set_thread_session(ctx)
                try:
                    return search_competitors(
                        need_title=report_title,
                        need_description=f"{report_desc}\n\n帖子关键内容摘要：\n{full_posts_summary[:2000]}",
                        posts_hint=full_posts_summary[:2000],
                        progress_callback=_comp_progress,
                        web_search_engine=ctx.web_search_engine,
                    )
                except Exception as e:
                    _comp_state["failed"] = True
                    _comp_state["msgs"].append(f"⚠️ 竞品搜索异常：{str(e)[:100]}，请在设置 > WebSearch 中检查引擎配置")
                    return "（竞品搜索失败）"

            _PARALLEL_TIMEOUT = 180
            with _cf.ThreadPoolExecutor(max_workers=2) as executor:
                signal_future = executor.submit(_run_signal_extraction)
                comp_future = executor.submit(_run_competitor_search)

                import time as _rpt_time
                _phase_msgs = [
                    "信号分析器正在评估帖子相关度...",
                    "提取用户痛点和使用场景...",
                    "竞品搜索进行中，收集定价和评分...",
                    "整理竞品链接和用户评价...",
                    "深度分析竞品数据...",
                    "信号提炼接近完成...",
                    "等待竞品搜索返回...",
                    "竞品数据汇总中...",
                    "前置分析即将完成...",
                ]
                _msg_idx = 0
                _max_p = 10
                _parallel_start = _rpt_time.time()
                while not (signal_future.done() and comp_future.done()):
                    if _rpt_time.time() - _parallel_start > _PARALLEL_TIMEOUT:
                        _report_emit(_max_p, "并行阶段超时，继续使用已完成的结果...")
                        break
                    _rpt_time.sleep(3)
                    _max_p = min(_max_p + 2, 48)
                    if _msg_idx < len(_phase_msgs):
                        _msg = _phase_msgs[_msg_idx]
                        _msg_idx += 1
                    else:
                        parts = []
                        if signal_future.done():
                            parts.append("信号提炼 ✓")
                        else:
                            parts.append("信号提炼中...")
                        if comp_future.done():
                            parts.append("竞品搜索 ✓")
                        elif _comp_state["msgs"]:
                            parts.append(_comp_state["msgs"][-1][:40])
                        else:
                            parts.append("竞品搜索中...")
                        _msg = " | ".join(parts)
                    _report_emit(_max_p, _msg)

                signal_result = signal_future.result(timeout=5) if signal_future.done() else ""
                competitor_research = comp_future.result(timeout=5) if comp_future.done() else "（竞品搜索超时）"

            print(f"[SignalExtraction] 信号提炼完成，长度={len(signal_result)}")

            signal_json = _parse_signal_result(signal_result)
            if signal_json:
                sigs = signal_json.get("extracted_signals", [])
                high_count = sum(1 for s in sigs if s.get("relevance") == "高")
                mid_count = sum(1 for s in sigs if s.get("relevance") in ("中", "中高"))
                low_count = sum(1 for s in sigs if s.get("relevance") in ("低", "无关"))
                print(f"[SignalFilter] 帖子分级：高={high_count} 中={mid_count} 低/无关={low_count}")
            else:
                print("[SignalFilter] 信号提炼 JSON 解析失败，帖子将全量传入报告")

            filtered_summary, relevant_count, quote_count, source_count = \
                _build_filtered_posts_summary(all_posts, signal_json, report_title)

            sources = list(set(
                p.get("source", "").split("/")[0] if "/" in p.get("source", "") else p.get("source", "unknown")
                for p in all_posts
            ))
            sources_str = ", ".join(s.capitalize() for s in sources) if sources else "未知"

            _report_emit(30, f"信号提炼完成（{relevant_count}/{post_count} 个帖子高度相关）")

            comp_failed = _comp_state["failed"]
            if comp_failed:
                _report_emit(34, "竞品搜索失败，将生成无竞品数据的报告（可稍后重试）")
                competitor_research = "（竞品联网搜索失败，报告中竞品相关章节数据不足，请在设置 > WebSearch 中检查引擎配置后重新生成）"

            _report_emit(45, "竞品调研完成，Lumon 正在撰写分析报告...")

            comp_data_note = "\n⚠️ 竞品格局：使用**精简表格**（产品/类型/定价/评分/AI/核心差异化/链接），不要编造下载量、收入、月活等数字。定价和评分从竞品搜索数据中提取。"

            prompt_text = DIRECT_REPORT_PROMPT \
                .replace("{need_title}", report_title) \
                .replace("{need_description}", report_desc) \
                .replace("{posts_summary}", filtered_summary) \
                .replace("{deep_dive_data}", deep_dive_data or "（暂无深挖数据）") \
                .replace("{competitor_research}", competitor_research) \
                .replace("{sources}", sources_str) \
                .replace("{post_count}", str(post_count))

            signal_context_parts = [
                "在生成报告之前，信号分析器已对帖子数据做了需求理解和相关度评估：\n"
            ]
            if signal_json:
                understanding = signal_json.get("need_understanding", {})
                signal_context_parts.append(
                    f"- 核心用户：{understanding.get('core_users', '未知')}\n"
                    f"- 核心场景：{understanding.get('core_scenario', '未知')}\n"
                    f"- 核心痛点：{understanding.get('core_pain', '未知')}\n"
                    f"- 综合信号：{signal_json.get('overall_signal_summary', '')}\n"
                )
            else:
                signal_context_parts.append(f"{signal_result[:800]}\n")

            signal_context_parts.append(
                f"\n帖子数据已按相关度分层（高相关=完整内容、中相关=摘要、低相关=仅标题）。\n"
                f"📊 共 {post_count} 个帖子，{relevant_count} 个与主题直接相关，来自 {source_count} 个数据源。"
                f"{comp_data_note}\n\n"
            )
            report_context = "".join(signal_context_parts)

            _report_emit(50, "Lumon 正在撰写分析报告...")

            messages = [
                {"role": "system", "content": f"你是一位资深产品分析师，擅长从用户反馈中发现产品机会。只推荐面向 C 端海外市场的 App/软件/AI 工具方案，不涉及实物硬件。竞品格局表格只列真实存在的软件产品。\n\n⚠️ 最重要的约束：这份报告必须紧密围绕「{report_title}」展开，所有分析、痛点、竞品都必须与这个主题直接相关。不要偏离到其他方向。"},
                {"role": "user", "content": report_context + prompt_text},
            ]

            for chunk_text in call_llm_stream(messages, max_tokens=16000):
                _report_chunk(chunk_text)

            with ctx.report_lock:
                all_chunks = ctx.report_job["chunks"]
            if not all_chunks:
                print("[ReportGen] WARNING: LLM returned empty report")
                with ctx.report_lock:
                    ctx.report_job["error"] = "模型未返回任何报告内容，请检查 API 配置后重试"
                    ctx.report_job["active"] = False
                return

            report = "".join(all_chunks)

            _report_emit(90, "正在保存报告...")

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            slug_src = need.get("original_topic", "") or need.get("need_title", "report")
            slug = slug_src[:30].replace(" ", "_").replace("/", "-")
            filename = f"{timestamp}_{slug}.json"
            report_data = {
                "need": need,
                "final_report": report,
                "report_format": "markdown",
                "debate_rounds": 0,
                "created_at": datetime.now().isoformat(),
            }
            with open(ctx.reports_dir / filename, "w", encoding="utf-8") as f:
                json.dump(report_data, f, ensure_ascii=False, indent=2)

            with ctx.report_lock:
                ctx.report_job["progress"] = 100
                ctx.report_job["message"] = "报告生成完成！"
                ctx.report_job["filename"] = filename
                ctx.report_job["done"] = True
                ctx.report_job["active"] = False
            print(f"[ReportGen] OK session={ctx.session_id} file={filename} len={len(report)}")

        except Exception as e:
            _log_sse_error("ReportGen", e, ctx)
            with ctx.report_lock:
                ctx.report_job["error"] = _friendly_error(e)
                ctx.report_job["active"] = False
        finally:
            clear_thread_session()

    # ===== 同步预检：模型不可用直接返回错误，不启动后台线程 =====
    set_thread_session(ctx)
    try:
        llm_ok, llm_err = check_llm_available()
    finally:
        clear_thread_session()
    if not llm_ok:
        model_name = "GPT" if ctx._general_model == "gpt" else "Claude"
        err_msg = f"{model_name} 模型不可用，请前往「设置」检查配置"
        def _err_stream():
            yield _sse("error", {"message": err_msg})
        return StreamingResponse(_err_stream(), media_type="text/event-stream")

    # 启动后台线程
    with ctx.report_lock:
        ctx.report_job = ctx._empty_report_job()
        ctx.report_job["active"] = True
        ctx.report_job["need_index"] = req.need_index
    t = threading.Thread(target=_run_report_bg, daemon=True)
    ctx.report_thread = t
    t.start()

    # SSE 流从 report_job 读取事件，客户端断开不影响后台线程
    def event_stream() -> Generator[str, None, None]:
        _last_progress = -1
        _chunk_cursor = 0
        while True:
            with ctx.report_lock:
                job = ctx.report_job
                active = job["active"]
                progress = job["progress"]
                message = job["message"]
                chunks = job["chunks"]
                error = job["error"]
                done = job["done"]
                filename = job["filename"]

            if error:
                yield _sse("error", {"message": error})
                return

            if progress != _last_progress and message:
                yield _sse("report_progress", {"progress": progress, "message": message})
                _last_progress = progress

            if _chunk_cursor < len(chunks):
                for i in range(_chunk_cursor, len(chunks)):
                    yield _sse("report_chunk", {"text": chunks[i]})
                _chunk_cursor = len(chunks)

            if done:
                yield _sse("report_progress", {"progress": 100, "message": "报告生成完成！"})
                yield _sse("report_done", {"report": "".join(chunks), "filename": filename})
                yield "\n"
                return

            if not active and not done:
                return

            _time.sleep(0.5)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/report-gen/status")
def report_gen_status(request: Request):
    """报告生成状态（用于页面刷新后恢复）。"""
    ctx = _get_session(request)
    with ctx.report_lock:
        job = ctx.report_job
        return {
            "active": job["active"],
            "need_index": job["need_index"],
            "progress": job["progress"],
            "message": job["message"],
            "error": job["error"],
            "done": job["done"],
            "filename": job["filename"],
            "chunk_count": len(job["chunks"]),
        }


@router.get("/report-gen/stream")
def report_gen_stream(request: Request):
    """重连 SSE 流，从 report_job 当前状态继续读取。"""
    ctx = _get_session(request)

    def event_stream() -> Generator[str, None, None]:
        _last_progress = -1
        _chunk_cursor = 0
        while True:
            with ctx.report_lock:
                job = ctx.report_job
                active = job["active"]
                progress = job["progress"]
                message = job["message"]
                chunks = job["chunks"]
                error = job["error"]
                done = job["done"]
                filename = job["filename"]

            if error:
                yield _sse("error", {"message": error})
                return

            if progress != _last_progress and message:
                yield _sse("report_progress", {"progress": progress, "message": message})
                _last_progress = progress

            if _chunk_cursor < len(chunks):
                for i in range(_chunk_cursor, len(chunks)):
                    yield _sse("report_chunk", {"text": chunks[i]})
                _chunk_cursor = len(chunks)

            if done:
                yield _sse("report_progress", {"progress": 100, "message": "报告生成完成！"})
                yield _sse("report_done", {"report": "".join(chunks), "filename": filename})
                yield "\n"
                return

            if not active and not done:
                return

            _time.sleep(0.5)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ============================================================
# Reports routes
# ============================================================

_report_list_cache: dict[str, dict] = {}

@router.get("/reports")
def list_reports(request: Request):
    ctx = _get_session(request)
    reports_dir = ctx.reports_dir
    cache_key = str(reports_dir.resolve())
    report_files = sorted(reports_dir.glob("*.json"), reverse=True)
    if not report_files:
        return {"reports": []}
    latest_mtime = max(f.stat().st_mtime for f in report_files)
    cached = _report_list_cache.get(cache_key)
    if cached and cached["data"] is not None and latest_mtime <= cached["mtime"]:
        return {"reports": cached["data"]}

    reports = []
    for rf in report_files:
        try:
            with open(rf, "r", encoding="utf-8") as f:
                data = json.load(f)
            title = data.get("need", {}).get("need_title") or data.get("post", {}).get("title", "未知")
            report_content = data.get("final_report", "")
            verdict = ""
            femwc_total = None
            ai_fit = ""
            if isinstance(report_content, dict):
                verdict = report_content.get("verdict", "")
                ai_fit = report_content.get("ai_fit", "")
                fs = report_content.get("femwc_scores") or report_content.get("femwc_after") or {}
                if isinstance(fs, dict) and "total" in fs:
                    femwc_total = fs["total"]
            elif isinstance(report_content, str):
                try:
                    rj = json.loads(report_content)
                    verdict = rj.get("verdict", "")
                    ai_fit = rj.get("ai_fit", "")
                    fs = rj.get("femwc_scores") or rj.get("femwc_after") or {}
                    if isinstance(fs, dict) and "total" in fs:
                        femwc_total = fs["total"]
                except Exception:
                    pass
            reports.append({
                "filename": rf.name,
                "title": title,
                "created_at": data.get("created_at", ""),
                "rounds": data.get("debate_rounds", 0),
                "report_format": data.get("report_format", "json"),
                "verdict": verdict,
                "femwc_total": femwc_total,
                "ai_fit": ai_fit,
            })
        except Exception:
            pass
    _report_list_cache[cache_key] = {"mtime": latest_mtime, "data": reports}
    return {"reports": reports}


@router.get("/reports/{filename}")
def get_report(filename: str, request: Request):
    ctx = _get_session(request)
    fpath = _safe_path(ctx.reports_dir, filename)
    if not fpath.exists():
        raise HTTPException(status_code=404, detail="Report not found")
    with open(fpath, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data


@router.delete("/reports/{filename}")
def delete_report(filename: str, request: Request):
    ctx = _get_session(request)
    fpath = _safe_path(ctx.reports_dir, filename)
    if not fpath.exists():
        raise HTTPException(status_code=404, detail="Report not found")
    fpath.unlink()
    cache_key = f"{ctx.session_id}:{ctx.reports_dir}"
    _report_list_cache.pop(cache_key, None)
    return {"ok": True}


@router.post("/reports/{filename}/export-feishu")
def export_to_feishu(filename: str, request: Request):
    """将报告导出为飞书在线文档。"""
    from feishu_client import is_feishu_configured, create_feishu_doc

    if not is_feishu_configured():
        raise HTTPException(status_code=400, detail="飞书未配置：请在设置中填写 App ID 和 App Secret")

    ctx = _get_session(request)
    fpath = _safe_path(ctx.reports_dir, filename)
    if not fpath.exists():
        raise HTTPException(status_code=404, detail="Report not found")

    with open(fpath, "r", encoding="utf-8") as f:
        data = json.load(f)

    title = data.get("need", {}).get("need_title") or data.get("post", {}).get("title", "需求分析报告")
    report = data.get("final_report", "")
    if not isinstance(report, str):
        report = json.dumps(report, ensure_ascii=False, indent=2)

    try:
        result = create_feishu_doc(title, report)
        # 持久化飞书发布信息到报告 JSON
        data["feishu"] = {"url": result["url"], "document_id": result["document_id"]}
        with open(fpath, "w", encoding="utf-8") as fw:
            json.dump(data, fw, ensure_ascii=False, indent=2)
        return {"ok": True, "url": result["url"], "document_id": result["document_id"]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/config/feishu-status")
def feishu_status():
    """返回飞书是否已配置（不暴露密钥）。"""
    from feishu_client import is_feishu_configured
    return {"configured": is_feishu_configured()}


@router.get("/config/st-status")
def sensortower_status():
    """返回 SensorTower (st-cli) 是否已安装且已认证。"""
    status = st_check_available()
    return {
        "installed": status.get("installed", False),
        "available": status.get("available", False),
        "api_ok": status.get("api_ok", False),
        "error": status.get("error", ""),
    }



# ============================================================
# Phase 2: Deep Dive (product proposal → web research → analysis)
# ============================================================

@router.post("/debate/proposal")
def generate_proposal(request: Request):
    """Generate a product proposal from Phase 1 discussion."""
    ctx = _get_session(request)
    needs_data = get_needs(request)["needs"]
    idx = ctx.debate_state.get("selected_need_idx")
    if idx is not None and 0 <= idx < len(needs_data):
        need = needs_data[idx]
    elif ctx.debate_state.get("free_topic_input"):
        need = {"need_title": ctx.debate_state["free_topic_input"], "need_description": "", "posts": []}
    else:
        raise HTTPException(status_code=400, detail="No need selected")
    debate_log = ctx.debate_state["debate_log"]

    def event_stream() -> Generator[str, None, None]:
        set_thread_session(ctx)
        try:
            ctx.debate_state["status"] = "generating_proposal"
            yield _sse("proposal_start", {})

            proposal = generate_product_proposal(need, debate_log)
            ctx.debate_state["product_proposal"] = proposal
            ctx.debate_state["status"] = "proposal_done"
            ctx.save_debate_cache()

            yield _sse("proposal_end", {"proposal": proposal})

        except Exception as e:
            _log_sse_error("DebateReport", e, ctx)
            ctx.debate_state["status"] = "debate_done"
            ctx.save_debate_cache()
            yield _sse("error", {"message": _friendly_error(e)})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/deep-dive/start")
def start_deep_dive(request: Request):
    """Phase 2: web search + deep dive analysis (SSE stream)."""
    ctx = _get_session(request)
    proposal = ctx.debate_state.get("product_proposal")
    if not proposal:
        raise HTTPException(status_code=400, detail="No product proposal yet")

    def event_stream() -> Generator[str, None, None]:
        set_thread_session(ctx)
        try:
            from web_search import run_deep_dive_searches, format_search_results_for_llm

            ctx.debate_state["status"] = "deep_diving"

            yield _sse("message_start", {"role": "researcher", "label": "调研员", "provider": _provider_for_role("analyst")})
            yield _sse("chunk", {"text": "收到产品方案了，我来做一轮深度调研。"})
            yield _sse("message_end", {"role": "researcher", "content": "收到产品方案了，我来做一轮深度调研。"})
            ctx.debate_state["debate_log"].append({"role": "researcher", "content": "收到产品方案了，我来做一轮深度调研。"})

            all_results: list[tuple[str, list[dict]]] = []

            def on_progress(msg: str):
                pass  # progress is sent via search_progress events

            for query, results in run_deep_dive_searches(proposal):
                all_results.append((query, results))
                result_count = sum(len(r) for _, r in all_results)
                yield _sse("search_progress", {
                    "query": query,
                    "result_count": len(results),
                    "total_results": result_count,
                    "total_queries": len(all_results),
                })

            search_text = format_search_results_for_llm(all_results)
            ctx.debate_state["search_results"] = search_text

            yield _sse("message_start", {"role": "researcher", "label": "调研员", "provider": _provider_for_role("analyst")})

            dive_msgs = prepare_deep_dive_messages(proposal, search_text)
            parts: list[str] = []
            for chunk in call_for_role_stream("analyst", dive_msgs):
                parts.append(chunk)
                yield _sse("chunk", {"text": chunk})
            analysis = "".join(parts)

            ctx.debate_state["deep_dive_analysis"] = analysis
            ctx.debate_state["debate_log"].append({"role": "researcher", "content": analysis})
            yield _sse("message_end", {"role": "researcher", "content": analysis})

            ctx.debate_state["status"] = "deep_dive_done"
            ctx.save_debate_cache()
            yield _sse("deep_dive_end", {})

        except Exception as e:
            import traceback
            traceback.print_exc()
            ctx.debate_state["status"] = "proposal_done"
            ctx.save_debate_cache()
            yield _sse("error", {"message": _friendly_error(e)})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ============================================================
# Engine status & Deep Mine
# ============================================================

@router.get("/engine-status")
def engine_status(request: Request, force: bool = False):
    """Return current Reddit engine status for the frontend."""
    ctx = _get_session(request)
    import asyncio as _aio
    loop = _aio.new_event_loop()
    try:
        fetcher = get_reddit_fetcher()
        async def _init():
            rdt_status = await fetcher.rdt.check_available(force=force)
            if rdt_status["installed"] and rdt_status["authenticated"]:
                fetcher._active_engine = "rdt-cli"
                return {"engine": "rdt-cli", "rdt_status": rdt_status}
            fetcher._active_engine = "none"
            return {"engine": "none", "rdt_status": rdt_status}
        info = loop.run_until_complete(_init())
        info["preference"] = ctx.engine_preference
        return info
    except Exception as e:
        return {"engine": "error", "error": str(e), "preference": ctx.engine_preference}
    finally:
        loop.close()


@router.get("/engine-preference")
def get_engine_preference(request: Request):
    ctx = _get_session(request)
    return {"preference": ctx.engine_preference}


@router.post("/engine-preference")
def set_engine_preference(body: dict, request: Request):
    ctx = _get_session(request)
    pref = body.get("preference", "auto")
    if pref not in ("auto", "rdt-cli"):
        raise HTTPException(status_code=400, detail="无效的引擎偏好")
    ctx.save_engine_preference(pref)
    return {"ok": True, "preference": pref}


@router.get("/web-search-engine")
def get_web_search_engine(request: Request):
    ctx = _get_session(request)
    return {"engine": ctx.web_search_engine}


@router.post("/web-search-engine")
def set_web_search_engine(body: dict, request: Request):
    ctx = _get_session(request)
    engine = body.get("engine", "tavily")
    if engine not in ("tavily", "claude", "gpt"):
        raise HTTPException(status_code=400, detail="无效的搜索引擎")
    ctx.save_web_search_engine(engine)
    return {"ok": True, "engine": engine}


@router.post("/web-search-test")
def test_web_search(body: dict, request: Request):
    """测试当前 WebSearch 引擎是否可用。"""
    ctx = _get_session(request)
    engine = body.get("engine", ctx.web_search_engine)
    if engine == "tavily":
        try:
            from web_search import _get_tavily_client
            client = _get_tavily_client()
            r = client.search(query="test", search_depth="basic", max_results=1, include_answer=False)
            if r and r.get("results") is not None:
                return {"ok": True, "message": "Tavily API 连接正常"}
            return {"ok": False, "message": "Tavily API 返回异常"}
        except ValueError as e:
            return {"ok": False, "message": str(e)}
        except Exception as e:
            return {"ok": False, "message": f"Tavily 连接失败: {str(e)[:100]}"}
    elif engine == "gpt":
        from openai import OpenAI
        cfg = get_provider_config("GPT")
        base_url, api_key, model = cfg["base_url"], cfg["api_key"], cfg["model"]
        if not base_url or not api_key:
            return {"ok": False, "message": "GPT 未配置（缺少 API Key 或 Base URL）"}
        try:
            client = OpenAI(base_url=base_url, api_key=api_key)
            from web_search import _test_web_search_support
            supported = _test_web_search_support(client, model, "GPT")
            if supported:
                return {"ok": True, "message": f"GPT WebSearch 可用（{model}）"}
            return {"ok": False, "message": f"GPT 模型不支持 web_search 工具（{model}）"}
        except Exception as e:
            return {"ok": False, "message": f"GPT 连接失败: {str(e)[:100]}"}
    elif engine == "claude":
        from openai import OpenAI
        cfg = get_provider_config("CLAUDE")
        base_url, api_key, model = cfg["base_url"], cfg["api_key"], cfg["model"]
        if not base_url or not api_key:
            return {"ok": False, "message": "Claude 未配置（缺少 API Key 或 Base URL）"}
        try:
            client = OpenAI(base_url=base_url, api_key=api_key)
            from web_search import _test_web_search_support
            supported = _test_web_search_support(client, model, "Claude")
            if supported:
                return {"ok": True, "message": f"Claude WebSearch 可用（{model}）"}
            return {"ok": False, "message": f"Claude 中转站不支持 web_search 工具（{model}）"}
        except Exception as e:
            return {"ok": False, "message": f"Claude 连接失败: {str(e)[:100]}"}
    return {"ok": False, "message": "未知引擎"}


# ============================================================
# Trending 热度排行模块
# ============================================================

TRENDING_DIR = ROOT / "data" / "trending"
TRENDING_HISTORY_DIR = TRENDING_DIR / "history"
TRENDING_CUSTOM_FILE = TRENDING_DIR / "custom_categories.json"
TRENDING_DIR.mkdir(parents=True, exist_ok=True)
TRENDING_HISTORY_DIR.mkdir(parents=True, exist_ok=True)

_trending_cache: dict = {"data": None, "ts": 0}
_TRENDING_TTL = 86400  # 24 小时
_trending_refreshing = False  # 是否正在后台刷新


def _load_latest_snapshot() -> dict | None:
    """从文件加载最新的快照作为兜底数据。"""
    import glob as _glob
    files = sorted(_glob.glob(str(TRENDING_HISTORY_DIR / "*.json")))
    if not files:
        return None
    try:
        snap = json.loads(Path(files[-1]).read_text())
        return {"categories": snap.get("categories", []), "scanned_at": snap.get("scanned_at", "")}
    except Exception:
        return None


def _get_snapshot_ts() -> float | None:
    """获取最新快照文件的修改时间戳。"""
    import glob as _glob
    files = sorted(_glob.glob(str(TRENDING_HISTORY_DIR / "*.json")))
    if not files:
        return None
    try:
        return Path(files[-1]).stat().st_mtime
    except Exception:
        return None


def _build_skeleton() -> dict:
    """返回品类骨架（仅名称，无数据），用于首次加载时立即展示。"""
    all_cats = _get_all_categories()
    skeleton = []
    for key, info in all_cats.items():
        skeleton.append({
            "key": key,
            "label": info.get("label", key),
            "label_en": info.get("label_en", ""),
            "st_queries": info.get("st_queries", []),
            "st_category_id": info.get("st_category_id"),
            "custom": info.get("custom", False),
            "subreddits": [],
            "hn_posts": [],
            "reddit_score": 0,
            "reddit_comments": 0,
            "hn_score": 0,
            "hn_comments": 0,
            "total_score": 0,
            "total_comments": 0,
            "total_subscribers": 0,
            "heat_index": 0,
            "market": None,
            "change_pct": 0,
            "alert": None,
        })
    return {"categories": skeleton, "scanned_at": "", "scanning": True}


def _load_custom_categories() -> dict:
    if TRENDING_CUSTOM_FILE.exists():
        try:
            return json.loads(TRENDING_CUSTOM_FILE.read_text())
        except Exception:
            pass
    return {}


def _get_all_categories() -> dict:
    merged = dict(REDDIT_CATEGORIES)
    custom = _load_custom_categories()
    for k, v in custom.items():
        v["custom"] = True
        merged[k] = v
    return merged


def _save_trending_snapshot(data: dict):
    """保存当日热度快照并清理 30 天前的历史"""
    today = datetime.now().strftime("%Y-%m-%d")
    snapshot = {"date": today, "scanned_at": data.get("scanned_at", ""), "categories": data.get("categories", [])}
    (TRENDING_HISTORY_DIR / f"{today}.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2))
    import glob as _glob
    files = sorted(_glob.glob(str(TRENDING_HISTORY_DIR / "*.json")))
    if len(files) > 30:
        for old in files[:-30]:
            try:
                Path(old).unlink()
            except Exception:
                pass


def _load_history_snapshots(days: int = 7) -> list[dict]:
    """加载最近 N 天的历史快照"""
    import glob as _glob
    files = sorted(_glob.glob(str(TRENDING_HISTORY_DIR / "*.json")))
    result = []
    for f in files[-days:]:
        try:
            result.append(json.loads(Path(f).read_text()))
        except Exception:
            pass
    return result


def _compute_alerts(categories: list[dict], history: list[dict]) -> dict[str, dict]:
    """对比历史数据计算涨跌幅和异常告警"""
    alerts: dict[str, dict] = {}
    if len(history) < 2:
        for cat in categories:
            alerts[cat["key"]] = {"change_pct": 0, "alert": None}
        return alerts

    yesterday_map: dict[str, float] = {}
    for snap in history:
        for c in snap.get("categories", []):
            yesterday_map[c["key"]] = c.get("heat_index", 0)

    history_values: dict[str, list[float]] = {}
    for snap in history[:-1]:
        for c in snap.get("categories", []):
            history_values.setdefault(c["key"], []).append(c.get("heat_index", 0))

    for cat in categories:
        key = cat["key"]
        current = cat.get("heat_index", 0)
        prev = yesterday_map.get(key, 0)
        change_pct = round((current - prev) / max(prev, 1) * 100, 1) if prev else 0

        alert = None
        vals = history_values.get(key, [])
        if len(vals) >= 3:
            import statistics
            mean = statistics.mean(vals)
            stdev = statistics.stdev(vals) if len(vals) > 1 else 0
            if stdev > 0:
                if current > mean + 1.5 * stdev:
                    alert = "surge"
                elif current < mean - 1.5 * stdev:
                    alert = "cool"

        alerts[key] = {"change_pct": change_pct, "alert": alert}

    return alerts


async def _scan_hn_for_category(cat_info: dict) -> dict:
    """用 Algolia 搜索 HN 上与赛道相关的当日热帖"""
    import time as _t
    hn_tags = cat_info.get("hn_tags", [])
    if not hn_tags:
        return {"hn_posts": [], "hn_score": 0, "hn_comments": 0}

    min_ts = int(_t.time()) - 86400
    all_posts: list[dict] = []
    seen_ids: set[str] = set()

    async with httpx.AsyncClient(timeout=20.0) as client:
        for tag in hn_tags[:3]:
            try:
                resp = await client.get(
                    f"{HN_ALGOLIA_BASE}/search",
                    params={"query": tag, "tags": "story", "hitsPerPage": 5,
                            "numericFilters": f"created_at_i>{min_ts}"},
                )
                if resp.status_code != 200:
                    continue
                for hit in resp.json().get("hits", []):
                    sid = hit.get("objectID", "")
                    if sid in seen_ids:
                        continue
                    seen_ids.add(sid)
                    score = hit.get("points", 0) or 0
                    nc = hit.get("num_comments", 0) or 0
                    if score < 3:
                        continue
                    all_posts.append({
                        "title": hit.get("title", ""),
                        "score": score,
                        "num_comments": nc,
                        "url": hit.get("url", "") or f"https://news.ycombinator.com/item?id={sid}",
                        "hn_url": f"https://news.ycombinator.com/item?id={sid}",
                        "source": "hackernews",
                    })
            except Exception:
                pass

    all_posts.sort(key=lambda p: p["score"], reverse=True)
    top = all_posts[:5]
    return {
        "hn_posts": top,
        "hn_score": sum(p["score"] for p in top),
        "hn_comments": sum(p["num_comments"] for p in top),
    }


async def _do_trending_scan():
    """实际执行热度扫描（社区 + ST 市场数据），结果写入缓存和快照。"""
    import asyncio, time, math
    from concurrent.futures import ThreadPoolExecutor
    from st_client import fetch_category_market_data, fetch_niche_market_data
    global _trending_refreshing

    all_categories = _get_all_categories()

    async def _scan_sub(sub_name: str) -> dict:
        hot = await fetch_subreddit_hot(sub_name, sort="top", time_filter="day", limit=5)
        sub_score = sum(p.get("score", 0) for p in hot)
        sub_comments = sum(p.get("num_comments", 0) for p in hot)
        print(f"[Trending] rdt r/{sub_name}: {len(hot)} posts, score={sub_score}, comments={sub_comments}")
        return {
            "name": sub_name,
            "subscribers": 0,
            "active_users": 0,
            "hot_posts": hot,
            "day_score": sub_score,
            "day_comments": sub_comments,
        }

    async def _scan_category_reddit(cat_key: str, cat_info: dict) -> tuple[str, list[dict]]:
        """串行扫描单个品类的 Reddit subreddits（避免 rate limit）。"""
        subs = cat_info.get("subreddits", [])[:3]
        sub_data: list[dict] = []
        for s in subs:
            result = await _scan_sub(s)
            sub_data.append(result)
        return cat_key, sub_data

    try:
        # 第一阶段：并行获取所有品类的 HN 数据（不受 rate limit）
        cat_items = list(all_categories.items())
        hn_tasks = {k: _scan_hn_for_category(v) for k, v in cat_items}
        hn_results_raw = await asyncio.gather(*hn_tasks.values(), return_exceptions=True)
        hn_map: dict[str, dict] = {}
        for (k, _), r in zip(cat_items, hn_results_raw):
            hn_map[k] = r if isinstance(r, dict) else {"hn_posts": [], "hn_score": 0, "hn_comments": 0}

        # 第二阶段：串行扫描 Reddit（每次只扫一个品类，避免 rate limit）
        reddit_map: dict[str, list[dict]] = {}
        for k, v in cat_items:
            _, sub_data = await _scan_category_reddit(k, v)
            reddit_map[k] = sub_data

        # 合并结果
        results: list[dict] = []
        for k, v in cat_items:
            sub_data = reddit_map.get(k, [])
            hn_data = hn_map.get(k, {"hn_posts": [], "hn_score": 0, "hn_comments": 0})
            reddit_score = sum(s["day_score"] for s in sub_data)
            reddit_comments = sum(s["day_comments"] for s in sub_data)
            result = {
                "key": k,
                "label": v.get("label", k),
                "label_en": v.get("label_en", ""),
                "st_queries": v.get("st_queries", []),
                "st_category_id": v.get("st_category_id"),
                "custom": v.get("custom", False),
                "subreddits": sub_data,
                "hn_posts": hn_data["hn_posts"],
                "reddit_score": reddit_score,
                "reddit_comments": reddit_comments,
                "hn_score": hn_data["hn_score"],
                "hn_comments": hn_data["hn_comments"],
                "total_score": reddit_score + hn_data["hn_score"],
                "total_comments": reddit_comments + hn_data["hn_comments"],
                "total_subscribers": 0,
                "heat_index": 0,
            }
            print(f"[Trending] category '{k}' done: reddit={reddit_score}/{reddit_comments} hn={hn_data['hn_score']}/{hn_data['hn_comments']}")
            results.append(result)

        st_queries_map = {
            r["key"]: r.get("st_queries", [])
            for r in results if r.get("st_queries")
        }
        market_map: dict[str, dict] = {}
        if st_queries_map:
            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor(max_workers=4) as pool:
                async def _fetch_market(key: str, queries: list[str]):
                    try:
                        return key, await loop.run_in_executor(
                            pool, lambda: fetch_niche_market_data(queries, top_n=20)
                        )
                    except Exception as e:
                        print(f"[Trending] ST market data error for {key}: {e}")
                        return key, None

                market_results = await asyncio.gather(
                    *[_fetch_market(k, qs) for k, qs in st_queries_map.items()],
                    return_exceptions=True,
                )
                for r in market_results:
                    if isinstance(r, tuple) and r[1] is not None:
                        market_map[r[0]] = r[1]

        for cat in results:
            cat["market"] = market_map.get(cat["key"])

        # 统一计算热度评分：结合 Reddit + HN + ST 三维数据
        for cat in results:
            r_score = cat.get("reddit_score", 0)
            r_comments = cat.get("reddit_comments", 0)
            h_score = cat.get("hn_score", 0)
            h_comments = cat.get("hn_comments", 0)
            market = cat.get("market") or {}

            # 社区热度：Reddit(投票×0.3 + 评论×0.7) + HN(投票×0.4 + 评论×0.6)
            community_heat = (r_score * 0.3 + r_comments * 0.7) + (h_score * 0.4 + h_comments * 0.6)

            # ST 市场活力：基于收入增长率和下载量级
            growth = market.get("revenue_growth_pct", 0) or 0
            revenue = market.get("revenue_sum", 0) or 0
            downloads = market.get("downloads_sum", 0) or 0
            st_heat = 0.0
            if revenue > 0 or downloads > 0:
                dl_factor = min(math.log10(max(downloads, 1)) * 5, 40)
                rev_factor = min(math.log10(max(revenue, 1)) * 3, 30)
                growth_factor = max(min(growth, 100), -50) * 0.3
                st_heat = dl_factor + rev_factor + growth_factor

            # 综合热度 = 社区热度(60%) + 市场活力(40%)
            if st_heat > 0:
                cat["heat_index"] = round(community_heat * 0.6 + st_heat * 0.4, 1)
            else:
                cat["heat_index"] = round(community_heat, 1)

        results.sort(key=lambda x: x.get("heat_index", 0), reverse=True)

        history = _load_history_snapshots(7)
        alerts = _compute_alerts(results, history)
        for cat in results:
            info = alerts.get(cat["key"], {})
            cat["change_pct"] = info.get("change_pct", 0)
            cat["alert"] = info.get("alert")

        import time
        response = {"categories": results, "scanned_at": datetime.now().isoformat()}
        _trending_cache["data"] = response
        _trending_cache["ts"] = time.time()
        _save_trending_snapshot(response)
        print(f"[Trending] scan complete, {len(results)} categories")
    except Exception as e:
        print(f"[Trending] background scan error: {e}")
    finally:
        _trending_refreshing = False


@router.get("/trending")
async def get_trending(refresh: bool = False):
    """热度雷达：先返回缓存/快照，后台异步刷新。每天自动刷新一次。"""
    import asyncio, time
    global _trending_refreshing

    now = time.time()
    cache_valid = _trending_cache["data"] and (now - _trending_cache["ts"]) < _TRENDING_TTL

    # 1. 有效缓存且非手动刷新 → 直接返回
    if not refresh and cache_valid:
        result = dict(_trending_cache["data"])
        result["scanning"] = _trending_refreshing
        return result

    # 2. 手动刷新 → 清空缓存标记，让后台重新扫描
    if refresh:
        _trending_cache["ts"] = 0

    # 3. 确定返回给前端的即时数据
    immediate = _trending_cache["data"]
    if not immediate:
        immediate = _load_latest_snapshot()
        if immediate:
            _trending_cache["data"] = immediate
            snap_ts = _get_snapshot_ts()
            if snap_ts:
                _trending_cache["ts"] = snap_ts
            cache_valid = _trending_cache["data"] and (now - _trending_cache["ts"]) < _TRENDING_TTL
    if not immediate:
        immediate = _build_skeleton()

    # 4. 如果缓存仍无效且没有在刷新中，启动后台扫描任务
    if not cache_valid and not _trending_refreshing:
        _trending_refreshing = True
        asyncio.create_task(_do_trending_scan())

    scanning = _trending_refreshing
    result = dict(immediate)
    result["scanning"] = scanning
    return result


@router.post("/trending/clear-cache")
def clear_trending_cache():
    """清除热度雷达所有内存和磁盘缓存，强制下次请求重新扫描/翻译。"""
    _trending_cache["data"] = None
    _trending_cache["ts"] = 0
    _detail_cache.clear()
    return {"ok": True, "message": "trending + detail cache cleared"}


@router.get("/trending/history")
def get_trending_history(category: str = "", days: int = 7):
    """获取指定赛道的历史热度趋势（供图表使用）"""
    snapshots = _load_history_snapshots(min(days, 30))
    if not category:
        series: list[dict] = []
        for snap in snapshots:
            entry: dict = {"date": snap.get("date", "")}
            for cat in snap.get("categories", []):
                entry[cat["key"]] = cat.get("heat_index", 0)
            series.append(entry)
        return {"series": series}

    series = []
    for snap in snapshots:
        for cat in snap.get("categories", []):
            if cat["key"] == category:
                series.append({
                    "date": snap.get("date", ""),
                    "heat_index": cat.get("heat_index", 0),
                    "reddit_score": cat.get("total_score", cat.get("reddit_score", 0)),
                    "reddit_comments": cat.get("total_comments", cat.get("reddit_comments", 0)),
                    "hn_score": cat.get("hn_score", 0),
                    "hn_comments": cat.get("hn_comments", 0),
                })
                break
    return {"category": category, "series": series}


_detail_cache: dict[str, dict] = {}

@router.get("/trending/detail/{category}")
async def get_trending_detail(category: str):
    """赛道详情：从 scan 缓存取数据，只做翻译+产品机会筛选。"""
    import asyncio, copy

    if category in _detail_cache:
        return _detail_cache[category]

    all_categories = _get_all_categories()
    cat_info = all_categories.get(category)
    if not cat_info:
        raise HTTPException(status_code=404, detail="赛道不存在")

    # 从 scan 缓存中取 subreddit 和 HN 数据（避免重复调 rdt）
    cached = _trending_cache.get("data") or {}
    cached_cats = cached.get("categories", []) if isinstance(cached, dict) else []
    cached_cat = next((c for c in cached_cats if c.get("key") == category), None)

    # 若内存缓存无数据，尝试从磁盘快照读取
    if not cached_cat:
        snap = _load_latest_snapshot()
        if snap:
            snap_cats = snap.get("categories", []) if isinstance(snap, dict) else []
            cached_cat = next((c for c in snap_cats if c.get("key") == category), None)

    if cached_cat:
        sub_data = copy.deepcopy(cached_cat.get("subreddits", []))
        hn_posts_raw = copy.deepcopy(cached_cat.get("hn_posts", []))
    else:
        sub_data = []
        hn_posts_raw = []

    # 收集 subreddit 名称 + 帖子标题
    sub_names = [s.get("name", "") for s in sub_data if s.get("name")]
    all_titles = []
    for s in sub_data:
        for p in s.get("hot_posts", []):
            all_titles.append(p.get("title", ""))
    for p in hn_posts_raw:
        all_titles.append(p.get("title", ""))

    # 批量翻译 + 产品机会筛选（一次 LLM 调用）
    title_map: dict[str, str] = {}
    sub_name_map: dict[str, str] = {}
    skip_titles: set[str] = set()
    unique_titles = list(dict.fromkeys(all_titles))[:25]
    translate_ok = False
    if unique_titles:
        try:
            sub_block = ""
            if sub_names:
                sub_block = (
                    "\n\n此外，以下是 Reddit 子版块名称列表，请翻译为简短中文：\n"
                    + json.dumps(sub_names, ensure_ascii=False)
                    + '\n在输出 JSON 中增加一个 "__subreddits__" 字段，格式：'
                    '{"__subreddits__": {"languagelearning": "语言学习", ...}}\n'
                )
            prompt = (
                "你是产品机会分析专家。下面是社区热帖标题列表，请完成两件事：\n"
                "1. 将每个标题翻译成中文\n"
                "2. 判断该帖子是否可能包含产品/工具/服务的用户需求或痛点（产品机会）\n\n"
                "输出纯 JSON 对象，key 是原标题，value 是对象 {\"zh\": \"中文翻译\", \"opp\": true/false}。\n"
                "opp=true 表示可能有产品机会（用户吐槽、求推荐、讨论工具优劣、分享工作流痛点等）。\n"
                "opp=false 表示纯新闻/meme/个人故事/政治/体育赛事等，不涉及任何产品需求。\n"
                "不要加任何解释。\n\n"
                + json.dumps(unique_titles, ensure_ascii=False)
                + sub_block
            )
            loop = asyncio.get_event_loop()
            resp = await loop.run_in_executor(
                None, lambda: call_llm([{"role": "user", "content": prompt}], max_tokens=4000)
            )
            parsed = _parse_json_from_text(resp)
            if parsed and isinstance(parsed, dict):
                sr_data = parsed.pop("__subreddits__", None)
                if isinstance(sr_data, dict):
                    sub_name_map = {k: v for k, v in sr_data.items() if isinstance(v, str)}
                for orig, val in parsed.items():
                    if isinstance(val, dict):
                        title_map[orig] = val.get("zh", "")
                        if not val.get("opp", True):
                            skip_titles.add(orig)
                    elif isinstance(val, str):
                        title_map[orig] = val
                if title_map:
                    translate_ok = True
        except Exception as e:
            print(f"[Trending] title translation failed: {e}")

    for s in sub_data:
        sn = s.get("name", "")
        if sn and sn in sub_name_map:
            s["name_zh"] = sub_name_map[sn]
        for p in s.get("hot_posts", []):
            t = p.get("title", "")
            if t in title_map:
                p["title_zh"] = title_map[t]
        s["hot_posts"] = [p for p in s.get("hot_posts", []) if p.get("title", "") not in skip_titles]

    for p in hn_posts_raw:
        t = p.get("title", "")
        if t in title_map:
            p["title_zh"] = title_map[t]
    hn_posts_filtered = [p for p in hn_posts_raw if p.get("title", "") not in skip_titles]
    if skip_titles:
        print(f"[Trending] filtered {len(skip_titles)} posts without product opportunity")

    result = {
        "key": category,
        "label": cat_info.get("label", category),
        "subreddits": sub_data,
        "hn_posts": hn_posts_filtered,
    }
    if translate_ok:
        _detail_cache[category] = result
    return result


@router.get("/trending/custom-categories")
def get_custom_categories():
    return _load_custom_categories()


@router.post("/trending/custom-categories")
def save_custom_categories(body: dict):
    categories = body.get("categories", {})
    TRENDING_CUSTOM_FILE.write_text(json.dumps(categories, ensure_ascii=False, indent=2))
    _trending_cache["data"] = None
    return {"ok": True}


_product_cache: dict[str, dict] = {}

@router.get("/trending/product/{product_key}")
def get_product_data(product_key: str):
    """获取自家产品数据及收入相近的竞品。"""
    if product_key in _product_cache:
        return _product_cache[product_key]

    PRODUCT_CONFIG = {
        "owll_translator": {
            "search_name": "Owll Translator",
            "queries": ["translator app", "AI translator", "voice translator", "real time translation", "speech translator"],
        },
        "owll_note": {
            "search_name": "Owll AI Note",
            "queries": ["AI note taker", "voice recorder notes", "audio recorder transcription", "meeting recorder", "lecture recorder"],
        },
        "bible_note": {
            "search_name": "Bible Note Taker",
            "queries": ["bible app", "bible study", "bible note", "scripture app", "devotional app"],
        },
    }

    config = PRODUCT_CONFIG.get(product_key)
    if not config:
        raise HTTPException(status_code=404, detail="Unknown product")

    try:
        from st_client import fetch_product_with_peers
        result = fetch_product_with_peers(
            product_name=config["search_name"],
            category_queries=config["queries"],
            peer_count=10,
        )
        if result:
            _product_cache[product_key] = result
            return result
        return {"product": None, "peers": []}
    except Exception as e:
        print(f"[product_data] {product_key} error: {e}")
        return {"product": None, "peers": [], "error": str(e)[:200]}


@router.post("/trending/product/clear-cache")
def clear_product_cache():
    _product_cache.clear()
    return {"ok": True}


@router.post("/deep-mine")
def deep_mine(req: StartDebateRequest, request: Request):
    """Phase B: Deep mining for a specific need — quote extraction + FEMWC scoring."""
    ctx = _get_session(request)
    import asyncio as _aio
    _dm_loop = _aio.new_event_loop()
    _aio.set_event_loop(_dm_loop)

    needs_data = get_needs(request)["needs"]
    if req.need_index < 0 or req.need_index >= len(needs_data):
        raise HTTPException(status_code=404, detail="Need not found")

    need = needs_data[req.need_index]

    def event_stream() -> Generator[str, None, None]:
        set_thread_session(ctx)
        try:
            yield _sse("fetch_progress", {"message": "开始深挖需求...", "progress": 5})

            # Step 1: Generate supplementary queries
            posts_summary = "\n".join(
                f"- {p['title']} (score={p.get('score', 0)})"
                for p in need.get("posts", [])[:5]
            )
            prompt = DEEP_MINING_QUERY_PROMPT.format(
                need_title=need.get("need_title", ""),
                need_description=need.get("need_description", ""),
                posts_summary=posts_summary,
            )
            try:
                resp = call_llm([{"role": "user", "content": prompt}])
                plan = _parse_json_from_text(resp)
                extra_queries = plan.get("search_queries", []) if plan else []
                extra_subs = plan.get("subreddits", []) if plan else []
                yield _sse("fetch_progress", {
                    "message": f"生成 {len(extra_queries)} 条补充搜索词",
                    "progress": 15,
                })
            except Exception as e:
                print(f"[DeepMine] Query gen failed: {e}")
                extra_queries = []
                extra_subs = []

            # Step 2: Deep fetch with rdt read for full comments
            fetcher = get_reddit_fetcher()
            all_deep_posts: list[dict] = []

            existing_posts = need.get("posts", [])
            for p in existing_posts:
                post_id = p.get("_post_id", "")
                if post_id and fetcher.engine_name == "rdt-cli":
                    full = _dm_loop.run_until_complete(fetcher.read_post(post_id))
                    if full:
                        all_deep_posts.append(full)
                        continue
                all_deep_posts.append(p)

            yield _sse("fetch_progress", {
                "message": f"已读取 {len(all_deep_posts)} 个帖子的完整评论",
                "progress": 35,
            })

            if extra_queries:
                for i, q in enumerate(extra_queries[:8]):
                    sub = extra_subs[i % len(extra_subs)] if extra_subs else ""
                    new_posts = _dm_loop.run_until_complete(fetcher.search(q, subreddit=sub, limit=5))
                    for np in new_posts:
                        if not any(np["title"].lower() == ep["title"].lower() for ep in all_deep_posts):
                            all_deep_posts.append(np)
                    yield _sse("fetch_progress", {
                        "message": f"补充搜索 {i+1}/{min(len(extra_queries), 8)}: +{len(new_posts)} 帖子",
                        "progress": 35 + int(20 * (i + 1) / min(len(extra_queries), 8)),
                    })

            # Apply hard filter
            all_deep_posts = [p for p in all_deep_posts if hard_filter(p)] or all_deep_posts

            yield _sse("fetch_progress", {
                "message": f"共 {len(all_deep_posts)} 个帖子，开始提取原文摘录...",
                "progress": 60,
            })

            # Step 3: Quote extraction
            quotes = extract_quotes(all_deep_posts)
            yield _sse("fetch_progress", {
                "message": f"提取到 {len(quotes)} 条原文摘录",
                "progress": 75,
            })

            # Step 4: FEMWC scoring
            yield _sse("fetch_progress", {"message": "FEMWC 五维评分中...", "progress": 82})

            updated_need = dict(need)
            updated_need["posts"] = all_deep_posts
            femwc = score_femwc(updated_need, quotes)

            yield _sse("fetch_progress", {
                "message": f"评分完成：{femwc.get('total', 0):.2f} 分 — {femwc.get('verdict', '')}",
                "progress": 92,
            })

            # Step 5: Build need package
            package = build_need_package(updated_need, quotes, femwc)

            # Save to cache
            updated_need["deep_mine_package"] = package
            needs_data[req.need_index] = updated_need
            _safe_json_write(ctx.needs_cache, needs_data, indent=2)

            yield _sse("fetch_progress", {"message": "深挖完成！", "progress": 100})
            yield _sse("deep_mine_result", {
                "package": package,
                "need_index": req.need_index,
            })
            yield _sse("done", {})

        except Exception as e:
            import traceback
            traceback.print_exc()
            yield _sse("error", {"message": _friendly_error(e)})

    return StreamingResponse(event_stream(), media_type="text/event-stream")



# ============================================================
# POC 产品准入评价
# ============================================================

POC_EVAL_DIR = ROOT / "data" / "poc_evaluations"
POC_EVAL_DIR.mkdir(parents=True, exist_ok=True)


class PocEvalRequest(BaseModel):
    idea_name: str
    idea_brief: str
    target_users: str
    pain_points: str
    simple_product: str
    report_filename: str = ""
    opportunity_index: int = -1


@router.post("/poc-evaluate")
def poc_evaluate(req: PocEvalRequest, request: Request):
    """执行 POC 产品准入评价 — 纯外部视角，不依赖报告"""
    ctx = _get_session(request)
    prompt = (POC_EVAL_PROMPT
        .replace("__IDEA_NAME__", req.idea_name)
        .replace("__IDEA_BRIEF__", req.idea_brief)
        .replace("__TARGET_USERS__", req.target_users)
        .replace("__PAIN_POINTS__", req.pain_points)
        .replace("__SIMPLE_PRODUCT__", req.simple_product)
    )

    messages = [
        {"role": "system", "content": "你是产品评审员。仅根据提供的创意信息，站在外部客观视角评估。直接输出 JSON。"},
        {"role": "user", "content": prompt},
    ]

    try:
        result_text = call_llm(messages, max_tokens=1500)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI 评价调用失败: {e}")

    parsed = _parse_json_from_text(result_text)
    if not parsed:
        raise HTTPException(status_code=500, detail="AI 返回格式异常，无法解析")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    eval_id = f"poc_{ts}"
    result = {
        "id": eval_id,
        "timestamp": datetime.now().isoformat(),
        "input": {
            "idea_name": req.idea_name,
            "idea_brief": req.idea_brief,
            "target_users": req.target_users,
            "pain_points": req.pain_points,
            "simple_product": req.simple_product,
        },
        "evaluation": parsed,
    }

    filepath = POC_EVAL_DIR / f"{eval_id}.json"
    filepath.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    # 持久化 eval_id 到报告的 opportunities 缓存
    if req.report_filename and req.opportunity_index >= 0:
        rp = _safe_path(ctx.reports_dir, req.report_filename)
        if rp.exists():
            try:
                rd = json.loads(rp.read_text(encoding="utf-8"))
                opps = rd.get("opportunities", [])
                if 0 <= req.opportunity_index < len(opps):
                    opps[req.opportunity_index]["eval_id"] = eval_id
                    rd["opportunities"] = opps
                    rp.write_text(json.dumps(rd, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass

    return result


@router.get("/poc-evaluate/{eval_id}")
def get_poc_evaluation(eval_id: str):
    """根据 eval_id 读取历史评价结果"""
    filepath = _safe_path(POC_EVAL_DIR, f"{eval_id}.json")
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="评价记录不存在")
    try:
        return json.loads(filepath.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取评价记录失败: {e}")


@router.post("/poc-evaluate/extract-opportunities")
def extract_opportunities(body: dict, request: Request):
    """从报告中确定性解析机会点，首次提取后缓存到报告文件"""
    ctx = _get_session(request)
    report_content = body.get("report_content", "")
    report_filename = body.get("report_filename", "")
    need_desc = body.get("need_description", "")

    # 尝试从缓存读取
    if report_filename:
        rp = _safe_path(ctx.reports_dir, report_filename)
        if rp.exists():
            try:
                rd = json.loads(rp.read_text(encoding="utf-8"))
                cached = rd.get("opportunities")
                if cached and isinstance(cached, list) and len(cached) > 0:
                    # 检查缓存质量：三个维度字段都应有内容
                    has_good_cache = all(
                        len(o.get("simple_product", "")) > 10
                        and len(o.get("target_users", "")) > 5
                        and len(o.get("pain_points", "")) > 5
                        for o in cached
                    )
                    if has_good_cache:
                        return {"opportunities": cached}
                # 如果没缓存，从文件中也取 need_description
                if not need_desc:
                    need_obj = rd.get("need", {})
                    if isinstance(need_obj, dict):
                        need_desc = need_obj.get("need_description", "")
            except Exception:
                pass

    if not report_content:
        return {"opportunities": []}

    opportunities = _parse_opportunities(report_content, need_desc)

    # 缓存到报告文件（保留旧缓存中的 eval_id）
    if report_filename and opportunities:
        rp = _safe_path(ctx.reports_dir, report_filename)
        if rp.exists():
            try:
                rd = json.loads(rp.read_text(encoding="utf-8"))
                old_opps = rd.get("opportunities", [])
                old_eval_ids = {}
                for i, o in enumerate(old_opps):
                    eid = o.get("eval_id")
                    if eid:
                        old_eval_ids[o.get("title", "")] = eid
                for opp in opportunities:
                    eid = old_eval_ids.get(opp.get("title", ""))
                    if eid:
                        opp["eval_id"] = eid
                rd["opportunities"] = opportunities
                rp.write_text(json.dumps(rd, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass

    return {"opportunities": opportunities}


def _parse_opportunities(report_content, need_desc: str = "") -> list[dict]:
    """从报告「产品方案」章节确定性解析产品方案"""
    import re

    opportunities = []

    if not isinstance(report_content, str):
        return opportunities

    def _field(body: str, *names: str) -> str:
        """尝试多个字段名匹配 **字段名** 后面的内容"""
        for name in names:
            m = re.search(rf'\*\*{re.escape(name)}\*\*[\s：:]+(.+?)(?:\n|$)', body)
            if m:
                return m.group(1).strip()
        return ""

    def _sub_section(body: str, heading: str) -> str:
        """提取 #### heading 下的全部要点，拼成一段"""
        m = re.search(rf'####\s*{re.escape(heading)}\s*\n([\s\S]*?)(?=####|\Z)', body)
        if not m:
            return ""
        items = re.findall(r'^[-*]\s+\*\*[^*]+\*\*[\s：:]+(.+)', m.group(1), re.M)
        return '；'.join(items) if items else ""

    # 从报告「产品方案」章节提取
    section = re.search(r'## 产品方案\s*\n+([\s\S]*?)(?=\n## |$)', report_content)
    if section:
        blocks = re.findall(
            r'### 方案\s*\d+[\s：:]+(.+?)(?:\n)([\s\S]*?)(?=### 方案\s*\d+|$)',
            section.group(1)
        )
        for title, body in blocks:
            target_users = _field(body, '目标人群', '目标用户')
            pain_points = _field(body, '具体痛点', '用户痛点', '解决的核心问题')
            product_desc = _field(body, '一句话描述', '产品描述')
            product_form = _field(body, '产品形态')
            features = [f for f in re.findall(r'^[-*]\s+(.+)', body, re.M) if not f.startswith('**')][:5]

            if not target_users:
                target_users = _sub_section(body, '清晰的用户')
            if not pain_points:
                pain_points = _sub_section(body, '真实的需求')
            if not product_desc:
                product_desc = _sub_section(body, '简单的产品')

            if not product_desc:
                sp_parts = []
                if product_form:
                    sp_parts.append(f"一个{product_form}")
                if features:
                    sp_parts.append(f"，核心功能包括：{'；'.join(features)}。" if sp_parts else f"核心功能包括：{'；'.join(features)}。")
                elif sp_parts:
                    sp_parts.append("。")
                product_desc = "".join(sp_parts)

            idea_brief = pain_points
            if product_form and idea_brief:
                idea_brief = f"通过{product_form}，{idea_brief}"

            opportunities.append({
                "title": title.strip().rstrip('。'),
                "description": idea_brief or product_desc,
                "target_users": target_users,
                "pain_points": pain_points,
                "features": features,
                "simple_product": product_desc,
            })

    # 兜底：从痛点地图的机会点提取
    if not opportunities:
        pain_section = re.search(r'## 痛点地图[\s\S]*?(?=\n## )', report_content)
        if pain_section:
            pain_blocks = re.findall(
                r'### \d+\.\s+(.+?)(?:\n)([\s\S]*?)(?=### \d+\.|## |$)',
                pain_section.group(0)
            )
            for title, body in pain_blocks[:3]:
                opp_match = re.search(r'\*\*机会点\*\*\s*\n([\s\S]*?)(?=\n\*\*|\n### |\n## |$)', body)
                features = re.findall(r'^[-*]\s+(.+)', opp_match.group(1), re.M) if opp_match else []
                pain_desc_m = re.search(r'\*\*强度.+?\n\n(.+?)(?:\n\n|\n\*\*)', body, re.S)
                pain_desc = pain_desc_m.group(1).strip() if pain_desc_m else title.strip()
                fallback_sp = ("核心功能：" + "；".join(features[:3]) + "。") if features else ""
                opportunities.append({
                    "title": title.strip().split('—')[0].strip().split(' — ')[0].strip(),
                    "description": "; ".join(features[:2]) if features else title.strip(),
                    "target_users": "",
                    "pain_points": pain_desc,
                    "features": features[:3],
                    "simple_product": fallback_sp,
                })

    return opportunities[:3]


# ---- 在线统计 ----

@router.get("/online-stats")
def online_stats():
    """返回在线人数、挖掘中人数、已挖掘需求累计数。"""
    now = _time.time()
    online = mining = 0
    with _sessions_lock:
        for ctx in _sessions.values():
            if now - ctx.last_active < 300:
                online += 1
                if ctx.fetch_job.get("active"):
                    mining += 1
    return {"online": online, "mining": mining, "needs": _read_global_needs_count(), "app_version": "1.1.0"}


# ============================================================
# 外部 CLI 数据接口 — SensorTower
# 供同事通过 HTTP 直接调用，需携带 X-API-Key header
# 与内部业务端点隔离：独立路径前缀 /cli/st、独立并发控制
# ============================================================

import asyncio as _cli_asyncio
from concurrent.futures import ThreadPoolExecutor as _CliThreadPool

# ---------- API Key 认证 ----------

def _verify_cli_api_key(request: Request):
    """校验外部接口的 API Key，无效则拒绝。"""
    expected = os.getenv("CLI_API_KEY", "")
    if not expected:
        raise HTTPException(status_code=503, detail="CLI_API_KEY 未配置，接口不可用")
    provided = request.headers.get("x-api-key", "")
    if not provided or provided != expected:
        raise HTTPException(status_code=401, detail="无效的 API Key")


# ---------- 并发控制 & 调用统计 ----------

_cli_st_semaphore = threading.Semaphore(1)
_cli_st_pool = _CliThreadPool(max_workers=2, thread_name_prefix="cli-st")

_cli_st_stats: dict[str, dict] = {
    "status": {"calls": 0, "last_call": 0.0},
    "app": {"calls": 0, "last_call": 0.0},
    "landscape": {"calls": 0, "last_call": 0.0},
    "market": {"calls": 0, "last_call": 0.0},
}

def _cli_st_record(endpoint: str):
    _cli_st_stats[endpoint]["calls"] += 1
    _cli_st_stats[endpoint]["last_call"] = _time.time()


def _cli_st_acquire_or_reject():
    """尝试获取信号量，获取不到直接返回 429。"""
    acquired = _cli_st_semaphore.acquire(blocking=False)
    if not acquired:
        raise HTTPException(
            status_code=429,
            detail="当前有其他请求正在处理，请稍后重试（同一时间仅允许 1 个外部 ST 请求）",
        )


# ---------- 请求模型 ----------

class CliStAppRequest(BaseModel):
    query: str  # App 名称或 App Store URL


class CliStLandscapeRequest(BaseModel):
    competitors: list[dict]  # [{"name": "Duolingo", "url": "https://..."}, ...]
    limit: int = 5


class CliStMarketRequest(BaseModel):
    mode: str  # "category" | "niche" | "product"
    category_id: int | None = None
    queries: list[str] | None = None
    top_n: int = 20
    product_name: str | None = None
    category_queries: list[str] | None = None
    peer_count: int = 8


# ---------- 端点 ----------

@router.get("/cli/st/status")
def cli_st_status(request: Request):
    """外部接口：检测 st-cli 可用状态 + 调用统计。"""
    _verify_cli_api_key(request)
    _cli_st_record("status")

    from st_client import check_available as _st_check
    status = _st_check()

    return {
        "ok": True,
        "data": {
            "installed": status.get("installed", False),
            "available": status.get("available", False),
            "api_ok": status.get("api_ok", False),
            "credential_source": status.get("credential_source", ""),
            "error": status.get("error", ""),
        },
        "stats": {k: v.copy() for k, v in _cli_st_stats.items()},
    }


@router.post("/cli/st/app")
async def cli_st_app(body: CliStAppRequest, request: Request):
    """外部接口：查询单个 App 的 SensorTower 数据。"""
    _verify_cli_api_key(request)
    _cli_st_record("app")
    _cli_st_acquire_or_reject()

    try:
        from st_client import fetch_app as _st_fetch_app
        loop = _cli_asyncio.get_event_loop()
        result = await loop.run_in_executor(_cli_st_pool, _st_fetch_app, body.query)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"ST 查询失败: {e}")
    finally:
        _cli_st_semaphore.release()

    if result is None:
        return {"ok": False, "error": "未找到匹配的 App 或 SensorTower 返回为空", "data": None}
    return {"ok": True, "data": result}


@router.post("/cli/st/landscape")
async def cli_st_landscape(body: CliStLandscapeRequest, request: Request):
    """外部接口：批量查询竞品 SensorTower 数据（重量级，最多 180 秒）。"""
    _verify_cli_api_key(request)

    if not body.competitors:
        raise HTTPException(status_code=400, detail="competitors 不能为空")
    if body.limit < 1 or body.limit > 10:
        raise HTTPException(status_code=400, detail="limit 范围 1-10")

    _cli_st_record("landscape")
    _cli_st_acquire_or_reject()

    try:
        from st_client import fetch_landscape as _st_landscape
        loop = _cli_asyncio.get_event_loop()
        result = await loop.run_in_executor(
            _cli_st_pool,
            lambda: _st_landscape(body.competitors, limit=body.limit),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"ST landscape 查询失败: {e}")
    finally:
        _cli_st_semaphore.release()

    return {"ok": True, "data": result}


@router.post("/cli/st/market")
async def cli_st_market(body: CliStMarketRequest, request: Request):
    """外部接口：品类/细分赛道/产品竞品的市场数据。"""
    _verify_cli_api_key(request)

    if body.mode == "category":
        if body.category_id is None:
            raise HTTPException(status_code=400, detail="category 模式需要 category_id")
    elif body.mode == "niche":
        if not body.queries:
            raise HTTPException(status_code=400, detail="niche 模式需要 queries（关键词列表）")
    elif body.mode == "product":
        if not body.product_name or not body.category_queries:
            raise HTTPException(status_code=400, detail="product 模式需要 product_name 和 category_queries")
    else:
        raise HTTPException(status_code=400, detail=f"不支持的 mode: {body.mode}，可选: category / niche / product")

    _cli_st_record("market")
    _cli_st_acquire_or_reject()

    try:
        from st_client import (
            fetch_category_market_data as _st_category,
            fetch_niche_market_data as _st_niche,
            fetch_product_with_peers as _st_product,
        )
        loop = _cli_asyncio.get_event_loop()

        if body.mode == "category":
            result = await loop.run_in_executor(
                _cli_st_pool,
                lambda: _st_category(body.category_id, top_n=body.top_n),
            )
        elif body.mode == "niche":
            result = await loop.run_in_executor(
                _cli_st_pool,
                lambda: _st_niche(body.queries, top_n=body.top_n),
            )
        else:
            result = await loop.run_in_executor(
                _cli_st_pool,
                lambda: _st_product(body.product_name, body.category_queries, peer_count=body.peer_count),
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"ST market 查询失败: {e}")
    finally:
        _cli_st_semaphore.release()

    if result is None:
        return {"ok": False, "error": "未找到数据或 SensorTower 返回为空", "data": None}
    return {"ok": True, "data": result}


# ============================================================
# 用户画像建模
# ============================================================

class PersonaRequest(BaseModel):
    need_index: int

@router.post("/generate-personas")
def generate_personas(req: PersonaRequest, request: Request):
    """基于需求主题下的真实帖子，用 LLM 两步法建模 2-4 个典型用户画像。SSE 流式返回。"""
    ctx = _get_session(request)
    needs_data = get_needs(request)["needs"]
    if req.need_index < 0 or req.need_index >= len(needs_data):
        raise HTTPException(status_code=400, detail="无效的需求索引")

    need = needs_data[req.need_index]

    # 检查 LLM 可用性
    set_thread_session(ctx)
    try:
        llm_ok, llm_err = check_llm_available()
    finally:
        clear_thread_session()
    if not llm_ok:
        model_name = "GPT" if ctx._general_model == "gpt" else "Claude"
        def _err():
            yield _sse("error", {"message": f"{model_name} 模型不可用，请前往「设置」检查配置"})
        return StreamingResponse(_err(), media_type="text/event-stream")

    def _format_posts_for_persona(need_data: dict) -> str:
        """将帖子格式化为画像建模的上下文素材。"""
        lines = []
        for i, post in enumerate(need_data.get("posts", []), 1):
            lines.append(f"### 帖子 {i}: {post.get('title', '')}")
            lines.append(f"- 来源: {post.get('source', 'unknown')}")
            lines.append(f"- 赞数: {post.get('score', 0)} | 评论数: {post.get('num_comments', 0)}")
            content = post.get("content", "")
            if content:
                lines.append(f"- 内容: {content[:1200]}")
            comments = post.get("comments", [])
            if comments:
                lines.append("- 用户评论:")
                for c in comments[:10]:
                    lines.append(f"  > {c[:400]}")
            lines.append("")
        return "\n".join(lines)

    posts_text = _format_posts_for_persona(need)

    # 初始化 persona_job
    with ctx.persona_lock:
        ctx.persona_job = ctx._empty_persona_job()
        ctx.persona_job["active"] = True
        ctx.persona_job["need_index"] = req.need_index

    def _update_progress(progress: int, message: str):
        with ctx.persona_lock:
            ctx.persona_job["progress"] = progress
            ctx.persona_job["message"] = message

    def _run_persona_bg():
        set_thread_session(ctx)
        try:
            # ===== Step 1: 聚类分析 — 识别用户群体 =====
            _update_progress(10, "正在分析用户发言，识别行为模式...")

            step1_prompt = f"""你是一位资深的用户研究专家。以下是围绕「{need.get('need_title', '')}」这一需求主题收集的真实用户帖子和评论。

## 需求描述
{need.get('need_description', '')}

## 真实帖子数据
{posts_text}

## 任务
请仔细阅读以上所有帖子和评论，识别出 2-4 个行为模式、动机、背景明显不同的用户群体。

要求：
1. 每个群体必须有明确不同的特征（不要只是年龄不同，要在动机、行为、痛点上有质的差异）
2. 群体划分必须有帖子/评论中的真实证据支撑
3. 为每个群体提供一个简短标签和核心特征关键词

请以 JSON 格式输出：
```json
{{
  "groups": [
    {{
      "label": "群体简短标签",
      "core_traits": ["特征1", "特征2", "特征3"],
      "motivation": "核心动机描述",
      "evidence_posts": [1, 3, 5]
    }}
  ]
}}
```"""

            step1_result = call_llm([
                {"role": "system", "content": "你是用户研究专家，擅长从定性数据中识别用户群体。严格输出 JSON。"},
                {"role": "user", "content": step1_prompt},
            ], max_tokens=4000)

            _update_progress(30, "聚类分析完成，开始建模画像...")

            groups_data = _parse_json_from_text(step1_result)
            if not groups_data or "groups" not in groups_data:
                # 降级：直接生成画像，不依赖聚类结果
                groups_data = {"groups": [
                    {"label": "核心用户", "core_traits": ["高频使用者"], "motivation": "解决核心痛点"},
                    {"label": "潜在用户", "core_traits": ["有需求但未行动"], "motivation": "寻找解决方案"},
                ]}

            groups = groups_data["groups"][:4]

            # ===== Step 2: 为每个群体生成完整画像 =====
            _update_progress(40, f"正在为 {len(groups)} 个用户群体建模详细画像...")

            groups_desc = "\n".join([
                f"- 群体{i+1}「{g.get('label', '')}」: {', '.join(g.get('core_traits', []))} — {g.get('motivation', '')}"
                for i, g in enumerate(groups)
            ])

            step2_prompt = f"""你是一位资深用户研究专家，现在需要为以下用户群体建模详细的用户画像。

## 需求主题
标题：{need.get('need_title', '')}
描述：{need.get('need_description', '')}

## 识别到的用户群体
{groups_desc}

## 真实帖子数据（作为画像素材）
{posts_text}

## 任务
为每个群体生成一个鲜活、具体的用户画像（Persona）。每个画像必须像一个真实的人，让产品经理读完后能在脑海里浮现这个人的形象。

## 核心原则
- 画像必须符合其所在地区的真实生活习惯（如北美用户的作息、通勤方式、社交习惯与中国用户截然不同）
- 性别必须明确，所有描述、人设、行为都要与性别一致
- 不同画像之间要有明显差异，覆盖不同的用户类型

要求：
1. name：必须用英文名（Western name），禁止使用中文名！格式为 "英文名, 年龄, 职业"（如 "Alex, 28, 前端工程师"、"Emily, 34, 产品经理"），名字要符合性别和种族特征
2. gender：明确指定 "male" 或 "female"，画像群体中男女应合理分布
3. avatar_hint：用英文描述此人的外貌特征，方便匹配头像（如 "young white male, brown hair, glasses" 或 "middle-aged asian female, professional"）
4. tagline：一句话中文人设标签，要有画面感（如 "被照片淹没的记录强迫症患者"）
5. bio：一句中文描述这个人是什么样的人，所有代词和描述要与性别一致
6. demographics：中文人口特征（age_range/occupation/location_hint/tech_savviness）
7. goals/frustrations：**必须用中文**，不要输出英文！基于真实帖子内容概括成中文痛点和目标，不要直接复制英文原文
8. quotes：从帖子中提取 2-3 条最能代表此画像的原文（text 保留英文原文），同时提供 text_zh（准确的中文翻译，不要机翻味），如果帖子数据中有 URL 则提供 source_url
9. day_in_life：中文，以第一人称写，用时间线格式（每个时间段换行，格式为 "HH:MM - 内容"），覆盖从早到晚 8-12 个时间节点，每个节点 2-4 句话。要求：
   - 深度结合需求主题，每个时间点都要体现这个需求/痛点在用户日常中的具体表现
   - 当叙事中出现与需求主题直接相关的关键短语时，用 **双星号** 将其加粗（如"我总想**把信息放进一个能随时搜到的地方**"）
   - 符合当地的生活习惯（如北美用户开车通勤、用 Slack 沟通、吃三明治午餐等，不要出现与当地文化不符的细节）
   - 描写情绪变化和心理活动，让读者能感同身受
   - 嵌入 2-3 条来自帖子的真实引用，自然融入叙事中
10. switching_trigger/deal_breaker：中文

请以 JSON 数组格式输出所有画像：
```json
[
  {{
    "name": "Alex, 28, 前端工程师",
    "avatar_seed": "alex-28-engineer",
    "gender": "male",
    "avatar_hint": "young white male, brown hair, casual",
    "tagline": "一句话人设标签",
    "bio": "一句话描述这个人是什么样的人",
    "demographics": {{
      "age_range": "25-32",
      "occupation": "前端工程师",
      "location_hint": "北美",
      "tech_savviness": "high"
    }},
    "goals": ["中文目标1", "中文目标2"],
    "frustrations": ["中文痛点1", "中文痛点2"],
    "behaviors": ["行为1", "行为2"],
    "tools_used": ["工具1", "工具2"],
    "willingness_to_pay": "付费意愿描述",
    "quotes": [
      {{"text": "Original English quote from post", "text_zh": "准确的中文翻译", "source_url": "https://reddit.com/r/..."}}
    ],
    "day_in_life": "07:00 - 闹钟响了，我从床上爬起来...\n07:30 - 洗漱完毕，打开笔记本，我总想**把信息放进一个能随时搜到的地方**...\n09:00 - 开车到公司...\n10:30 - 晨会结束后...\n12:30 - 午餐时间...\n14:00 - 下午第一个会议...\n16:00 - 又遇到了老问题...\n18:00 - 收拾东西准备下班...\n19:30 - 到家后...\n21:00 - 坐在沙发上...\n23:00 - 睡前刷手机...",
    "priority_rank": ["需求1", "需求2", "需求3"],
    "switching_trigger": "什么会让 TA 换产品",
    "deal_breaker": "绝对不能接受什么"
  }}
]
```"""

            _update_progress(50, "正在深度建模用户画像，预计 30-40 秒...")

            step2_result = call_llm([
                {"role": "system", "content": "你是用户研究专家，擅长建模鲜活的用户画像。基于真实数据，不要编造。严格输出 JSON 数组。name 字段必须使用英文名（如 Alex、Emily、Marcus），严禁中文名！goals、frustrations、tagline、bio、day_in_life、demographics 等所有字段必须用中文，绝对不能出现英文！唯一例外：quotes 中的 text 保留英文原文并附 text_zh 中文翻译，avatar_hint 用英文。day_in_life 中与需求相关的关键短语请用 **双星号** 加粗。"},
                {"role": "user", "content": step2_prompt},
            ], max_tokens=12000)

            _update_progress(85, "画像生成完成，正在解析结果...")

            personas = _parse_json_from_text(step2_result)
            if personas is None:
                with ctx.persona_lock:
                    ctx.persona_job["error"] = "画像生成结果解析失败，请重试"
                    ctx.persona_job["active"] = False
                return

            # 兼容两种格式：直接数组或包在对象里
            if isinstance(personas, dict):
                personas = personas.get("personas", [])
            if not isinstance(personas, list) or len(personas) == 0:
                with ctx.persona_lock:
                    ctx.persona_job["error"] = "未能生成有效画像，请重试"
                    ctx.persona_job["active"] = False
                return

            _update_progress(95, "整理画像数据...")

            # 持久化到 session 目录
            persona_file = ctx.data_dir / f"personas_{req.need_index}_{int(_time.time())}.json"
            _safe_json_write(persona_file, {
                "need_index": req.need_index,
                "need_title": need.get("need_title", ""),
                "personas": personas,
                "created_at": datetime.now().isoformat(),
            })

            with ctx.persona_lock:
                ctx.persona_job["personas"] = personas
                ctx.persona_job["progress"] = 100
                ctx.persona_job["message"] = "画像建模完成！"
                ctx.persona_job["done"] = True
                ctx.persona_job["active"] = False

        except Exception as e:
            with ctx.persona_lock:
                ctx.persona_job["error"] = f"画像生成失败：{str(e)}"
                ctx.persona_job["active"] = False

    t = threading.Thread(target=_run_persona_bg, daemon=True)
    t.start()

    # SSE 流：从 persona_job 读取状态
    def event_stream() -> Generator[str, None, None]:
        _last_progress = -1
        while True:
            with ctx.persona_lock:
                job = ctx.persona_job
                progress = job["progress"]
                message = job["message"]
                error = job["error"]
                done = job["done"]
                personas = job["personas"]

            if error:
                yield _sse("persona_error", {"message": error})
                return

            if progress != _last_progress and message:
                yield _sse("persona_progress", {"progress": progress, "message": message})
                _last_progress = progress

            if done and personas is not None:
                yield _sse("persona_done", {"personas": personas})
                yield "\n"
                return

            if not job["active"] and not done:
                return

            _time.sleep(0.5)

    return StreamingResponse(event_stream(), media_type="text/event-stream")
