---
name: context-brief
description: >
  Auto-load user context on session start. Detect keywords in the user's first
  message and load ONLY relevant memory files — not everything. During session,
  watch for new info (projects, preferences, status changes) and proactively
  write to memory without waiting for the user to ask. On /clear or session end,
  save a bridge snapshot: decisions made, work started, blockers, next steps.
  Use when: (1) session starts, (2) user invokes this skill, (3) switching
  models/platforms, (4) user says "load my context" or "remember who I am",
  (5) after /clear to restore state.
---

# Context Brief — 智能上下文持久化

让模型在 30 秒内认出你，自动续上上次没做完的事。

## 启动：智能加载（不浪费 token）

收到用户第一条消息后，不要全读。根据消息中的关键词匹配相关记忆域：

```
用户提到 →
  咖啡、Villashaka、大事报  → 读 villashaka-daily-report.md + rant-style-copywriting.md
  芭蕾、品牌搭建            → 读对应 project-*.md
  小红书、文案、种草         → 读 social-media-content-workflow.md
  齐家、房地产、业主         → 读 project-qijia-owner-interview.md
  日报、自动生成            → 读 daily-news-system.md
  报错、bug、修复           → 读 feedback-communication.md
  推演、逻辑、skill搞错了   → 读 feedback-communication.md（避免重复踩坑）
```

**兜底**：如果关键词不命中任何域，或用户只是说"加载我的上下文"，就走完整流程：

```
1. ~/.claude/CLAUDE.md                           → 身份 + 规则
2. memory/MEMORY.md                               → 记忆索引
3. memory/_last-context.md                        → 上次会话快照
4. 按 MEMORY.md 索引按需细读
```

## 运行中：自动维护检测

会话中检测到以下信号时，主动判断要不要写入记忆：

| 信号 | 示例 | 行动 |
|------|------|------|
| 新项目信息 | "我最近在做一个xx" | 写 project-xx.md |
| 偏好变化 | "以后别用xx了" / "改成用yy" | 更新 feedback-*.md |
| 身份/状态变化 | "我现在全职xx了" | 更新 user-profile.md |
| 新工作流 | "以后每次xx都先跑yy" | 写 *-workflow.md |
| 新品牌/新角色 | "我又开了一个xx" | 更新 CLAUDE.md + user-profile.md |

写入前确认一条："我把这个记下来了，下次换模型也能自动加载。" 不需要用户批准——用户说过不要操心。直接写。

## /clear 或会话结束：桥接强化

Stop hook 自动保存文件变更记录。同时你必须在 `/clear` 或会话结束前写一个**桥接快照**到 `memory/_bridge.md`：

```markdown
# 桥接快照 — YYYY-MM-DD

## 做了什么决定
- 决定用 A 方案做 xx，因为...

## 开了什么坑
- [进行中] 功能 A — 做到一半，卡在 xx
- [进行中] 功能 B — 刚开了头，需要...

## 堵塞
- xx 被阻塞因为...

## 下一步（按优先级）
1. 先做 xx
2. 然后检查 yy
3. 最后处理 zz

## 关键上下文
- 相关的文件路径 / API / 人名 / 账号
```

下次会话启动时，第一步先读 `_bridge.md` 而不是 `_last-context.md`。有桥接快照就优先桥接快照。

## 跨平台

切新模型时告诉它：

> 先读 ~/.claude/CLAUDE.md，然后找到项目的 memory/MEMORY.md，读 _bridge.md

如果新平台没有 memory 目录结构，用 CLAUDE.md 里的用户信息已经够搭基本认知，memory 文件随你后续迁移。
