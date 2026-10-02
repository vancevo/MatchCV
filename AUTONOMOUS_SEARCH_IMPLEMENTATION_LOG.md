# Autonomous Candidate Search Implementation Log

Date: 2026-10-01 (Asia/Ho_Chi_Minh)

This log records the questions the implementation raised, the autonomous answers, and the reason for each decision. It is intentionally separate from runtime logs so product and engineering decisions remain reviewable.

## Checklist at start

| Priority | Work item | Starting state | Target in this run |
|---|---|---|---|
| P0 | Fix default-score inflation | Implemented, uncommitted | Verify and preserve |
| P0 | Separate similarity, ranking score, confidence | Implemented, uncommitted | Verify and preserve |
| P0 | Gold dataset and metrics | Metrics + synthetic seed implemented | Add private pilot export; do not invent recruiter labels |
| P1 | pgvector top-K | HNSW index exists, API still scans in Python | Add dialect-aware top-K repository |
| P1 | Hybrid dense + lexical | Dense + token overlap implemented | Add PostgreSQL FTS and keep portable fallback |
| P1 | Chunking and evidence | Not implemented | Add versioned PII-free chunks, embeddings, and source evidence |
| P1 | Reranker | Not implemented | Add optional free local multilingual cross-encoder with fail-open fallback |
| P2 | Calibration | Not implemented | Add feedback-driven Platt calibration with minimum-sample gate |
| P2 | Feedback and production monitoring | Not implemented | Add immutable search events/results, feedback API, and metrics |

## Autonomous questions and decisions

### Q1. What counts as relevant?

**Decision:** relevance grades remain 0–3; grades 2 and 3 are positive for Precision, Recall, MRR, and calibration. nDCG keeps all four grades. A hard-filter violation cannot be treated as a positive result.

**Reason:** this preserves graded judgments while giving binary metrics and calibration a stable target.

### Q2. Can the existing 24 CV embeddings be called a gold dataset?

**Decision:** no. They may be exported as a PII-free pilot annotation corpus, but remain `UNREVIEWED` until a human recruiter labels query–CV pairs.

**Reason:** automatically generated labels would make the evaluation circular and falsely inflate quality claims.

### Q3. Where should real evaluation data live?

**Decision:** private storage outside Git. Git contains schemas, synthetic fixtures, manifests, and scripts only. `backend/evals/private/` is ignored.

**Reason:** CVs and recruiter judgments are sensitive personal/employment data.

### Q4. Which lexical retrieval method should be used?

**Decision:** PostgreSQL full-text search with the `simple` configuration in production, with deterministic Unicode token overlap for SQLite/local fallback.

**Reason:** it is free, already available with PostgreSQL, operationally simpler than a second sparse-vector stack, and preserves exact technology names.

### Q5. How should dense and lexical retrieval be combined?

**Decision:** retrieve an oversampled candidate set from both channels, retain inspectable dense and lexical components, then apply the versioned hybrid score. Avoid claiming it is calibrated probability.

**Reason:** score fusion is easy to audit and the existing evaluation runner can compare it against dense and keyword baselines.

### Q6. How should CVs be chunked?

**Decision:** create stable chunks from PII-free structured sections (roles, skills, experience, projects, impact, education, summary). Each chunk stores section, ordinal, content hash, model revision, vector, and status.

**Reason:** section chunks prevent long-CV dilution and let evidence point to the exact stored source text.

### Q7. Which reranker should be preferred?

**Decision:** an optional local multilingual BGE cross-encoder, loaded only from disk, reranking a bounded top-N batch. If absent, disabled, or errors, search continues with hybrid results.

**Reason:** the official FlagEmbedding documentation recommends `BAAI/bge-reranker-v2-m3` for multilingual and efficient reranking. Local-only operation avoids per-request cost and external CV disclosure.

### Q8. When may confidence be shown?

**Decision:** only after at least 30 human-labeled search-result feedback records for a tenant, using a versioned Platt calibrator. Before that, confidence stays `null`.

**Reason:** a score-to-probability mapping without labels is not calibration.

### Q9. What telemetry may be stored?

**Decision:** store query hash, filters, versions, timing, result scores, rank, fallback, and user feedback. Do not persist raw query text or raw CV text in telemetry tables.

