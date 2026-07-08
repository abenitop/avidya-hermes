# Hermes-Agent Manifesto v1.0

*The runtime counterpart to rag-pipeline's Gated Pipeline Manifesto (v2.0,
17 principles). That document governs a batch document-ingest pipeline;
this one governs a live, multi-platform, self-modifying agent runtime. The
two systems share no code and live in separate repos — this document does
not merge into or replace the other. Where a principle transfers directly,
it is renumbered and re-scarred with real hermes-agent incidents, not
copied. Where one doesn't transfer, that is stated explicitly rather than
forced. DRAFT — not yet committed. Every scar below is a real, verifiable
commit or documented incident in this repository; nothing here is
invented.*

---

## Part I — Architecture

### 1. State is the system

Every conversation's complete, authoritative history lives in `state.db`.
The in-memory agent object is a cache of that history for the current
process lifetime — not a second source of truth. If the two diverge, the
database is what the next turn, the next process restart, and the next
`hermes logs` inspection will see. A turn that happened only in memory and
never reached the database did not happen, as far as the rest of the
system is concerned.

This is a harder property to hold in a live agent runtime than in a batch
pipeline: writes happen continuously, under compaction, under concurrent
forks (background review, subagents), and across process restarts — there
is no natural "end of phase" checkpoint to verify against.

*Implementation:* `hermes_state.py` — schema, `_reconcile_columns()`,
per-turn persistence in `_flush_messages_to_session_db`.

