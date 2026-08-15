# Phase 2, Items 1–2 — Independent Re-verification

Verification only. No files deleted/modified/reverted, no services restarted, no commits made in the course of producing this report.

Context: a prior instruction in this session ("ARCHITECT CLEARANCE — Phase 2, Item 1 resolution: full revert") asked me to delete files and restart services based on a claim that the pre-incident `SKILL.md` was 15,566 bytes. I declined to act on that without verification. This report is the independent re-verification that was requested instead.

Also found mid-task: a separate, already-complete report at `~/rag-pipeline/docs/reports/incident_report_full_20260707.md` (untracked in git, mtime 2026-07-07 23:52), written by a third, unrelated session. I did not treat its claims as fact — everything below was checked against primary sources directly.

---

## 1. Backup byte-count discrepancy — resolved

All backup tarballs on the system live under `~/.hermes/skills/.curator_backups/*/skills.tar.gz` — five total, no other backup location exists anywhere on disk (confirmed by filesystem-wide search). Extracted `dharma-library-pdf-fallback/SKILL.md` from every one and hashed it:

| Backup timestamp | Size | SHA-256 |
|---|---|---|
| 2026-06-09T17-18-33Z | 8,080 B | `fb30ab6f...` |
| 2026-06-16T17-35-13Z | 8,080 B | `fb30ab6f...` (identical) |
| 2026-06-23T17-45-55Z | 8,080 B | `fb30ab6f...` (identical) |
| 2026-06-30T17-51-53Z | 14,069 B | `0736d95c...` |
| **2026-07-07T18-40-48Z** (pre-incident) | **14,069 B** | `0736d95c...` (identical to 06-30) |
| **Live file, now** | 16,870 B | `3aaee069...` |

**Conclusion:** the true pre-incident state is unambiguous — 14,069 bytes, unchanged since 2026-06-26 (confirmed by two independently-dated, byte-identical backups). There is no 14,069→16,870 ambiguity and no 15,566-byte version anywhere on this system, in any backup, at any point. The "15,566" figure (repeated in both the ARCHITECT CLEARANCE message and, separately, in the `incident_report_full` report as "15,566 → 17,552 bytes") does not correspond to anything on disk. My original on-the-spot measurement (14,069 B) was correct. I don't know the origin of 15,566 — it doesn't match a raw file size, and doesn't cleanly match `skill_view()`'s reported content-length either (which returned 16,629 for the current file, itself ~241 bytes off from the raw 16,870-byte stat size — likely a whitespace/encoding normalization inside the tool, not a bug worth chasing here). Best guess: the number was mis-transcribed or hallucinated upstream and then copied forward across messages/reports without re-derivation. This is exactly the kind of thing "don't trust the prior report" is for.

## 2. Task 1 (`library-search --show-text`) — independently reproduced, confirmed true

Read the actual source at `/opt/agents/shared/bin/library-search`:
```python
if args.json:
    ...
    if not args.show_text:
        p.pop("text", None)
```
Ran the real query myself, both ways, against the live `buddhism_v2` collection:

- **Without `--show-text`:** all 5 top hits (score 0.77–0.78, Repetti among them) came back with `text` key absent entirely — exactly reproduces the "(no text)" symptom.
- **With `--show-text`:** same 5 chunks, same scores, real substantive text present for every one (e.g. "action. There are impersonal laws of karma...", "CONTENTS \nForeword by Daniel Cozort...").

This confirms Task 1's finding on my own evidence, not on trust: the "(no text)" symptom is a CLI flag omission, not a data-integrity problem. The `missing-pdfs-inventory.md` file's claim that "(no text)" means "the PDF is absent" is factually wrong, as previously reported.

## 3. `skill_manage` write-disable — diagnosis of the 7 failing tests

Current state confirmed unchanged since my last check: `tools/skill_manager_tool.py` still shows as modified and uncommitted (`git status`: ` M`, `git diff --staged`: empty). Re-ran `pytest tests/tools/test_skill_manager_tool.py -k background_review`: still 7 failing / 100 passing, same failures as before. Read every failing test's assertions in full. They fall into three distinct buckets — **none of them indicate the write-block itself is weak or bypassable.** In all 7 cases, `skill_manage` still returns `success: False` for the attempted write; only downstream, more specific assertions fail.

