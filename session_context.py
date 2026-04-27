"""
session_context.py — 多用户 Session 隔离管理

每个浏览器通过 X-Session-Id header 标识身份，后端为每个 session 维护独立的：
- LLM 配置和客户端实例
- 挖掘任务状态
- 辩论状态
- 数据文件目录（needs/debate/reports/token_stats 等）
"""

import json
import threading
import time
from pathlib import Path
from openai import OpenAI


ROOT = Path(__file__).parent
SESSIONS_DIR = ROOT / "data" / "sessions"
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

_sessions_lock = threading.Lock()
_sessions: dict[str, "SessionContext"] = {}

SESSION_EXPIRE_SECONDS = 90 * 24 * 3600  # 90 天无活跃才清理磁盘（保护报告和需求）
MEMORY_EXPIRE_SECONDS = 30 * 60          # 30 分钟无请求则释放内存（磁盘数据保留）

_LLM_TIMEOUT = 120

DEFAULT_ROLE_NAMES = {"director": "导演", "analyst": "产品经理", "critic": "杠精", "investor": "投资人"}

_DEFAULT_CONFIG = {
    "CLAUDE_BASE_URL": "",
    "CLAUDE_API_KEY": "",
    "CLAUDE_MODEL": "claude-sonnet-4",
    "GPT_BASE_URL": "",
    "GPT_API_KEY": "",
    "GPT_MODEL": "gpt-5.4",
    "TAVILY_API_KEY": "",
    "FEISHU_APP_ID": "",
    "FEISHU_APP_SECRET": "",
}


def _config_hash(cfg: dict) -> str:
    key = cfg.get("api_key", "")
    return f"{cfg.get('base_url', '')}|{key[:8] if key else ''}|{cfg.get('model', '')}"


