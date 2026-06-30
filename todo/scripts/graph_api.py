"""Microsoft Graph API client for Microsoft To Do tasks."""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlencode

TOKEN_FILE = Path(__file__).resolve().parent.parent / ".ms_token.json"

# Use 'common' so both org accounts (北大) and personal Microsoft accounts work
AUTH_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
DEVICE_CODE_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/devicecode"
GRAPH_BASE = "https://graph.microsoft.com/v1.0"
SCOPE = "Tasks.ReadWrite User.Read offline_access"


# ── helpers ────────────────────────────────────────────────────────

def _post_form(url: str, data: dict, *, timeout: int = 30) -> dict:
    """POST x-www-form-urlencoded, return parsed JSON."""
    payload = urlencode(data).encode()
    req = urllib.request.Request(url, data=payload)
    resp = urllib.request.urlopen(req, timeout=timeout)
    return json.loads(resp.read())


def _json_request(method: str, url: str, headers: dict,
                  body: dict | None = None, timeout: int = 30) -> tuple[int, dict | None]:
    """Make a JSON request. Returns (status_code, parsed_body)."""
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        raw = resp.read()
        return resp.getcode(), json.loads(raw) if raw.strip() else None
    except urllib.error.HTTPError as e:
        raw = e.read()
        return e.code, json.loads(raw) if raw.strip() else {}


def _resolve_ms_cfg(config: dict) -> dict:
    """Resolve microsoft_graph config with env var fallbacks."""
    ms_cfg = dict(config.get("todo", {}).get("microsoft_graph", {}))
    for key in ("client_id", "client_secret", "tenant_id"):
        env_key = f"MS_{key.upper()}"
        if not ms_cfg.get(key) and os.environ.get(env_key):
            ms_cfg[key] = os.environ[env_key]
    return ms_cfg


# ── auth ────────────────────────────────────────────────────────────

def _get_token(config: dict) -> str:
    """Get a valid access token, refreshing or starting device code flow."""
    # 1. Cached valid token
    if TOKEN_FILE.exists():
        cached = json.loads(TOKEN_FILE.read_text())
        access_token = cached.get("access_token")
        expires_at = cached.get("expires_at")
        if access_token and expires_at and time.time() < expires_at - 60:
            return access_token

        # 2. Refresh token
        refresh_token = cached.get("refresh_token")
        if refresh_token:
            ms_cfg = _resolve_ms_cfg(config)
            token_url = AUTH_URL
            try:
                data = _post_form(token_url, {
                    "client_id": ms_cfg["client_id"],
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                })
                data["expires_at"] = time.time() + data["expires_in"]
                if "refresh_token" not in data:
                    data["refresh_token"] = refresh_token
                TOKEN_FILE.write_text(json.dumps(data))
                return data["access_token"]
            except Exception:
                pass

    # 3. Device code flow
    ms_cfg = _resolve_ms_cfg(config)
    device_url = DEVICE_CODE_URL
    dc_data = _post_form(device_url, {
        "client_id": ms_cfg["client_id"],
        "scope": SCOPE,
    })

    print(f"\n🔐 请登录 Microsoft 账号：", file=sys.stderr)
    print(f"   {dc_data['verification_uri']}", file=sys.stderr)
    print(f"   验证码: {dc_data['user_code']}", file=sys.stderr)
    print(f"   （{dc_data['expires_in']} 秒内有效）\n", file=sys.stderr)

    token_url = AUTH_URL
    interval = dc_data.get("interval", 5)
    deadline = time.time() + dc_data["expires_in"]
    while time.time() < deadline:
        time.sleep(interval)
        poll_data = _post_form(token_url, {
            "client_id": ms_cfg["client_id"],
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": dc_data["device_code"],
        })
        if "access_token" in poll_data:
            poll_data["expires_at"] = time.time() + poll_data["expires_in"]
            TOKEN_FILE.write_text(json.dumps(poll_data))
            return poll_data["access_token"]
        if poll_data.get("error") != "authorization_pending":
            raise RuntimeError(
                f"Device code auth failed: {poll_data.get('error_description', poll_data)}"
            )

    raise TimeoutError("设备代码登录超时，请重新运行")


