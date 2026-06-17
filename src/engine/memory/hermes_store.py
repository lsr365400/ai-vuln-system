"""Hermes-architecture long-term memory store.

File structure:
    data/memory/
        MEMORY.md              # Index (one line per entry, ~150 chars each)
        target-{hash}.md       # Target profile
        finding-{hash}.md      # Past vulnerability finding
        progress-{hash}.md     # Test progress snapshot
        noise-{hash}.md        # False-positive / noise pattern learned

Each memory file has YAML frontmatter:
    ---
    name: short-kebab-slug
    description: one-line summary
    metadata:
      type: target_profile | past_finding | test_progress | noise_pattern
      originSessionId: <uuid>
    ---
    <markdown body>
"""

import hashlib
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

MEMORY_TYPES = ("target_profile", "past_finding", "test_progress", "noise_pattern")


@dataclass
class MemoryEntry:
    name: str
    description: str
    mem_type: str
    body: str
    session_id: str
    file_path: Optional[Path] = None

    def to_frontmatter(self) -> str:
        return f"""---
name: {self.name}
description: {self.description}
metadata:
  type: {self.mem_type}
  originSessionId: {self.session_id}
  savedAt: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
---
"""

    def to_file_content(self) -> str:
        return self.to_frontmatter() + "\n" + self.body


