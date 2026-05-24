#!/usr/bin/env python3
"""
model-context-loader v2 — 收集+结构化+去重，不负责合成
零外部依赖，兼容含中文/空格的路径
"""
import os
import sys
import argparse
from pathlib import Path
from datetime import datetime, timedelta

LOADER_DIR = Path.home() / ".model-context-loader"
CONFIG_PATH = LOADER_DIR / "config.json"

# 扫描优先级：当前工具 → 其他工具 → git（最后兜底）
KNOWN_TOOL_DIRS = [
    Path.home() / ".claude",   # Claude Code
    Path.home() / ".cursor",   # Cursor
    Path.home() / ".gemini",   # Gemini CLI
    Path.home() / ".codex",    # OpenAI Codex
]

CLAUDE_BASE = KNOWN_TOOL_DIRS[0]  # 默认，但实际扫描时遍历所有目录


# ═══════════════════════════════════════════════════════════════════════
# 1. 基础工具
# ═══════════════════════════════════════════════════════════════════════

def parse_frontmatter(text):
    """解析 --- YAML frontmatter → (metadata_dict, body_text)"""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    meta = {}
    for line in parts[1].strip().split("\n"):
        line = line.strip()
        if ":" in line and not line.startswith("#"):
            key, _, val = line.partition(":")
            meta[key.strip()] = val.strip().strip('"').strip("'")
    return meta, parts[2].strip()


def safe_read(path, max_bytes=50000):
    try:
        p = Path(path)
        if not p.exists():
            return None
        size = p.stat().st_size
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(min(size, max_bytes))
    except (OSError, PermissionError):
        return None


def is_sensitive(filename):
    name = Path(filename).name.lower()
    if name in {".env", "credentials.json", "token.json", "id_rsa", "id_ed25519"}:
        return True
    for pat in ["*.key", "*.pem", "*.p12", "*.pfx", "secrets.*", "config.private.*"]:
        if Path(name).match(pat):
            return True
    return False


def estimate_tokens(text):
    if not text:
        return 0
    cjk = sum(1 for c in text if "一" <= c <= "鿿" or "　" <= c <= "〿")
    words = len(text.split())
    base = cjk * 2 + max(0, len(text) - cjk) * 0.4 + words * 0.5
    return int(base * 1.15)


def freshness_label(mtime, category):
    if mtime is None:
        return ""
    days = (datetime.now() - mtime).days
    if category == "project":
        if days >= 30:
            return f"[已过期 — {days}天前]"
        if days >= 15:
            return f"[可能过期 — {days}天前]"
    elif category == "task":
        if days >= 14:
            return f"[已过期 — {days}天前]"
        if days > 7:
            return f"[可能过期 — {days}天前]"
    return ""


# ═══════════════════════════════════════════════════════════════════════
# 2. 结构化解析器 — 从原始 markdown 提取结构数据
# ═══════════════════════════════════════════════════════════════════════

def parse_md_sections(text):
    """把 markdown 按 # / ## / ### 标题拆成 {标题: 内容} 字典"""
    if not text:
        return {}
    sections = {}
    current_heading = "_head"
    current_lines = []
    for line in text.split("\n"):
        if line.startswith("#") and line.lstrip("#").startswith(" "):
            if current_lines:
                sections[current_heading] = "\n".join(current_lines).strip()
            current_heading = line.lstrip("#").strip()
            current_lines = []
        else:
            current_lines.append(line)
    if current_lines:
        sections[current_heading] = "\n".join(current_lines).strip()
    return sections


def parse_claude_md(text):
    """从 CLAUDE.md 提取结构化用户画像"""
    if not text:
        return {}
    # 过滤自引用指针行
    cleaned = "\n".join(
        line for line in text.split("\n")
        if "model-context-loader" not in line and "context-brief.md" not in line
    )
    sections = parse_md_sections(cleaned)
    # 合并 # 我是谁 的多余前缀
    identity = sections.get("我是谁", "").replace("# 我是谁\n", "").strip()
    daily = sections.get("我每天在干什么", "").strip()
    brands = sections.get("我的品牌", "").strip()
    comm = sections.get("沟通", "").strip()
    style = sections.get("文风", "").strip()
    dirs = sections.get("重要目录", "").strip()
    status = sections.get("我的状态", "").strip()
    habits = sections.get("工作习惯", "").strip()
    return {
        "identity": identity,
        "daily_work": daily,
        "brands": brands,
        "communication": comm,
        "writing_style": style,
        "key_dirs": dirs,
        "status": status,
        "work_habits": habits,
    }