**Reason:** these fields support monitoring and calibration without duplicating sensitive search content.

### Q10. What should a not-yet-ready pgvector row contain?

**Decision:** store a zero vector with the configured dimension and exclude it by `status != READY`; never write `[]` into PostgreSQL vector columns.

**Reason:** the real PostgreSQL verification exposed `vector must have at least 1 dimension` during autoflush. A dimension-correct placeholder preserves the non-null schema and cannot enter retrieval.

### Q11. Should lexical search require every query term?

**Decision:** no. Build a sanitized OR `to_tsquery('simple', ...)`, retrieve lexical top-K, normalize its rank score, and union it with dense top-K.

**Reason:** an AND query dropped an otherwise excellent skills chunk merely because it did not contain the generic word “backend”. OR retrieval improves candidate generation; later ranking and reranking restore precision.

### Q12. Should the downloaded reranker be enabled locally?

**Decision:** yes, with `max_length=256`, batch size 8, top-K 20, weight 0.70, CPU inference, and fail-open fallback. Deployment remains explicitly feature-flagged.

**Reason:** it is free and local. Measured warm top-20 model time was about 0.44 seconds; a full warm search over the current SQLite corpus was about 1.40 seconds.

## External references consulted

- BAAI/FlagEmbedding official reranker documentation: multilingual `BAAI/bge-reranker-v2-m3`, query–passage cross-encoder scoring, local Transformers inference.
- BAAI BGE-M3 model documentation: multilingual dense retrieval and recommended hybrid retrieval plus reranking pipeline.

## Completion notes

| Priority | Work item | Result |
|---|---|---|
| P0 | Fix default-score inflation | Complete: inactive criteria contribute neither points nor weight; tests cover this contract. |
| P0 | Separate similarity, ranking score, confidence | Complete: raw dense/lexical/reranker scores are inspectable; `ranking_score` is labeled ranking-only; confidence remains null until calibrated. |
| P0 | Gold dataset and metrics | Framework complete; synthetic seed and Precision@5, Recall@10/20, MRR, nDCG@10, slices and hard-filter violation metrics run. The 24 current CVs were exported as a private unreviewed pilot, not misrepresented as gold. |
| P1 | pgvector top-K | Complete: PostgreSQL HNSW top-K query and index verified against a real `pgvector/pgvector:pg16` database. |
| P1 | Hybrid dense + lexical | Complete: dense and PostgreSQL FTS top-K are unioned, with portable SQLite token fallback. |
| P1 | Chunking and evidence | Complete: 76 PII-free section chunks were built for the current 24-CV SQLite corpus; results point to exact chunk IDs/text/sections. |
| P1 | Reranker | Complete: pinned `BAAI/bge-reranker-v2-m3` installed locally and enabled; errors retain hybrid order. |
| P2 | Calibration | Implementation complete and deliberately gated: Platt confidence activates only after 30 human judgments containing both classes. Current confidence is correctly null. |
| P2 | Feedback and production monitoring | Complete: immutable search/result events, recruiter relevance UI/API, query-hash privacy, latency/fallback/feedback metrics and deletion handling. |

Verification performed:

- Backend: `138 passed`.
- Frontend: production Next.js build passed TypeScript and static generation.
- SQLite end-to-end: semantic + lexical + local reranker returned HTTP 200; warm request about 1.395 seconds; 76 chunks READY.
- PostgreSQL: migrations 0001–0019 passed on a clean real pgvector database; dense and lexical chunk retrieval returned the expected skills evidence; HNSW index scan was confirmed by `EXPLAIN`.
- Synthetic seed comparison: keyword MRR 0.8611 / nDCG@10 0.7991; hybrid and reranked MRR 1.0 / nDCG@10 0.9029. These are smoke-test numbers, not production claims.

Intentionally open human-data gates:

- The private pilot has 24 CVs and 1 job-derived query, all labels null/`UNREVIEWED`. Two recruiters must label independently; a lead recruiter adjudicates disagreements before the dataset is promoted to `GOLD`.
- Production confidence is inactive until at least 30 valid judgments exist. The rebuild API returns HTTP 409 before that threshold rather than inventing confidence.
