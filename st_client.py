"""
st_client.py — SensorTower CLI (st-cli) 封装

通过调用本机安装的 st-cli 命令行工具获取竞品数据，
包括月收入、月下载、月活、市占率、增长率、App Store 评论等。
同时支持品类级别的 Top Apps 和市场数据查询（用于热度监控模块）。
"""

import json
import subprocess
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# st-cli 内部模块动态导入（用于品类级 API 调用）
# ---------------------------------------------------------------------------
_ST_CLI_SITE_PKGS: str | None = None
_st_api_mod: Any = None
_st_auth_mod: Any = None
_st_client_mod: Any = None


def _ensure_st_cli_imports() -> bool:
    """延迟导入 st-cli 内部模块，加入 sys.path（仅一次）。"""
    global _ST_CLI_SITE_PKGS, _st_api_mod, _st_auth_mod, _st_client_mod
    if _st_api_mod is not None:
        return True
    try:
        import importlib
        st_bin = subprocess.run(
            ["which", "st"], capture_output=True, text=True, timeout=5
        ).stdout.strip()
        if not st_bin:
            return False
        st_script = Path(st_bin).resolve()
        # uv 安装路径: .../sensortower-st-cli/bin/python → .../lib/python3.*/site-packages/
        venv_root = st_script.parent.parent
        site_dirs = list(venv_root.glob("lib/python*/site-packages"))
        if not site_dirs:
            return False
        sp = str(site_dirs[0])
        if sp not in sys.path:
            sys.path.insert(0, sp)
        _ST_CLI_SITE_PKGS = sp
        _st_api_mod = importlib.import_module("st_cli.st_api")
        _st_auth_mod = importlib.import_module("st_cli.auth")
        _st_client_mod = importlib.import_module("st_cli.st_client")
        return True
    except Exception as e:
        print(f"[st_client] failed to import st-cli internals: {e}")
        return False


def _get_st_http_client():
    """获取带 cookie 认证的 SensorTower httpx 客户端。"""
    if not _ensure_st_cli_imports():
        return None
    cred = _st_auth_mod.get_credential()
    if not cred or not cred.is_valid:
        print("[st_client] no valid ST credential")
        return None
    return _st_client_mod.create_st_client(cred.cookies)


