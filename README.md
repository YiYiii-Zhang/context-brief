# Context Brief

智能上下文持久化。切模型后 30 秒认出你，自动续上上次没做完的事。

## 三个核心能力

**1. 智能加载** — 不浪费 token。模型根据你第一条消息的关键词只载入相关记忆。提到咖啡就加载咖啡域，不提就不载。

**2. 自动维护检测** — 会话中说漏了新项目、偏好变了、状态更新了，模型自动写进 memory。不问你，你不管。

**3. 桥接快照** — 每次 `/clear` 或会话结束，自动写 `_bridge.md`：做了什么决定、开了什么坑、卡在哪儿、下一步是什么。下个模型载入后直接续上。

## 安装

```bash
git clone https://github.com/YiYiii-Zhang/context-brief.git ~/.claude/skills/context-brief
```

## 使用

对话里输入 `/context-brief`。模型自动智能加载上下文。

## 依赖

- Claude Code memory 系统（CLAUDE.md + memory/）
- Stop hook（自动保存文件快照）

不依赖任何特定平台，纯 Markdown。
