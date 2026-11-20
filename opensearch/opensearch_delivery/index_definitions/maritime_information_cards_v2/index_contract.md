# maritime_information_cards_v2

Academic information cards stay in a separate index from laws and cases.

- Read alias: `maritime_information_cards_read`
- Identity: `card_id`; provenance and unknown page numbers are preserved.
- BM25: `retrieval_text`, `claim_or_fact`, `source_title`, `caveat`.
- Dense channel: pinned BGE-M3, CLS pooling, L2 normalization, 1024 dimensions,
  `retrieval_text`, at most 2048 tokens. Truncation is recorded in the vector sidecar.
- Fusion: existing law retrieval `rrf`, k=60, 50 candidates per lane; identical
  metadata filters apply in both lanes. Returned ranks identify each lane.
- A failed semantic channel yields an explicit partial BM25 response; an empty
  result is never used to conceal an unavailable service.
- Normalized `cards.jsonl`, `sources.jsonl`, precomputed vectors and identity/hash sidecars live in
  `opensearch/import/information_cards/`; these are derived data, never source edits.
- v1 migrates by validating all v2 records/vectors before an atomic alias switch.
  v1 is retained. No law or case alias is changed.
