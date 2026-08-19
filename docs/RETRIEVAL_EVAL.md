# Retrieval Eval Protocol

`paragraph retrieve` ships with parameters pinned to a labeled eval
(ParaNote corpus, recall@10 = 0.792 at pinning). Before re-tuning any of the
do-not-tune constants in `retrieve.py`, build an eval for **your** corpus so
changes are measured, not vibed.

## Method

Hand-label 15–25 queries drawn from real agent-session questions. Each query
declares expectations across the three content types:

| Field | Meaning | Scoring |
|---|---|---|
| `expected_nodes` | code-node labels that should rank in the top 10 | each scored by case-insensitive substring match on chunk labels |
| `expected_observations` | keyword group; any top-10 chunk containing any keyword counts | 0/1 for the group |
| `expected_docs` | keyword group matched only against `document` chunks | 0/1 for the group |
| `negative_nodes` | labels that must NOT appear despite keyword similarity | counted as negative hits |

**Recall@10** is the primary metric (found / expected, averaged over
queries); negative hits track false positives from keyword collision.

## Running

```bash
cp docs/examples/retrieval_eval.json myproject/retrieval_eval.json
# ...edit queries...
paragraph retrieve --eval myproject/retrieval_eval.json
```

Requires `paragraph enrich` to have built `graphify-out/vectors.db`, and a
local ollama.

## Labeling discipline (learned the hard way in ParaNote)

- **Label what is *reachable*, not what you wish ranked.** If a node sits at
  rank 131 for a query, expecting it only makes every run look like a
  regression. Audit ranks before labeling.
- **Answers do not always live in code.** Many "where is X" questions resolve
  to a doc chunk or an observation; label the type where the answer actually
  lives.
- **Never silently relabel.** When the corpus changes and labels rot, bump
  the eval version and document each change in the file's `changelog` —
  otherwise you cannot tell label corrections from system improvements.
- **Re-verify labels after any re-extraction or corpus prune** — a flat score
  on rotten labels is meaningless.
