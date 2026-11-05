# Context Wrangler — Integration CLAUDE.md

Course: "AI Agents and the Real World" (Aalto, Future Interfaces Lab). Partner firm: Vertical (Zedocan Oy). This file is the integration contract: each team member built one pipeline stage independently; this document is what a Claude Code session needs to wire those six pieces into one coherent, runnable pipeline on NanoClaw. It states intent and constraints — stage internals (prompts, model logic) are each owner's, already built; don't rewrite them unless a contract below is actually violated.

**MVP deadline:** 6 Nov 2026. **Final presentation + demo:** 26 Nov 2026 (table-side demo for walking attendees). Inbox routing and public-knowledge handling (see §7) are explicitly out of MVP scope but in scope for the final deliverable — don't block MVP integration on them.

## 1. What this system does

Reads each client engagement's workspace (files a consultant produced or collected), distils de-identified, reusable lessons, and merges them into a shared lessons library — without ever letting client-confidential or personally-identifying content cross workspace boundaries. Maps to the brief's generic concepts: Context Cabinet (a workspace), Context Closet (the lessons library), Context Wrangler (this whole pipeline), Red/Amber/Green taxonomy, k≥3 confirmation gate, generalisation/elevation step, audit log with provenance.

Runs **headless**: deterministic orchestration code drives the pipeline and calls each stage's model like a function. No agent decides which files to read — this is a security property, not just a style choice, since it means injected document text can't redirect the run to read or exfiltrate something it shouldn't.

## 2. Pipeline and ownership

```
Raw workspace files
  -> Sanitizer          (strips prompt injections / unsafe content)
  -> Parser             (-> entries: specific + general per insight)        [Anselmi]
  -> Entry classifier    -- red --> deleted
                          -- green --> Entry aggregator
                          -- amber --> Evidence counter
  Evidence counter       -- k>=3 distinct workspaces --> Entry aggregator
                          -- k<3 --> quarantine --> re-checked as new entries arrive
  Entry aggregator  (reads lessons library + accepted entries -> candidate lessons)
  -> Checker        -- accept --> lessons library
                     -- block --> deleted / rejected.jsonl
```