def parse_bridge_sections(text):
    """从 _bridge.md 提取结构化项目状态"""
    if not text:
        return {}
    sections = parse_md_sections(text)
    result = {}
    # H2 名映射：bridge 的「下一步（按优先级）」→ 归一化 key「下一步」
    _key_map = {"下一步": "下一步（按优先级）"}
    for key in ["待提醒", "做了什么决定", "开了什么坑", "堵塞", "下一步", "关键上下文"]:
        content = sections.get(key, "") or sections.get(_key_map.get(key, ""), "")
        if key == "下一步":
            # 提取编号行: 1. 2. 3. ...
            items = []
            for l in content.split("\n"):
                stripped = l.strip()
                # 匹配 "1. xxx" 或 "12. xxx" 格式
                if stripped and stripped[0].isdigit():
                    m = stripped.split(". ", 1)
                    if len(m) == 2 and m[0].isdigit():
                        items.append(m[1].strip())
        else:
            # 提取 bullet 行: - xxx
            items = [l.strip("- ").strip() for l in content.split("\n") if l.strip().startswith("-")]
        if not items:
            items = [content.strip()] if content.strip() else []
        result[key] = items
    return result


def parse_last_context_sections(text):
    """从 _last-context.md 提取结构化数据，区分桥接区和其他区"""
    if not text:
        return {}
    import re
    sections = parse_md_sections(text)

    # 路径1: 旧格式 — ## 上次对话现场（来自 _bridge.md）直接包含 bridge
    bridge_raw = sections.get("上次对话现场（来自 _bridge.md）", "")
    if not bridge_raw:
        m = re.search(
            r'## 上次对话现场.*?\n(.*?)(?=\n## (?:项目进度|行为规则|最近修改|今日日报|恢复指引))',
            text, re.DOTALL,
        )
        if m:
            bridge_raw = m.group(1).strip()

    # 路径2: save-session-state.py 产出的实际格式 — ## 本次关键决策与下一步
    # 内含 ## 做了什么决定 / ## 开了什么坑 等 H2，提取到下一个外层 H2 或文本末尾
    if not bridge_raw:
        m = re.search(
            r'## 本次关键决策与下一步\n(.*?)(?=\n## (?:最近更新|最近操作|恢复|本次关键决策)|\Z)',
            text, re.DOTALL,
        )
        if m:
            bridge_raw = m.group(1).strip()

    bridge = parse_bridge_sections(bridge_raw) if bridge_raw else {}

    progress = sections.get("项目进度（来自 project-*.md）", "")
    rules = sections.get("行为规则提醒", "")
    recent_files = sections.get("最近修改的文件", "") or sections.get("最近操作的文件", "")
    daily_status = sections.get("今日日报生成状态", "")

    return {
        "bridge": bridge,
        "project_progress": progress.strip(),
        "behavior_rules": rules.strip(),
        "recent_files": recent_files.strip(),
        "daily_status": daily_status.strip(),
    }


# ═══════════════════════════════════════════════════════════════════════
# 3. 扫描 — 收集所有源数据
# ═══════════════════════════════════════════════════════════════════════

def find_memory_dirs(tool_filter=None):
    """扫描工具目录，当前工具优先。返回 (项目列表, 冲突列表, 扫描报告)"""
    tools_to_scan = list(KNOWN_TOOL_DIRS)
    if tool_filter:
        tools_to_scan = []
        for f in tool_filter:
            p = Path(f) if f.startswith("/") else Path.home() / f
            tools_to_scan.append(p)

    current_tool = str(tools_to_scan[0]) if tools_to_scan else str(KNOWN_TOOL_DIRS[0])

    dirs = []
    seen = set()
    conflicts = []
    scan_report = {}

    # 第一遍：扫当前工具（排最前的那个）
    primary = tools_to_scan[0] if tools_to_scan else None
    other_tools = tools_to_scan[1:] if len(tools_to_scan) > 1 else []

    for tool_dir in [primary] if primary else []:
        if not tool_dir.exists():
            scan_report[str(tool_dir)] = 0
            continue
        count = 0
        for p in sorted(tool_dir.glob("projects/*/memory")):
            if p.is_dir():
                key = p.parent.name
                count += 1
                if key not in seen:
                    seen.add(key)
                dirs.append((key, str(p), str(tool_dir), True))
        scan_report[str(tool_dir)] = count

    # 第二遍：扫其余工具，当前工具已有同名项目则跳过
    for tool_dir in other_tools:
        if not tool_dir.exists():
            scan_report[str(tool_dir)] = 0
            continue
        count = 0
        for p in sorted(tool_dir.glob("projects/*/memory")):
            if p.is_dir():
                key = p.parent.name
                count += 1
                if key in seen:
                    conflicts.append((key, str(tool_dir)))
                else:
                    seen.add(key)
                    dirs.append((key, str(p), str(tool_dir), False))
        scan_report[str(tool_dir)] = count

    return dirs, conflicts, scan_report


