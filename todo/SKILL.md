# Todo 模块

通过 Microsoft Graph API 管理用户的 Microsoft To Do 任务。

所有操作通过 CLI 完成，任何目录可运行：

```bash
python3 /ductor/workspace/skills/personal-assistant/todo/scripts/todo_cli.py <command>
```

## 查任务

用户说"待办/任务/还有什么没做"时：

```bash
python3 /ductor/workspace/skills/personal-assistant/todo/scripts/todo_cli.py --list
```

可选过滤：
- `--status notStarted` — 仅未开始
- `--status completed` — 仅已完成

输出：JSON 数组。按优先级 + 截止时间排列后展示：

✅ 当前任务 (已完成数/总数)

⬜ 任务名 (❗高优先级) 📅明日截止
⬜ 任务名 📅下周五
✅ 任务名 ✓ 已完成

## 创建任务

用户说"加个任务/提醒我..."时：

1. 解析任务名、可选截止时间、可选优先级
2. 展示确认卡片
3. 用户确认后执行：

```bash
python3 /ductor/workspace/skills/personal-assistant/todo/scripts/todo_cli.py \
  --create "任务标题" --due "2026-07-05" --priority high
```

`--due` 格式：YYYY-MM-DD。`--priority`：high / normal / low。

## 完成/修改任务

- "标记 xx 完成"：

```bash
python3 /ductor/workspace/skills/personal-assistant/todo/scripts/todo_cli.py \
  --complete <task-id>
```

- "删除 xx"：先确认再执行

```bash
python3 /ductor/workspace/skills/personal-assistant/todo/scripts/todo_cli.py \
  --delete <task-id> --force
```

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