def fetch_category_market_data(
    category_id: int,
    *,
    top_n: int = 30,
) -> dict[str, Any] | None:
    """查询指定 SensorTower 品类的市场聚合数据。

    返回: {product_count, revenue_sum, revenue_avg, downloads_sum,
           revenue_growth_pct, top_apps: [{name, revenue, downloads}, ...]}
    """
    if not _ensure_st_cli_imports():
        return None
    client = _get_st_http_client()
    if not client:
        return None

    try:
        today = date.today()
        month_start = date(today.year, today.month, 1)
        if today.month == 1:
            prev_start = date(today.year - 1, 12, 1)
        else:
            prev_start = date(today.year, today.month - 1, 1)
        prev_end = month_start - timedelta(days=1)
        month_end = today

        csrf = _st_api_mod.get_csrf_token_for_top_apps_page(client)

        regions = ["US"]

        # 获取 top_apps 原始数据（含 unified_app_id + sub_app_ids）
        top_apps_params = {
            "os": "unified",
            "filters": {
                "measure": "revenue",
                "comparison_attribute": "absolute",
                "category": category_id,
                "devices": ["iphone", "ipad", "android"],
                "regions": regions,
                "start_date": prev_start.strftime("%Y-%m-%d"),
                "end_date": prev_end.strftime("%Y-%m-%d"),
                "time_range": "day",
            },
            "pagination": {"limit": top_n, "offset": 0},
            "data_model": _st_api_mod.DEFAULT_DATA_MODEL,
        }
        headers: dict[str, str] = dict(_st_api_mod.POST_JSON_HEADERS)
        if csrf:
            headers["x-csrf-token"] = csrf
        r = client.post("/api/unified/top_apps", json=top_apps_params, headers=headers)
        top_raw = r.json()
        items = top_raw.get("data", {}).get("apps_ids", []) if isinstance(top_raw, dict) else []

        # 提取 sub_app_ids 和 unified_app_ids 的映射
        app_ids: list[int | str] = []
        unified_to_sub: dict[str, list] = {}
        unified_ids: list[str] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            uid = it.get("unified_app_id", "")
            subs = it.get("sub_app_ids") or []
            if uid:
                unified_ids.append(uid)
                unified_to_sub[uid] = subs
            for sid in subs:
                if sid is not None:
                    app_ids.append(sid)
        if not app_ids:
            return None

        orig_regions = _st_api_mod.DEFAULT_FACET_REGIONS
        _st_api_mod.DEFAULT_FACET_REGIONS = ["US"]
        try:
            facet_rows = _st_api_mod.apps_facets_v2_month_slice(
                client,
                app_ids=app_ids,
                month_start=prev_start,
                month_end=prev_end,
                comparison_start=prev_start - timedelta(days=30),
                comparison_end=prev_start - timedelta(days=1),
                csrf_token=csrf,
                limit=top_n + 5,
            )
        finally:
            _st_api_mod.DEFAULT_FACET_REGIONS = orig_regions

        revenue_sum = 0.0
        downloads_sum = 0.0
        growth_vals: list[float] = []
        product_count = 0
        top_apps: list[dict] = []

        for row in facet_rows:
            if row.get("appId") is not None:
                continue
            product_count += 1

            rev_raw = row.get("revenueAbsolute")
            rev = 0.0
            if rev_raw is not None and rev_raw != "":
                try:
                    rev = float(rev_raw) / 100.0
                except (ValueError, TypeError):
                    pass
            revenue_sum += rev

            dl_raw = row.get("downloadsAbsolute")
            dl = 0
            if dl_raw is not None and dl_raw != "":
                try:
                    dl = int(float(dl_raw))
                except (ValueError, TypeError):
                    pass
            downloads_sum += dl

            g = row.get("revenueGrowthPercent")
            g_pct = None
            if g is not None and g != "":
                try:
                    g_pct = float(g) * 100
                    growth_vals.append(g_pct)
                except (ValueError, TypeError):
                    pass

            dl_g = row.get("downloadsGrowthPercent")
            dl_g_pct = None
            if dl_g is not None and dl_g != "":
                try:
                    dl_g_pct = round(float(dl_g) * 100, 1)
                except (ValueError, TypeError):
                    pass

            dau_raw = row.get("activeUsersDAUAbsolute")
            dau = 0
            if dau_raw is not None and dau_raw != "":
                try:
                    dau = int(float(dau_raw))
                except (ValueError, TypeError):
                    pass

            top_apps.append({
                "name": "",
                "icon_url": "",
                "publisher": "",
                "_unified_id": row.get("unifiedAppId", ""),
                "revenue": round(rev, 2),
                "revenue_display": _format_currency(rev) if rev else "-",
                "downloads": dl,
                "downloads_display": _format_number(dl) if dl else "-",
                "growth_pct": round(g_pct, 1) if g_pct is not None else None,
                "downloads_growth_pct": dl_g_pct,
                "dau": dau,
                "dau_display": _format_number(dau) if dau else "-",
            })

        top_apps.sort(key=lambda x: x["revenue"], reverse=True)

        # 获取产品名称、icon（通过 internal_entities）
        if unified_ids:
            try:
                entities = _st_api_mod.internal_entities(
                    client, unified_ids[:top_n], csrf_token=csrf
                )
                uid_info: dict[str, dict] = {}
                for ent in entities:
                    eid = ent.get("id") or ent.get("app_id") or ""
                    uid_info[eid] = {
                        "name": ent.get("name") or ent.get("humanized_name") or "",
                        "publisher": ent.get("publisher_name") or "",
                        "icon_url": ent.get("icon_url") or "",
                    }
                # facet_rows 按 unifiedAppId 关联
                for app in top_apps:
                    uid = app.pop("_unified_id", "")
                    if uid and uid in uid_info:
                        info = uid_info[uid]
                        app["name"] = info["name"]
                        app["publisher"] = info["publisher"]
                        app["icon_url"] = info["icon_url"]
            except Exception as e:
                print(f"[st_client] internal_entities error: {e}")

        revenue_avg = revenue_sum / product_count if product_count else 0
        avg_growth = sum(growth_vals) / len(growth_vals) if growth_vals else 0

        final_top = []
        for a in top_apps[:5]:
            a.pop("_unified_id", None)
            final_top.append(a)

        return {
            "product_count": product_count,
            "revenue_sum": round(revenue_sum, 2),
            "revenue_avg": round(revenue_avg, 2),
            "downloads_sum": round(downloads_sum),
            "revenue_growth_pct": round(avg_growth, 1),
            "top_apps": final_top,
        }
    except Exception as e:
        print(f"[st_client] category market data error for {category_id}: {e}")
        return None
    finally:
        try:
            client.close()
        except Exception:
            pass