def scan_memory(include_global=False, domain=None, tool_filter=None):
    data = {
        "claude_parsed": {},
        "global_raw": None,
        "projects": {},
        "memories": {"user": [], "feedback": [], "project": [], "reference": []},
        "last_contexts_parsed": {},
        "bridges_parsed": {},
        "files": {},
        "sources": [],
        "scan_report": {},
        "scan_conflicts": [],
    }

    if include_global:
        for tool_dir in KNOWN_TOOL_DIRS:
            claude_md = tool_dir / "CLAUDE.md"
            if not claude_md.exists():
                continue
            content = safe_read(str(claude_md))
            if content:
                data["global_raw"] = content
                data["claude_parsed"] = parse_claude_md(content)
                data["files"][str(claude_md)] = datetime.fromtimestamp(claude_md.stat().st_mtime)
                data["sources"].append(str(tool_dir))
                break  # 找到第一个就停

    results, conflicts, report = find_memory_dirs(tool_filter)
    data["scan_report"] = report
    data["scan_conflicts"] = conflicts

    for project_name, mem_dir, source_dir, is_current in results:
        mem_path = Path(mem_dir)
        if source_dir not in data["sources"]:
            data["sources"].append(source_dir)

        # _bridge.md → 解析
        bridge_content = safe_read(str(mem_path / "_bridge.md"))
        if bridge_content:
            data["bridges_parsed"][project_name] = parse_bridge_sections(bridge_content)
            data["files"][str(mem_path / "_bridge.md")] = datetime.fromtimestamp(
                (mem_path / "_bridge.md").stat().st_mtime)

        # _last-context.md → 解析
        lc_content = safe_read(str(mem_path / "_last-context.md"))
        if lc_content:
            parsed_lc = parse_last_context_sections(lc_content)
            data["last_contexts_parsed"][project_name] = parsed_lc
            data["files"][str(mem_path / "_last-context.md")] = datetime.fromtimestamp(
                (mem_path / "_last-context.md").stat().st_mtime)
            # 无独立 _bridge.md 时，嵌入的 bridge 提升到 bridges_parsed
            if project_name not in data["bridges_parsed"]:
                embedded = parsed_lc.get("bridge", {})
                if embedded and any(embedded.get(k) for k in ["待提醒", "做了什么决定", "开了什么坑", "堵塞", "下一步"]):
                    data["bridges_parsed"][project_name] = embedded

        # MEMORY.md
        mem_md = safe_read(str(mem_path / "MEMORY.md"))
        if mem_md:
            data["projects"][project_name] = {"memory_index": mem_md}
            data["files"][str(mem_path / "MEMORY.md")] = datetime.fromtimestamp(
                (mem_path / "MEMORY.md").stat().st_mtime)

        # 分类型 memory/*.md
        for md_file in sorted(mem_path.glob("*.md")):
            fname = md_file.name
            if fname.startswith("_") or fname == "MEMORY.md":
                continue
            if is_sensitive(str(md_file)):
                continue
            content = safe_read(str(md_file))
            if not content:
                continue
            meta, body = parse_frontmatter(content)
            mem_type = meta.get("type", "reference")
            if mem_type not in data["memories"]:
                mem_type = "reference"
            data["memories"][mem_type].append({
                "path": str(md_file),
                "name": meta.get("name", md_file.stem),
                "description": meta.get("description", ""),
                "type": mem_type,
                "content": body.strip(),
                "mtime": datetime.fromtimestamp(md_file.stat().st_mtime),
            })
            data["files"][str(md_file)] = datetime.fromtimestamp(md_file.stat().st_mtime)

    if domain:
        data = filter_by_domain(data, domain)
    return data


