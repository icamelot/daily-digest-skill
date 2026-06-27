"""Microsoft Graph API client for Microsoft To Do tasks."""
import json
import sys
import time
from pathlib import Path

import requests

TOKEN_FILE = Path(__file__).resolve().parent.parent / ".ms_token.json"

AUTH_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
GRAPH_BASE = "https://graph.microsoft.com/v1.0"
TODO_LISTS_URL = f"{GRAPH_BASE}/me/todo/lists"
TASKS_URL = f"{GRAPH_BASE}/me/todo/lists/{{list_id}}/tasks"


def _get_token(config: dict) -> str:
    """Get a valid access token, refreshing if needed."""
    if TOKEN_FILE.exists():
        cached = json.loads(TOKEN_FILE.read_text())
        access_token = cached.get("access_token")
        expires_at = cached.get("expires_at")
        if access_token and expires_at:
            # Reuse if still valid with a 60-second safety buffer
            if time.time() < expires_at - 60:
                return access_token

    ms_cfg = config["todo"]["microsoft_graph"]
    token_url = AUTH_URL.format(tenant=ms_cfg["tenant_id"])
    payload = {
        "client_id": ms_cfg["client_id"],
        "client_secret": ms_cfg["client_secret"],
        "scope": "https://graph.microsoft.com/.default",
        "grant_type": "client_credentials",
    }
    resp = requests.post(token_url, data=payload, timeout=30)
    resp.raise_for_status()
    token_data = resp.json()
    token_data["expires_at"] = time.time() + token_data["expires_in"]
    TOKEN_FILE.write_text(json.dumps(token_data))
    return token_data["access_token"]


def _headers(config: dict) -> dict:
    return {
        "Authorization": f"Bearer {_get_token(config)}",
        "Content-Type": "application/json",
    }


def get_tasks(config: dict) -> list[dict]:
    """Fetch all tasks from all To Do lists."""
    try:
        lists_resp = requests.get(
            TODO_LISTS_URL, headers=_headers(config), timeout=30
        )
        lists_resp.raise_for_status()
        lists_data = lists_resp.json()

        all_tasks = []
        for task_list in lists_data.get("value", []):
            list_id = task_list["id"]
            tasks_url = TASKS_URL.format(list_id=list_id)
            tasks_resp = requests.get(
                tasks_url, headers=_headers(config), timeout=30
            )
            tasks_resp.raise_for_status()
            tasks_data = tasks_resp.json()
            for task in tasks_data.get("value", []):
                all_tasks.append({
                    "id": task["id"],
                    "title": task.get("title", ""),
                    "status": task.get("status", "notStarted"),
                    "priority": _priority_label(task.get("importance", "")),
                    "due_date": task.get("dueDateTime", {}).get("dateTime", ""),
                    "list_name": task_list.get("displayName", "Tasks"),
                })

        return all_tasks
    except requests.RequestException as e:
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
    """
    Create a task in the default task list.
    priority: 'high', 'normal', 'low'
    due_date: ISO 8601 datetime string
    """
    try:
        lists_resp = requests.get(
            TODO_LISTS_URL, headers=_headers(config), timeout=30
        )
        lists_resp.raise_for_status()
        lists = lists_resp.json().get("value", [])
        if not lists:
            return None
        default_list_id = lists[0]["id"]

        task_data = {"title": title}
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

        tasks_url = TASKS_URL.format(list_id=default_list_id)
        resp = requests.post(
            tasks_url, headers=_headers(config), json=task_data, timeout=30
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        print(f"Graph API error (create_task): {e}", file=sys.stderr)
        return None


def update_task(config: dict, task_id: str, updates: dict) -> bool:
    """
    Update a task. updates can include: title, status, dueDateTime, importance.
    status: 'notStarted', 'inProgress', 'completed', 'waitingOnOthers', 'deferred'
    """
    try:
        lists_resp = requests.get(
            TODO_LISTS_URL, headers=_headers(config), timeout=30
        )
        lists_resp.raise_for_status()
        for task_list in lists_resp.json().get("value", []):
            task_url = (
                f"{TASKS_URL.format(list_id=task_list['id'])}/{task_id}"
            )
            resp = requests.patch(
                task_url, headers=_headers(config), json=updates, timeout=30
            )
            if resp.status_code == 200:
                return True
        return False
    except requests.RequestException as e:
        print(f"Graph API error (update_task): {e}", file=sys.stderr)
        return False


def delete_task(config: dict, task_id: str) -> bool:
    """Delete a task by ID."""
    try:
        lists_resp = requests.get(
            TODO_LISTS_URL, headers=_headers(config), timeout=30
        )
        lists_resp.raise_for_status()
        for task_list in lists_resp.json().get("value", []):
            task_url = (
                f"{TASKS_URL.format(list_id=task_list['id'])}/{task_id}"
            )
            resp = requests.delete(
                task_url, headers=_headers(config), timeout=30
            )
            if resp.status_code == 204:
                return True
        return False
    except requests.RequestException as e:
        print(f"Graph API error (delete_task): {e}", file=sys.stderr)
        return False
