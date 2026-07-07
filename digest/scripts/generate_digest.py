#!/usr/bin/env python3
"""Digest daemon — collect data, call agent for summary, render, send. Zero-LLM outer loop.

Replaces personal-morning-digest and personal-evening-digest cron jobs.
"""
import json
import math
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(SKILL_DIR / "digest" / "scripts"))
sys.path.insert(0, str(SKILL_DIR / "digest" / "templates"))

from digest_template import render_digest, build_agent_prompt

PID_FILE = SKILL_DIR / ".digest_daemon.pid"
HEARTBEAT_FILE = SKILL_DIR / ".digest_daemon.heartbeat"
RUN_DIGEST_SCRIPT = str(SKILL_DIR / "digest" / "scripts" / "run_digest.py")
ASK_AGENT_SCRIPT = "/ductor/workspace/tools/agent_tools/ask_agent.py"

CHECK_INTERVAL = 30     # 30s check granularity
RETRY_GAP = 300         # 5min between retries
MAX_RETRIES = 3         # max attempts per digest
AGENT_TIMEOUT = 270     # 4.5min — leaves buffer before next 5min check

_shutdown_requested = False


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _log(msg: str) -> None:
    print(f"[digest_daemon] {_ts()} {msg}", file=sys.stderr, flush=True)


def _update_heartbeat() -> None:
    try:
        HEARTBEAT_FILE.write_text(_ts())
    except Exception:
        pass


def _acquire_lock() -> bool:
    our_pid = os.getpid()
    if PID_FILE.exists():
        try:
            stale = int(PID_FILE.read_text().strip())
            try:
                with open(f"/proc/{stale}/status") as f:
                    for line in f:
                        if line.startswith("State:"):
                            state = line.split()[1]
                            if state not in ("Z", "X"):
                                _log(f"Another instance running (PID {stale}, state={state}). Exiting.")
                                return False
                            break
            except FileNotFoundError:
                pass
        except (ValueError, OSError):
            pass
    PID_FILE.write_text(str(our_pid))
    return True


def _release_lock() -> None:
    try:
        PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def _signal_handler(signum, frame):
    global _shutdown_requested
    sig_name = signal.Signals(signum).name
    _log(f"Received {sig_name} — shutting down gracefully")
    _shutdown_requested = True


def _load_env() -> None:
    env_file = os.path.expanduser("~/.ductor/.env")
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    k, v = k.strip(), v.strip().strip('"').strip("'")
                    if k and not k.startswith("export"):
                        os.environ.setdefault(k, v)


def _run_digest(digest_type: str) -> dict | None:
    """Run data collection. Returns parsed JSON or None on failure."""
    try:
        result = subprocess.run(
            [sys.executable, RUN_DIGEST_SCRIPT, "--type", digest_type],
            capture_output=True, text=True, timeout=60,
            env={**os.environ, "DUCTOR_AGENT_NAME": os.environ.get("DUCTOR_AGENT_NAME", "main")},
        )
        if result.returncode != 0:
            _log(f"run_digest.py failed (rc={result.returncode}): {result.stderr[:200]}")
            return None
        return json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        _log("run_digest.py timeout after 60s")
        return None
    except json.JSONDecodeError as e:
        _log(f"run_digest.py invalid JSON: {e}")
        return None
    except Exception as e:
        _log(f"run_digest.py error: {e}")
        return None


def _ask_agent(prompt: str) -> dict | None:
    """Call ask_agent.py main to generate summary JSON. Returns parsed dict or None."""
    try:
        result = subprocess.run(
            [sys.executable, ASK_AGENT_SCRIPT, "main", prompt],
            capture_output=True, text=True, timeout=AGENT_TIMEOUT,
            env={**os.environ, "DUCTOR_AGENT_NAME": "digest_daemon"},
        )
        if result.returncode != 0:
            _log(f"ask_agent.py failed (rc={result.returncode}): {result.stderr[:200]}")
            return None

        response = result.stdout.strip()

        # Try to extract JSON from agent response (may have surrounding text)
        # Look for the outermost { ... }
        start = response.find("{")
        end = response.rfind("}")
        if start == -1 or end == -1:
            _log(f"ask_agent.py response contains no JSON: {response[:200]}")
            return None

        json_str = response[start:end + 1]
        return json.loads(json_str)
    except subprocess.TimeoutExpired:
        _log(f"ask_agent.py timeout after {AGENT_TIMEOUT}s")
        return None
    except json.JSONDecodeError as e:
        _log(f"ask_agent.py invalid JSON: {e}")
        return None
    except Exception as e:
        _log(f"ask_agent.py error: {e}")
        return None


def _send_telegram(message: str, reply_markup: dict | None = None) -> bool:
    """Send digest message to Telegram. Returns True on success."""
    tg_token = os.environ.get("DUCTOR_TG_TOKEN", "")
    tg_chat_id = os.environ.get("DUCTOR_TG_CHAT_ID", "")
    if not tg_token or not tg_chat_id:
        _log("Missing TG credentials (DUCTOR_TG_TOKEN or DUCTOR_TG_CHAT_ID)")
        return False

    url = f"https://api.telegram.org/bot{tg_token}/sendMessage"
    payload = {"chat_id": tg_chat_id, "text": message}
    if reply_markup:
        payload["reply_markup"] = json.dumps({"inline_keyboard": reply_markup})
    data = json.dumps(payload).encode()

    try:
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read())
            if body.get("ok", False):
                msg_id = body.get("result", {}).get("message_id", "?")
                _log(f"TG send OK → message_id={msg_id}")
                return True
            else:
                _log(f"TG API error: {body.get('description', 'unknown')}")
                return False
    except Exception as e:
        _log(f"TG send failed: {e}")
        return False


