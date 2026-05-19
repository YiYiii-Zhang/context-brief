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
3. memory/_last-context.md                        → 先读顶部「自动检查警告」区块，有 !!! 必须先处理
4. memory/_bridge.md                              → 桥接快照
5. 按 MEMORY.md 索引按需细读
```

**铁律**：_last-context.md 顶部 `## 自动检查警告` 有 `!!!` 行时，必须先处理警告再继续后续对话。警告是由 Stop hook 脚本自动生成的，不依赖模型记忆。

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

## 持续桥接快照（关键——不怕 /clear 突袭）

### 核心原则：delta 是唯一的主写入路径

**bridge.md 的 6 个 H2 段（待提醒/决定/坑/堵塞/下一步/上下文）由 hook 从 delta 自动生成，模型不要手动编辑它们。**

模型的写入职责：

| 写什么 | 怎么写 | 何时写 |
|--------|--------|--------|
| 决定、完成任务、新坑、堵塞、下一步、关键上下文、项目状态 | 写 `_session-delta.json` | 每完成一个实质任务 |
| 待提醒（用户说「提醒我」「别忘了」） | 直接写 `_bridge.md` 的 `## 待提醒` 段 | 立即，不等 |
| 紧急 crash recovery（预感要崩） | 直接写 `_bridge.md` | 只在模型感知到不稳定时 |

**hook 的职责**：读取 delta → 机械应用到 bridge.md 的 H2 段 + project 文件。模型只管写 delta，不用操心 bridge.md 的格式和去重。

### 写入 _session-delta.json（主路径）

**每次有实质性进展时必须写 `memory/_session-delta.json`**。这是唯一的「存」路径。

Schema：
```json
{
  "session_id": "当前 session ID（从系统提示可获取）",
  "timestamp": "2026-05-18T17:00:00+08:00",
  "decisions": ["决定1", "决定2"],
  "tasks_completed": ["完成的任务描述"],
  "tasks_started": ["新开的坑"],
  "tasks_cleared": ["从待提醒/坑中移除的项"],
  "tasks_pending": ["待提醒事项"],
  "blockers": ["堵塞项"],
  "next_steps": ["按优先级排列的下一步1", "下一步2"],
  "key_context": ["关键文件路径/人名/账号"],
  "project_updates": {
    "project-xxx": {
      "status": "项目最新状态描述（一句话）",
      "key_files": ["相关文件路径"]
    }
  }
}
```

**写入时机**：
- 做完一个任务 → 更新 decisions + tasks_completed + tasks_cleared（从坑中移除）
- 用户给了新待办 → 更新 tasks_pending
- 提醒事项完成了 → 更新 tasks_cleared（从待提醒中移除）
- 项目有实质进展（文件产出、决策、阶段变化）→ 更新 project_updates
- 任何时候优先级变了 → 更新 next_steps（替换语义，写完整列表）
- /clear 或 session 结束前 → 确保 delta 包含了本次所有关键进展

**注意**：
- **每次写 delta 都是覆盖文件，必须包含本次 session 至今所有累积进展，不只是刚做完的这一件事**
- 字段都用 `[]` 数组，空字段可以省略
- `tasks_started` 不需要手动加 `[进行中]` 前缀，hook 自动加
- `tasks_completed`：hook 会把 `[进行中] xxx` 替换为 `[已完成] xxx`，task 名写一致即可
- `tasks_cleared`：从 待提醒 + 开了什么坑 中删除包含该文本的行
- `next_steps` 是替换语义——每次都写当前最新的完整优先级列表
- `project_updates` 的 key 是 project 文件名（不含 .md），必须写完整 slug（如 `project-qijia-owner-interview`）
- `project_updates.status` 直接追加到 project 文件的 `## 状态` 段（带日期前缀），不会丢失
- delta 写入后 hook 会自动归档为 `_session-delta.applied.json` 并删除原文件
- 如果 hook 没跑（崩溃），delta 会残留，下次 hook 运行时处理

### bridge.md 手动编辑（仅一种例外）

**crash recovery**：如果模型感知到不稳定（连续 tool 失败等），在崩溃前把当前状态直接写到 bridge.md。

**其他所有情况——包括待提醒、做完任务、新开坑、改优先级——都只写 delta，不碰 bridge.md。** 待提醒通过 `tasks_pending`（新增）+ `tasks_cleared`（消除）管理。

### bridge.md 中引用项目的约定

提到项目时必须使用完整 slug（如 `project-qijia-owner-interview`），至少出现一次。这样 hook 的 `extract_project_names()` 才能正确识别并同步对应的 project 文件。

### 双向同步机制（save-session-state.py Stop hook）

Stop hook 运行时的完整流程：

```
_session-delta.json ──→ bridge.md + project-*.md  (主路径，结构化)
bridge.md ──(hash兜底)──→ project-*.md             (备路径，bridge 变了但 project 没变)
cleanup_auto_blocks()                               (全局清理 auto-sync 块)
run_checks()                                        (一致性验证)
compile_context() → _last-context.md                (编译快照)
```

**sync_project_files_from_bridge()** 的工作方式：
1. 对比 bridge 和 project 文件的哈希——如果 bridge 变了但 project 没变
2. 自动从 bridge 提取「做了什么决定」「开了什么坑」「下一步」
3. 追加到 project 文件的「## 状态」段（带 `<!-- auto-sync from bridge -->` 标记）
4. 更新 project 文件 frontmatter 的 `updated` 时间戳
5. **只追加不覆盖**，保护手动编辑的内容
6. cleanup_auto_blocks() 在每次 hook 运行结束时清除所有标记块

这意味着：模型只需写 delta，hook 自动把进展同步到 bridge 和 project 文件。

格式：

```markdown
# 桥接快照 — YYYY-MM-DD

## 待提醒
- 今日大事报
- 审核 dry-run 机制

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

## 明确 brief 指令（手动触发「只写」）

用户说以下任意触发词时，**立即执行完整写入流程**——写 delta + 跑 hook，不等 session 结束：

**触发词**：`brief`、`存一下`、`更新上下文`、`记一下`、`保存进度`、`存档`、`写bridge`、`存bridge`、`写快照`

**执行流程**：
1. 写 `memory/_session-delta.json`（包含本次 session 至今所有进展）
2. 立刻跑 hook 脚本，让 project 文件也同步更新：
   `echo '{}' | python3 .claude/scripts/save-session-state.py`
3. 告诉用户「已存入，project 文件也已同步」

**为什么跑 hook**：delta 只更新 bridge.md，project 文件的同步需要 hook 的 `apply_session_delta()` + `sync_project_files_from_bridge()`。不跑 hook 的话 project 文件会滞后到 session 结束。

**注意**：
- 这是显式的「只写」路径，和 session 结束时 hook 自动跑是两套保险
- 如果 delta 写入了但用户又继续干活了，没关系——下次 brief 指令或 hook 会覆盖

## 跨平台

切新模型时告诉它：

> 先读 ~/.claude/CLAUDE.md，然后找到项目的 memory/MEMORY.md，读 _bridge.md

如果新平台没有 memory 目录结构，用 CLAUDE.md 里的用户信息已经够搭基本认知，memory 文件随你后续迁移。
