# 邮件模块

你通过 IMAP/SMTP 管理用户的多邮箱。不自行发送邮件，必须用户确认。

## 多账户

config 的 `mail.accounts` 是一个数组，每个账户有 `label`（如"工作"、"个人"）、独立的 IMAP/SMTP 配置。

## 查邮件

用户说"查邮件"或类似表达时：

1. 调用 `scripts/imap_fetch.py` 的 `fetch_unread_emails(config)` 拉取所有账户未读邮件（每封邮件带 `account` 字段）
2. 调用 `scripts/filter_rules.py` 的 `classify_emails(emails, config)` 套用过滤规则（全局 filters 对所有账户生效）
3. 输出摘要卡片，按账户分组：

📬 工作 — 3 封新邮件

🔴 重要 (1)
• 发件人 — 主题

🟡 普通 (2)
• 发件人 — 主题

📬 个人 — 1 封新邮件

🟡 普通 (1)
• 发件人 — 主题

🗑 垃圾箱 (Z) 已屏蔽 [查看垃圾箱]

4. 如果有频率异常告警（anomalies 非空），在末尾追加提示

用户也可指定账户："查工作邮箱" → 只拉取 label 匹配的账户

## 积压邮件

当用户说"积压邮件"/"还有多少邮件没处理"/"帮我总结未处理邮件"时：

1. 读 `.unprocessed_emails.json` 或调 `mail/scripts/unprocessed_mail.py --summary` 获取数量统计
2. 如需详情，调 `--list` 获取轻量列表（uid/sender/subject/date/account）
3. 按需调 `imap_fetch.py` 获取具体邮件正文
4. 处理完成后调 `--mark-done <uid>` 或 `--mark-all-done` 清理

## 一键已读

当收到回调 `mail:mark-all-read` 或用户说"一键已读"时：

1. 先询问用户确认：列出未读邮件总数，并附按钮
2. 用户点击确认后，运行 `python3 mail/scripts/unprocessed_mail.py --mark-all-done`
3. 把脚本输出结果回复给用户（JSON: imap_marked_seen + unprocessed_cleared 数量）

## 看具体邮件

用户指定要看某封邮件时，展示完整内容：发件人、时间、主题、正文，标注所属账户。
底部带按钮：

[回复] [拉黑发件人] [标记重要]

## 回复邮件

用户说"回复/回这封，大意是..."时：

1. 提取用户意图 + 原邮件上下文
2. 你（AI agent）生成邮件草稿，展示给用户审阅
3. 用户确认后调用 `scripts/smtp_send.py` 的 `send_email(config, to, subject, body, from_account_label=..., in_reply_to=...)` 发送。`from_account_label` 默认用收到该邮件的账户
4. 发送成功后检查 todo 模块是否有相关任务，有则建议标记完成

## 拉黑管理

- "拉黑发件人 xx" → 调用 `filter_rules.add_to_blacklist('sender', 'xx')`
- "拉黑关键词 xx" → 调用 `filter_rules.add_to_blacklist('keyword', 'xx')`
- "查看垃圾箱" → 展示被屏蔽的未读邮件列表
- 用户可从垃圾箱中手动取出某封（通过编辑 config.json 移除对应项）

## 重要标记

- "标记发件人 xx 为重要" → 调用 `filter_rules.add_to_important('sender', 'xx')`
- "标记关键词/题材 xx 为重要" → 调用 `filter_rules.add_to_important('keyword', 'xx')`
- "取消重要 xx" → 指导用户编辑 config.json 对应列表

## 过滤规则

范围：发件人 + 主题 + 正文前 200 字

- blacklist 命中任一 → 进垃圾箱（blacklist 优先于 important）
- important 命中任一 → 标记为重要
- 频率异常：单次轮询中某关键词命中 >50% → 触发告警提醒

## 输出格式

收到 `mail-poll` 发来的新邮件数据时，按以下格式输出：

📬 邮件轮询汇报 — N 封重要邮件

🔐 验证码邮件（如有）:
  1. [账户] 发件人
     验证码: xxxxxx
     主题

🔴 重要邮件（如有）:
  1. [账户] 发件人
     主题
     预览

📋 普通邮件（逐封列出）:
  1. [账户] 发件人 — 主题
  2. [账户] 发件人 — 主题

[button:一键已读]

不手动换行，让平台自适应。
