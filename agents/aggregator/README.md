# Entry Aggregator

The Aggregator turns accepted entries into complete Markdown lesson candidates for the independent
Checker. It reads `accepted_entries.jsonl` and the approved lesson library, groups amber entries by
`cluster_id`, and treats every green entry as its own aggregation unit.

Only `general` entry text is sent to the model. The Aggregator never sends `specific`, workspace
names, entry ids, source file names, routes, K values, or cluster ids. Existing lessons and entry
text are treated as untrusted data.

## Inputs

Accepted entries have the five Parser fields plus `route`, `k`, and `cluster_id`:

```json
{"entry_id":"acme#003","workspace":"acme","sources":["02-notes.md"],"specific":"...","general":"A team committed before testing at scale.","route":"amber","k":3,"cluster_id":"c-example"}
```

The approved library is a directory of `L-*.md` files using the data pack's schema: YAML front
matter followed by `Lesson`, `Why`, and `Watch out` sections. Existing library records must have
`reviewed_by_checker: true`.

## Outputs

`checker-input.jsonl` uses the standalone Checker's exact input contract:

```json
{"id":"candidate-a1b2c3","candidate":"---\nid: L-0005\n..."}
```

The candidate is a complete lesson with `reviewed_by_checker: false`. The Checker or promotion step
is responsible for allowing it, changing that field to `true`, and writing the final Markdown file.

Client-identifying provenance never appears in the candidate. It remains in the private
`candidate-metadata.jsonl`, keyed by the same candidate id. Semantic `skip` decisions produce no
Checker input and are retained only in private state and decision logs.

## Run

```sh
python -m aggregator \
  --accepted data/accepted_entries.jsonl \
  --lessons-dir /path/to/lessons-library \
  --as-of-date 2026-06-30 \
  --actions-log submission/actions.log.jsonl
```

Other defaults under `data/` are `checker-input.jsonl`, `candidate-metadata.jsonl`,
`aggregator-state.jsonl`, and `aggregator-decisions.jsonl`. Use the corresponding CLI flags to
override them. The model defaults to `claude-sonnet-5-5` and can be changed with `--model`.

The optional real-model smoke test uses only invented records under `smoke_test/` and makes two API
calls on its first run:

```sh
uv run python smoke_test/run_smoke.py
```

## Safety and behavior

- Amber entries require `k >= 3` and a non-empty `cluster_id`.
- Every amber cluster must contain exactly the claimed number of distinct workspaces; incomplete or
  inconsistent cluster input is rejected.
- Green entries require `k: null` and `cluster_id: null`.
- Duplicate entry ids are rejected.
- Existing lessons are compared on every new unit so later cluster ids do not create duplicates.
- The same unchanged unit is processed once while the approved library is unchanged. A library
  change triggers comparison again, so a newly promoted lesson can suppress an older duplicate.
- Model failures, refusals, and malformed tool calls produce no candidate and no completed state,
  so a later run can retry.
- Candidate, metadata, state, and decision files are private and cannot be written below a
  `submission/` directory.
- File reads, writes, and each Claude request are written in the submission-compatible action-log
  format. Logs contain paths, ids, counts, reasons, and token usage, never entry or lesson text.
- For an update, the evidence count is the conservative lower bound `max(existing, new cluster)`;
  the shared library intentionally lacks engagement identifiers, so overlap cannot be disproved.