def fetch_niche_market_data(
    queries: list[str],
    *,
    top_n: int = 20,
) -> dict[str, Any] | None:
    """用多个搜索关键词查找细分赛道的头部产品，然后聚合市场数据。

    流程：autocomplete_search 多词搜索 → 去重 → facets 月度数据 → 聚合 + Top5。
    返回格式与 fetch_category_market_data 一致。
    """
    if not _ensure_st_cli_imports():
        return None
    client = _get_st_http_client()
    if not client:
        return None

    try:
        today = date.today()
        month_start = date(today.year, today.month, 1)
        if today.month == 1:
            prev_start = date(today.year - 1, 12, 1)
        else:
            prev_start = date(today.year, today.month - 1, 1)
        prev_end = month_start - timedelta(days=1)

        csrf = _st_api_mod.get_csrf_token_for_top_apps_page(client)

        # 1. 多关键词搜索，按 unified_app_id 去重
        seen_uids: set[str] = set()
        app_entries: list[dict] = []
        for q in queries:
            try:
                results = _st_api_mod.autocomplete_search(client, q, limit=8)
                for ent in results:
                    uid = str(ent.get("id") or ent.get("app_id") or "")
                    if not uid or uid in seen_uids:
                        continue
                    seen_uids.add(uid)
                    app_entries.append(ent)
            except Exception as e:
                print(f"[st_client] autocomplete '{q}' error: {e}")
        if not app_entries:
            return None

        # 2. 提取 sub_app_ids
        sub_app_ids: list[int | str] = []
        unified_ids: list[str] = []
        for ent in app_entries:
            uid = str(ent.get("id") or ent.get("app_id") or "")
            if uid:
                unified_ids.append(uid)
            for sub in ent.get("ios_apps", []) + ent.get("android_apps", []):
                sid = sub.get("id") or sub.get("app_id")
                if sid is not None:
                    sub_app_ids.append(sid)
        if not sub_app_ids:
            return None

        # 3. facets 月度数据（仅美区）
        orig_regions = _st_api_mod.DEFAULT_FACET_REGIONS
        _st_api_mod.DEFAULT_FACET_REGIONS = ["US"]
        try:
            facet_rows = _st_api_mod.apps_facets_v2_month_slice(
                client,
                app_ids=sub_app_ids[:60],
                month_start=prev_start,
                month_end=prev_end,
                comparison_start=prev_start - timedelta(days=30),
                comparison_end=prev_start - timedelta(days=1),
                csrf_token=csrf,
                limit=len(sub_app_ids) + 10,
            )
        finally:
            _st_api_mod.DEFAULT_FACET_REGIONS = orig_regions

        # 4. 只取汇总行 (appId=None) 聚合
        revenue_sum = 0.0
        downloads_sum = 0.0
        growth_vals: list[float] = []
        product_count = 0
        top_apps: list[dict] = []

        for row in facet_rows:
            if row.get("appId") is not None:
                continue
            product_count += 1

            rev_raw = row.get("revenueAbsolute")
            rev = 0.0
            if rev_raw is not None and rev_raw != "":
                try:
                    rev = float(rev_raw) / 100.0
                except (ValueError, TypeError):
                    pass
            revenue_sum += rev

            dl_raw = row.get("downloadsAbsolute")
            dl = 0
            if dl_raw is not None and dl_raw != "":
                try:
                    dl = int(float(dl_raw))
                except (ValueError, TypeError):
                    pass
            downloads_sum += dl

            g = row.get("revenueGrowthPercent")
            g_pct = None
            if g is not None and g != "":
                try:
                    g_pct = float(g) * 100
                    growth_vals.append(g_pct)
                except (ValueError, TypeError):
                    pass

            dl_g = row.get("downloadsGrowthPercent")
            dl_g_pct = None
            if dl_g is not None and dl_g != "":
                try:
                    dl_g_pct = round(float(dl_g) * 100, 1)
                except (ValueError, TypeError):
                    pass

            dau_raw = row.get("activeUsersDAUAbsolute")
            dau = 0
            if dau_raw is not None and dau_raw != "":
                try:
                    dau = int(float(dau_raw))
                except (ValueError, TypeError):
                    pass

            top_apps.append({
                "name": "",
                "icon_url": "",
                "publisher": "",
                "_unified_id": row.get("unifiedAppId", ""),
                "revenue": round(rev, 2),
                "revenue_display": _format_currency(rev) if rev else "-",
                "downloads": dl,
                "downloads_display": _format_number(dl) if dl else "-",
                "growth_pct": round(g_pct, 1) if g_pct is not None else None,
                "downloads_growth_pct": dl_g_pct,
                "dau": dau,
                "dau_display": _format_number(dau) if dau else "-",
            })

        top_apps.sort(key=lambda x: x["revenue"], reverse=True)

        # 5. 用 internal_entities 获取产品名称/icon
        #    先从 autocomplete 原始数据建 fallback 名称映射
        name_fallback: dict[str, dict] = {}
        for ent in app_entries:
            uid = str(ent.get("id") or ent.get("app_id") or "")
            fb_name = ent.get("name") or ent.get("humanized_name") or ""
            fb_icon = ent.get("icon_url") or ""
            fb_pub = ent.get("publisher_name") or ""
            if uid and fb_name:
                name_fallback[uid] = {"name": fb_name, "icon_url": fb_icon, "publisher": fb_pub}

        facet_uids = list({a["_unified_id"] for a in top_apps if a.get("_unified_id")})
        if facet_uids:
            try:
                entities = _st_api_mod.internal_entities(
                    client, facet_uids[:30], csrf_token=csrf
                )
                uid_info: dict[str, dict] = {}
                for ent in entities:
                    eid = str(ent.get("id") or ent.get("app_id") or "")
                    uid_info[eid] = {
                        "name": ent.get("name") or ent.get("humanized_name") or "",
                        "publisher": ent.get("publisher_name") or "",
                        "icon_url": ent.get("icon_url") or "",
                    }
                for app in top_apps:
                    uid = app.get("_unified_id", "")
                    if uid and uid in uid_info:
                        info = uid_info[uid]
                        app["name"] = info["name"]
                        app["publisher"] = info["publisher"]
                        app["icon_url"] = info["icon_url"]
            except Exception as e:
                print(f"[st_client] internal_entities error: {e}")

        for app in top_apps:
            if not app.get("name"):
                uid = app.get("_unified_id", "")
                if uid and uid in name_fallback:
                    fb = name_fallback[uid]
                    app["name"] = fb["name"]
                    if not app.get("icon_url"):
                        app["icon_url"] = fb["icon_url"]
                    if not app.get("publisher"):
                        app["publisher"] = fb["publisher"]

        revenue_avg = revenue_sum / product_count if product_count else 0
        avg_growth = sum(growth_vals) / len(growth_vals) if growth_vals else 0

        final_top = []
        for a in top_apps[:5]:
            a.pop("_unified_id", None)
            final_top.append(a)

        return {
            "product_count": product_count,
            "revenue_sum": round(revenue_sum, 2),
            "revenue_avg": round(revenue_avg, 2),
            "downloads_sum": round(downloads_sum),
            "revenue_growth_pct": round(avg_growth, 1),
            "top_apps": final_top,
        }
    except Exception as e:
        print(f"[st_client] niche market data error: {e}")
        return None
    finally:
        try:
            client.close()
        except Exception:
            pass


