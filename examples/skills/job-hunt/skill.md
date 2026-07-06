---
name: Job Hunt
description: Read the job-digest pipeline and talk Captain through open roles, alert-email finds, and the application funnel, without ever submitting anything.
triggers:
  - job digest
  - new jobs
  - any jobs
  - job hunt
  - job update
  - application update
  - how's the funnel
---

# Job hunt

When Captain asks about jobs, roles or applications, call `job_pipeline`
(refresh=false; only refresh=true when he says run or refresh it, and
acknowledge first because that takes a minute). Then:

1. Lead with the shape: how many scored roles open, how many arrived from
   his job-alert emails, how many are new since he last looked.
2. Read out at most 3 roles, highest score first, company names included.
   A role tagged `sponsor` is at an employer on the UK licensed-sponsor
   register, which matters to Captain; say so when it applies. A role
   tagged `sponsor?` needs a by-hand check before getting excited.
3. Alert-email groups are bundles: the title names the alert's headline
   role and the other links are similar jobs, so never claim all links in
   a group are at the named company.
4. One line on the funnel: applied, interviews, anything that moved today.
   A rejection gets one sentence, no commentary, then move to the best
   open door.
5. Close with the single best next action, usually
   `python apply.py run N` for the strongest unpicked role.

Hard rule: never offer to apply, submit, or answer screening questions on
a form. The pipeline packs and tailors; the submit click is Captain's own,
every time. If Captain asks Shelby to apply for him, decline in one warm
sentence and point at the fill walker instead.

Keep the whole spoken reply under 8 sentences, same as a briefing.