| # | Stage | Reads | Writes | Sees `specific`? | Owner (as of 5 Nov 2026) |
|---|---|---|---|---|---|
| 1 | Sanitizer | Raw workspace files | Sanitized files | Yes | other member |
| 2 | Parser | Sanitized files, one workspace | `entries/<workspace>.jsonl` | Yes | Anselmi |
| 3 | Entry classifier | Entries, one workspace | `classified/<workspace>.jsonl` (green+amber, `general` only) | Yes — last stage that does | handed off from Anselmi's initial sketch to another member (currently the checker's owner) |
| 4 | Evidence counter | Amber entries, quarantine | Promoted entries to aggregator, or quarantine | No | other member |
| 5 | Entry aggregator | Green/promoted-amber entries, existing lessons | Candidate lessons | No | other member |
| 6 | Checker | Candidate lesson text only | Lessons library, or `rejected.jsonl` | No | other member (co-owns classifier with Anselmi's former sketch) |

**Isolation boundary = the classifier.** Up to and including it, every model call sees exactly one workspace, code-enforced. After it, only de-identified `general` text + a workspace id travel, so stages 4-6 can work cross-workspace without seeing raw client material. **The checker is an independent second gate** — required even if the classifier is perfect, because aggregation can recombine individually-safe entries into an unsafe lesson.

## 3. Data contracts (the integration surface)

These are the only shapes that should cross a stage boundary. If a stage's actual output doesn't match, fix the adapter at the boundary — don't let a mismatch silently change a downstream stage's assumptions.

**Entry** (parser → classifier):
```json
{
  "entry_id": "client-a#007",
  "workspace": "client-a",
  "sources": ["03-project-review.md", "05-email-dealer-feedback.eml"],
  "specific": "Dealers ignored the CarePlus service plan for two quarters because they earned no commission on it; uptake rose only after a EUR 25 per-plan bonus was introduced.",
  "general": "Dealers did not promote a new service offering until they were paid for selling it."
}
```
`entry_id` is `<workspace>#<seq>`, unique within a run only (entries are purged after each run, so it need not be stable across runs). `sources` is for tracing/debugging only. `specific` is read only by the classifier — no stage after it should ever see this field.

**Classified entry** (classifier → counter/aggregator):
```json
{"entry_id": "client-a#007", "workspace": "client-a", "general": "...", "label": "amber", "reason_code": "engagement_specific"}
```
`specific` is dropped here, permanently. Red entries are not written at all — only `entry_id` + `reason_code` go to the stats file.

**Quarantine record** (owned by the evidence counter):
```json
{"general": "...", "workspace": "client-a", "first_seen": "<run as_of_date>"}
```
Never `specific`. Storage format (JSONL vs SQLite) is the counter owner's call.

**Checker input:** `{id, candidate}` — candidate lesson text only, nothing upstream of the aggregator.

## 4. Persistence rules

| Store | Lifetime | Contents |
|---|---|---|
| Sanitized files | Run | Per workspace, overwritten each run |
| Entries | Run | `entries/<workspace>.jsonl`, purged after the run |
| Classified entries | Run | `classified/<workspace>.jsonl`, purged after the run |
| Quarantine | Across runs | `general` + `workspace` only |
| Lessons library | Across runs | `lessons/*.md`, lesson schema per aggregator's design |
| Logs and stats | Run | Per-stage, merged into submission files by the orchestrator |

Only the lessons library and quarantine persist across runs. Nothing confidential persists outside the lessons, and everything in the lessons has passed the checker. The integration layer must not add any other cross-run persistence without re-checking this invariant.

## 5. Runtime constraints (NanoClaw)

- Stages 1-3 run once per workspace, each in its **own NanoClaw container**, with only that workspace's folder mounted **read-only**. Code must check every path as a second line of defence beyond the mount itself.
- Engagement data lives in its own folder under `home`, with the relevant subdirectory mounted per agent — this is the concrete mount source for the rule above (decided by the team, 5 Nov 2026).
- Model calls use forced tool calls with JSON schemas, temperature 0, **one retry on invalid output, then fail closed**. Every stage must follow this — a stage that free-forms output or retries indefinitely is a contract violation to fix during integration, not a style choice to leave alone.
- API credentials route through NanoClaw's credential proxy. **Open item:** confirm direct SDK calls from inside a container actually route through it — verify before relying on it for a stage that calls the SDK directly rather than through a wrapper.
- Consultant-facing interface is **Slack**: triggering a run, receiving a report, resolving a conflict (decided by the team, 5 Nov 2026 — not yet built, needs its own integration point into the orchestrator, likely around run-trigger and checker-conflict-resolution).

## 6. Fail-closed, everywhere

Every stage must fail closed: an error means red, block, or no output — never a pass. Concretely: a parser file that fails contributes nothing (not a partial/guessed entry); a failed merge passes unmerged drafts through (safe, since duplicates within one workspace can't inflate a cross-workspace count); a classifier judge failure after one retry → red, `classifier_error`; a checker failure blocks. When integrating, treat any stage that silently defaults to "pass" on error as a bug to fix before wiring it into the pipeline, not an edge case to document around.

## 7. Scope: MVP vs final deliverable

**Out of MVP scope, in scope for the final deliverable** (confirmed 5 Nov 2026 — don't build these into the integrated MVP pipeline, but the orchestrator's design shouldn't actively preclude adding them later):
- **Inbox routing** — directing incoming items to the correct engagement workspace. For MVP, assume items already land in the right workspace folder.
- **Public knowledge handling** — material that should merge into lessons even without a green classification or k≥3 evidence (e.g. a cited public market fact). Relates to open question #1 below: the parser currently strips the exact figures that would make a public fact useful, so this needs its own path, not just a rule relaxation.
- **Per-run human review layer** — also explicitly out of MVP scope.

## 8. Submission/operations output

| Requirement | How it's met |
|---|---|
| `actions.log.jsonl` | Each stage writes `logs/<workspace>.<stage>.jsonl` (read/write/http) through one shared logging wrapper; the orchestrator concatenates them. **Open item:** who owns this concatenation — the orchestrator or a final step — not yet decided. |
| `run-info.json` | Each stage writes token counts, call counts, failures, duration to `stats/`; orchestrator sums them. |
| `checker-decisions.jsonl` | Checker runs standalone on `{id, candidate}` lines. |
| No duplicates on re-run | Run-scoped files overwritten atomically; lesson ids deterministic. |
| Checker failure lets nothing through | Covered by §6. |

## 9. Shared taxonomy (classifier ↔ checker)

Both gates share the same reason-code taxonomy so logs/audits read consistently across the boundary, even though prompts/examples (and ideally the model) are kept independent so one blind spot can't pass both gates. Key codes: `general_practice`/`process` (green), `engagement_specific` (amber), `identifier_residue`/`quasi_identifier`/`counterparty_term`/`price_cost_fee`/`secret_plan`/`cross_client`/`person_judgement`/`internal_incident`/`instruction_content`/`no_insight`/`classifier_error` (red). Full table and worked examples are in `memory/anselmi/course-project/20261105_context-wrangler-architecture.md` §4.4-4.5 — pull from there rather than re-deriving during integration.

## 10. Open questions to resolve during integration

Carried over from the 5 Nov architecture discussion, still unresolved — flag to the team rather than silently deciding one way during integration:

1. Does public-fact material (see §7) need its own path with a citation check, since the parser strips the numbers that make it useful?
2. Should a `general` that leaks get one more generalisation pass instead of being deleted outright?
3. Can the classifier be given a list of other clients' names (to catch `cross_client`) without breaking the isolation boundary in §2?
4. Should green-labelled entries also require evidence-counter corroboration before entering the library, or is single-workspace evidence acceptable for green?
5. How does the evidence counter decide two `general` texts state the same insight — embeddings, an LLM judge, or both? This sets how often amber gets promoted.
6. Should the classifier judge run twice and keep the stricter label (costs 2x, likely better red recall)?
7. Confirm the credential-proxy routing (§5).
8. Who owns the `actions.log.jsonl` merge (§8)?

## 11. Testing/evaluation conventions already in use

- **Parser:** hand-annotate 2-3 sample documents as gold entries and check known-good lessons are derivable; run twice on the same workspace and diff entries (instability flags prompt rules to sharpen); seed a canary string per workspace and assert it never appears in another workspace's model calls (isolation check).
- **Classifier:** needs its own fixture set (~40 invented entries, separate domain from the dev set, roughly a third per label, every red code covered at least twice — not derived from the real test pack, to avoid leaking tuning into evaluation). Target: Red mislabelled as green/amber = 0; Amber→green low; Green→amber and anything→red reported. Run fixtures twice, unstable labels mark rules to sharpen.
- Apply the same spirit (isolation canaries, dev/test separation, fail-closed assertions) when writing integration tests across stage boundaries, not just within a stage.