def filter_by_domain(data, domain):
    keywords = {
        "coffee": ["咖啡", "villashaka", "精品咖啡"],
        "ballet": ["芭蕾"],
        "realestate": ["齐家", "qijia", "房产", "房地产", "业主"],
        "social": ["小红书", "自媒体", "social", "种草"],
    }.get(domain, [])
    if not keywords:
        return data

    def match(text):
        return any(kw in (text or "") for kw in keywords)

    # bridge 和 _last-context 是会话状态，跟领域无关，永远全量保留
    return {
        "claude_parsed": data.get("claude_parsed", {}),
        "global_raw": data.get("global_raw"),
        "projects": {k: v for k, v in data.get("projects", {}).items()
                     if match(k) or match(v.get("memory_index", ""))},
        "memories": {
            mt: [e for e in entries if match(e.get("name", "") + e.get("description", "") + e.get("content", ""))]
            for mt, entries in data.get("memories", {}).items()
        },
        "last_contexts_parsed": data.get("last_contexts_parsed", {}),
        "bridges_parsed": data.get("bridges_parsed", {}),
        "files": data.get("files", {}),
        "scan_report": data.get("scan_report", {}),
        "scan_conflicts": data.get("scan_conflicts", []),
    }


# ═══════════════════════════════════════════════════════════════════════
# 4. 去重 + 截断检测
# ═══════════════════════════════════════════════════════════════════════

class DedupSet:
    """跟踪已输出的行，避免跨板块重复"""

    def __init__(self):
        self.seen = set()

    def _hash(self, text):
        return hash(text.strip()[:80])

    def add(self, text):
        h = self._hash(text)
        if h in self.seen:
            return False
        self.seen.add(h)
        return True

    def filter_lines(self, lines):
        return [l for l in lines if self.add(l)]


def detect_truncation(text, source_path):
    """检测截断：行末 ... / 不完整的句子结尾 / 单词断掉"""
    issues = []
    lines = text.split("\n")
    for i, line in enumerate(lines, 1):
        stripped = line.rstrip()
        # 行末 ...
        if stripped.endswith("...") and len(stripped) < 80:
            issues.append(f"截断行{i}: 「{stripped[:60]}」→ {source_path}")
        # 最后一个非空行突然很短且不以标点结尾
        if i == len(lines) and 5 < len(stripped) < 40:
            if stripped[-1] not in "。！？.!?)]」\"'":
                issues.append(f"疑似截断行{i}: 「{stripped}」→ {source_path}")
    return issues


# ═══════════════════════════════════════════════════════════════════════
# 5. 简报组装 — 结构化 + 去重
# ═══════════════════════════════════════════════════════════════════════

def _check_data_health(data):
    """检测是否为非标准结构（bridge 解析失败 / memory 稀疏）"""
    bridges = data.get("bridges_parsed", {})
    lc = data.get("last_contexts_parsed", {})
    memories = data.get("memories", {})
    claude = data.get("claude_parsed", {})
    files = data.get("files", {})

    # bridge 有效 = 至少有决定/任务/上下文之一
    def _bridge_has_content(b):
        if not b:
            return False
        return any(b.get(k) for k in ["待提醒", "做了什么决定", "开了什么坑", "堵塞", "关键上下文", "下一步"])
    has_bridge = any(_bridge_has_content(v) for v in bridges.values())
    # last_context 有效 = bridge 有内容 或 project_progress 有内容
    has_lc = any(
        _bridge_has_content(v.get("bridge", {})) or v.get("project_progress", "").strip()
        for v in lc.values()
    )
    has_mem = sum(len(v) for v in memories.values())
    has_identity = bool(claude.get("identity", "").strip())

    if has_bridge or has_lc:
        return True, []  # 结构正常

    warnings = []
    if not has_identity and files:
        warnings.append("CLAUDE.md 解析失败或无身份信息")
    if has_mem == 0:
        warnings.append("未找到有效的 memory/*.md 文件")
    elif files and has_mem < 2:
        warnings.append("memory 文件稀疏，可能使用了非标准命名/格式")

    return False, warnings