def _headers(config: dict) -> dict:
    return {
        "Authorization": f"Bearer {_get_token(config)}",
        "Content-Type": "application/json",
    }


# ── API URLs ────────────────────────────────────────────────────────

def _lists_url() -> str:
    return f"{GRAPH_BASE}/me/todo/lists"


def _tasks_url(list_id: str) -> str:
    return f"{GRAPH_BASE}/me/todo/lists/{list_id}/tasks"


# ── public API ──────────────────────────────────────────────────────

def get_tasks(config: dict) -> list[dict]:
    """Fetch all tasks from all To Do lists."""
    try:
        hdrs = _headers(config)
        code, lists_data = _json_request("GET", _lists_url(), hdrs)
        if code != 200:
            print(f"Graph API error (get_tasks/lists): {code}", file=sys.stderr)
            return []

        all_tasks = []
        for task_list in lists_data.get("value", []):
            list_id = task_list["id"]
            code, tasks_data = _json_request("GET", _tasks_url(list_id), hdrs)
            if code != 200:
                continue
            for task in tasks_data.get("value", []):
                completed_raw = task.get("completedDateTime", {}) or {}
                all_tasks.append({
                    "id": task["id"],
                    "title": task.get("title", ""),
                    "status": task.get("status", "notStarted"),
                    "priority": _priority_label(task.get("importance", "")),
                    "due_date": task.get("dueDateTime", {}).get("dateTime", ""),
                    "completed_date": completed_raw.get("dateTime", ""),
                    "list_name": task_list.get("displayName", "Tasks"),
                })
        return all_tasks
    except Exception as e:
        print(f"Graph API error (get_tasks): {e}", file=sys.stderr)
        return []


def _priority_label(importance: str) -> str:
    mapping = {"high": "❗高", "normal": "🟡中", "low": "🟢低"}
    return mapping.get(importance, "🟡中")


def create_task(
    config: dict,
    title: str,
    due_date: str | None = None,
    priority: str | None = None,
) -> dict | None:
    """Create a task in the default task list."""
    try:
        hdrs = _headers(config)
        code, lists_data = _json_request("GET", _lists_url(), hdrs)
        if code != 200:
            return None
        lists = lists_data.get("value", [])
        if not lists:
            return None
        default_list_id = lists[0]["id"]

        task_data: dict = {"title": title}
        if due_date:
            task_data["dueDateTime"] = {
                "dateTime": due_date,
                "timeZone": "Asia/Shanghai",
            }
        if priority:
            importance = {"high": "high", "normal": "normal", "low": "low"}.get(
                priority, "normal"
            )
            task_data["importance"] = importance

        code, result = _json_request("POST", _tasks_url(default_list_id), hdrs, task_data)
        if code in (200, 201):
            return result
        return None
    except Exception as e:
        print(f"Graph API error (create_task): {e}", file=sys.stderr)
        return None


def update_task(config: dict, task_id: str, updates: dict) -> bool:
    """Update a task. updates: title, status, dueDateTime, importance."""
    try:
        hdrs = _headers(config)
        code, lists_data = _json_request("GET", _lists_url(), hdrs)
        if code != 200:
            return False
        for task_list in lists_data.get("value", []):
            url = f"{_tasks_url(task_list['id'])}/{task_id}"
            code, _ = _json_request("PATCH", url, hdrs, updates)
            if code == 200:
                return True
        return False
    except Exception as e:
        print(f"Graph API error (update_task): {e}", file=sys.stderr)
        return False


def delete_task(config: dict, task_id: str) -> bool:
    """Delete a task by ID."""
    try:
        hdrs = _headers(config)
        code, lists_data = _json_request("GET", _lists_url(), hdrs)
        if code != 200:
            return False
        for task_list in lists_data.get("value", []):
            url = f"{_tasks_url(task_list['id'])}/{task_id}"
            code, _ = _json_request("DELETE", url, hdrs)
            if code == 204:
                return True
        return False
    except Exception as e:
        print(f"Graph API error (delete_task): {e}", file=sys.stderr)
        return False
