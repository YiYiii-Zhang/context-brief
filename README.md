# Context Brief

自动上下文持久化 skill。让任何 AI 模型在对话开始时自动载入你的背景、偏好、项目进度。

## 解决什么问题

切新模型 / 新平台时，AI 不认识你。每次都要重新介绍自己。这个 skill 告诉模型去哪里读你的上下文文件，30 秒内恢复完整记忆。

## 原理

不建新工具，不维护新格式。只定义一套读取顺序：

```
CLAUDE.md → memory/MEMORY.md → _last-context.md → 按需读取具体文件
```

你的上下文本来就存在这些文件里（CLAUDE.md、memory 系统、Stop hook 快照），这个 skill 只是告诉模型怎么高效地读它们。

## 安装

```bash
git clone https://github.com/YiYiii-Zhang/context-brief.git ~/.claude/skills/context-brief
```

或者放到 `.agents/skills/context-brief/`。

## 使用

安装后在对话里输入 `/context-brief`，模型自动加载全部上下文。

## 依赖

- Claude Code memory 系统（CLAUDE.md + memory/ 目录）
- Stop hook（自动生成 _last-context.md 快照）

不依赖任何特定平台。纯 Markdown。
