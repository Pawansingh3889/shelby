"""Job-hunt pipeline bridge.

Shelby does not implement job search itself; it drives Captain's
job-digest checkout (https://github.com/Pawansingh3889/job-digest), a
separate tool that pulls postings from job-board APIs, harvests the
job-alert emails LinkedIn and Indeed send, scores everything against a
profile (sponsor register, salary floor, junior-first), and tracks the
application funnel in a local SQLite file.

This module knows how to find that checkout and read its state back as
a compact text summary the voice loop can speak. The brain's
job_pipeline tool wraps it and optionally reruns the fetch first.
Override the checkout location with SHELBY_JOBDIGEST_DIR.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Optional

_PROBE_DIRS = (
    Path.home() / "job-digest",
    Path.home() / "Projects" / "job-digest",
    Path.home() / "work" / "job-digest",
    Path("C:/Projects/job-digest"),
)


def find_digest_dir() -> Optional[Path]:
    """The job-digest checkout: SHELBY_JOBDIGEST_DIR wins, else probe the
    usual places. A directory only counts if digest.py is in it."""
    override = os.environ.get("SHELBY_JOBDIGEST_DIR", "").strip()
    if override:
        p = Path(override).expanduser()
        return p if (p / "digest.py").exists() else None
    for p in _PROBE_DIRS:
        if (p / "digest.py").exists():
            return p
    return None


def read_state(digest_dir: Path, max_roles: int = 8) -> str:
    """Summarise the pipeline's current state from seen.db: the latest
    digest's scored roles, the roles harvested from job-alert emails, and
    the application funnel. Pure read, no network."""
    db_path = digest_dir / "seen.db"
    if not db_path.exists():
        return (
            "The job pipeline has never run here. Run 'python digest.py' in "
            f"{digest_dir} once, then ask again."
        )
    con = sqlite3.connect(db_path)
    try:
        run = con.execute("SELECT MAX(run_date) FROM shown").fetchone()[0]
        if not run:
            return "No digest recorded yet. Run the pipeline first."
        rows = con.execute(
            "SELECT sh.score, se.title, se.company, se.payload FROM shown sh "
            "JOIN seen se ON sh.url = se.url WHERE sh.run_date = ? "
            "ORDER BY sh.score DESC, sh.url",
            (run,),
        ).fetchall()
        boards, alerts = [], {}
        for score, title, company, payload in rows:
            try:
                source = json.loads(payload or "{}").get("source", "")
            except json.JSONDecodeError:
                source = ""
            if str(source).endswith("-alert"):
                key = title or "(job-alert link)"
                alerts[key] = alerts.get(key, 0) + 1
            else:
                boards.append((score, title, company, source))
        stages = dict(
            con.execute("SELECT stage, COUNT(*) FROM apps GROUP BY stage").fetchall()
        )
        moved = con.execute(
            "SELECT company, title, stage FROM apps "
            "WHERE substr(updated, 1, 10) = date('now') "
            "ORDER BY updated DESC LIMIT 5"
        ).fetchall()
    except sqlite3.OperationalError as exc:
        return f"The pipeline database exists but could not be read: {exc}."
    finally:
        con.close()

    lines = [
        f"Job digest {run}: {len(boards)} scored roles open, "
        f"{sum(alerts.values())} roles from job-alert emails."
    ]
    for score, title, company, source in boards[:max_roles]:
        lines.append(f"  [{score}] {title} - {company or source}")
    if len(boards) > max_roles:
        lines.append(f"  ...and {len(boards) - max_roles} more scored roles.")
    if alerts:
        lines.append(
            "Alert-email groups (the title names the alert's headline role; "
            "the links need a login to read):"
        )
        for key, n in sorted(alerts.items(), key=lambda kv: -kv[1])[:6]:
            lines.append(f"  {n} link(s) {key}")
    if stages:
        order = ("applied", "interview", "offer", "picked", "rejected")
        funnel = ", ".join(f"{stages[s]} {s}" for s in order if s in stages)
        lines.append(f"Funnel: {funnel}.")
    if moved:
        lines.append(
            "Moved today: "
            + "; ".join(f"{c or t} -> {s}" for c, t, s in moved)
            + "."
        )
    lines.append(
        "Next: 'python apply.py run N' packs a role and walks the form; "
        "the submit click stays with Captain."
    )
    return "\n".join(lines)
