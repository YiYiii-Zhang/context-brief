#!/usr/bin/env python3
"""
Doctor checks for context-brief/session-state health.

Default mode is read-only. Use --fix-stale to remove delta files whose hash has
already been archived as applied.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


HOME = Path.home()
PRIMARY_MEMORY = HOME / ".claude/projects/-Users-yiyidexiaopingguo-Documents-test-claude/memory"
CODEX_MEMORY = HOME / ".codex/memories"
LEGACY_MEMORY = HOME / "memory"
MODEL_CONTEXT_BRIEF = HOME / ".model-context-loader/context-brief.md"

DELTA_CANDIDATES = [
    PRIMARY_MEMORY / "_session-delta.json",
    CODEX_MEMORY / "_session-delta.json",
    LEGACY_MEMORY / "_session-delta.json",
]

APPLIED_ARCHIVES = [
    PRIMARY_MEMORY / "_session-delta.applied.json",
    CODEX_MEMORY / "_session-delta.applied.json",
    LEGACY_MEMORY / "_session-delta.applied.json",
]

REQUIRED_MEMORY_FILES = [
    PRIMARY_MEMORY / "_bridge.md",
    PRIMARY_MEMORY / "_last-context.md",
]


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {path}: {exc}") from exc


def delta_hash(data: dict[str, Any]) -> str:
    encoded = json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def file_age(path: Path) -> timedelta | None:
    if not path.exists():
        return None
    return datetime.now().astimezone() - datetime.fromtimestamp(path.stat().st_mtime).astimezone()


def age_label(age: timedelta | None) -> str:
    if age is None:
        return "missing"
    seconds = int(age.total_seconds())
    if seconds < 120:
        return f"{seconds}s ago"
    minutes = seconds // 60
    if minutes < 120:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 72:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


def load_applied_hashes() -> set[str]:
    hashes: set[str] = set()
    for path in APPLIED_ARCHIVES:
        data = read_json(path)
        if data and data.get("_delta_hash"):
            hashes.add(str(data["_delta_hash"]))
    return hashes


def extract_bridge_sections(text: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    current: str | None = None
    lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if current is not None:
                sections[current] = "\n".join(lines).strip()
            current = line[3:].strip()
            lines = []
        elif current is not None:
            lines.append(line)
    if current is not None:
        sections[current] = "\n".join(lines).strip()
    return sections


def count_items(section: str) -> int:
    count = 0
    for raw in section.splitlines():
        line = raw.strip()
        if line.startswith("- ") or re.match(r"^\d+\.\s+", line):
            count += 1
    return count


def check(args: argparse.Namespace) -> int:
    errors: list[str] = []
    warnings: list[str] = []
    notes: list[str] = []

    applied_hashes = load_applied_hashes()
    if not applied_hashes:
        errors.append("no applied delta archive hash found")

    pending: list[tuple[Path, str]] = []
    stale: list[tuple[Path, str]] = []
    duplicate_hashes: dict[str, list[Path]] = {}

    for path in DELTA_CANDIDATES:
        try:
            data = read_json(path)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if not data:
            continue
        fingerprint = delta_hash(data)
        duplicate_hashes.setdefault(fingerprint, []).append(path)
        if fingerprint in applied_hashes:
            stale.append((path, fingerprint))
        else:
            pending.append((path, fingerprint))

    for fingerprint, paths in duplicate_hashes.items():
        if len(paths) > 1:
            notes.append(f"duplicate delta hash {fingerprint[:12]} appears in {len(paths)} paths")

    if stale and args.fix_stale:
        removed = 0
        for path, _ in stale:
            try:
                path.unlink()
                removed += 1
            except OSError as exc:
                errors.append(f"could not remove stale delta {path}: {exc}")
        notes.append(f"removed {removed}/{len(stale)} stale delta file(s)")
        stale = []

    if stale:
        warnings.append(f"{len(stale)} already-applied delta file(s) still present; run with --fix-stale")
    if pending:
        warnings.append(f"{len(pending)} pending delta file(s) not yet consumed by hook")

    for path in REQUIRED_MEMORY_FILES:
        age = file_age(path)
        if age is None:
            errors.append(f"missing required memory file: {path}")
            continue
        notes.append(f"{path.name}: {age_label(age)}")
        if age > timedelta(hours=args.max_age_hours):
            warnings.append(f"{path.name} is older than {args.max_age_hours}h: {path}")

    brief_age = file_age(MODEL_CONTEXT_BRIEF)
    if brief_age is None:
        warnings.append(f"missing model context brief: {MODEL_CONTEXT_BRIEF}")
    else:
        notes.append(f"context-brief.md: {age_label(brief_age)}")
        if brief_age > timedelta(hours=args.max_age_hours):
            warnings.append(f"context-brief.md is older than {args.max_age_hours}h: {MODEL_CONTEXT_BRIEF}")

    bridge_path = PRIMARY_MEMORY / "_bridge.md"
    if bridge_path.exists():
        sections = extract_bridge_sections(bridge_path.read_text(encoding="utf-8", errors="replace"))
        required_sections = ["待提醒", "做了什么决定", "开了什么坑", "堵塞", "下一步（按优先级）", "关键上下文"]
        missing_sections = [section for section in required_sections if section not in sections]
        if missing_sections:
            errors.append(f"_bridge.md missing section(s): {', '.join(missing_sections)}")
        else:
            counts = {section: count_items(sections.get(section, "")) for section in required_sections}
            notes.append("bridge section item counts: " + ", ".join(f"{k}={v}" for k, v in counts.items()))

    last_context_path = PRIMARY_MEMORY / "_last-context.md"
    if last_context_path.exists():
        last_context = last_context_path.read_text(encoding="utf-8", errors="replace")
        for marker in ["## 自动检查警告", "## 本次关键决策与下一步", "## 恢复指引"]:
            if marker not in last_context:
                errors.append(f"_last-context.md missing marker: {marker}")

    print("context-brief doctor")
    for note in notes:
        print(f"OK  {note}")
    for warning in warnings:
        print(f"WARN {warning}")
    for error in errors:
        print(f"FAIL {error}")

    if errors:
        return 2
    if warnings and args.strict:
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Check context-brief/session-state health.")
    parser.add_argument("--fix-stale", action="store_true", help="remove already-applied delta files")
    parser.add_argument("--strict", action="store_true", help="exit non-zero on warnings")
    parser.add_argument("--max-age-hours", type=float, default=24.0, help="warn when generated files are older than this")
    args = parser.parse_args()
    return check(args)


if __name__ == "__main__":
    raise SystemExit(main())