def _wait_until(target_hour: int, target_minute: int) -> None:
    """Sleep until the target hour:minute. Returns immediately if already past."""
    now = datetime.now()
    target = now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
    if target <= now:
        return  # already past, don't wait
    wait_sec = (target - now).total_seconds()
    _log(f"Waiting {wait_sec:.0f}s until {target_hour:02d}:{target_minute:02d}")
    time.sleep(wait_sec)


def _in_window(now: datetime, schedule_hour: int) -> bool:
    """Check if we're in the execution window for a digest.
    Window: schedule_hour-5 to schedule_hour+5 minutes.
    E.g. for 8:00 schedule: 7:55-8:05."""
    target_minutes = schedule_hour * 60
    now_minutes = now.hour * 60 + now.minute
    window_start = target_minutes - 5   # e.g. 475 for 8:00 (7:55)
    window_end = target_minutes + 5     # e.g. 485 for 8:00 (8:05)
    return window_start <= now_minutes <= window_end


def main():
    global _shutdown_requested

    # Only run in main agent container, not sub-agents
    agent_name = os.environ.get("DUCTOR_AGENT_NAME", "")
    if agent_name and agent_name != "main":
        _log(f"Skipping — not the main agent (current: {agent_name})")
        return

    _load_env()
    socket.setdefaulttimeout(15)

    if not _acquire_lock():
        sys.exit(1)

    _log(f"Started (check_interval={CHECK_INTERVAL}s, retry_gap={RETRY_GAP}s, max_retries={MAX_RETRIES})")

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    # State per digest type
    state = {
        "morning": {"done_today": False, "attempts": 0, "last_attempt": None},
        "evening": {"done_today": False, "attempts": 0, "last_attempt": None},
    }
    last_day = datetime.now().day

    while not _shutdown_requested:
        try:
            _update_heartbeat()

            now = datetime.now()

            # Reset daily state at midnight
            if now.day != last_day:
                _log("New day — resetting state")
                state["morning"] = {"done_today": False, "attempts": 0, "last_attempt": None}
                state["evening"] = {"done_today": False, "attempts": 0, "last_attempt": None}
                last_day = now.day

            # Check each digest type
            for digest_type, schedule_hour in [("morning", 8), ("evening", 22)]:
                s = state[digest_type]

                if not _in_window(now, schedule_hour) or s["done_today"]:
                    continue

                # Check retry gap
                if s["last_attempt"] is not None:
                    gap = (now - s["last_attempt"]).total_seconds()
                    if gap < RETRY_GAP:
                        continue

                s["last_attempt"] = now
                s["attempts"] += 1
                _log(f"{digest_type} digest attempt {s['attempts']}/{MAX_RETRIES}")

                try:
                    # Step 1: Collect data
                    data = _run_digest(digest_type)
                    if not data:
                        raise RuntimeError("Data collection failed")

                    # Step 2: Build prompt and ask agent
                    prompt = build_agent_prompt(data)
                    summary = _ask_agent(prompt)
                    if not summary:
                        raise RuntimeError("Agent summary generation failed")

                    # Step 3: Render message + keyboard
                    message, keyboard = render_digest(summary, data)

                    # Step 4: Wait for punctual delivery
                    wait_minute = 0  # 8:00 or 22:00
                    _wait_until(schedule_hour, wait_minute)

                    # Step 5: Send
                    if _send_telegram(message, reply_markup=keyboard):
                        _log(f"{digest_type} digest sent successfully")
                        s["done_today"] = True
                    else:
                        raise RuntimeError("Telegram send failed")

                except Exception as e:
                    _log(f"{digest_type} digest attempt {s['attempts']} failed: {e}")
                    if s["attempts"] >= MAX_RETRIES:
                        # All retries exhausted — send error notification
                        type_label = "早报" if digest_type == "morning" else "晚报"
                        error_msg = f"⚠️ {type_label}生成失败，已重试{MAX_RETRIES}次"
                        _send_telegram(error_msg)
                        _log(f"{digest_type} digest failed after {MAX_RETRIES} attempts — error sent")
                        s["done_today"] = True  # mark done to stop retrying

        except Exception as e:
            _log(f"Cycle error: {e}")
            import traceback
            traceback.print_exc(file=sys.stderr)

        if _shutdown_requested:
            break

        # Clock-aligned sleep
        now = time.time()
        sleep_sec = max(1, math.ceil(now / CHECK_INTERVAL) * CHECK_INTERVAL - now)
        time.sleep(sleep_sec)

    _log("Daemon stopped")
    _release_lock()


if __name__ == "__main__":
    # Outer self-healing loop
    RESTART_DELAY = 5
    while True:
        _shutdown_requested = False
        try:
            main()
        except BaseException as e:
            print(f"[digest_daemon] {_ts()} FATAL: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
            import traceback
            traceback.print_exc(file=sys.stderr)
            _release_lock()
        print(f"[digest_daemon] {_ts()} Restarting in {RESTART_DELAY}s...", file=sys.stderr, flush=True)
        time.sleep(RESTART_DELAY)