**The scar:** `_flush_messages_to_session_db` deduped already-persisted
messages using a retained `{id(msg)}` set kept across turns. Once a
flushed dict was dropped from the live message list (scaffolding rewind,
in-place compaction) and garbage-collected, CPython could recycle its
memory address onto a brand-new assistant/tool dict. The new dict's `id()`
collided with the stale entry in the retained set, so the dedup logic
believed the real, never-before-seen turn had *already* been persisted —
and silently never wrote it to `state.db`. The bug was not a race, not a
crash, not an exception anywhere: a real conversation turn simply
vanished, indistinguishable from a turn that never happened, exactly the
failure mode this principle exists to name. Fixed by replacing the
identity-based (`id()`) dedup with an intrinsic marker stamped onto each
message dict, so no recycled address can alias a future message.
(`e4c6d1b22`, salvaged from #50372.)

---

### 2. Security by design

Every write-capable surface — skill files, cron job configs, MCP server
entries, dashboard file access, gateway session resume — is a potential
persistence or exfiltration vector, whether or not it was designed with
that in mind. A feature that seems purely functional (file browsing,
scheduled jobs, plugin config) is a security surface the moment it can be
reached by an untrusted or partially-trusted caller: a prompt-injected
tool call, a forked review agent, an unauthenticated network client, or a
scanner probing an exposed instance.

*Implementation:* `hermes_cli/mcp_security.py`, `tools/skill_manager_tool.py`'s
provenance guards, gateway session-ownership checks.

**The scar (three, because one is not enough to establish the pattern):**

- **The "hermes-0day" campaign, June 2026.** A real, externally-observed
  attack campaign found internet-exposed Hermes instances running with
  `--insecure` (which fully disabled the auth gate), used the exposed
  agent to write an MCP server entry with `command: bash` that appended an
  attacker SSH key to `authorized_keys`, and had cron/startup re-execute
  that entry every tick — a persistent backdoor installed entirely through
  a "just configuration" surface, no code execution bug required. Fixed by
  removing `--insecure` as a real bypass and adding persistence-surface
  validation to MCP config writes, checked at both save and spawn time.
  (`7726ce304`.)
- **Dashboard managed-files credential exfiltration, issue #57505.** The
  dashboard's file browser let an operator point its root at `HERMES_HOME`,
  at which point `.env`, OAuth token files, and webhook secrets became
  listable and downloadable through the API. The guard was fixed
  incrementally four times across weeks — each time closing a narrower gap
  the previous fix's basename-only check had missed (case sensitivity, then
  a directory-tree-scoped secret store the basename check couldn't see into)
  — because a guard that "mirrors" another security boundary drifts out of
  sync with it unless it's kept in lockstep. (`bc55c201c` → `1bcc52c14` →
  `43ec69cef` → `8b24376d6`, PR #58222.)
- **Gateway `/resume` cross-user session access.** An ownership check for
  resuming a persisted session existed for exactly one messaging adapter
  (Matrix). Every other adapter (Telegram, Slack, Discord...) had none, so
  an authenticated caller on any of those platforms could bind their
  session to a different user's transcript and read it. A single-platform
  guard was mistaken for a general one. Fixed by generalizing the
  Matrix-only check into an adapter-agnostic ownership check applied
  everywhere. (`c4f278c02` and its immediate hardening chain.)

---

### 3. Observability at every state

A component that fails must be visibly failing — not silently degraded,
not passing a health check that doesn't exercise the actual failure
surface, and not producing a technically-correct-but-useless error that
never names the real cause. "The process is still running" and "the health
check is green" are both compatible with a component that has stopped
doing its job.

*Implementation:* `agent/ssl_guard.py`, `gateway/run.py`'s
`_process_message_background` last-resort handler.

**The scar (two distinct failure shapes):**

- **A read-only health check missed a write-path corruption.** A corrupt
  `messages_fts` index made every `INSERT INTO messages` fail with
  `database disk image is malformed`, while base-table reads and
  `PRAGMA integrity_check` — the existing health probe — still reported
  the database healthy, because the probe never exercised the write path
  that was actually broken. Because the gateway reloads history from disk
  each turn, this produced apparent session amnesia: the live in-memory
  agent still held the real transcript, but the next turn saw stale or
  empty history. Traced to a single root cause only after three
  independent users filed what looked like unrelated bug reports.
  (`0b7128582`, closing #50504, #52165, #50576.)
- **An opaque low-level exception replaced a diagnosable one.** After a
  partial `hermes update`, a stale CA-bundle env var left TLS config
  pointing at a missing cert file. The first outbound HTTPS client raised
  a raw `FileNotFoundError`/SSL error that never named the broken CA path
  — a real stack trace, but not an actionable one. "There is an error
  message" is not the same claim as "the error message is observable" —
  fixed by validating each CA source explicitly and raising a typed error
  with a repair hint before the opaque one could surface.
  (`docs/rca-ssl-cacert-post-git-pull.md`, `agent/ssl_guard.py`.)

---

### 4. Data privacy as a constraint

A credential and the destination it's sent to are two independently
resolved values in this codebase — a provider name, a base URL, a
profile's secret scope. Whenever those two resolutions happen at different
places or different times, the gap between them is a place a real
credential can end up going to the wrong destination: an attacker-supplied
URL, an unrelated third-party API, another tenant's profile, or a debug
log with no redaction. This is the same shape of bug appearing over and
over, not a series of unrelated leaks.

*Implementation:* `agent/redact.py`'s `redact_sensitive_text()`,
per-profile secret scoping (`get_secret()`).

**The scar (a pattern, evidenced four times):**

- A prompt-injected cron job could set `provider=anthropic` with an
  attacker `base_url`, and the scheduler paired the *named* provider's
  real credential with the attacker's URL — sending a real Anthropic key
  to an attacker endpoint. Explicitly classified CWE-200/CWE-522, fixed at
  both the tool-creation boundary and a scheduler-runtime backstop.
  (`b24708eda`.)
- A provider without its own configured API key silently fell back to the
  user's real Anthropic OAuth token for any `anthropic_messages`-shaped
  third-party provider (MiniMax, DashScope, others) — a fallback scoped by
  an *excludelist* of known-bad destinations, which by construction cannot
  cover a destination nobody has thought of yet. Fixed by inverting to an
  allowlist: the fallback only fires for `provider == "anthropic"` itself.
  (`525caadd8`.)
- MCP server configs — a "just configuration" surface — could specify a
  shell interpreter plus network-egress tooling shaped like exfiltration
  (`curl ... -X POST --data-binary @.env`), requiring no code-execution bug
  at all. (`972a9885e`, #46083.)
- The same "profile secret scope was bypassed via a raw `os.environ` read
  instead of the scoped accessor" bug recurred across at least four
  distinct credential types — OAuth files, `auth.json`, a hooks directory,
  and GCP Vertex service-account credentials — in a multi-profile gateway
  process, each fixed independently before the pattern was named as a
  pattern. (`7f64cce96` and its cited predecessors.)

---

## Part II — Process

### 5. Tests are the specification

A fix that "looks right" and a fix that is *verified* against the full
test suite are different claims, and the gap between them is exactly where
collateral damage hides. Running only the tests a bug report mentioned
checks that the reported symptom is gone; it says nothing about what else
the change touched.

*Implementation:* `pytest` discipline referenced in nearly every fix commit
message in this repo's history (e.g. "Full tests/cron/ suite: 510 passed").

**The scar:** the 2026-07-07 skill-write incident's emergency fix
(`_background_review_write_guard`) was written and deployed to production
against the 7 tests a prior session had identified as affected. Running
the *full* `test_skill_manager_tool.py` suite — not just those 7 — surfaced
6 more failures in a completely unrelated feature (the weekly curator's
consolidation/archiving), which the narrowly-scoped fix had silently
disabled as collateral damage with no test coverage anywhere flagging it
until the full suite ran. See Principle 16 below for the full incident;
cited here specifically because the catch mechanism *was* this principle,
applied literally, not a new process invented in response.

---

### 6. The commit contract

Code that is live in production and code that is committed to version
control are supposed to be the same code. Every hour they diverge is an
hour where `git log`, `git blame`, and every review process built on top
of them are describing a system that no longer exists.

*Implementation:* one fix, one commit, one test-gate result in the
message — no exceptions for "emergency" changes.

**The scar:** the 2026-07-07 incident's emergency write-block fix was
correctly deployed to the live gateway process within the hour (a genuine
security response to an active unattended-write incident), but was left
**uncommitted** — with 7 known-failing tests — for the rest of that session
and into the next. For that entire window, `git status` on the production
host and the actual behavior of the running process told two different
stories, and nothing in the repository recorded *why* the running code
differed from HEAD. Closed the same session it was investigated: tests
fixed to 111/111 (target file) and 729/729 (full affected surface), fix
narrowed to the actual incident scope (Principle 16), and committed with a
message documenting the incident, the root cause, and the test-gate result
— `5bb158927`.

---

### 7. The self-checking loop

A single review pass, however careful, is a sample of one. The discipline
that catches what the first pass misses is not "review harder" — it's
running an independent second pass and treating what it finds as real,
even when the first pass was already shipped as "done."

This repository has no batch-pipeline phase gates in rag-pipeline's sense
— there is no equivalent of "Phase N is frozen, Phase N+1 requires an
explicit go-ahead," because hermes-agent ships continuously via PR review
rather than in discrete, operator-gated batches. The closest real analog
is a recurring, if informally named, convention across this codebase's
history: a deliberate, separate adversarial review pass after a primary
fix, explicitly labeled as such (and often numbered) in commit history —
not a single team's habit, but a pattern that recurs across unrelated
subsystems, months apart.

*Implementation:* no dedicated module — a review discipline, evidenced in
commit-message convention (`adversarial review round N`, `(ultracode
review)`, `(2nd ultracode pass)`, `address review findings`).

**The scar (the pattern, then the sharpest illustration of why it matters):**

- **MCP, three rounds, 2026-06-19.** The same subsystem went through three
  explicitly-labeled adversarial review passes within about twenty minutes
  of each other: round 1 found cache-parity, gating, and race-condition
  holes (`b6e2a54a9`); round 2 — run *after* round 1 was already fixed —
  found a stale-publish race and further parity holes round 1 had missed
  (`88d523220`); round 3 polished a generation-capture adjacency issue and
  a gateway contract note (`f3e967aae`). Three independent passes on the
  same code, each finding something real the previous pass didn't. This is
  the primary evidence the discipline is load-bearing, not anecdotal — the
  same shape recurs on unrelated subsystems months apart (LSP review
  findings — TOCTOU, a missing None guard, JSON safety, `e0a177802`;
  wallet persistence/policy-ordering/duplicate-wallet review issues,
  `07808ca7f`).
- **Billing step-up resume, 2026-07-01/02 — the sharpest single example of
  what a second pass catches.** A P1 billing fix split an auto-replay into
  a user-triggered `resume()` action. The *first* review pass (labeled
  "ultracode review" in this instance) caught and fixed several real
  money-path holes: a stale token-cache bug that could re-open a charge
  loop, a UI default that let a bare Enter move money, fail-open behavior
  on an unrecognized billing effect. A **second** pass on the same code,
  run separately, found a bug the first pass missed entirely: `resume()`
  had no re-entrancy guard, so a double-Enter could fire two replays and
  duplicate a real charge. The first pass was genuinely rigorous and still
  incomplete — the concrete argument for why a second independent pass is
  structural, not optional, even after the first pass already shipped as
  "done." (`ee93fc8e4` → `96ff097a6`.)

---

## Part III — Operational

### 8. External calls are always fallible

Every dependency this agent doesn't control — an LLM provider's API, a
messaging platform's API, an anti-abuse rate limiter tuned by a vendor who
owes no notice — will fail, throttle, or silently change behavior on its
own schedule. The failure mode to design against is not just "the call
returns an error" but "the call, or its retry logic, makes things worse
than a clean failure would have."

*Implementation:* `create_openai_client` chokepoint (`max_retries=0`),
per-adapter reconnect/backoff logic.

**The scar:** the OpenAI/aggregator client was built with the SDK default
of 2 automatic retries. The SDK's own 1-2 second backoff ignored the
provider's `Retry-After` header and retried *inside* hermes's own outer
conversation-loop retry logic — two independent retry layers compounding
against an already-rate-limited request bucket, burning through the quota
faster than a single well-behaved retry layer would have. The fix wasn't
"retry smarter," it was recognizing that two uncoordinated retry layers on
the same call is worse than one. (`0c372274c`, same bug class as a prior
Anthropic-client fix, #26293.)

---

### 9. Failures are first-class states

A failure that gets silently retried, deferred, or fast-forwarded forever
is not a failure that has been handled — it's a failure that has been
hidden from whoever would otherwise have noticed it. A stuck state needs a
name and a terminal outcome, not an infinite loop of "not yet."

*Implementation:* `cron/scheduler.py`'s `_get_due_jobs_locked`.

**The scar:** a recurring job whose real execution time exceeded its
configured `interval + grace` entered a permanent "missed → fast-forward →
skip" cycle — a guard originally written to prevent burst-catchup after
genuine gateway downtime, wrongly applied to a job that missed its slot
because it was *still running*, not because the process was down. A real
production job logged **42 consecutive "missed" events over 9 hours
without executing once** — every one of those 42 events was, technically,
logged; none of them was surfaced as the terminal, actionable state
("this job is stuck") it actually was. Fixed by keeping the fast-forward
(no burst) but letting the job actually run once it's due, rather than
skip again. (`6777a6bd6`, #33315.)

---

### 10. Concurrency through atomic transitions

Reading state, deciding what to do, and writing state back are three
separate operations unless something makes them one. Every gap between
"read" and "write" is a window where a concurrent actor can change what
you're about to overwrite, and the size of that window is often the
difference between "theoretically possible" and "happens in production."

*Implementation:* `gateway/status.py`'s `write_pid_file()` (atomic
`O_CREAT|O_EXCL`), `cron/scheduler.py`'s single-read job loading.

**The scar (two shapes of the same root cause):**

- `get_due_jobs()` called `load_jobs()` twice — once to filter due jobs,
  once to save updates — with no lock between the two reads. Another
  process could modify `jobs.json` in the gap, so the filter pass and the
  save pass operated on two different versions of the same file. Fixed by
  reading once and working from a single snapshot. (`1f0bb8742`.)
- Starting the gateway with `--replace` under concurrent invocation could
  leave two instances running simultaneously, because `write_pid_file()`
  used a plain overwrite — the second racer silently replaced the first
  process's PID record instead of detecting it. Fixed by making PID-file
  creation atomic (`O_CREAT|O_EXCL`) so exactly one concurrent invocation
  can win. (`cbe29db77`, #11718.)

---

### 11. Idempotency by design

An operation that produces a different result the second time it's called
with the same input isn't idempotent — it's a bug waiting for a retry,
a double-tap, or a duplicate delivery to trigger it. This matters most
exactly where retries are expected: message replay across strict API
providers, and any user-facing action fast enough to double-fire before
the UI can catch up.

*Implementation:* `repair_message_sequence`'s tool-call-id dedup,
`sanitize_api_messages`'s pre-request pass.

**The scar:** strict providers (DeepSeek, Kimi) reject a request payload
containing a duplicate `tool_call_id` with a hard 400. Two independent
code paths (`repair_message_sequence` and the final `sanitize_api_messages`
pass) each needed their own dedup logic, because a prior partial fix had
closed the gap in only one of the two chokepoints a duplicate ID could
reach. A related bug in the same area: the Codex Responses format gives a
tool call both an `id` and a distinct `call_id`; matching only on `id`
made a validly-keyed tool result look orphaned, silently dropping it and
producing the same class of 400 on every subsequent turn for the rest of
that session. (`dba585c17` #58327, `88f2c0caf` #58168.)

---

### 12. Self-healing infrastructure

Self-repair is not "automatically fix anything that looks wrong" — it's
"automatically correct a state that is provably, mechanically wrong,"
while leaving anything the system cannot distinguish from deliberate user
intent alone. The bar is: would a human reviewing this specific correction
in isolation agree it was never a legitimate state to begin with?

*Implementation:* `hermes_state.py`'s `_reconcile_columns()` (schema-drift
self-repair on startup), `tools/skills_sync.py`'s hash-based bundled-skill
sync, `tools/process_registry.py`'s `_reconcile_local_exit`.

**Not yet scarred** — included on general engineering grounds. All three
implementations already embody the "bounded, intent-respecting" discipline
rag-pipeline's own version of this principle argues for: `skills_sync.py`
explicitly skips any bundled skill the user has modified (hash mismatch =
leave it alone, never overwrite customization), and `_reconcile_columns`
only ever adds columns a live schema is missing, never removes data. No
incident was found where one of these mechanisms over-corrected or
silently destroyed user intent — worth stating as a real, working pattern
even without a failure behind it, per the instruction not to fabricate a
scar where none exists.

---

### 13. Simplicity as a constraint

**Not yet scarred** — included on general engineering grounds, not backed
by a specific "we over-built this and had to rip it out" incident found in
this repository's history or design docs. Flagging honestly rather than
manufacturing a retrospective: this principle is carried forward because
it's a real and load-bearing engineering value in this codebase (visible
in e.g. the narrow, single-purpose scope of individual guard functions
throughout `tools/skill_manager_tool.py`), not because a specific failure
proves it.

> **Rule:** No abstraction, config flag, or generalized code path should
> exist before a second real caller needs it. When a principle in this
> document can't cite a real incident, say so — an aspirational rule
> stated honestly is worth more than a fabricated scar borrowing this
> principle's authority.

---

### 14. API-first, test-first

**Not yet scarred** — included on general engineering grounds. Constructor
injection and monkeypatchable seams are pervasive in this codebase's test
suite (`tests/tools/test_skill_manager_tool.py`'s `_curator_pass`/
`_live_fork_pass` fixtures being one example this session relied on
directly) and are what made Principle 5's full-suite catch possible in the
first place — but that's evidence the pattern *works*, not evidence of an
incident where its absence caused a failure. Carried forward as a
precondition for Principle 5 and 16 both being enforceable at all, rather
than as an independently scarred principle.

---

### 15. Notifications close the loop — silence is failure

A background action that completes or fails with no notification reaching
anyone is functionally the same as an action that never ran, from the
operator's perspective — the gap between "it happened" and "someone knows
it happened" is exactly where an operator loses the ability to catch a
mistake before it compounds.

*Implementation:* `gateway/run.py`'s post-delivery callback chain,
platform-adapter notification filters.

**The scar (three, escalating):**

- A gateway's last-resort error-notification handler — the safety net for
  when something *else* has already gone wrong — had its own failure path
  as a bare `except: pass`. If the failure notice itself failed to send,
  there was zero trace anywhere, not even a log line. Salvaged from a
  stale PR that had identified this gap without landing a fix.
  (`f1cbe4308`, #54472.)
- Two features composing a `post_delivery_callback` chain for the same
  session produced a `_chained` wrapper declared `def` instead of
  `async def`; an async coroutine passed through it was constructed and
  silently discarded, never awaited — Python's only signal was a
  `RuntimeWarning` nobody was watching for, and the user simply never
  received the message. Confirmed as a real, previously-silent production
  symptom before the fix. (`74d2660ae`.)
- **2026-07-07, this incident:** the live per-turn `background_review`
  fork wrote unreviewed, factually incorrect content into
  `dharma-library-pdf-fallback/SKILL.md` mid-conversation. The write
  itself is Principle 16's scar; independently, **no notification of that
  write ever reached WhatsApp, any adapter log, or any other channel** —
  confirmed by searching `bridge.log` (raw and JSON-parsed), `agent.log`,
  `gateway.log`, and `errors.log` for the expected
  "💾 Self-improvement review: ..." delivery message across every window
  the writes actually occurred, all empty. A real, correctly-attributed
  write happened with zero observability of the fact that it happened —
  even a *correct* autonomous write with this same silence would still be
  a violation of this principle on its own. Root cause not yet
  investigated (flagged candidates: the post-delivery-callback release
  gate never firing for that session, or the `memory_notifications` config
  path) — held as future work, not yet fixed.

---

## Part IV — This Incident's Own Principle

### 16. Live Context Is Not a Trust Level

A system has one origin field and calls it trust. That is the mistake this
principle exists to prevent.

Every autonomous write capability eventually needs to answer one question:
*who is asking, and under what conditions were they verified?* It is
tempting to answer that question once, with a single tag —
`background_review`, `curator`, `automated` — and treat every caller
carrying that tag as equally trusted. This is wrong, and it is wrong in a
way that hides itself, because the tag looks like a security boundary
right up until two callers with the same tag turn out to have nothing else
in common.

A live conversational agent, mid-turn, reacting to whatever a user just
typed, is not the same caller as a scheduled batch process running on a
fixed cadence against pre-verified inputs — even if both were written by
the same team, live inside the same file, and share the same string in an
`origin` field. The first has no independent verification step between
"the agent believes something" and "the agent acts on it." The second, if
built correctly, has already had its dangerous actions constrained by a
separate, purpose-built guard, tested against a real prior failure.
Collapsing them into one trust level means a fix aimed at the first will
either fail to constrain it, or — as happened here — succeed at
constraining it while also disabling the second's unrelated, already-safe
behavior as collateral damage.

The fix is not "more logging" or "more review." It is a design discipline:
**origin tags must encode enough information to distinguish trigger
context, not just capability class.** A field that answers "is this
background_review" is not sufficient. A field that answers "is this a live
conversational fork, or a scheduled job operating on pre-verified input"
is. The distinction should exist before the first incident, not get
reconstructed from journal timestamps after the second.

*Implementation:* `tools/skill_provenance.py`'s `set_current_write_origin`
(capability class) and `set_current_write_platform`/`CURATOR_PLATFORM`
(trigger context), bound together in `agent/turn_context.py`'s per-turn
prologue; consumed by `tools/skill_manager_tool.py`'s
`_background_review_write_guard`.

> **Rule:** Any capability reachable by more than one caller must have an
> origin/trust field expressive enough to distinguish *live,
> conversation-triggered* invocation from *scheduled, pre-verified*
> invocation — not merely which subsystem is calling. When a new guard is
> added in response to an incident, it must be scoped to the specific
> trigger context that caused the incident, verified against every other
> caller sharing that capability, before being treated as complete. A
> guard that blocks more than the incident that motivated it is not a
> safety margin — it is an unreviewed, undocumented capability regression
> that happens to look like caution.

**The scar:** a live, per-turn agent fork wrote unverified, factually
incorrect content into a production skill file mid-conversation (see
Principle 15 for the accompanying notification-silence failure). The
reactive fix — block all writes carrying the fork's origin tag — was
correct for the incident and simultaneously, silently, disabled an
unrelated weekly consolidation job that had its own separate,
already-battle-tested, fail-closed guard from a prior incident (#29912).
The second failure was not caught by design — the new guard's scope was
never explicitly checked against every other caller sharing its origin tag
before being treated as complete. It was caught because the fix was
verified against the full test suite rather than only the tests it was
originally reported to affect, per Principle 5, and because the unexpected
failures were surfaced and investigated rather than silently resolved by
rewriting them to match the new, narrower behavior. The scope-check the
rule above requires did not exist beforehand; only the pre-existing
discipline of running the whole suite caught what the scope-check would
have caught directly. Resolved by adding the trigger-context field this
principle requires (`is_curator_platform()`), restoring the scheduled
job's pre-incident behavior exactly while leaving the live fork's block
unconditional and unchanged. (`5bb158927`.)

---

## Known Gaps

Documented honestly rather than papered over with a forced principle:

- **No unified audit engine.** rag-pipeline's `PipelineAuditor` — an
  independent, second view that cross-references two data sources for
  drift and repairs what it finds, run as a gate before every batch —
  has no equivalent here. Reconciliation exists, but it is scattered and
  subsystem-local: `hermes_state._reconcile_columns` (schema drift only),
  `cron/scheduler_provider.reconcile()` (Chronos one-shot arming only),
  `process_registry._reconcile_local_exit` (orphaned process handles
  only). None of them cross-reference against an independent second
  source of truth the way an audit engine does, and none run as a
  pre-flight gate. This is a real, named gap, not a principle — building
  one, if it's ever justified, is future work.
- **rag-pipeline's own "Technical Debt" principle does not exist to carry
  forward.** The current canonical `docs/MANIFESTO.md` in rag-pipeline is
  v2.0 with 17 principles; it has no "Technical Debt" principle. (An
  earlier reference to "v3.0, 18 principles" in this system was checked
  against the actual file and found to be stale/inaccurate — flagging
  here so the discrepancy doesn't get silently re-assumed later.)
- **Phase gates (rag-pipeline Principle 5) have no direct batch-pipeline
  analog.** This runtime ships continuously via PR review, not in
  discrete operator-gated phases. The closest real equivalent — the
  "ultracode review" second-pass discipline — is folded into Principle 7
  above rather than stated as its own weaker, adapted principle.

---

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| v1.0 | 2026-07-08 | Initial hermes-agent-native manifesto. 15 principles carried forward and re-scarred from rag-pipeline's Gated Pipeline Manifesto v2.0 (State, Security, Observability, Data Privacy, Tests, Commit Contract, Self-Checking Loop, External Calls, Failures as States, Concurrency, Idempotency, Self-Healing, Simplicity, API-First, Notifications/Silence — Phase Gates folded into Self-Checking Loop, no direct analog). Principle 16 ("Live Context Is Not a Trust Level") is native to this document, drafted from the 2026-07-07 skill-write incident. Audit Engine has no analog — recorded as a known gap, not a principle. |
