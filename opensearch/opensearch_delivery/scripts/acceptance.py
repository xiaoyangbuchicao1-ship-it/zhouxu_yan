"""Reproducible six-question acceptance; outputs only under root/.ai-tmp."""
import argparse
from pathlib import Path
import sys
import time

import numpy as np
import maritime_index as app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=app.DEFAULT_ROOT)
    parser.add_argument("--index", default=app.INDEX)
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    root, run = args.root.resolve(), args.run_dir.resolve()
    app.require(run.is_relative_to(root / ".ai-tmp"), "Run directory must be under root/.ai-tmp")
    run.mkdir(parents=True, exist_ok=True)
    client = app.Client(root)
    meta = app.read_json(app.DELIVERY / "queries/demo_questions.json")
    vectors_path = app.DELIVERY / "queries/demo_vectors.npy"
    app.require(app.file_hash(vectors_path) == meta["vectors_sha256"], "Demo query vectors changed")
    query_vectors = np.load(vectors_path, allow_pickle=False)
    arrays = {k: np.load(root / f"import/{k}_embeddings.npy", mmap_mode="r") for k in ("article", "source")}
    rows = list(app.read_jsonl(root / "corpus/manifest/chunks.jsonl"))
    by_id = {r["chunk_id"]: r for r in rows}
    filters = [
        [{"term": {"kind": "article"}}], [], [], [],
        [{"term": {"kind": "article"}}, {"term": {"is_current": True}}],
        [{"term": {"kind": "source"}}],
    ]
    checks, results = {}, []
    for i, question in enumerate(meta["queries"]):
        result = app.hybrid(client, args.index, question["text"], query_vectors[i], filters[i])
        result["name"] = question["name"]
        expected_count = client.call("POST", f"/{args.index}/_count", {
            "query": {"bool": {"filter": filters[i]}}})["count"]
        checks[question["name"] + "_vector_fills_50"] = len(result["vector"]) == min(50, expected_count)
        # Independent exact cosine baseline over ALL eligible local vectors.
        q = query_vectors[i] / np.linalg.norm(query_vectors[i])
        scores = {k: arrays[k] @ q for k in arrays}
        eligible = [r for r in rows if all(r[next(iter(f["term"]))] == next(iter(f["term"].values())) for f in filters[i])]
        score_by_id = {r["chunk_id"]: float(scores[r["kind"]][r["embedding_row_index"]]) for r in eligible}
        threshold = sorted(score_by_id.values(), reverse=True)[min(10, len(eligible)) - 1]
        top = result["vector"][:10]
        # Ties: equivalent cosine scores count as nearest-neighbor matches.
        recall = sum(score_by_id.get(h["_id"], -2) >= threshold - 1e-6 for h in top) / len(top)
        result["exact_cosine_recall_at_10_tie_tolerant"] = recall
        checks[question["name"] + "_ann_recall_at_least_0_9"] = recall >= .9
        checks[question["name"] + "_filters_both_channels"] = all(
            all(hit["_source"][next(iter(f["term"]))] == next(iter(f["term"].values())) for f in filters[i])
            for channel in ("bm25", "vector") for hit in result[channel])
        for hit in result["results"]:
            app.require(hit["chunk"]["chunk_id"] in by_id, "Unknown returned chunk")
        results.append(result)
        print(question["name"] + f": ANN recall@10={recall:.2f}, wall={result['retrieval_wall_ms']} ms", flush=True)
    known = {"d648c06b2cbc9b6d#chunk-0001", "b56b1a10f518be90#chunk-0001", "70c25b90ab37c40a#chunk-0001"}
    checks["filing_known_article_in_hybrid_top10"] = any(r["chunk"]["chunk_id"] in known for r in results[0]["results"])
    checks["filing_current_article_in_hybrid_top10"] = any(r["chunk"]["chunk_id"] == "b56b1a10f518be90#chunk-0001" for r in results[4]["results"])
    started = time.perf_counter()
    followup = app.follow_version(client, args.index, "d648c06b2cbc9b6d#chunk-0001")
    followup["wall_ms"] = round((time.perf_counter() - started) * 1000, 2)
    checks["history_follows_to_current_article"] = (
        followup["targets"][0]["chunk_id"] == "b56b1a10f518be90#chunk-0001"
        and all(r["is_current"] is True for r in followup["targets"]))
    amendment = app.follow_version(client, args.index, "70c25b90ab37c40a#chunk-0001")
    checks["amendment_not_substituted_as_article"] = amendment["status"] == "document_research_required"
    shifted = app.follow_version(client, args.index, "28ddaefba8143aa4#chunk-0001")
    checks["known_renumbered_article_not_auto_substituted"] = shifted["status"] == "candidate_requires_review" and not shifted["targets"]
    current_base = app.hybrid(client, args.index, meta["queries"][0]["text"], query_vectors[0],
        [{"term": {"kind": "article"}}, {"term": {"is_current": True}}, {"term": {"document_role": "base_act"}}])
    checks["current_base_query_excludes_amendments"] = all(
        r["chunk"]["is_current"] is True and r["chunk"]["document_role"] == "base_act" for r in current_base["results"])
    result = {"index": args.index, "corpus_count": len(rows), "all_checks_passed": all(checks.values()),
              "checks": checks, "queries": results, "history_followup": followup,
              "renumbered_article_followup": shifted,
              "amendment_followup": amendment, "current_base": current_base,
              "scoring": "RRF(k=60); neural cross-encoder reranker not run; scores are not relevance probabilities"}
    app.write_json(run / "retrieval_results.json", result)
    app.require(all(checks.values()), "Acceptance failed; inspect retrieval_results.json before activation")
    print("All automated retrieval acceptance checks passed.")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    main()
