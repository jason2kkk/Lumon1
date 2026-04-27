"""
rdt_client.py — Reddit 数据采集：通过 rdt-cli 本地工具

rdt-cli: 免费、完整评论树、通过 subprocess 调用本地 rdt 命令

对上层暴露统一的 RedditFetcher 接口。
"""

import asyncio
import json
import os
import shutil
import subprocess
from typing import Any

from scrapers import has_need_signals

_RDT_SEARCH_TIMEOUT = 30
_RDT_READ_TIMEOUT = 20
_RDT_REQUEST_DELAY = 2.0

_RDT_CONCURRENCY = 2

_rdt_semaphores: dict[int, asyncio.Semaphore] = {}

def _get_rdt_semaphore() -> asyncio.Semaphore:
    """返回绑定到当前事件循环的 Semaphore，避免跨事件循环使用出错。"""
    loop_id = id(asyncio.get_event_loop())
    if loop_id not in _rdt_semaphores:
        _rdt_semaphores[loop_id] = asyncio.Semaphore(_RDT_CONCURRENCY)
    return _rdt_semaphores[loop_id]


# ------------------------------------------------------------------
# rdt-cli Engine
# ------------------------------------------------------------------

class RdtEngineError(Exception):
    """rdt-cli 引擎级别的错误（超时、进程崩溃等），区别于"搜索无结果"。"""
    pass


import time as _time

_STATUS_CACHE_TTL = 60
_status_cache: dict[str, Any] = {}
_status_cache_ts: float = 0.0