def check_available() -> dict:
    """检测 st-cli 是否安装且已认证。"""
    try:
        result = subprocess.run(
            ["st", "status", "--json"],
            capture_output=True, text=True, timeout=15,
        )
        data = json.loads(result.stdout.strip()) if result.stdout.strip() else {}
        ok = data.get("ok", False)
        inner = data.get("data", {}) or {}
        err_info = data.get("error", {}) or {}
        err_details = err_info.get("details", {}) or {}
        return {
            "installed": True,
            "available": ok,
            "api_ok": inner.get("api_ok", False) or err_details.get("api_ok", False),
            "credential_source": inner.get("credential_source", "") or err_details.get("credential_source", ""),
            "error": err_info.get("message", "") if not ok else "",
        }
    except FileNotFoundError:
        return {"installed": False, "available": False, "api_ok": False, "error": "st-cli 未安装"}
    except Exception as e:
        return {"installed": False, "available": False, "api_ok": False, "error": str(e)[:200]}


def fetch_app(query: str) -> dict | None:
    """查询单个 App 的 SensorTower 数据。

    query 可以是 App 名称或 App Store URL。
    返回归一化后的 dict 或 None。
    """
    try:
        result = subprocess.run(
            ["st", "fetch", query, "--json"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            print(f"[st-cli] fetch failed: {result.stderr[:200]}")
            return None

        data = json.loads(result.stdout.strip())
        if not data.get("ok"):
            return None

        inner = data.get("data", {})

        if inner.get("needs_disambiguation"):
            candidates = inner.get("candidates", [])
            if candidates:
                return _normalize_app(candidates[0])
            return None

        selected = inner.get("selected")
        if selected:
            return _normalize_app(selected)

        return None
    except Exception as e:
        print(f"[st-cli] fetch error: {e}")
        return None


def fetch_landscape(competitors: list[dict], limit: int = 5) -> list[dict]:
    """批量查询竞品的 SensorTower 数据。

    competitors: [{"name": "Duolingo", "url": "https://apps.apple.com/app/id570060128"}, ...]
    返回归一化后的竞品数据列表。
    """
    if not competitors:
        return []

    entries = competitors[:limit]

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8",
    ) as f:
        for c in entries:
            url = c.get("url") or c.get("app_store_url") or ""
            name = c.get("name", "")
            if url:
                f.write(f"{name}\t{url}\n")
            else:
                f.write(f"{name}\n")
        tmp_path = f.name

    try:
        result = subprocess.run(
            [
                "st", "landscape",
                "--competitors-file", tmp_path,
                "--limit", str(limit),
                "--json",
            ],
            capture_output=True, text=True, timeout=180,
        )

        Path(tmp_path).unlink(missing_ok=True)

        if result.returncode != 0:
            print(f"[st-cli] landscape failed: {result.stderr[:300]}")
            return []

        data = json.loads(result.stdout.strip())
        if not data.get("ok"):
            print(f"[st-cli] landscape not ok: {data.get('error', {})}")
            return []

        raw_competitors = data.get("data", {}).get("competitors", [])
        results = []
        for rc in raw_competitors:
            normalized = _normalize_competitor(rc)
            if normalized:
                results.append(normalized)

        return results

    except subprocess.TimeoutExpired:
        print("[st-cli] landscape timeout (180s)")
        Path(tmp_path).unlink(missing_ok=True)
        return []
    except Exception as e:
        print(f"[st-cli] landscape error: {e}")
        Path(tmp_path).unlink(missing_ok=True)
        return []


def _normalize_app(app: dict) -> dict:
    """从 st fetch 的 autocomplete 结果中提取关键字段。"""
    rev = app.get("humanized_worldwide_last_month_revenue", {})
    dl = app.get("humanized_worldwide_last_month_downloads", {})
    return {
        "name": app.get("name", ""),
        "publisher": app.get("publisher_name", ""),
        "revenue_last_month": rev.get("revenue"),
        "revenue_display": rev.get("string", "-"),
        "downloads_last_month": dl.get("downloads"),
        "downloads_display": dl.get("string", "-"),
        "icon_url": app.get("icon_url", ""),
        "release_date": app.get("release_date", ""),
    }


def _normalize_competitor(rc: dict) -> dict | None:
    """从 st landscape 的竞品数据中提取报告所需的归一化字段。"""
    st = rc.get("st")
    if not st:
        return {
            "name": rc.get("name", ""),
            "store_url": rc.get("store_url", ""),
            "error": rc.get("error", "SensorTower 未匹配到"),
            "has_st_data": False,
        }

    selected = st.get("selected", {})
    rev_humanized = selected.get("humanized_worldwide_last_month_revenue", {})
    dl_humanized = selected.get("humanized_worldwide_last_month_downloads", {})

    revenue_last = st.get("revenue_last_month_usd") or st.get("revenue_as_of_last_month_usd")
    downloads_last = (st.get("downloads_as_of_last_month") or {}).get("downloads_absolute")
    mau = (st.get("mau_as_of_last_month") or {}).get("mau_absolute")
    market_share = (st.get("market_share_as_of_last_month") or {}).get("share_percent")
    growth_6m = st.get("growth_vs_6m_percent")
    first_release = st.get("first_release_date_us", "")

    # App Store 评论（优先选负面/mixed 的）
    raw_comments = st.get("comments", [])
    negative_comments = [
        c for c in raw_comments
        if c.get("sentiment") in ("unhappy", "mixed") or (c.get("rating") or 5) <= 3
    ]
    if not negative_comments:
        negative_comments = raw_comments[:3]
    comments = [
        {
            "rating": c.get("rating"),
            "title": c.get("title", ""),
            "content": c.get("content", "")[:300],
            "sentiment": c.get("sentiment", ""),
            "tags": c.get("tags", []),
        }
        for c in negative_comments[:3]
    ]

    return {
        "name": rc.get("name") or selected.get("name", ""),
        "store_url": rc.get("store_url", ""),
        "has_st_data": True,
        "revenue_last_month": revenue_last,
        "revenue_display": _format_currency(revenue_last) if revenue_last else rev_humanized.get("string", "-"),
        "downloads_last_month": downloads_last,
        "downloads_display": _format_number(downloads_last) if downloads_last else dl_humanized.get("string", "-"),
        "mau": mau,
        "mau_display": _format_number(mau) if mau else "-",
        "market_share_percent": round(market_share, 2) if market_share else None,
        "market_share_display": f"{market_share:.1f}%" if market_share else "-",
        "growth_6m_percent": round(growth_6m, 1) if growth_6m is not None else None,
        "growth_6m_display": f"+{growth_6m:.1f}%" if growth_6m and growth_6m >= 0 else (f"{growth_6m:.1f}%" if growth_6m else "-"),
        "first_release": first_release,
        "release_year": first_release[:4] if first_release else "-",
        "ai_label": rc.get("ai_label", "-"),
        "segment": rc.get("segment", "-"),
        "strengths": rc.get("strengths", []),
        "weaknesses": rc.get("weaknesses", []),
        "comments": comments,
        "publisher": selected.get("publisher_name", ""),
        "icon_url": selected.get("icon_url", ""),
    }


def _format_currency(value: float | None) -> str:
    """将美元金额格式化为可读字符串。"""
    if value is None:
        return "-"
    if value >= 1_000_000:
        return f"${value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"${value / 1_000:.0f}K"
    return f"${value:.0f}"


def _format_number(value: float | None) -> str:
    """将数字格式化为可读字符串。"""
    if value is None:
        return "-"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.0f}K"
    return f"{value:.0f}"


