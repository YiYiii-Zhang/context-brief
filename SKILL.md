---
name: context-brief
description: >
  Auto-load user context and background on session start. READ MEMORY.md and
  CLAUDE.md first before ANY response. Update memory when user shares new info
  about themselves, projects, preferences, or workflows. Use when: (1) session
  starts, (2) user invokes this skill, (3) switching models/platforms, (4) user
  says "load my context" or "remember who I am", (5) after /clear to restore state.
---

# Context Brief — 自动上下文持久化

让任何模型在对话开始时自动载入你的背景、偏好、项目进度。

## 你的上下文存在哪

```
~/.claude/CLAUDE.md                              ← 你是谁、品牌、沟通风格、工作习惯
~/.claude/projects/<project>/memory/              ← 项目级记忆
  ├── MEMORY.md                                   ← 记忆索引（先读这个）
  ├── _last-context.md                            ← 上次会话快照（Stop hook 自动生成）
  ├── _last-session.json                          ← 会话元数据
  ├── user-profile.md                             ← 用户详细背景
  ├── project-*.md                                ← 每个项目的进度和上下文
  ├── feedback-*.md                               ← 用户的行为反馈和偏好
  └── *-workflow.md                               ← 工作流规范
```

## 会话启动流程

每次对话开始，模型必须按顺序做：

```
1. 读 ~/.claude/CLAUDE.md         → 了解用户身份和基本规则
2. 读 ~/.claude/projects/*/memory/MEMORY.md  → 找到所有记忆文件索引
3. 读 _last-context.md            → 了解上次会话做了什么
4. 读 _last-session.json          → 会话元数据
5. 按需读 project-*.md、feedback-*.md  → 获取详细上下文
```

这套流程确保模型第一次见你就知道你是谁、在做什么、有什么偏好。

## 会话中的自动保存

Stop hook 已在以下时机自动触发保存（无需手动操作）：
- `/clear` 时
- 会话结束时
- context compaction 前后

hook 自动生成 `_last-context.md`，包含：
- 今日工作状态
- 项目进度摘要
- 行为规则提醒
- 最近修改的文件
- 恢复指引

## 跨模型 / 跨平台

切模型或换平台时，新模型只需要读上面那些文件就能载入全部上下文。所有信息都在纯文本 Markdown 里，不依赖任何特定平台。

如果新平台不支持 Claude Code 的 memory 系统，用户只需告诉模型：

> 先读 ~/.claude/CLAUDE.md 和 ~/.claude/projects/*/memory/MEMORY.md

## 手动维护

通常不需要。但如果用户说了新的重要信息（新项目、新偏好、个人状态变化），主动更新对应的 memory 文件。编辑现有文件，不新建。