class RdtEngine:
    """Wraps local `rdt` CLI as an async subprocess."""

    async def check_available(self, force: bool = False) -> dict[str, Any]:
        global _status_cache, _status_cache_ts
        if not force and _status_cache and (_time.time() - _status_cache_ts < _STATUS_CACHE_TTL):
            return _status_cache

        installed = shutil.which("rdt") is not None
        if not installed:
            result = {"installed": False, "authenticated": False, "version": "", "error": "rdt-cli 未安装"}
            _status_cache, _status_cache_ts = result, _time.time()
            return result

        try:
            proc = await asyncio.create_subprocess_exec(
                "rdt", "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            version = stdout.decode().strip()
        except Exception as e:
            result = {"installed": True, "authenticated": False, "version": "", "error": f"版本检测失败: {e}"}
            _status_cache, _status_cache_ts = result, _time.time()
            return result

        try:
            proc = await asyncio.create_subprocess_exec(
                "rdt", "status", "--json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=20)
            status = json.loads(stdout.decode())
            data = status.get("data", {})
            auth = data.get("authenticated", False)
            if not auth:
                cookie_count = data.get("cookie_count", 0)
                modhash = data.get("modhash_present", False)
                if cookie_count > 0 and modhash:
                    auth = True
            if not auth:
                result = {"installed": True, "authenticated": False, "version": version,
                          "error": "未认证，前往终端输入：rdt login 连接"}
                _status_cache, _status_cache_ts = result, _time.time()
                return result
        except Exception as e:
            result = {"installed": True, "authenticated": False, "version": version,
                      "error": f"认证检测失败: {e}"}
            _status_cache, _status_cache_ts = result, _time.time()
            return result

        result = {"installed": True, "authenticated": True, "version": version, "error": ""}
        _status_cache, _status_cache_ts = result, _time.time()
        return result

    def _is_rate_limited(self, raw: str) -> bool:
        """检测 rdt 返回是否为 rate_limited 错误。"""
        if "rate_limited" in raw or "Rate limited" in raw:
            return True
        return False

    async def search(
        self,
        query: str,
        subreddit: str = "",
        sort: str = "top",
        time_filter: str = "month",
        limit: int = 10,
    ) -> list[dict]:
        """搜索 Reddit 帖子。
        正常返回 list[dict]（可能为空）。
        仅当引擎本身出问题时抛出 RdtEngineError。
        """
        _MAX_RETRIES = 2
        for attempt in range(_MAX_RETRIES + 1):
            async with _get_rdt_semaphore():
                args = ["rdt", "search", query, "-s", sort, "-t", time_filter, "--limit", str(limit), "--json"]
                if subreddit:
                    args.extend(["-r", subreddit])

                try:
                    proc = await asyncio.create_subprocess_exec(
                        *args,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_RDT_SEARCH_TIMEOUT)
                except asyncio.TimeoutError:
                    raise RdtEngineError(f"search timeout: {query[:60]}")
                except Exception as e:
                    raise RdtEngineError(f"search subprocess error: {e}")

                raw = stdout.decode()
                err_out = stderr.decode()
                if err_out.strip():
                    print(f"[rdt-search] stderr: {err_out[:300]}")

                if self._is_rate_limited(raw):
                    if attempt < _MAX_RETRIES:
                        wait = 6 + attempt * 3
                        print(f"[rdt-search] rate limited, retry {attempt+1}/{_MAX_RETRIES} after {wait}s")
                        await asyncio.sleep(wait)
                        continue
                    else:
                        print(f"[rdt-search] rate limited after {_MAX_RETRIES} retries: q={query[:40]}")
                        return []

                await asyncio.sleep(_RDT_REQUEST_DELAY)

                if proc.returncode not in (0, 1):
                    raise RdtEngineError(
                        f"search failed (rc={proc.returncode}): {err_out[:200]}"
                    )

                if proc.returncode == 1 and not raw.strip():
                    print(f"[rdt-search] rc=1, no output for: q={query[:40]} sub={subreddit}")
                    return []

                posts = self._parse_search_results(raw, subreddit)
                if not posts and raw.strip():
                    print(f"[rdt-search] WARNING: got output but parsed 0 posts. rc={proc.returncode}, raw[:500]={raw[:500]}")
                return posts
        return []

    async def read_post(self, post_id: str) -> dict | None:
        """读取单个帖子详情。
        仅当引擎本身出问题时抛出 RdtEngineError。
        """
        _MAX_RETRIES = 1
        for attempt in range(_MAX_RETRIES + 1):
            async with _get_rdt_semaphore():
                try:
                    proc = await asyncio.create_subprocess_exec(
                        "rdt", "read", post_id, "--json",
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_RDT_READ_TIMEOUT)
                except asyncio.TimeoutError:
                    raise RdtEngineError(f"read timeout: {post_id}")
                except Exception as e:
                    raise RdtEngineError(f"read subprocess error: {e}")

                raw = stdout.decode()

                if self._is_rate_limited(raw):
                    if attempt < _MAX_RETRIES:
                        wait = 6
                        print(f"[rdt-read] rate limited for {post_id}, retry after {wait}s")
                        await asyncio.sleep(wait)
                        continue
                    else:
                        print(f"[rdt-read] rate limited after retry: {post_id}")
                        return None

                await asyncio.sleep(_RDT_REQUEST_DELAY)

                if proc.returncode not in (0, 1):
                    raise RdtEngineError(
                        f"read failed (rc={proc.returncode}): {stderr.decode()[:200]}"
                    )

                if proc.returncode == 1 and not raw.strip():
                    return None

                return self._parse_read_result(raw)
        return None

    def _parse_search_results(self, raw: str, subreddit_hint: str = "") -> list[dict]:
        posts = []
        for obj in self._parse_ndjson(raw):
            self._extract_posts_from_obj(obj, subreddit_hint, posts)
        return posts

    def _parse_ndjson(self, raw: str) -> list[dict]:
        """解析可能包含多个 JSON 对象的 NDJSON 输出。"""
        results = []
        decoder = json.JSONDecoder()
        idx = 0
        while idx < len(raw):
            stripped = raw[idx:].lstrip()
            if not stripped:
                break
            try:
                obj, end = decoder.raw_decode(stripped)
                idx += (len(raw[idx:]) - len(stripped)) + end
                if isinstance(obj, dict):
                    results.append(obj)
                elif isinstance(obj, list):
                    results.extend(o for o in obj if isinstance(o, dict))
            except json.JSONDecodeError:
                break
        return results

    def _extract_posts_from_obj(self, data: dict, subreddit_hint: str, posts: list[dict]):
        """从一个 JSON 对象中提取帖子。"""
        children = data.get("data", {}).get("data", {}).get("children", [])
        if children:
            for c in children:
                p = c.get("data", {})
                post = self._rdt_to_post(p, subreddit_hint)
                if post:
                    posts.append(post)
        else:
            post = self._rdt_to_post(data, subreddit_hint)
            if post:
                posts.append(post)

    def _rdt_to_post(self, item: dict, subreddit_hint: str = "") -> dict | None:
        title = item.get("title", "")
        if not title:
            return None

        sub = item.get("subreddit", "") or item.get("sub", "") or subreddit_hint
        body = item.get("selftext", "") or item.get("body", "") or ""
        score = item.get("score", 0) or item.get("ups", 0) or 0
        num_comments = item.get("num_comments", 0) or 0
        post_id = item.get("id", "")
        permalink = item.get("permalink", "")
        url = f"https://reddit.com{permalink}" if permalink else item.get("url", "")

        return {
            "source": f"reddit/{sub}",
            "title": title,
            "content": body[:2000],
            "comments": [],
            "url": url,
            "hn_url": "",
            "score": score,
            "num_comments": num_comments,
            "has_need_signals": has_need_signals(title + " " + body),
            "created_utc": item.get("created_utc", 0) or item.get("created", 0) or 0,
            "_post_id": post_id,
            "_engine": "rdt-cli",
        }

    def _parse_read_result(self, raw: str) -> dict | None:
        objects = self._parse_ndjson(raw)
        if not objects:
            return None

        wrapper = objects[0]
        inner = wrapper.get("data", wrapper)

        # rdt read 返回 data: [Listing(帖子), Listing(评论)]
        post_data = {}
        comment_listing = None
        if isinstance(inner, list):
            for listing in inner:
                children = listing.get("data", {}).get("children", [])
                for c in children:
                    cd = c.get("data", c)
                    if c.get("kind") == "t3" or cd.get("title"):
                        post_data = cd
                    elif c.get("kind") == "t1" or cd.get("body"):
                        if comment_listing is None:
                            comment_listing = listing
        elif isinstance(inner, dict):
            children = inner.get("data", {}).get("children", [])
            if children:
                post_data = children[0].get("data", children[0])
            else:
                post_data = inner

        title = post_data.get("title", "")
        sub = post_data.get("subreddit", "") or post_data.get("sub", "")
        body = post_data.get("selftext", "") or post_data.get("body", "") or ""
        score = post_data.get("score", 0) or 0
        permalink = post_data.get("permalink", "")
        url = f"https://reddit.com{permalink}" if permalink else ""

        comments = self._extract_comments(inner)

        return {
            "source": f"reddit/{sub}",
            "title": title,
            "content": body[:3000],
            "comments": comments,
            "url": url,
            "hn_url": "",
            "score": score,
            "num_comments": len(comments),
            "created_utc": post_data.get("created_utc", 0) or post_data.get("created", 0) or 0,
            "has_need_signals": has_need_signals(title + " " + body + " " + " ".join(comments)),
            "_engine": "rdt-cli",
            "_full_body": body,
        }

    def _extract_comments(self, data: Any, max_comments: int = 35) -> list[str]:
        """提取评论，保留 2-3 层深度信息。

        返回字符串列表，高赞评论优先。每条评论前缀标注层级深度。
        """
        raw_comments: list[dict] = []

        def _walk(node: Any, depth: int = 0):
            if len(raw_comments) >= max_comments * 2:
                return
            if depth > 3:
                return
            if isinstance(node, dict):
                kind = node.get("kind", "")
                if kind == "t1" or (node.get("body") and kind != "t3"):
                    body = node.get("body", "")
                    score = node.get("score", 0) or 0
                    if body and body not in ("[deleted]", "[removed]") and len(body) > 30:
                        raw_comments.append({
                            "body": body[:800],
                            "score": score,
                            "depth": depth,
                        })
                children = node.get("data", {}).get("children", []) if isinstance(node.get("data"), dict) else []
                for child in children:
                    _walk(child.get("data", child) if isinstance(child, dict) else child, depth)
                replies = node.get("replies", {})
                if isinstance(replies, dict):
                    for child in replies.get("data", {}).get("children", []):
                        _walk(child.get("data", child) if isinstance(child, dict) else child, depth + 1)
                for child in node.get("children", []):
                    _walk(child, depth)
            elif isinstance(node, list):
                for item in node:
                    _walk(item, depth)

        _walk(data)

        raw_comments.sort(key=lambda c: (c["score"], -c["depth"]), reverse=True)

        comments: list[str] = []
        for c in raw_comments[:max_comments]:
            prefix = ""
            if c["depth"] > 0:
                prefix = f"[reply-L{c['depth']}] "
            comments.append(f"{prefix}{c['body']}")

        return comments


# ------------------------------------------------------------------
# Unified Fetcher
# ------------------------------------------------------------------

class RedditFetcher:
    def __init__(self):
        self.rdt = RdtEngine()
        self._active_engine: str = "unknown"
        self.force_engine: str | None = None

    async def initialize(self) -> dict[str, Any]:
        rdt_status = await self.rdt.check_available()
        if rdt_status["installed"] and rdt_status["authenticated"]:
            self._active_engine = "rdt-cli"
            return {"engine": "rdt-cli", "rdt_status": rdt_status}

        self._active_engine = "none"
        return {"engine": "none", "rdt_status": rdt_status}

    async def search(
        self,
        query: str,
        subreddit: str = "",
        sort: str = "top",
        time_filter: str = "month",
        limit: int = 10,
    ) -> list[dict]:
        if self._active_engine == "rdt-cli":
            try:
                return await self.rdt.search(query, subreddit, sort, time_filter, limit)
            except RdtEngineError as e:
                print(f"[RedditFetcher] rdt-cli error: {e}")
                return []
        return []

    async def read_post(self, post_id: str) -> dict | None:
        if self._active_engine == "rdt-cli":
            try:
                return await self.rdt.read_post(post_id)
            except RdtEngineError as e:
                print(f"[RedditFetcher] rdt-cli read error: {e}")
        return None

    @property
    def engine_name(self) -> str:
        return self._active_engine

    def get_status(self) -> dict[str, Any]:
        return {
            "active_engine": self._active_engine,
            "rdt_failures": self._rdt_failures,
        }


_fetcher: RedditFetcher | None = None


def get_reddit_fetcher() -> RedditFetcher:
    global _fetcher
    if _fetcher is None:
        _fetcher = RedditFetcher()
    return _fetcher


async def init_reddit_fetcher() -> dict[str, Any]:
    fetcher = get_reddit_fetcher()
    return await fetcher.initialize()


# ------------------------------------------------------------------
# Trending / hot data helpers
# ------------------------------------------------------------------

_trending_semaphores: dict[int, asyncio.Semaphore] = {}

def _get_trending_semaphore() -> asyncio.Semaphore:
    loop_id = id(asyncio.get_event_loop())
    if loop_id not in _trending_semaphores:
        _trending_semaphores[loop_id] = asyncio.Semaphore(1)
    return _trending_semaphores[loop_id]

_TRENDING_RDT_DELAY = 1.5

async def fetch_subreddit_info(subreddit: str) -> dict | None:
    """Get subreddit metadata (subscribers, description) via rdt sub-info."""
    try:
        async with _get_trending_semaphore():
            proc = await asyncio.create_subprocess_exec(
                "rdt", "sub-info", subreddit, "--json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        if proc.returncode != 0:
            return None
        data = json.loads(stdout.decode())
        info = data.get("data", data)
        return {
            "name": info.get("display_name", subreddit),
            "subscribers": info.get("subscribers", 0),
            "active_users": info.get("accounts_active", 0),
            "description": (info.get("public_description", "") or "")[:200],
            "title": info.get("title", ""),
        }
    except Exception as e:
        print(f"[rdt] sub-info {subreddit} error: {e}")
        return None


async def fetch_subreddit_hot(subreddit: str, sort: str = "top", time_filter: str = "day", limit: int = 5) -> list[dict]:
    """Get hot/top posts from a subreddit via rdt sub, with rate-limit retry."""
    max_retries = 2
    for attempt in range(max_retries + 1):
        try:
            async with _get_trending_semaphore():
                await asyncio.sleep(_TRENDING_RDT_DELAY)
                proc = await asyncio.create_subprocess_exec(
                    "rdt", "sub", subreddit, "-s", sort, "-t", time_filter,
                    "--limit", str(limit), "--json",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            raw = stdout.decode()
            if not raw.strip():
                print(f"[rdt] sub {subreddit}: empty stdout")
                return []
            data = json.loads(raw)
            if not data.get("ok", True):
                err_info = data.get("error", {})
                err_code = err_info.get("code", "") if isinstance(err_info, dict) else str(err_info)
                if "rate_limit" in str(err_code) and attempt < max_retries:
                    wait = 6 + attempt * 3
                    print(f"[rdt] sub {subreddit}: rate limited, retry in {wait}s (attempt {attempt+1})")
                    await asyncio.sleep(wait)
                    continue
                print(f"[rdt] sub {subreddit}: error {err_code}")
                return []
            children = data.get("data", {}).get("data", {}).get("children", [])
            posts = []
            for c in children:
                p = c.get("data", {})
                title = p.get("title", "")
                if not title:
                    continue
                posts.append({
                    "title": title,
                    "score": p.get("score", 0),
                    "num_comments": p.get("num_comments", 0),
                    "subreddit": p.get("subreddit", subreddit),
                    "permalink": p.get("permalink", ""),
                    "url": f"https://reddit.com{p.get('permalink', '')}",
                    "created_utc": p.get("created_utc", 0),
                })
            return posts
        except Exception as e:
            print(f"[rdt] sub {subreddit} hot error: {e}")
            return []
    return []
