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

## 写信与回复邮件

用户要求写信或回复时，不自行发送，按以下流程执行：

1. 提取用户意图；回复时同时读取原邮件上下文，并默认使用收到该邮件的账户作为 `from_account_label`。
2. 如有附件，先调用 `scripts/smtp_send.py` 的 `prepare_attachments(attachments, max_attachment_bytes=...)` 做草稿阶段校验，不连接 SMTP。默认总上限为 20 MiB（`20 * 1024 * 1024` 原始字节），只有用户指定时才覆盖。
3. 展示完整草稿：发件账户、收件人、主题和正文。有附件时逐项展示附件名、单个原始大小，并展示附件数量和附件总原始大小；大小标为 KiB/MiB（二进制单位）。不得展示本地目录或附件内容。
4. 请求覆盖邮件正文和所列附件的明确肯定确认。沉默、歧义回复或旧草稿确认均不算授权。收件人、主题、正文、附件列表、路径、附件名或大小任一变化，都必须重新校验、重新展示草稿并再次确认。
5. 只有当前草稿获明确确认后，才调用 `send_email(config, to, subject, body, from_account_label=..., in_reply_to=..., references=..., attachments=..., max_attachment_bytes=...)`。`send_email` 会在联网前重新校验全部附件。
6. 取消时不得调用 send_email；取消或发送失败不得删除任何附件。
7. `send_email` 返回 `True` 才报告 SMTP 已接受邮件。若同时输出“Email sent; attachment cleanup failed”，必须明确报告“邮件已发送、附件清理失败”及未清理路径，绝不重发；非 `telegram_files` 文件不会自动删除。
8. 发送成功后检查 todo 模块是否有相关任务，有则建议标记完成。

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