def assemble_brief(data, mode="quick", max_tokens=None):
    dedup = DedupSet()
    claude = data.get("claude_parsed", {})
    user_mem = data.get("memories", {}).get("user", [])
    feedback_mem = data.get("memories", {}).get("feedback", [])
    project_mem = data.get("memories", {}).get("project", [])
    bridges = data.get("bridges_parsed", {})
    last_contexts = data.get("last_contexts_parsed", {})
    files = data.get("files", {})

    truncation_issues = []

    sections = []

    # 非标准结构检测
    healthy, health_warnings = _check_data_health(data)

    # ── 板块1: 用户核心背景 ──
    identity = claude.get("identity", "")
    if identity:
        identity = identity.rstrip("。")
    unique_facts = []
    for m in user_mem:
        body = m.get("content", "")
        for line in body.split("\n"):
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if "会用 Claude Code" in s:
                if "。" in s:
                    s = s.split("。", 1)[1].strip("。").strip() + "。"
                unique_facts.append(s)
            elif "生酮" in s:
                unique_facts.append(s.split("。")[0] + "。")
            elif "基本全天工作" in s:
                unique_facts.append(s)
    parts = [identity] if identity else []
    parts.extend(unique_facts)
    body = "\n".join(parts)
    if not healthy:
        banner = "⚠️ 非标准上下文结构，以下为尽力解析结果"
        if health_warnings:
            banner += "（" + "；".join(health_warnings) + "）"
        body = banner + ("\n" + body if body else "")
    sections.append(("板块1", "用户核心背景", body))

    # ── 板块2: 当前项目状态（从 bridge 解析提取，不 dump 原文） ──
    lines = []
    all_project_names = set(list(bridges.keys()) + list(last_contexts.keys()))
    multi_project = len(all_project_names) > 1
    for proj_name, bridge in bridges.items():
        if multi_project:
            lines.append(f"**{proj_name}**")
        decisions = bridge.get("做了什么决定", [])
        for d in decisions[:5]:
            lines.append(f"- {d}")
        ctx = bridge.get("关键上下文", [])
        for c in ctx[:3]:
            lines.append(f"- {c}")
    for proj_name, lc in last_contexts.items():
        if proj_name in bridges:
            continue
        progress = lc.get("project_progress", "")
        if progress:
            if multi_project:
                lines.append(f"**{proj_name}**")
            lines.append(progress[:200])
    sections.append(("板块2", "当前项目状态", "\n".join(dedup.filter_lines(lines))))

    # ── 板块3: 未完成任务 + 阻塞点 + 待提醒 ──
    lines = []
    for proj_name, bridge in bridges.items():
        # 待提醒（置顶，更紧急）
        reminders = bridge.get("待提醒", [])
        for r in reminders:
            lines.append(f"- [待提醒] {r}")
        tasks = bridge.get("开了什么坑", [])
        for t in tasks:
            if "[已完成]" not in t:
                lines.append(f"- {t}")
        blockers = bridge.get("堵塞", [])
        for b in blockers:
            lines.append(f"- 堵塞: {b}")
    for proj_name, lc in last_contexts.items():
        lc_bridge = lc.get("bridge", {})
        if proj_name not in bridges:
            tasks = lc_bridge.get("开了什么坑", [])
            for t in tasks[:5]:
                if "[已完成]" not in t:
                    lines.append(f"- {t}")
    sections.append(("板块3", "未完成任务+阻塞点", "\n".join(dedup.filter_lines(lines))))

    # ── 板块4: 必须遵守的关键偏好（紧凑格式，同类合并） ──
    comm_items = [l.strip("- ").strip() for l in claude.get("communication", "").split("\n") if l.strip("- ").strip()]
    style_items = [l.strip("- ").strip() for l in claude.get("writing_style", "").split("\n")
                   if l.strip("- ").strip() and "别假设" not in l]
    habit_items = [l.strip("- ").strip() for l in claude.get("work_habits", "").split("\n") if l.strip("- ").strip()]
    rule_items = []
    for m in feedback_mem:
        content = m.get("content", "")
        if "硬规则" in content or "Why:" in content:
            for line in content.split("\n"):
                if line.startswith("## ") and "规则" in line:
                    rule_items.append(line.strip("# "))

    lines = []
    if comm_items:
        lines.append("**沟通** " + "。".join(comm_items))
    if style_items:
        lines.append("**文风** " + style_items[0])  # 只取底线，不列全部
    if habit_items:
        lines.append("**习惯** " + "；".join(habit_items))
    if rule_items:
        lines.append("**硬规则** " + " | ".join(rule_items))
    sections.append(("板块4", "必须遵守的关键偏好", "\n".join(lines)))

    # ── 板块5: 推荐下一步 ──
    lines = []
    for proj_name, bridge in bridges.items():
        steps = bridge.get("下一步", [])
        for s in steps:
            lines.append(f"- {s}")
    for proj_name, lc in last_contexts.items():
        lc_bridge = lc.get("bridge", {})
        if proj_name not in bridges:
            steps = lc_bridge.get("下一步", [])
            for s in steps[:3]:
                lines.append(f"- {s}")
    # 没有显式下一步时，从 [待确认] 推导
    if not lines:
        for proj_name, bridge in bridges.items():
            tasks = bridge.get("开了什么坑", [])
            for t in tasks:
                if "[待确认]" in t:
                    lines.append(f"- 确认: {t.replace('[待确认] ', '')}")
    if not lines:
        for proj_name, lc in last_contexts.items():
            lc_bridge = lc.get("bridge", {})
            tasks = lc_bridge.get("开了什么坑", [])
            for t in tasks[:3]:
                if "[待确认]" in t:
                    lines.append(f"- 确认: {t.replace('[待确认] ', '')}")
    sections.append(("板块5", "推荐下一步", "\n".join(dedup.filter_lines(lines))))

    # 非标准结构兜底：松散摘要（板块5之后，含原文件内容片段）
    if not healthy and files:
        loose_lines = []
        for path in sorted(files.keys())[:10]:
            raw = data.get("global_raw", "") if path.endswith("CLAUDE.md") else ""
            if not raw:
                # 尝试从 memories 中找对应内容
                for mlist in data.get("memories", {}).values():
                    for m in mlist:
                        if m.get("path") == path:
                            raw = m.get("content", "")
                            break
            snippet = raw[:300].replace("\n", " ").strip()
            if snippet:
                loose_lines.append(f"- `{path}`: {snippet}...")
        if loose_lines:
            sections.append(("_loose", "原始内容摘要（非标准结构兜底）", "\n".join(loose_lines)))

    # ── Full 模式扩展 ──
    if mode == "full":
        # 板块6: 长期项目
        lines = []
        for m in project_mem:
            name = m.get("name", "")
            desc = m.get("description", "")
            content = m.get("content", "")[:150]
            freshness = freshness_label(m.get("mtime"), "project")
            lines.append(f"- **{name}**: {desc} {freshness}")
            if content:
                lines.append(f"  {content}")
        sections.append(("板块6", "长期项目与常见任务", "\n".join(dedup.filter_lines(lines))))

        # 板块7: 事实来源文件
        lines = []
        for path, mtime in sorted(files.items()):
            label = freshness_label(mtime, "project")
            lines.append(f"- `{path}` — {mtime.strftime('%Y-%m-%d')} {label}")
        sections.append(("板块7", "事实来源文件", "\n".join(lines)))

        # 板块8: 信息新鲜度
        decay = [
            "稳定偏好（feedback-*, user-*）：不过期",
            "项目状态（project-*）：>=15天标可能过期，>=30天标已过期",
            "任务上下文（_last-context, _bridge）：>7天标可能过期，>14天标已过期",
        ]
        sections.append(("板块8", "信息新鲜度说明", "\n".join(decay)))

        # 板块9: 不要重复询问
        lines = []
        for m in feedback_mem:
            name = m.get("name", "").replace("feedback-", "")
            desc = m.get("description", "")
            lines.append(f"- {name}: {desc}")
        sections.append(("板块9", "不要重复询问的信息", "\n".join(dedup.filter_lines(lines))))

    # ── 拼接输出 ──
    output_parts = [
        f"> 本文件包含个人上下文，请勿分享/提交/上传。最后更新：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
    ]

    for sec_key, sec_title, sec_body in sections:
        body = sec_body.strip() or "（暂无）"
        output_parts.append(f"<!-- @auto: {sec_key} -->")
        output_parts.append(f"## {sec_title}")
        output_parts.append(body)
        output_parts.append("<!-- @auto-end -->")
        output_parts.append("")

    full_text = "\n".join(output_parts)

    # 截断
    if max_tokens:
        # 高优先级板块排在前面，按序保留
        blocks = full_text.split("<!-- @auto-end -->")
        kept_blocks = []
        token_budget = 0
        for block in blocks:
            t = estimate_tokens(block)
            if token_budget + t > max_tokens:
                break
            kept_blocks.append(block)
            token_budget += t
        full_text = "<!-- @auto-end -->".join(kept_blocks)

    return full_text, truncation_issues


