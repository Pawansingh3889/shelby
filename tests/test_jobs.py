"""Tests for the job-digest bridge (shelby/jobs.py)."""
from __future__ import annotations

import json
import sqlite3

from shelby import jobs


def make_digest_dir(tmp_path):
    """A fake job-digest checkout with a seeded seen.db."""
    d = tmp_path / "job-digest"
    d.mkdir()
    (d / "digest.py").write_text("# marker\n", encoding="utf-8")
    con = sqlite3.connect(d / "seen.db")
    con.execute(
        "CREATE TABLE seen (url TEXT PRIMARY KEY, first_seen TEXT, "
        "title TEXT, company TEXT, payload TEXT)"
    )
    con.execute(
        "CREATE TABLE shown (run_date TEXT, url TEXT, score INT, "
        "PRIMARY KEY (run_date, url))"
    )
    con.execute(
        "CREATE TABLE apps (url TEXT PRIMARY KEY, slug TEXT UNIQUE, "
        "company TEXT, title TEXT, stage TEXT, created TEXT, updated TEXT)"
    )

    def seed(url, title, company, source, score):
        payload = json.dumps({"source": source, "title": title, "company": company})
        con.execute(
            "INSERT INTO seen VALUES (?, '2026-01-01T00:00:00', ?, ?, ?)",
            (url, title, company, payload),
        )
        con.execute("INSERT INTO shown VALUES ('2026-01-02', ?, ?)", (url, score))

    seed("https://a.example/1", "Analytics Engineer", "Monzo", "greenhouse:monzo", 69)
    seed("https://a.example/2", "Data Engineer", "Acme", "remotive", 55)
    seed("https://l.example/3", "(from the alert: Junior Analyst at Veeam)", "", "linkedin-alert", 40)
    seed("https://l.example/4", "(from the alert: Junior Analyst at Veeam)", "", "linkedin-alert", 40)

    con.execute(
        "INSERT INTO apps VALUES ('https://a.example/9', 'x-corp-de', 'X Corp', "
        "'Data Engineer', 'applied', '2026-01-01T00:00:00', '2026-01-01T00:00:00')"
    )
    con.execute(
        "INSERT INTO apps VALUES ('https://a.example/8', 'y-corp-da', 'Y Corp', "
        "'Data Analyst', 'picked', '2026-01-01T00:00:00', '2026-01-01T00:00:00')"
    )
    con.commit()
    con.close()
    return d


def test_find_digest_dir_env_override(tmp_path, monkeypatch):
    d = make_digest_dir(tmp_path)
    monkeypatch.setenv("SHELBY_JOBDIGEST_DIR", str(d))
    assert jobs.find_digest_dir() == d


def test_find_digest_dir_rejects_wrong_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELBY_JOBDIGEST_DIR", str(tmp_path))  # no digest.py
    assert jobs.find_digest_dir() is None


def test_read_state_summarises(tmp_path):
    d = make_digest_dir(tmp_path)
    text = jobs.read_state(d)
    assert "2 scored roles open" in text
    assert "2 roles from job-alert emails" in text
    # highest score first
    assert text.index("Analytics Engineer") < text.index("Data Engineer - Acme")
    assert "[69] Analytics Engineer - Monzo" in text
    # alert links grouped, not itemised
    assert "2 link(s) (from the alert: Junior Analyst at Veeam)" in text
    # funnel counts by stage
    assert "1 applied" in text
    assert "1 picked" in text
    # the submit stays human
    assert "submit click stays with Captain" in text


def test_read_state_without_db(tmp_path):
    d = tmp_path / "job-digest"
    d.mkdir()
    (d / "digest.py").write_text("# marker\n", encoding="utf-8")
    assert "never run" in jobs.read_state(d)
