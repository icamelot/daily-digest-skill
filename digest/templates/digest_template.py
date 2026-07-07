"""Digest message renderer — fixed template + conditional buttons."""


def render_inline_keyboard(data: dict) -> list[list[dict]] | None:
    """Generate Telegram inline_keyboard markup from data conditions.

    Returns list of button rows, each row is list of {text, callback_data} dicts.
    Returns None if no buttons needed.
    """
    keyboard = []

    # Email: "查看详情" if there are emails
    if data.get("emails", {}).get("total", 0) > 0:
        keyboard.append([{"text": "📬 查看邮件详情", "callback_data": "digest:mail:detail"}])

    # Todo: "完成: {title}" for each pending task (first 5)
    todos = data.get("todos", [])
    pending = [t for t in todos if not t.get("completed", False)]
    for i, task in enumerate(pending[:5]):
        title = task.get("title", "任务")[:20]
        keyboard.append([{"text": f"✅ {title}", "callback_data": f"digest:todo:{i}"}])

    # Chat escalation: "查看详情" if triggered
    if data.get("escalation", {}).get("should_escalate"):
        keyboard.append([{"text": "⚠️ 查看群聊详情", "callback_data": "digest:chat:detail"}])

    return keyboard if keyboard else None


def _format_todos(todos: list[dict]) -> str:
    """Format todo list into a human-readable summary for the agent prompt."""
    if not todos:
        return "无待办任务"

    pending = [t for t in todos if not t.get("completed", False)]
    completed = [t for t in todos if t.get("completed", False)]

    lines = [f"{len(todos)} 个任务 ({len(pending)} 未完成, {len(completed)} 已完成)"]
    for task in pending:
        priority = task.get("priority", "normal")
        icon = {"high": "❗", "medium": "🟡", "low": "  "}.get(priority, "·")
        due = task.get("due_date", "")
        due_str = f" — 截止: {due}" if due else ""
        lines.append(f"  {icon} {task.get('title', '')}{due_str}")
    return "\n".join(lines)


def render_digest(summary: dict, raw_data: dict) -> tuple[str, list | None]:
    """Render the final digest message from agent summary + raw data.

    Args:
        summary: Agent's JSON output with greeting, deepseek_line, escalation, sections
        raw_data: run_digest.py output for button conditions

    Returns:
        (message_text, inline_keyboard) — keyboard is Telegram inline_keyboard or None
    """
    lines = []

    # Escalation alert (if triggered)
    if summary.get("escalation", {}).get("triggered"):
        alert = summary["escalation"].get("alert", "")
        if alert:
            lines.append(alert)
            lines.append("")

    # Greeting
    lines.append(summary.get("greeting", ""))
    lines.append("")

    # Sections
    for sec in summary.get("sections", []):
        title = sec.get("title", "")
        lines.append(title)

        if sec.get("items"):
            # Fine-grained: numbered list with priority icons
            for i, item in enumerate(sec["items"]):
                importance = item.get("importance", "normal")
                icon = {"high": "❗", "normal": "·", "low": "·"}.get(importance, "·")
                lines.append(f"  {i+1}. {icon} {item['label']} — {item.get('detail', '')}")
        else:
            # Coarse-grained: use AI-generated text
            text = sec.get("text", "")
            if text:
                lines.append(text)

        lines.append("")

    # Token usage — from computed raw data
    ds = raw_data.get("deepseek", {}) or {}
    ds_display = ds.get("display", "")
    if ds_display:
        lines.append("📊 Token 用量")
        lines.append(f"  · {ds_display}")

    # Generate inline keyboard from data conditions (not from agent output)
    keyboard = render_inline_keyboard(raw_data)

    return "\n".join(lines), keyboard


def build_agent_prompt(data: dict) -> str:
    """Build the prompt for ask_agent.py main from raw digest data.

    Args:
        data: Output from run_digest.py

    Returns:
        Prompt string for the agent
    """
    digest_type = data.get("digest_type", "morning")
    type_label = "早报" if digest_type == "morning" else "晚报"
    time_label = "早上" if digest_type == "morning" else "晚上"

    # Build a compact data summary for the agent
    emails = data.get("emails", {})
    email_important = emails.get("classification", {}).get("important", 0)
    email_total = emails.get("total", 0)
    email_details = []
    for item in emails.get("details", {}).get("important", [])[:5]:
        email_details.append(f"  - {item.get('sender', '?')}: {item.get('subject', '')[:60]}")
    for item in emails.get("details", {}).get("normal", [])[:5]:
        email_details.append(f"  - {item.get('sender', '?')}: {item.get('subject', '')[:60]}")

    chat_data = data.get("group_messages", {})
    chat_total = chat_data.get("total", 0)
    chat_senders = set()
    for msg in chat_data.get("messages", []):
        chat_senders.add(msg.get("sender_name", ""))
    chat_senders_str = ", ".join(sorted(chat_senders)[:10]) if chat_senders else "无"

    escalation = data.get("escalation", {})

    raw_todos = data.get("todos", [])
    todo_pending = len([t for t in raw_todos if not t.get("completed", False)])
    todo_total = len(raw_todos)
    todos = _format_todos(raw_todos)

    prompt = f"""你是日报摘要生成器。根据以下数据生成一份{type_label} JSON。只输出JSON，不要其他文字。

【时间】{time_label}，{data.get('timestamp', '')}
【类型】{digest_type}

【邮件】共{email_total}封，{email_important}封重要
{chr(10).join(email_details) if email_details else '无新邮件'}

【群聊】{chat_total}条消息，发言者: {chat_senders_str}

【Escalation】{"触发" if escalation.get('should_escalate') else "未触发"}
{"原因: " + ", ".join(escalation.get('reasons', [])) if escalation.get('should_escalate') else ""}

【待办】
{todos}

输出格式（严格遵守）:
{{
  "greeting": "{time_label}好！……（含日期和星期）",
  "escalation": {{"triggered": true/false, "alert": "⚠️ 警告文字（triggered=true时必填）"}},
  "sections": [
    {{
      "type": "email",
      "title": "📬 邮件（{email_total}封，{email_important}封重要）",
      "text": "AI生成的邮件总结markdown",
      "items": [{{"label": "发件人", "detail": "描述", "importance": "high/normal/low"}}]
    }},
    {{
      "type": "chat",
      "title": "💬 群聊（{chat_total}条消息）",
      "text": "AI生成的群聊总结",
      "items": []
    }},
    {{
      "type": "todo",
      "title": "✅ 待办（{todo_pending}项未完成 / {todo_total}项总计）",
      "text": "AI生成的待办总结",
      "items": [{{"label": "任务名", "detail": "优先级/截止日", "importance": "high/normal/low"}}]
    }}
  ]
}}

规则:
- 邮件和todo section用items模式（结构化的label/detail），不用text
- 群聊section用text模式（自然语言总结）
- items中: label是简短名称, detail是补充描述
- 不要把常规通知（IEEE简报、GitHub actions）标为high importance
- 不包含buttons字段（按钮由代码自动生成）
- 不含deepseek_line字段（Token用量由代码自动添加）
- greeting包含日期和星期几"""

    return prompt