# ═══════════════════════════════════════════════════════════════════════
# 6. 手动编辑保护
# ═══════════════════════════════════════════════════════════════════════

def preserve_manual_edits(existing_path, new_brief):
    existing = safe_read(str(existing_path))
    if not existing:
        return new_brief

    import re
    new_blocks = {}
    for m in re.finditer(r"<!-- @auto: (.+?) -->\n(.*?)\n<!-- @auto-end -->", new_brief, re.DOTALL):
        new_blocks[m.group(1).strip()] = m.group(2).strip()

    def replacer(m):
        key = m.group(1).strip()
        if key in new_blocks:
            return f"<!-- @auto: {key} -->\n{new_blocks[key]}\n<!-- @auto-end -->"
        return m.group(0)

    return re.sub(
        r"<!-- @auto: (.+?) -->\n.*?\n<!-- @auto-end -->",
        replacer, existing, flags=re.DOTALL,
    )


# ═══════════════════════════════════════════════════════════════════════
# 7. Git 跨机器拉取
# ═══════════════════════════════════════════════════════════════════════

def load_config():
    try:
        if CONFIG_PATH.exists():
            import json
            with open(str(CONFIG_PATH), "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def save_config(cfg):
    import json
    LOADER_DIR.mkdir(parents=True, exist_ok=True)
    with open(str(CONFIG_PATH), "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.chmod(str(CONFIG_PATH), 0o600)


SKILL_PATH = LOADER_DIR / "SKILL.md"

def _read_git_remote_from_skill():
    """从 SKILL.md frontmatter 读取 git_remote（跨机器存活）"""
    content = safe_read(str(SKILL_PATH))
    if not content:
        return None
    meta, _ = parse_frontmatter(content)
    return meta.get("git_remote")


def _write_git_remote_to_skill(url):
    """将 git URL 写入 SKILL.md frontmatter 的 git_remote 字段"""
    content = safe_read(str(SKILL_PATH))
    if not content:
        return
    meta, body = parse_frontmatter(content)
    if meta.get("git_remote") == url:
        return  # 没变，不写
    meta["git_remote"] = url
    lines = ["---"]
    for k, v in meta.items():
        lines.append(f"{k}: {v}")
    lines.append("---")
    new_content = "\n".join(lines) + "\n" + body
    SKILL_PATH.write_text(new_content, encoding="utf-8")
    os.chmod(str(SKILL_PATH), 0o600)


def git_pull_context(repo_url, force=False):
    import subprocess
    import shutil

    if shutil.which("git") is None:
        return None, "git 未安装，无法拉取远程上下文"

    pull_dir = LOADER_DIR / "pulled-context"

    if pull_dir.exists():
        if not force:
            return str(pull_dir), None
        shutil.rmtree(str(pull_dir))

    result = subprocess.run(
        ["git", "clone", "--depth", "1", repo_url, str(pull_dir)],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        err = result.stderr.strip().split("\n")[-1] if result.stderr else "unknown error"
        return None, f"git clone 失败: {err}"

    # 找到记忆目录的位置
    # 情况1: repo 根目录有 CLAUDE.md 或 projects/ → 直接用作 CLAUDE_BASE
    # 情况2: repo 根目录有 .claude/ 子目录 → 用 .claude/ 作为 CLAUDE_BASE
    if (pull_dir / "projects").is_dir() or (pull_dir / "CLAUDE.md").exists():
        return str(pull_dir), None
    if (pull_dir / ".claude").is_dir():
        return str(pull_dir / ".claude"), None

    # 没找到记忆文件
    shutil.rmtree(str(pull_dir))
    return None, "仓库中未找到 projects/ 或 CLAUDE.md，请确认推送了正确的目录结构"


# ═══════════════════════════════════════════════════════════════════════
# 8. 主入口
# ═══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="model-context-loader v2")
    parser.add_argument("--mode", choices=["quick", "full"], default="quick")
    parser.add_argument("--domain", choices=["coffee", "ballet", "realestate", "social"], default=None)
    parser.add_argument("--max-tokens", type=int, default=None)
    parser.add_argument("--include-global", action="store_true", default=False)
    parser.add_argument("--include-vault", type=str, default=None)
    parser.add_argument("--tools", type=str, default=None, metavar="DIR1,DIR2",
                        help="限定扫描的工具目录，逗号分隔（如 .claude,.cursor）")
    parser.add_argument("--output", choices=["stdout", "file"], default="file")
    parser.add_argument("--pull-from-git", type=str, default=None, metavar="URL",
                        help="从 git 仓库拉取上下文记忆（新机器最后兜底）")
    parser.add_argument("--save-git-remote", type=str, default=None, metavar="URL",
                        help="记录 git 远程地址到配置，供后续自动使用")
    args = parser.parse_args()

    cfg = load_config()

    # --save-git-remote: 记录 URL 到 config.json + SKILL.md frontmatter
    if args.save_git_remote:
        cfg["remote_repo"] = args.save_git_remote
        save_config(cfg)
        _write_git_remote_to_skill(args.save_git_remote)
        print(f"已记录远程上下文仓库: {args.save_git_remote}")
        print("  URL 已写入 config.json + SKILL.md，新机器上可自动识别")
        return

    # 优先级：本地已知工具目录 → git（最后兜底）
    results, _, _ = find_memory_dirs()
    pulled_source = None

    if not results:
        # 解析顺序：命令行 → config.json → SKILL.md frontmatter（跨机器）
        git_url = args.pull_from_git or cfg.get("remote_repo") or _read_git_remote_from_skill()
        if git_url:
            print(f"本地无记忆文件，尝试从 git 拉取: {git_url}", file=sys.stderr)
            result, error = git_pull_context(git_url)
            if error:
                print(f"⚠ {error}", file=sys.stderr)
                print("将仅使用全局 CLAUDE.md 生成简报\n", file=sys.stderr)
            else:
                pulled_source = result
                KNOWN_TOOL_DIRS.insert(0, Path(result))
                print("已从 git 加载上下文记忆\n", file=sys.stderr)

    # 解析 --tools
    tool_filter = None
    if args.tools:
        tool_filter = [t.strip() for t in args.tools.split(",") if t.strip()]

    data = scan_memory(include_global=args.include_global, domain=args.domain, tool_filter=tool_filter)

    if args.include_vault:
        vault_path = Path(os.path.expanduser(args.include_vault))
        if vault_path.exists():
            for md_file in vault_path.rglob("*.md"):
                if md_file.name.startswith(".") or is_sensitive(str(md_file)):
                    continue
                c = safe_read(str(md_file), max_bytes=10000)
                if c:
                    data.setdefault("memories", {}).setdefault("reference", []).append({
                        "path": str(md_file),
                        "name": md_file.stem,
                        "description": f"Vault: {md_file.relative_to(vault_path)}",
                        "type": "reference",
                        "content": c[:1000],
                        "mtime": datetime.fromtimestamp(md_file.stat().st_mtime),
                    })

    brief, issues = assemble_brief(data, mode=args.mode, max_tokens=args.max_tokens)

    if issues:
        for issue in issues:
            print(f"⚠ {issue}", file=sys.stderr)

    token_est = estimate_tokens(brief)

    # 扫描报告
    report_lines = []
    report = data.get("scan_report", {})
    primary_tool = str(Path.home() / tool_filter[0]) if tool_filter else str(KNOWN_TOOL_DIRS[0])
    if report:
        for tool, count in report.items():
            label = "当前工具" if tool == primary_tool else ""
            tag = f" ({label})" if label else ""
            report_lines.append(f"  {tool}{tag}: {count} 个项目")
    conflicts = data.get("scan_conflicts", [])
    if conflicts:
        for proj, tool in conflicts:
            report_lines.append(f"  ⚠ {proj} 在 {tool} 也有同名项目，已跳过（优先当前工具）")

    if args.output == "stdout":
        print(brief)
        print(f"\n> 预估 token: ~{token_est}")
        if report_lines:
            print("> 扫描报告:")
            for line in report_lines:
                print(line)
            print("> 提示: 可用 --tools .claude 限定只扫指定目录")
        return

    LOADER_DIR.mkdir(parents=True, exist_ok=True)
    output_path = LOADER_DIR / "context-brief.md"
    if output_path.exists():
        brief = preserve_manual_edits(output_path, brief)
    output_path.write_text(brief, encoding="utf-8")
    os.chmod(str(output_path), 0o600)

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    archive_path = LOADER_DIR / f"context-brief-{ts}.md"
    archive_path.write_text(brief, encoding="utf-8")
    os.chmod(str(archive_path), 0o600)

    print(f"context-brief.md ({args.mode})")
    print(f"  token: ~{token_est}")
    if pulled_source:
        print(f"  来源: git remote")
    if data.get("sources"):
        print(f"  本地来源: {', '.join(data['sources'])}")
    if report_lines:
        for line in report_lines:
            print(line)
        print("  提示: 可用 --tools .claude 限定只扫指定目录")
    print(f"  archive: {archive_path.name}")


if __name__ == "__main__":
    main()