class SessionContext:
    """单个用户 session 的全部隔离状态。"""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.lock = threading.Lock()
        self.last_active = time.time()

        # 数据目录
        self.data_dir = SESSIONS_DIR / session_id
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir = self.data_dir / "reports"
        self.reports_dir.mkdir(exist_ok=True)
        self.needs_cache = self.data_dir / "fetched_needs.json"
        self.debate_cache = self.data_dir / "debate_state.json"
        self.config_file = self.data_dir / "config.json"
        self.token_stats_file = self.data_dir / "token_stats.json"
        self.role_models_file = self.data_dir / "role_models.json"
        self.general_model_file = self.data_dir / "general_model.json"
        self.role_names_file = self.data_dir / "role_names.json"
        self.engine_pref_file = self.data_dir / "engine_preference.json"
        self.web_search_pref_file = self.data_dir / "web_search_preference.json"

        # LLM 客户端（按 session 独立）
        self._runtime_config: dict = {}
        self._claude_client: OpenAI | None = None
        self._gpt_client: OpenAI | None = None
        self._claude_config_hash: str = ""
        self._gpt_config_hash: str = ""

        # Token 统计
        self._token_lock = threading.Lock()
        self._token_stats: dict[str, dict[str, int]] = {
            "claude": {"input": 0, "output": 0, "calls": 0},
            "gpt": {"input": 0, "output": 0, "calls": 0},
        }

        # 角色模型映射
        self._role_model_map: dict[str, str] = {
            "director": "gpt",
            "analyst": "gpt",
            "critic": "gpt",
            "investor": "gpt",
        }

        # 全局模型偏好
        self._general_model: str = "gpt"

        # 角色名称
        self.role_names: dict[str, str] = dict(DEFAULT_ROLE_NAMES)

        # 引擎偏好
        self.engine_preference: str = "auto"
        self.web_search_engine: str = "gpt"

        # 挖掘任务状态
        self.fetch_lock = threading.Lock()
        self.fetch_thread: threading.Thread | None = None
        self.fetch_job: dict = self._empty_fetch_job()

        # 报告生成任务状态
        self.report_lock = threading.Lock()
        self.report_thread: threading.Thread | None = None
        self.report_job: dict = self._empty_report_job()

        # 辩论状态
        self.debate_state: dict = self._empty_debate_state()
        self.investor_thread: threading.Thread | None = None

        # 从磁盘加载持久化数据
        self._load_all()

        # 更新活跃时间
        self._touch()

    @staticmethod
    def _empty_fetch_job() -> dict:
        return {
            "active": False,
            "stop_requested": False,
            "progress": 0,
            "history": [],
            "error": "",
            "needs": None,
            "engine": "",
            "clustering_fallback": False,
        }

    @staticmethod
    def _empty_report_job() -> dict:
        return {
            "active": False,
            "need_index": -1,
            "progress": 0,
            "message": "",
            "chunks": [],
            "error": "",
            "filename": "",
            "done": False,
            "cursor": 0,
        }

    @staticmethod
    def _empty_debate_state() -> dict:
        return {
            "status": "idle",
            "round": 0,
            "max_rounds": 5,
            "debate_log": [],
            "analyst_messages": [],
            "analysis_result": None,
            "final_report": None,
            "selected_need_idx": None,
            "product_proposal": None,
            "deep_dive_analysis": None,
            "search_results": None,
            "topics": [],
            "current_topic_idx": -1,
            "topic_conclusions": [],
            "current_topic_exchanges": [],
        }

    def _touch(self):
        self.last_active = time.time()
        try:
            (self.data_dir / ".last_active").write_text(str(self.last_active))
        except Exception:
            pass

    def touch(self):
        """每次 API 请求时调用，更新活跃时间。"""
        self.last_active = time.time()
        # 不每次都写磁盘，每 5 分钟写一次
        try:
            ts_file = self.data_dir / ".last_active"
            if not ts_file.exists() or time.time() - ts_file.stat().st_mtime > 300:
                ts_file.write_text(str(self.last_active))
        except Exception:
            pass

    def _load_all(self):
        """从磁盘加载该 session 的持久化数据。"""
        self._load_config()
        self._load_token_stats()
        self._load_role_model_config()
        self._load_general_model()
        self._load_role_names()
        self._load_engine_prefs()
        self._load_debate_cache()

    # ── 配置 ──

    def _load_config(self):
        if self.config_file.exists():
            try:
                self._runtime_config = json.loads(self.config_file.read_text(encoding="utf-8"))
            except Exception:
                pass

    def save_config(self, config: dict):
        for k, v in config.items():
            if v:
                self._runtime_config[k] = v
        self.config_file.write_text(
            json.dumps(self._runtime_config, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self._claude_client = None
        self._gpt_client = None
        self._claude_config_hash = ""
        self._gpt_config_hash = ""

    def set_runtime_config(self, config: dict):
        self._runtime_config = config
        self._claude_client = None
        self._gpt_client = None
        self._claude_config_hash = ""
        self._gpt_config_hash = ""

    def get_config(self, prefix: str) -> dict:
        """读取 provider 配置，优先级：session 运行时 > .env > 默认值。"""
        import os
        base_url = (self._runtime_config.get(f"{prefix}_BASE_URL")
                    or os.getenv(f"{prefix}_BASE_URL")
                    or _DEFAULT_CONFIG.get(f"{prefix}_BASE_URL", ""))
        api_key = (self._runtime_config.get(f"{prefix}_API_KEY")
                   or os.getenv(f"{prefix}_API_KEY")
                   or _DEFAULT_CONFIG.get(f"{prefix}_API_KEY", ""))
        model = (self._runtime_config.get(f"{prefix}_MODEL")
                 or os.getenv(f"{prefix}_MODEL")
                 or _DEFAULT_CONFIG.get(f"{prefix}_MODEL", ""))
        return {"base_url": base_url, "api_key": api_key, "model": model}

    def get_provider_config(self, prefix: str) -> dict:
        return self.get_config(prefix)

    # ── LLM 客户端 ──

    def get_claude_client(self) -> OpenAI:
        cfg = self.get_config("CLAUDE")
        h = _config_hash(cfg)
        if self._claude_client is None or h != self._claude_config_hash:
            self._claude_client = OpenAI(base_url=cfg["base_url"], api_key=cfg["api_key"], timeout=_LLM_TIMEOUT)
            self._claude_config_hash = h
        return self._claude_client

    def get_gpt_client(self) -> OpenAI:
        cfg = self.get_config("GPT")
        h = _config_hash(cfg)
        if self._gpt_client is None or h != self._gpt_config_hash:
            self._gpt_client = OpenAI(base_url=cfg["base_url"], api_key=cfg["api_key"], timeout=_LLM_TIMEOUT)
            self._gpt_config_hash = h
        return self._gpt_client

    def reset_clients(self):
        self._claude_client = None
        self._gpt_client = None
        self._claude_config_hash = ""
        self._gpt_config_hash = ""

    # ── LLM 调用 ──

    def call_claude(self, messages: list[dict], max_tokens: int | None = None) -> str:
        client = self.get_claude_client()
        cfg = self.get_config("CLAUDE")
        kwargs: dict = {"model": cfg["model"], "messages": messages, "temperature": 0.7}
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        response = client.chat.completions.create(**kwargs)
        self.record_usage("claude", response.usage)
        return response.choices[0].message.content

    def call_gpt(self, messages: list[dict], max_tokens: int | None = None) -> str:
        client = self.get_gpt_client()
        cfg = self.get_config("GPT")
        kwargs: dict = {"model": cfg["model"], "messages": messages, "temperature": 0.7}
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        response = client.chat.completions.create(**kwargs)
        self.record_usage("gpt", response.usage)
        return response.choices[0].message.content

    def check_llm_available(self) -> tuple[bool, str]:
        """轻量级检测当前配置的 LLM 是否可用。返回 (ok, error_message)。"""
        provider = self._general_model
        if provider == "gpt":
            cfg = self.get_config("GPT")
            if not cfg["api_key"]:
                return False, "GPT API Key 未配置"
            try:
                client = self.get_gpt_client()
                client.chat.completions.create(
                    model=cfg["model"],
                    messages=[{"role": "user", "content": "Hi"}],
                    max_tokens=5,
                )
                return True, ""
            except Exception as e:
                return False, f"GPT 连接失败：{str(e)[:80]}"
        else:
            cfg = self.get_config("CLAUDE")
            if not cfg["api_key"]:
                return False, "Claude API Key 未配置"
            try:
                client = self.get_claude_client()
                client.chat.completions.create(
                    model=cfg["model"],
                    messages=[{"role": "user", "content": "Hi"}],
                    max_tokens=5,
                )
                return True, ""
            except Exception as e:
                return False, f"Claude 连接失败：{str(e)[:80]}"

    def check_role_models_available(self) -> tuple[bool, str]:
        """检测角色分配使用的模型是否可用。检查所有角色实际指向的 provider。"""
        providers_needed = set(self._role_model_map.values())
        for provider in providers_needed:
            if provider == "gpt":
                cfg = self.get_config("GPT")
                if not cfg["api_key"]:
                    return False, "角色使用了 GPT，但 GPT API Key 未配置"
                try:
                    client = self.get_gpt_client()
                    client.chat.completions.create(
                        model=cfg["model"],
                        messages=[{"role": "user", "content": "Hi"}],
                        max_tokens=5,
                    )
                except Exception as e:
                    return False, f"角色使用了 GPT，但 GPT 连接失败：{str(e)[:80]}"
            else:
                cfg = self.get_config("CLAUDE")
                if not cfg["api_key"]:
                    return False, "角色使用了 Claude，但 Claude API Key 未配置"
                try:
                    client = self.get_claude_client()
                    client.chat.completions.create(
                        model=cfg["model"],
                        messages=[{"role": "user", "content": "Hi"}],
                        max_tokens=5,
                    )
                except Exception as e:
                    return False, f"角色使用了 Claude，但 Claude 连接失败：{str(e)[:80]}"
        return True, ""

    def call_llm(self, messages: list[dict], max_tokens: int | None = None) -> str:
        if self._general_model == "gpt":
            cfg = self.get_config("GPT")
            if cfg["api_key"]:
                return self.call_gpt(messages, max_tokens)
        return self.call_claude(messages, max_tokens)

    def call_llm_stream(self, messages: list[dict], max_tokens: int | None = None):
        """流式调用 LLM，根据 general_model 设置选择 GPT 或 Claude。"""
        if self._general_model == "gpt":
            cfg = self.get_config("GPT")
            if cfg["api_key"]:
                yield from self.call_gpt_stream(messages, max_tokens)
                return
        yield from self.call_claude_stream(messages, max_tokens)

    def call_claude_stream(self, messages: list[dict], max_tokens: int | None = None):
        client = self.get_claude_client()
        cfg = self.get_config("CLAUDE")
        for attempt in range(2):
            had_content = False
            try:
                kwargs: dict = {
                    "model": cfg["model"], "messages": messages, "temperature": 0.7,
                    "stream": True, "stream_options": {"include_usage": True},
                }
                if max_tokens is not None:
                    kwargs["max_tokens"] = max_tokens
                stream = client.chat.completions.create(**kwargs)
                for chunk in stream:
                    if chunk.usage:
                        self.record_usage("claude", chunk.usage)
                    if chunk.choices and chunk.choices[0].delta.content:
                        had_content = True
                        yield chunk.choices[0].delta.content
                return
            except Exception as e:
                print(f"[Claude] stream attempt {attempt+1}/2 failed (had_content={had_content}): {e}")
                if had_content:
                    return
                if attempt < 1:
                    import time as _t
                    _t.sleep(1)
                    continue
                raise

    def call_gpt_stream(self, messages: list[dict], max_tokens: int | None = None):
        client = self.get_gpt_client()
        cfg = self.get_config("GPT")
        model = cfg["model"]
        for attempt in range(3):
            had_content = False
            try:
                kwargs: dict = {
                    "model": model, "messages": messages, "temperature": 0.7, "stream": True,
                }
                if attempt == 0:
                    kwargs["stream_options"] = {"include_usage": True}
                if max_tokens is not None:
                    kwargs["max_tokens"] = max_tokens
                stream = client.chat.completions.create(**kwargs)
                for chunk in stream:
                    if hasattr(chunk, 'usage') and chunk.usage:
                        self.record_usage("gpt", chunk.usage)
                    if chunk.choices and chunk.choices[0].delta.content:
                        had_content = True
                        yield chunk.choices[0].delta.content
                return
            except Exception as e:
                err_msg = str(e).lower()
                print(f"[GPT] stream attempt {attempt+1}/3 failed (had_content={had_content}): {e}")
                if had_content:
                    return
                if attempt < 2 and ("stream" in err_msg or "codex" in err_msg or "500" in err_msg or "server" in err_msg):
                    import time as _t
                    _t.sleep(1)
                    continue
                raise

    def call_for_role(self, role: str, messages: list[dict], max_tokens: int | None = None) -> str:
        provider = self._role_model_map.get(role, "claude")
        if provider == "gpt":
            cfg_g = self.get_config("GPT")
            if cfg_g["api_key"]:
                return self.call_gpt(messages, max_tokens)
        return self.call_claude(messages, max_tokens)

    def call_for_role_stream(self, role: str, messages: list[dict], max_tokens: int | None = None):
        provider = self._role_model_map.get(role, "claude")
        if provider == "gpt":
            cfg_g = self.get_config("GPT")
            if cfg_g["api_key"]:
                return self.call_gpt_stream(messages, max_tokens)
        return self.call_claude_stream(messages, max_tokens)

    # ── Token 统计 ──

    def record_usage(self, provider: str, usage):
        if not usage:
            return
        inp = getattr(usage, "prompt_tokens", 0) or 0
        out = getattr(usage, "completion_tokens", 0) or 0
        if inp == 0 and out == 0:
            return
        with self._token_lock:
            self._token_stats[provider]["input"] += inp
            self._token_stats[provider]["output"] += out
            self._token_stats[provider]["calls"] += 1
            self._save_token_stats()

    def _load_token_stats(self):
        if self.token_stats_file.exists():
            try:
                data = json.loads(self.token_stats_file.read_text(encoding="utf-8"))
                for k in ("claude", "gpt"):
                    if k in data and isinstance(data[k], dict):
                        self._token_stats[k] = {
                            "input": int(data[k].get("input", 0)),
                            "output": int(data[k].get("output", 0)),
                            "calls": int(data[k].get("calls", 0)),
                        }
            except Exception:
                pass

    def _save_token_stats(self):
        try:
            self.token_stats_file.write_text(
                json.dumps(self._token_stats, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass

    def get_token_stats(self) -> dict:
        with self._token_lock:
            return {k: dict(v) for k, v in self._token_stats.items()}

    def reset_token_stats(self):
        with self._token_lock:
            self._token_stats = {
                "claude": {"input": 0, "output": 0, "calls": 0},
                "gpt": {"input": 0, "output": 0, "calls": 0},
            }
            self._save_token_stats()

    # ── 角色模型 ──

    def _load_role_model_config(self):
        if self.role_models_file.exists():
            try:
                data = json.loads(self.role_models_file.read_text(encoding="utf-8"))
                for role in ("director", "analyst", "critic", "investor"):
                    if role in data and data[role] in ("claude", "gpt"):
                        self._role_model_map[role] = data[role]
            except Exception:
                pass

    def get_role_model_config(self) -> dict:
        return dict(self._role_model_map)

    def set_role_model_config(self, mapping: dict):
        for role in ("director", "analyst", "critic", "investor"):
            if role in mapping and mapping[role] in ("claude", "gpt"):
                self._role_model_map[role] = mapping[role]
        self.role_models_file.write_text(json.dumps(self._role_model_map, ensure_ascii=False), encoding="utf-8")

    # ── 全局模型偏好 ──

    def _load_general_model(self):
        if self.general_model_file.exists():
            try:
                data = json.loads(self.general_model_file.read_text(encoding="utf-8"))
                if data.get("model") in ("claude", "gpt"):
                    self._general_model = data["model"]
            except Exception:
                pass

    def get_general_model(self) -> str:
        return self._general_model

    def set_general_model(self, model: str):
        if model in ("claude", "gpt"):
            self._general_model = model
            self.general_model_file.write_text(json.dumps({"model": model}), encoding="utf-8")

    # ── 角色名称 ──

    def _load_role_names(self):
        if self.role_names_file.exists():
            try:
                saved = json.loads(self.role_names_file.read_text(encoding="utf-8"))
                if isinstance(saved, dict):
                    self.role_names.update(saved)
            except Exception:
                pass

    def save_role_names(self, mapping: dict):
        for key in ("director", "analyst", "critic", "investor"):
            if key in mapping and isinstance(mapping[key], str) and mapping[key].strip():
                self.role_names[key] = mapping[key].strip()[:10]
        self.role_names_file.write_text(
            json.dumps(self.role_names, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ── 引擎偏好 ──

    def _load_engine_prefs(self):
        if self.engine_pref_file.exists():
            try:
                self.engine_preference = json.loads(self.engine_pref_file.read_text()).get("preference", "auto")
            except Exception:
                pass
        if self.web_search_pref_file.exists():
            try:
                self.web_search_engine = json.loads(self.web_search_pref_file.read_text()).get("engine", "tavily")
            except Exception:
                pass

    def save_engine_preference(self, pref: str):
        self.engine_preference = pref
        self.engine_pref_file.write_text(json.dumps({"preference": pref}), encoding="utf-8")

    def save_web_search_engine(self, engine: str):
        self.web_search_engine = engine
        self.web_search_pref_file.write_text(json.dumps({"engine": engine}), encoding="utf-8")

    # ── 辩论 ──

    def _load_debate_cache(self):
        if self.debate_cache.exists():
            try:
                with open(self.debate_cache, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                self.debate_state.update(loaded)
            except Exception:
                pass

    def save_debate_cache(self):
        try:
            tmp = self.debate_cache.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.debate_state, f, ensure_ascii=False)
            tmp.replace(self.debate_cache)
        except Exception as e:
            print(f"[Session {self.session_id}] save_debate_cache failed: {e}")

    def reset_debate(self):
        max_rounds = self.debate_state.get("max_rounds", 5)
        self.debate_state = self._empty_debate_state()
        self.debate_state["max_rounds"] = max_rounds
        if self.debate_cache.exists():
            self.debate_cache.unlink()

    # ── 挖掘 ──

    def fetch_emit(self, message: str, progress: int):
        with self.fetch_lock:
            self.fetch_job["progress"] = progress
            self.fetch_job["history"].append(message)

    def fetch_is_stopped(self) -> bool:
        with self.fetch_lock:
            return self.fetch_job["stop_requested"]

    def reset_fetch_job(self):
        with self.fetch_lock:
            self.fetch_job = self._empty_fetch_job()

    # ── 配置检查 ──

    def check_config(self) -> dict:
        errors = []
        claude_ok = True
        gpt_ok = True
        cfg_c = self.get_config("CLAUDE")
        cfg_g = self.get_config("GPT")

        if not cfg_c["base_url"]:
            claude_ok = False
        if not cfg_c["api_key"]:
            claude_ok = False
        if not cfg_g["base_url"]:
            gpt_ok = False
        if not cfg_g["api_key"]:
            gpt_ok = False

        return {"ready": claude_ok or gpt_ok, "claude_ok": claude_ok, "gpt_ok": gpt_ok, "errors": errors}

    def _is_gpt_builtin(self) -> bool:
        """GPT 配置是否来自系统内置（.env）而非用户自行配置。"""
        import os
        has_user_key = bool(self._runtime_config.get("GPT_API_KEY"))
        has_env_key = bool(os.getenv("GPT_API_KEY", ""))
        return has_env_key and not has_user_key

    def get_config_values(self) -> dict:
        import os
        cfg_c = self.get_config("CLAUDE")
        cfg_g = self.get_config("GPT")
        tavily_key = self._runtime_config.get("TAVILY_API_KEY") or os.getenv("TAVILY_API_KEY", "")
        feishu_id = self._runtime_config.get("FEISHU_APP_ID") or os.getenv("FEISHU_APP_ID", "")
        feishu_secret = self._runtime_config.get("FEISHU_APP_SECRET") or os.getenv("FEISHU_APP_SECRET", "")
        gpt_builtin = self._is_gpt_builtin()
        return {
            "CLAUDE_BASE_URL": cfg_c["base_url"],
            "CLAUDE_API_KEY": _mask_key(cfg_c["api_key"]),
            "CLAUDE_API_KEY_SET": bool(cfg_c["api_key"]),
            "CLAUDE_MODEL": cfg_c["model"],
            "GPT_BASE_URL": "" if gpt_builtin else cfg_g["base_url"],
            "GPT_API_KEY": "" if gpt_builtin else _mask_key(cfg_g["api_key"]),
            "GPT_API_KEY_SET": bool(cfg_g["api_key"]),
            "GPT_MODEL": cfg_g["model"] if not gpt_builtin else "",
            "GPT_BUILTIN": gpt_builtin,
            "TAVILY_API_KEY": _mask_key(tavily_key),
            "FEISHU_APP_ID": feishu_id,
            "FEISHU_APP_SECRET": _mask_key(feishu_secret),
            "role_models": self.get_role_model_config(),
        }

    def test_connection(self, prefix: str, override: dict | None = None) -> tuple[bool, str]:
        cfg = self.get_config(prefix)
        if override:
            for k, v in override.items():
                if v:
                    cfg[k] = v
        if not cfg["base_url"] or not cfg["api_key"]:
            return False, "Missing base_url or api_key"
        try:
            client = OpenAI(base_url=cfg["base_url"], api_key=cfg["api_key"])
            resp = client.chat.completions.create(
                model=cfg["model"],
                messages=[{"role": "user", "content": "Say OK"}],
                max_tokens=10,
            )
            content = resp.choices[0].message.content
            return True, f"Connected. Response: {content[:50]}"
        except Exception as e:
            return False, str(e)[:200]


def _mask_key(key: str) -> str:
    if not key or len(key) < 8:
        return ""
    return key[:3] + "..." + key[-4:]


import re

_SAFE_SESSION_RE = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")


def _sanitize_session_id(raw: str) -> str:
    """校验 session_id 只含安全字符，防止路径穿越。"""
    if not raw or not _SAFE_SESSION_RE.match(raw):
        return "default"
    return raw


def get_session(session_id: str) -> SessionContext:
    """获取或创建 session。线程安全。"""
    session_id = _sanitize_session_id(session_id)
    with _sessions_lock:
        ctx = _sessions.get(session_id)
        if ctx is not None:
            ctx.touch()
            return ctx
    # 在锁外创建（可能有磁盘 I/O），然后再加锁放入
    new_ctx = SessionContext(session_id)
    with _sessions_lock:
        # double-check
        existing = _sessions.get(session_id)
        if existing is not None:
            existing.touch()
            return existing
        _sessions[session_id] = new_ctx
        return new_ctx


def cleanup_expired_sessions():
    """清理过期的 session 目录和内存缓存。在后端启动时调用。"""
    now = time.time()

    # 清理内存中过期的 session
    with _sessions_lock:
        expired_ids = [
            sid for sid, ctx in _sessions.items()
            if now - ctx.last_active > MEMORY_EXPIRE_SECONDS
            and not ctx.fetch_job.get("active", False)
            and not ctx.report_job.get("active", False)
        ]
        for sid in expired_ids:
            del _sessions[sid]
            print(f"[Session] Released memory for inactive session: {sid[:8]}...")

    # 清理磁盘上过期的 session 目录
    if not SESSIONS_DIR.exists():
        return
    for session_dir in SESSIONS_DIR.iterdir():
        if not session_dir.is_dir():
            continue
        ts_file = session_dir / ".last_active"
        last = 0
        if ts_file.exists():
            try:
                last = float(ts_file.read_text().strip())
            except Exception:
                last = ts_file.stat().st_mtime
        else:
            last = session_dir.stat().st_mtime

        if now - last > SESSION_EXPIRE_SECONDS:
            import shutil
            try:
                shutil.rmtree(session_dir)
                print(f"[Session] Cleaned up expired session dir: {session_dir.name[:8]}...")
            except Exception as e:
                print(f"[Session] Failed to clean {session_dir.name}: {e}")
