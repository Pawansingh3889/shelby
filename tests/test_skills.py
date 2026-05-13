"""Smoke tests for shelby.skills (OpenClaw-compatible loader)."""
from __future__ import annotations


def _write_skill(dir_path, slug, frontmatter, body):
    skill_dir = dir_path / slug
    skill_dir.mkdir()
    md = skill_dir / "skill.md"
    md.write_text(f"---\n{frontmatter}\n---\n{body}\n", encoding="utf-8")
    return md


def test_discover_empty_dir(tmp_skills_dir):
    from shelby import skills
    assert skills.discover() == []


def test_discover_single_skill(tmp_skills_dir):
    from shelby import skills
    _write_skill(
        tmp_skills_dir,
        "stove-checker",
        "name: stove checker\ntriggers: [stove, oven]",
        "Remind Captain to check the oven temperature.",
    )
    found = skills.discover()
    assert len(found) == 1
    s = found[0]
    assert s.slug == "stove-checker"
    assert "stove" in s.triggers
    assert "oven temperature" in s.body.lower()


def test_discover_multiple_skills_sorted(tmp_skills_dir):
    from shelby import skills
    _write_skill(tmp_skills_dir, "zebra", "name: Zebra", "body")
    _write_skill(tmp_skills_dir, "alpha", "name: Alpha", "body")
    found = skills.discover()
    slugs = [s.slug for s in found]
    assert slugs == sorted(slugs)


def test_skill_without_frontmatter(tmp_skills_dir):
    from shelby import skills
    skill_dir = tmp_skills_dir / "no-front"
    skill_dir.mkdir()
    (skill_dir / "skill.md").write_text("just a body, no frontmatter", encoding="utf-8")
    found = skills.discover()
    assert len(found) == 1
    assert found[0].slug == "no-front"


def test_skill_caching_via_discover_call(tmp_skills_dir):
    from shelby import skills
    _write_skill(tmp_skills_dir, "first", "name: First", "one")
    a = skills.discover()
    b = skills.discover()
    # Same content; behaviour is consistent across calls.
    assert [s.slug for s in a] == [s.slug for s in b]
