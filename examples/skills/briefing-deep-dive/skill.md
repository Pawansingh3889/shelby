---
name: Briefing Deep-Dive
description: Run an extra-thorough briefing covering weather, news, GitHub, Gmail, calendar, plus this week's PRs and any blocker patterns.
triggers:
  - deep briefing
  - full briefing
  - executive briefing
  - rundown
---

# Briefing deep-dive

When Captain asks for a "deep briefing", "full briefing", "executive briefing"
or "rundown", run the standard briefing PLUS:

1. Pull the past 7 days of GitHub PR activity (use github_pending then mention
   any PRs that have been open more than 3 days without movement).
2. Skim the Gmail unread list for anything that mentions a deadline, an "ASAP",
   or a meeting time. Surface those first.
3. Note any blocker patterns: if two different PRs are waiting on the same
   reviewer, name that reviewer.
4. End with one clarifying question that's specific to what you found, not the
   generic "what's the plan today".

Keep the whole thing under 8 sentences when spoken. Long enough to be useful,
short enough to listen to over coffee.
