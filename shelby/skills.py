"""OpenClaw-compatible skills loader for Shelby.

Reads skills from a directory tree:

    ~/.shelby/skills/
      check-pr/
        skill.md
      summarise-meeting/
        skill.md
      ...

Each `skill.md` carries YAML frontmatter (name, description, optional
trigger keywords) and a body of instructions. The loader surfaces each
skill as a callable MCP tool — when the model fires the tool, the
skill body is returned as the tool result, which guides the model's
next response.

This matches the OpenClaw skill format closely enough that ClawHub
skills should drop in with minimal edits. The skill body becomes
inline guidance for Shelby's next turn rather than executable code,
which is the right abstraction for a voice assistant where Captain
isn't sitting at a keyboard reviewing diffs.

Override the skills directory via SHELBY_SKILLS_DIR.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# Lightweight YAML parser: handles "key: value" lines plus simple list
# values written as "key: [a, b, c]" or "key:\n  - a\n  - b". Avoids a
# PyYAML dependency for this tiny use case.
_LIST_INLINE = re.compile(r"^\[(.*)\]$")


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split a markdown doc into (frontmatter dict, body).

    Recognises `---` fenced YAML at the top. If there's no fence, returns
    ({}, text) so plain markdown files still work.
    """
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    header = text[3:end].strip()
    body = text[end + 4:].lstrip("\n")

    out: dict = {}
    current_list_key: Optional[str] = None
    current_list: list[str] = []
    for raw in header.splitlines():
        line = raw.rstrip()
        if not line.strip():
            current_list_key = None
            continue
        # Continuation of a multiline list
        if current_list_key and line.startswith(" ") and line.lstrip().startswith("- "):
            current_list.append(line.lstrip()[2:].strip().strip('"\''))
            out[current_list_key] = current_list
            continue
        current_list_key = None
        current_list = []
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if not value:
            current_list_key = key
            current_list = []
            out[key] = current_list
            continue
        m = _LIST_INLINE.match(value)
        if m:
            items = [v.strip().strip('"\'') for v in m.group(1).split(",") if v.strip()]
            out[key] = items
        else:
            out[key] = value.strip().strip('"\'')
    return out, body


@dataclass
class Skill:
    slug: str           # directory name, used as tool slug
    name: str           # human-readable
    description: str
    body: str
    triggers: list[str] = field(default_factory=list)

    @property
    def tool_name(self) -> str:
        # MCP tool names must be word-safe.
        return "skill_" + re.sub(r"[^a-z0-9_]+", "_", self.slug.lower()).strip("_")


def _skills_dir() -> Path:
    override = os.environ.get("SHELBY_SKILLS_DIR")
    if override:
        return Path(override)
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "shelby" / "skills"
    return Path.home() / ".shelby" / "skills"


def discover() -> list[Skill]:
    """Scan the skills directory and return one Skill per subdirectory
    that contains a readable skill.md."""
    root = _skills_dir()
    if not root.is_dir():
        return []

    skills: list[Skill] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        md = entry / "skill.md"
        if not md.is_file():
            continue
        try:
            text = md.read_text(encoding="utf-8")
        except OSError:
            continue
        meta, body = _parse_frontmatter(text)
        name = str(meta.get("name") or entry.name).strip()
        description = str(meta.get("description") or "").strip()
        if not description:
            # Fall back to first non-empty body line as a quick description.
            first_line = next(
                (line for line in body.splitlines() if line.strip()),
                "skill (no description)",
            )
            description = first_line.strip().lstrip("#").strip()
        triggers_raw = meta.get("triggers") or meta.get("trigger") or []
        triggers = (
            triggers_raw
            if isinstance(triggers_raw, list)
            else [s.strip() for s in str(triggers_raw).split(",") if s.strip()]
        )
        skills.append(
            Skill(
                slug=entry.name,
                name=name,
                description=description,
                body=body.strip(),
                triggers=list(triggers),
            )
        )
    return skills


def make_skill_tools(skills: list[Skill]):
    """Wrap each Skill as an @tool-decorated async function.

    Tool invocation returns the skill body so the model can follow the
    instructions in its next turn — the OpenClaw pattern.
    """
    # Local import so this module doesn't pull in claude_agent_sdk
    # at import time (keeps tests + offline tooling lightweight).
    from claude_agent_sdk import tool

    decorated: list = []
    for skill in skills:
        # Capture skill by default arg to dodge late-binding in the loop.
        async def _impl(args, _body=skill.body):
            return {"content": [{"type": "text", "text": _body}]}

        # Each skill's description gets a leading marker so the model
        # knows it's a guidance skill, not a data-fetching tool.
        full_desc = (
            f"[skill] {skill.description}\n"
            "Call this when Captain's request matches "
            f"{', '.join(skill.triggers) or 'the skill name'}. "
            "The tool returns guidance for how to handle the task — "
            "follow the returned instructions in your next message."
        )
        wrapped = tool(skill.tool_name, full_desc, {})(_impl)
        decorated.append(wrapped)
    return decorated


def system_prompt_snippet(skills: list[Skill]) -> str:
    """One-line catalogue of available skills for the system prompt."""
    if not skills:
        return ""
    lines = ["AVAILABLE SKILLS (call the matching tool when Captain's request fits):"]
    for s in skills:
        trig = f" [triggers: {', '.join(s.triggers)}]" if s.triggers else ""
        lines.append(f"- {s.tool_name}: {s.description}{trig}")
    return "\n".join(lines)
