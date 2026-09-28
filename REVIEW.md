# Owner Review Queue

Pending decisions and reviews that need the owner's eyes — kept here so
they don't get lost in DESIGN.md's history. Counts refresh as night
batches run. No personal data in this file (counts and commands only).

## Pending

- [ ] **Crash detection enable** (data-supported since 2026-09-27):
  tuned gate re-scan = 14 would-fire / 6 insufficient of 20 EVENT
  clips; calibration over 45 NORMAL clips = 0 no-G false-fires.
  Action: set `[crash] enabled = true` in the local config; review
  crash candidates in the Provenance panel after the next batch.
  Ref: DESIGN → Crash detection → Re-validation 2026-09-27.
- [ ] **Intrusion-candidate review** (grew with each batch — refresh
  before starting): 2026-09-28 count = 790 unsuppressed priority-0.9
  intrusions (717 on done jobs; the rest on harvested/pending backlog
  that will keep producing more as batches run). Per-event review via
  the web Provenance panel + suppress button — never bulk-suppress
  (the 72 post-gate pending ones may be genuine; only job 420's 11
  were ever individually observed). `--restore` exists for regret.
  Ref: DESIGN → Person-flicker false intrusions → Note 2026-09-27.
- [ ] **Cloud comparison experiment** (~$0.50 one-time): write 15-20
  labeled cases (`vs eval --cases`, format: tests/fixtures/eval_cases.toml)
  — job-420 flicker negatives, a few known-good positives, 2-3 night
  hard cases — then one `vs eval --backend cloud` run (needs `[cloud]`
  endpoint + prices + a small `monthly_budget_usd`, key in
  `VS_CLOUD_API_KEY`) and `--compare-baseline` vs the local run. The
  night-stratum delta decides the cloud escalation tier. Ledger starts
  at $0 (2026-09-27).
- [ ] **Native report viewer parity review** (branch `native-report`,
  built 2026-09-28): A/B the native React report vs the iframe report
  behind `[web] native_report = true` — parity checklist in DESIGN →
  v2 thoughts → Fully dynamic report. Decide: merge, iterate, or drop.

## Reviewed / closed

- 2026-09-27 — index-faces archive re-run: validated; 66 faces / 38
  events / 2 clusters is the steady state (no new faces exist in the
  archive). Closed without action.
- 2026-09-27 — crash-scan step-1: 20/20 would-fire exposed the
  permissive gate; tuning applied same day (see Pending → enable).
