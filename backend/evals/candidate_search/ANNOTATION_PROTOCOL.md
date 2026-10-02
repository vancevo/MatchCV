# Candidate Search Gold Annotation Protocol

## Scope and privacy

- Keep real CV documents and annotations in `backend/evals/private/` or approved private storage; never commit them.
- Use pseudonymous candidate IDs. Recruiters may inspect the authorized source CV, but the exported search document must remain PII-free.
- Queries must come from realistic hiring needs and must not identify a particular candidate.

## Labels

| Grade | Meaning |
|---|---|
| 0 | Not relevant; important role/domain requirements do not match. |
| 1 | Partly relevant; some evidence matches, but the candidate is not a useful result for this query. |
| 2 | Relevant; useful recruiter result with the core requirements satisfied. |
| 3 | Highly relevant; strong direct evidence and should rank near the top. |

Grades 2–3 are positive for Precision, Recall, MRR, and calibration. nDCG uses all grades.

## Blind dual review

1. Two recruiters label every query–candidate pair independently. They do not see model rank, score, another recruiter's label, or candidate identity fields.
2. The annotation export records `reviewer_a`, `reviewer_b`, short evidence/reason, and timestamp separately.
3. Exact agreement needs no adjudication. Any disagreement is sent to a lead recruiter, who records `final_relevance` and an adjudication reason.
4. The lead recruiter resolves policy ambiguity, not model output. The final label must never be chosen merely because it agrees with the model.

## Promotion gate

A pilot may be renamed/promoted to `GOLD` only when:

- every included pair has a final 0–3 label;
- every disagreement has an adjudication record;
- the corpus and query IDs are unique and pass `dataset.schema.json`;
- the split prevents candidate/query leakage between tuning and final test sets;
- metrics are reported overall and by language and role family;
- dataset version, annotation policy version, and creation date are immutable.

Do not tune weights on the final test split. Compare keyword, dense, hybrid, and reranked modes using the same frozen dataset.