def fetch_product_with_peers(
    product_name: str,
    category_queries: list[str],
    *,
    peer_count: int = 8,
) -> dict[str, Any] | None:
    """获取自家产品数据 + 收入规模相近的竞品列表。

    1. autocomplete 搜索自家产品，获取图标/收入/下载
    2. 用 category_queries 搜索该赛道所有产品
    3. 按收入与自家产品的差距排序，取最接近的 peer_count 个作为竞品
    """
    if not _ensure_st_cli_imports():
        return None
    client = _get_st_http_client()
    if not client:
        return None

    try:
        today = date.today()
        month_start = date(today.year, today.month, 1)
        prev_start = date(today.year - 1, 12, 1) if today.month == 1 else date(today.year, today.month - 1, 1)
        prev_end = month_start - timedelta(days=1)
        csrf = _st_api_mod.get_csrf_token_for_top_apps_page(client)

        # -- 1. 搜索自家产品 --
        my_results = _st_api_mod.autocomplete_search(client, product_name, limit=5)
        if not my_results:
            return None
        my_app = my_results[0]
        my_uid = str(my_app.get("id") or my_app.get("app_id") or "")
        my_info = {
            "name": my_app.get("name") or my_app.get("humanized_name") or product_name,
            "icon_url": my_app.get("icon_url") or "",
            "publisher": my_app.get("publisher_name") or "",
        }

        my_sub_ids = []
        for sub in my_app.get("ios_apps", []) + my_app.get("android_apps", []):
            sid = sub.get("id") or sub.get("app_id")
            if sid is not None:
                my_sub_ids.append(sid)

        my_revenue = 0.0
        my_downloads = 0
        my_dau = 0
        my_growth = None
        my_dl_growth = None
        if my_sub_ids:
            orig_regions = _st_api_mod.DEFAULT_FACET_REGIONS
            _st_api_mod.DEFAULT_FACET_REGIONS = ["US"]
            try:
                my_facets = _st_api_mod.apps_facets_v2_month_slice(
                    client, app_ids=my_sub_ids, month_start=prev_start, month_end=prev_end,
                    comparison_start=prev_start - timedelta(days=30), comparison_end=prev_start - timedelta(days=1),
                    csrf_token=csrf, limit=20,
                )
            finally:
                _st_api_mod.DEFAULT_FACET_REGIONS = orig_regions
            for row in my_facets:
                if row.get("appId") is not None:
                    continue
                rev_raw = row.get("revenueAbsolute")
                if rev_raw:
                    try: my_revenue = float(rev_raw) / 100.0
                    except: pass
                dl_raw = row.get("downloadsAbsolute")
                if dl_raw:
                    try: my_downloads = int(float(dl_raw))
                    except: pass
                dau_raw = row.get("activeUsersDAUAbsolute")
                if dau_raw:
                    try: my_dau = int(float(dau_raw))
                    except: pass
                g = row.get("revenueGrowthPercent")
                my_growth = None
                if g is not None and g != "":
                    try: my_growth = round(float(g) * 100, 1)
                    except: pass
                dl_g = row.get("downloadsGrowthPercent")
                my_dl_growth = None
                if dl_g is not None and dl_g != "":
                    try: my_dl_growth = round(float(dl_g) * 100, 1)
                    except: pass

        product_data = {
            **my_info,
            "revenue": round(my_revenue, 2),
            "revenue_display": _format_currency(my_revenue) if my_revenue else "-",
            "downloads": my_downloads,
            "downloads_display": _format_number(my_downloads) if my_downloads else "-",
            "dau": my_dau,
            "dau_display": _format_number(my_dau) if my_dau else "-",
            "growth_pct": my_growth,
            "downloads_growth_pct": my_dl_growth,
        }

        # -- 2. 搜索赛道内所有产品 --
        seen_uids: set[str] = set()
        if my_uid:
            seen_uids.add(my_uid)
        all_entries: list[dict] = []
        for q in category_queries:
            try:
                results = _st_api_mod.autocomplete_search(client, q, limit=10)
                for ent in results:
                    uid = str(ent.get("id") or ent.get("app_id") or "")
                    if not uid or uid in seen_uids:
                        continue
                    seen_uids.add(uid)
                    all_entries.append(ent)
            except Exception as e:
                print(f"[st_client] peer search '{q}' error: {e}")

        if not all_entries:
            return {"product": product_data, "peers": []}

        # 提取所有 sub_app_ids
        peer_sub_ids: list[int | str] = []
        for ent in all_entries:
            for sub in ent.get("ios_apps", []) + ent.get("android_apps", []):
                sid = sub.get("id") or sub.get("app_id")
                if sid is not None:
                    peer_sub_ids.append(sid)

        if not peer_sub_ids:
            return {"product": product_data, "peers": []}

        # -- 3. 获取所有候选竞品的 facets 数据 --
        orig_regions = _st_api_mod.DEFAULT_FACET_REGIONS
        _st_api_mod.DEFAULT_FACET_REGIONS = ["US"]
        try:
            peer_facets = _st_api_mod.apps_facets_v2_month_slice(
                client, app_ids=peer_sub_ids[:80], month_start=prev_start, month_end=prev_end,
                comparison_start=prev_start - timedelta(days=30), comparison_end=prev_start - timedelta(days=1),
                csrf_token=csrf, limit=len(peer_sub_ids) + 10,
            )
        finally:
            _st_api_mod.DEFAULT_FACET_REGIONS = orig_regions

        peer_apps: list[dict] = []
        for row in peer_facets:
            if row.get("appId") is not None:
                continue
            rev_raw = row.get("revenueAbsolute")
            rev = 0.0
            if rev_raw:
                try: rev = float(rev_raw) / 100.0
                except: pass
            dl_raw = row.get("downloadsAbsolute")
            dl = 0
            if dl_raw:
                try: dl = int(float(dl_raw))
                except: pass
            g = row.get("revenueGrowthPercent")
            g_pct = None
            if g is not None and g != "":
                try: g_pct = round(float(g) * 100, 1)
                except: pass
            dl_g = row.get("downloadsGrowthPercent")
            dl_g_pct = None
            if dl_g is not None and dl_g != "":
                try: dl_g_pct = round(float(dl_g) * 100, 1)
                except: pass
            dau_raw = row.get("activeUsersDAUAbsolute")
            dau = 0
            if dau_raw:
                try: dau = int(float(dau_raw))
                except: pass

            peer_apps.append({
                "name": "",
                "icon_url": "",
                "publisher": "",
                "_unified_id": row.get("unifiedAppId", ""),
                "revenue": round(rev, 2),
                "revenue_display": _format_currency(rev) if rev else "-",
                "downloads": dl,
                "downloads_display": _format_number(dl) if dl else "-",
                "growth_pct": g_pct,
                "downloads_growth_pct": dl_g_pct,
                "dau": dau,
                "dau_display": _format_number(dau) if dau else "-",
                "_rev_distance": abs(rev - my_revenue),
            })

        # -- 4. 把自家产品插入列表，按收入降序排，取周围各5个（总共10个含自己）--
        my_entry = {
            **product_data,
            "_unified_id": my_uid,
            "_is_ours": True,
            "_rev_distance": 0,
        }
        peer_apps.append(my_entry)
        peer_apps.sort(key=lambda x: x["revenue"], reverse=True)

        my_idx = next((i for i, a in enumerate(peer_apps) if a.get("_is_ours")), 0)
        half = (peer_count - 1) // 2
        start = max(0, my_idx - half)
        end = start + peer_count
        if end > len(peer_apps):
            end = len(peer_apps)
            start = max(0, end - peer_count)
        selected_peers = peer_apps[start:end]

        # 补充名称/图标
        name_fb: dict[str, dict] = {}
        for ent in all_entries:
            uid = str(ent.get("id") or ent.get("app_id") or "")
            n = ent.get("name") or ent.get("humanized_name") or ""
            ic = ent.get("icon_url") or ""
            pub = ent.get("publisher_name") or ""
            if uid and n:
                name_fb[uid] = {"name": n, "icon_url": ic, "publisher": pub}

        non_ours = [a for a in selected_peers if not a.get("_is_ours")]
        facet_uids = list({a["_unified_id"] for a in non_ours if a.get("_unified_id")})
        if facet_uids:
            try:
                entities = _st_api_mod.internal_entities(client, facet_uids[:30], csrf_token=csrf)
                uid_info: dict[str, dict] = {}
                for ent in entities:
                    eid = str(ent.get("id") or ent.get("app_id") or "")
                    uid_info[eid] = {
                        "name": ent.get("name") or ent.get("humanized_name") or "",
                        "publisher": ent.get("publisher_name") or "",
                        "icon_url": ent.get("icon_url") or "",
                    }
                for app in non_ours:
                    uid = app.get("_unified_id", "")
                    if uid and uid in uid_info:
                        info = uid_info[uid]
                        app["name"] = info["name"]
                        app["publisher"] = info["publisher"]
                        app["icon_url"] = info["icon_url"]
            except Exception as e:
                print(f"[st_client] peer entities error: {e}")

        for app in non_ours:
            if not app.get("name"):
                uid = app.get("_unified_id", "")
                if uid and uid in name_fb:
                    fb = name_fb[uid]
                    app["name"] = fb["name"]
                    if not app.get("icon_url"):
                        app["icon_url"] = fb["icon_url"]
                    if not app.get("publisher"):
                        app["publisher"] = fb["publisher"]

        # 计算全局排名（1-based）
        global_rank_offset = start
        final_peers = []
        for i, a in enumerate(selected_peers):
            a["rank"] = global_rank_offset + i + 1
            is_ours = bool(a.pop("_is_ours", False))
            a["is_ours"] = is_ours
            a.pop("_unified_id", None)
            a.pop("_rev_distance", None)
            final_peers.append(a)

        return {"product": product_data, "peers": final_peers}

    except Exception as e:
        print(f"[st_client] fetch_product_with_peers error: {e}")
        return None
    finally:
        try:
            client.close()
        except Exception:
            pass