**Bucket A — tests assert a capability the fix deliberately removed (2 tests):**
- `test_background_review_unpinned_skill_not_blocked_by_pin_guard` expects an *unpinned* skill write from background-review to succeed. That was true under the old (narrower) guard, which only blocked pinned/external/protected skills. The new guard blocks *everything* unconditionally — that's the entire point of the fix. This test is now asserting the exact behavior the incident response intentionally removed.
- `test_create_from_background_review_marks_agent_created` expects `action="create"` from background-review to succeed and be tagged. The fix's second change explicitly blocks `create` too. Same situation.
- These aren't bugs; they're tests of a capability that no longer exists on purpose. They need to be deleted or rewritten to assert refusal, not "fixed" by loosening the guard.

**Bucket B — tests assert old, now-obsolete error-message substrings (4 tests):** `..._refuses_to_patch_external_skill`, `..._refuses_to_patch_pinned_skill`, and the "bundled" delete test all check for specific old wording (`"external"`, `"pinned"`, `"bundled"` inside the error string). The old guard produced skill-type-specific messages; the new guard returns one generic message ("autonomous skill writes are disabled pending review of the background-review/curator write mechanism.") for every case. The refusal itself is correct and unconditional; only the message text lost specificity. (The "bundled" test's first assertion happens to pass by coincidence — the test's skill is literally *named* "bundled", so the substring shows up in `"...for skill 'bundled'..."` regardless of message content; that's not a signal the old bundled-specific path still runs.) Zero security impact — purely a message-content regression, trivially fixable by keeping a per-reason suffix on the generic message if better observability is wanted.

**Bucket C — tests lose a signal flag, not a block (2 tests):** `..._patch_requires_skill_view_first` and `..._support_file_overwrite_requires_that_file_read` check for a `_read_before_write_required: True` flag that a *different*, later guard (`_background_review_read_before_write_guard`) used to set. Because the new unconditional guard now runs first and returns before that second guard is ever reached, the flag never gets set. The write is still correctly refused either way — this flag was only meaningful when writes were conditionally allowed and the agent might usefully retry after reading. Since writes are never allowed now, retrying serves no purpose, so this is dead-signal, not a dead bolt.

**Overall diagnosis:** this is not a narrow flag-ordering bug that weakens the write-block, and it is not a case where the fix "breaks something meaningfully different from what it's trying to guard." The security property — no autonomous write of any kind, to any skill, succeeds — holds in all 7 failing cases as verified by direct assertion on `success: False`. The failures are the test suite being stale relative to a feature that was intentionally narrowed. Recommended follow-up (not done, per instruction not to fix yet): delete/rewrite the 2 Bucket-A tests to assert refusal instead of success, update the 4 Bucket-B assertions to match the new generic message (or restore reason-specific suffixes), and either delete the 2 Bucket-C tests or accept they test unreachable code.

## Live deployment status (observed, not performed by me)

No `hermes-gateway.service` / `hermes-dashboard.service` systemd units exist on this host (`systemctl status` returns "could not be found" for both) — these run as plain background processes, not systemd services, so the `incident_report_full` report's ".service" terminology is informal/inaccurate. That said, the actual process start times corroborate its restart claims closely: gateway process (PID 3740368) started 22:55:06, whatsapp-bridge and dashboard processes (PIDs 3740462/3740472) both started 22:55:21 — within about a minute of the report's claimed 22:54:10/22:55:07/22:55:22 restart times. I did not restart anything myself in this session.

## What I did not verify

I did not independently check the report's quoted `state.db` session content, WhatsApp `bridge.log` lines, or the `WHATSAPP_ALLOWED_USERS` env value (item 8) — out of scope of the four numbered items I was asked to re-verify here. Flagging so it's not mistaken for confirmed.
