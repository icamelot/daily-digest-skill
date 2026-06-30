# Todo 模块

通过 Microsoft Graph API 管理用户的 Microsoft To Do 任务。

## 查任务

用户说"待办/任务/还有什么没做"时：

1. 调用 `scripts/graph_api.py` 的 `get_tasks(config)` 拉取任务列表
2. 按优先级 + 截止时间排列
3. 展示：

✅ 当前任务 (已完成数/总数)

⬜ 任务名 (❗高优先级) 📅明日截止
⬜ 任务名 📅下周五
✅ 任务名 ✓ 已完成

## 创建任务

用户说"加个任务/提醒我..."时：

1. 解析任务名、可选截止时间、可选优先级
2. 展示确认卡片
3. 用户确认后调用 `create_task(config, title, due_date, priority)`

## 完成/修改任务

- "标记 xx 完成" → 调用 `update_task(config, task_id, {"status": "completed"})`
- "改 xx 截止时间为 yy" → 调用 `update_task(config, task_id, {"dueDateTime": ...})`
- "删除 xx" → 先确认再调用 `delete_task(config, task_id)`

## 清理旧任务

用户说"清理已完成任务/删除超过一周的任务"时：

1. 先 dry-run 预览：`python3 todo/scripts/cleanup_old_completed.py --days 7 --dry-run`
2. 告知用户将要删除的数量，获得确认
3. 执行删除：`python3 todo/scripts/cleanup_old_completed.py --days 7`
4. 报告结果（删除数 + 失败数）

`--days N` 控制天数阈值，默认 7 天。

## 主动感知

当你在对话中检测到用户可能完成了某个任务时，主动询问：

"检测到你似乎完成了 'xxx'，要标记为完成吗？ [标记完成]"

触发信号：
- 邮件模块中发送草稿成功 + 存在关联任务
- 用户说"做完了/搞定了/提交了/发了"
- 群聊日报中识别到任务相关结论

## 输出格式

不手动换行，让平台自适应。