def format_for_report(competitors: list[dict]) -> str:
    """将竞品数据格式化为注入 prompt 的文本。"""
    if not competitors:
        return "（SensorTower 竞品数据未获取）"

    lines = ["### SensorTower 竞品数据（真实数据，报告中必须使用这些数字）\n"]

    for i, c in enumerate(competitors, 1):
        lines.append(f"#### {i}. {c['name']}")

        if c.get("has_st_data"):
            lines.append(f"- 月收入: {c.get('revenue_display', '-')}")
            lines.append(f"- 月下载: {c.get('downloads_display', '-')}")
            lines.append(f"- 月活跃: {c.get('mau_display', '-')}")
            lines.append(f"- 市占率: {c.get('market_share_display', '-')}")
            lines.append(f"- 6M增长: {c.get('growth_6m_display', '-')}")
            lines.append(f"- 上线时间: {c.get('first_release', '-')}")
            lines.append(f"- AI: {c.get('ai_label', '-')}")
            lines.append(f"- 链接: {c.get('store_url', '-')}")

            strengths = c.get("strengths", [])
            weaknesses = c.get("weaknesses", [])
            if strengths:
                lines.append(f"- 核心优势: {'; '.join(strengths[:3])}")
            if weaknesses:
                lines.append(f"- 核心劣势: {'; '.join(weaknesses[:3])}")

            comments = c.get("comments", [])
            if comments:
                lines.append("- App Store 用户评论:")
                for cm in comments:
                    stars = f"{'★' * (cm.get('rating') or 0)}{'☆' * (5 - (cm.get('rating') or 0))}"
                    lines.append(f'  - {stars} "{cm["content"][:200]}"')
        else:
            lines.append(f"- SensorTower 未匹配: {c.get('error', '未知原因')}")
            lines.append(f"- 链接: {c.get('store_url', '-')}")

        lines.append("")

    lines.append("---")
    lines.append("⚠️ 以上数据来自 SensorTower，报告中竞品概览表的数字列必须直接使用这些数据，不要编造或修改。")

    return "\n".join(lines)