class HermesStore:
    """Hermes-architecture memory store backed by markdown files."""

    def __init__(self, memory_dir: Path):
        self.dir = Path(memory_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.dir / "MEMORY.md"
        if not self.index_path.exists():
            self.index_path.write_text("# Memory Index\n\n", encoding="utf-8")

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_index_entries(self) -> list[str]:
        """Read MEMORY.md and return non-empty, non-header lines."""
        if not self.index_path.exists():
            return []
        lines = self.index_path.read_text(encoding="utf-8").splitlines()
        return [l for l in lines if l.startswith("- [")]

    def load(self, name: str) -> Optional[MemoryEntry]:
        """Load a single memory by slug name."""
        filepath = self._find_by_name(name)
        if not filepath:
            return None
        return self._parse_file(filepath)

    def search_by_type(self, mem_type: str) -> list[MemoryEntry]:
        """Return all memories of a given type."""
        results = []
        for entry_line in self.get_index_entries():
            # Extract filename from markdown link: "- [Title](file.md) — ..."
            try:
                filename = entry_line.split("](")[1].split(")")[0]
                filepath = self.dir / filename
                if filepath.exists():
                    entry = self._parse_file(filepath)
                    if entry and entry.mem_type == mem_type:
                        results.append(entry)
            except (IndexError, AttributeError):
                continue
        return results

    def get_target_profile(self, target_url: str) -> Optional[MemoryEntry]:
        """Load the target profile for a specific URL."""
        slug = _slugify(target_url)
        return self.load(f"target-{slug}")

    def get_past_findings(self, target_url: str) -> list[MemoryEntry]:
        """Load all past findings for a target URL."""
        slug = _slugify(target_url)
        all_findings = self.search_by_type("past_finding")
        return [f for f in all_findings if slug in f.name]

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def save(self, entry: MemoryEntry) -> Path:
        """Save a memory entry (create or update)."""
        existing = self._find_by_name(entry.name)
        filepath = existing or self.dir / f"{entry.name}.md"

        filepath.write_text(entry.to_file_content(), encoding="utf-8")
        entry.file_path = filepath
        self._update_index(entry)
        logger.info("memory saved: %s (%s)", entry.name, entry.mem_type)
        return filepath

    def save_target_profile(self, target_url: str, body: str, session_id: str) -> MemoryEntry:
        """Save or update a target profile."""
        slug = _slugify(target_url)
        entry = MemoryEntry(
            name=f"target-{slug}",
            description=f"Target profile for {target_url}",
            mem_type="target_profile",
            body=body,
            session_id=session_id,
        )
        self.save(entry)
        return entry

    def save_finding(self, target_url: str, title: str, body: str, session_id: str) -> MemoryEntry:
        """Save a vulnerability finding."""
        t_slug = _slugify(target_url)
        f_slug = _slugify(title)[:40]
        entry = MemoryEntry(
            name=f"finding-{t_slug}-{f_slug}",
            description=title,
            mem_type="past_finding",
            body=body,
            session_id=session_id,
        )
        self.save(entry)
        return entry

    def save_progress(self, target_url: str, body: str, session_id: str) -> MemoryEntry:
        """Save a test-progress snapshot."""
        slug = _slugify(target_url)
        entry = MemoryEntry(
            name=f"progress-{slug}",
            description=f"Test progress for {target_url}",
            mem_type="test_progress",
            body=body,
            session_id=session_id,
        )
        self.save(entry)
        return entry

    def save_noise(self, pattern_name: str, body: str, session_id: str) -> MemoryEntry:
        """Save a learned noise/false-positive pattern."""
        slug = _slugify(pattern_name)[:50]
        entry = MemoryEntry(
            name=f"noise-{slug}",
            description=f"Noise pattern: {pattern_name}",
            mem_type="noise_pattern",
            body=body,
            session_id=session_id,
        )
        self.save(entry)
        return entry

    # ------------------------------------------------------------------
    # Build context for session prompt
    # ------------------------------------------------------------------

    def build_memory_context(self, target_url: str) -> str:
        """Build a context string to inject into the system prompt.

        Includes: target profile + past findings + progress + recent noise patterns.
        """
        parts = []
        slug = _slugify(target_url)

        # Target profile
        profile = self.get_target_profile(target_url)
        if profile:
            parts.append(f"## 目标历史画像\n\n{profile.body}")

        # Past findings
        findings = self.get_past_findings(target_url)
        if findings:
            items = "\n".join(f"- **{f.description}**\n{f.body[:300]}" for f in findings)
            parts.append(f"## 历史漏洞发现 ({len(findings)}条)\n\n{items}")

        # Progress
        progress = self.load(f"progress-{slug}")
        if progress:
            parts.append(f"## 上次测试进度\n\n{progress.body}")

        # Recent noise patterns (global, not target-specific)
        noises = self.search_by_type("noise_pattern")
        if noises:
            items = "\n".join(f"- **{n.description}**: {n.body[:150]}" for n in noises[-5:])
            parts.append(f"## 已知误报模式（避免重复报告）\n\n{items}")

        return "\n\n".join(parts) if parts else ""


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _slugify(text: str) -> str:
    """Turn a URL or title into a filesystem-safe slug."""
    h = hashlib.sha256(text.encode()).hexdigest()[:12]
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in text)
    return safe[:40] + "-" + h


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from markdown text. Returns (meta, body)."""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        meta = {}
    return meta, parts[2].strip()


def _find_by_name(self, name: str) -> Optional[Path]:
    filepath = self.dir / f"{name}.md"
    return filepath if filepath.exists() else None


def _parse_file(self, filepath: Path) -> Optional[MemoryEntry]:
    try:
        text = filepath.read_text(encoding="utf-8")
    except Exception:
        return None
    meta, body = _parse_frontmatter(text)
    if not meta:
        return None

    metadata = meta.get("metadata", {})
    return MemoryEntry(
        name=meta.get("name", filepath.stem),
        description=meta.get("description", ""),
        mem_type=metadata.get("type", "unknown"),
        body=body,
        session_id=metadata.get("originSessionId", ""),
        file_path=filepath,
    )


def _update_index(self, entry: MemoryEntry) -> None:
    """Upsert an entry line into MEMORY.md."""
    lines = self.index_path.read_text(encoding="utf-8").splitlines()
    new_line = f"- [{entry.description}]({entry.name}.md) — {entry.mem_type}"

    # Replace existing line for same file
    marker = f"]({entry.name}.md)"
    replaced = False
    for i, line in enumerate(lines):
        if marker in line:
            lines[i] = new_line
            replaced = True
            break

    if not replaced:
        lines.append(new_line)

    # Keep under 200 lines (trim oldest non-header)
    while len([l for l in lines if l.startswith("- [")]) > 150:
        for i, line in enumerate(lines):
            if line.startswith("- ["):
                lines.pop(i)
                break

    self.index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# Monkey-patch the methods onto HermesStore (they reference self)
HermesStore._find_by_name = _find_by_name
HermesStore._parse_file = _parse_file
HermesStore._update_index = _update_index
