"""Portable OpenSearch import, validation and hybrid retrieval. Python 3.10+."""
from __future__ import annotations

import argparse
import base64
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from difflib import SequenceMatcher
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import numpy as np

DELIVERY = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = DELIVERY.parent
INDEX = "maritime_law_chunks_v2"
SCHEMA_DIRECTORY = "maritime_law_chunks_v2"
READ_ALIAS = "maritime_law_chunks_read"
WRITE_ALIAS = "maritime_law_chunks_write"
VERSION_FIELDS = ("version_relation", "version_target_document_id", "version_target_parent_id")
INPUTS = ["corpus/manifest/chunks.jsonl", "corpus/manifest/document_registry.jsonl",
          "corpus/manifest/corpus_manifest.json", "import/embedding_manifest.json"] + [
    f"import/{kind}_embeddings{suffix}" for kind in ("article", "source")
    for suffix in (".npy", "_meta.jsonl")]


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def read_jsonl(path):
    with Path(path).open(encoding="utf-8-sig") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def rooted(root, relative):
    require(isinstance(relative, str) and relative and "\\" not in relative,
            f"Expected portable relative path: {relative!r}")
    path = PurePosixPath(relative)
    require(not path.is_absolute() and ":" not in relative and ".." not in path.parts,
            f"Invalid corpus path: {relative!r}")
    result = (root / relative).resolve()
    require(result.is_relative_to(root.resolve()), "Path escapes corpus root")
    return result


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def project(row):
    source = dict(row)
    if source.get("version_date") == "":
        source["version_date"] = None
    return source


def source_hash(row):
    return hashlib.sha256(json.dumps(project(row), ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def definition():
    folder = DELIVERY / "index_definitions" / SCHEMA_DIRECTORY
    return {"settings": read_json(folder / "settings.json"), "mappings": read_json(folder / "mappings.json")}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("OpenSearch credential-bearing redirects are disabled")


class Client:
    def __init__(self, root, url="https://localhost:19200", ca_file=None):
        parsed = urllib.parse.urlparse(url)
        require(parsed.scheme == "https" and parsed.hostname and not parsed.username and
                not parsed.query and parsed.path in ("", "/"), "Use an HTTPS OpenSearch origin")
        env_path = root / "opensearch_delivery/dev/.env"
        env = {}
        for raw in env_path.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                env[key.strip()] = value.strip().strip("\"'")
        password = env.get("OPENSEARCH_INITIAL_ADMIN_PASSWORD")
        require(password and not password.startswith("CHANGE_ME"), f"Set OPENSEARCH_INITIAL_ADMIN_PASSWORD in {env_path}")
        self.auth = "Basic " + base64.b64encode(("admin:" + password).encode()).decode()
        self.url = url.rstrip("/")
        if ca_file:
            self.context = ssl.create_default_context(cafile=ca_file)
        elif parsed.hostname in ("localhost", "127.0.0.1", "::1"):
            self.context = ssl._create_unverified_context()  # Existing local demo certificate only.
        else:
            self.context = ssl.create_default_context()

    def call(self, method, path, body=None, missing_ok=False):
        payload = body if isinstance(body, bytes) else (None if body is None else
            json.dumps(body, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode())
        request = urllib.request.Request(self.url + path, data=payload, method=method, headers={
            "Authorization": self.auth,
            "Content-Type": "application/x-ndjson" if isinstance(body, bytes) else "application/json"})
        # A new opener per call keeps parallel read queries independent.
        opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=self.context))
        try:
            with opener.open(request, timeout=180) as response:
                data = response.read()
                return json.loads(data) if data else {}
        except urllib.error.HTTPError as exc:
            if missing_ok and exc.code == 404:
                return None
            error = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenSearch {method} {path}: HTTP {exc.code}: {error[:1200]}") from None


def validate(root):
    """Read-only full input checks, including embedding identity and metadata mirrors."""
    started = time.perf_counter()
    manifest = read_json(root / "corpus/manifest/corpus_manifest.json")
    schema = definition()["mappings"]["properties"]
    docs_list = list(read_jsonl(root / "corpus/manifest/document_registry.jsonl"))
    docs = {row["document_id"]: row for row in docs_list}
    require(len(docs) == len(docs_list) == manifest["document_registry_records"], "Document registry count/ID mismatch")
    vectors, metadata = {}, {}
    for kind in ("article", "source"):
        vectors[kind] = np.load(root / f"import/{kind}_embeddings.npy", mmap_mode="r", allow_pickle=False)
        metadata[kind] = list(read_jsonl(root / f"import/{kind}_embeddings_meta.jsonl"))
        require(vectors[kind].shape == (manifest[f"{kind}_chunks"], 1024), f"{kind}: vector shape mismatch")
        require(vectors[kind].dtype == np.float32, f"{kind}: expected float32")
        require(len(metadata[kind]) == len(vectors[kind]), f"{kind}: sidecar count mismatch")
        for offset in range(0, len(vectors[kind]), 4096):
            part = vectors[kind][offset:offset + 4096]
            require(bool(np.isfinite(part).all()), f"{kind}: non-finite vector")
            require(bool(np.all(np.abs(np.linalg.norm(part, axis=1) - 1) < 0.001)), f"{kind}: non-unit vector")
    ids, row_indices, paths = set(), {k: set() for k in vectors}, set()
    parents, links = set(), []
    kinds, relations, states = Counter(), Counter(), Counter()
    for row in read_jsonl(root / "corpus/manifest/chunks.jsonl"):
        cid, kind = row["chunk_id"], row["kind"]
        require(cid not in ids, f"Duplicate chunk: {cid}")
        require(kind in vectors and row["document_id"] in docs, f"Invalid kind/document: {cid}")
        require(not (set(row) - set(schema)), f"Unexpected fields: {cid}")
        require(not (set(row.get("classification", {})) - set(schema["classification"]["properties"])), f"Unexpected classification: {cid}")
        require(row.get("chunk_text") and row.get("embed_text"), f"Empty text: {cid}")
        if row.get("version_date"):
            require(bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", row["version_date"])), f"Invalid date format: {cid}")
            date.fromisoformat(row["version_date"])
        i = row["embedding_row_index"]
        require(type(i) is int and 0 <= i < len(vectors[kind]) and i not in row_indices[kind], f"Invalid vector row: {cid}")
        require(row["embedding_file"] == f"import/{kind}_embeddings.npy", f"Wrong embedding file: {cid}")
        meta = metadata[kind][i]
        require(meta["row_index"] == i and meta["chunk_id"] == cid, f"Vector identity mismatch: {cid}")
        require(meta["input_text_sha256"] == hashlib.sha256(row["embed_text"].encode()).hexdigest(), f"Embedding input changed: {cid}")
        for key in (*VERSION_FIELDS, "document_id", "parent_id", "law_family_id", "is_current", "chunk_index", "chunk_count"):
            require(meta.get(key) == row.get(key), f"Sidecar mismatch {key}: {cid}")
        require(meta.get("embedding_model") == "BAAI/bge-m3" and meta.get("embedding_dim") == 1024, f"Wrong model: {cid}")
        require(0 <= row["chunk_index"] < row["chunk_count"], f"Invalid chunk ordering: {cid}")
        relation = row.get("version_relation")
        target, parent = row.get("version_target_document_id"), row.get("version_target_parent_id")
        require(relation in ("current", "superseded_by", "amends", "unresolved"), f"Invalid relation: {cid}")
        if relation == "unresolved":
            require(target is None and parent is None, f"Unresolved with target: {cid}")
        else:
            require(target in docs and docs[target]["is_current"] is True, f"Target is not marked current: {cid}")
            if relation == "current":
                require(target == row["document_id"] and row["is_current"] is True, f"Invalid self link: {cid}")
            if parent:
                links.append((target, parent, cid))
        parents.add((row["document_id"], row["parent_id"]))
        for path in [row["canonical_text_path"], *row.get("pdf_relative_paths", [])]:
            if path not in paths:
                require(rooted(root, path).is_file(), f"Missing source: {path}")
                paths.add(path)
        ids.add(cid)
        row_indices[kind].add(i)
        kinds[kind] += 1
        relations[relation] += 1
        states[str(row["is_current"])] += 1
    require(all((doc, parent) in parents for doc, parent, _ in links), "Dangling target parent")
    require(dict(relations) == manifest["version_relation_counts"], "Version relation counts mismatch")
    for kind in vectors:
        require(kinds[kind] == len(vectors[kind]), f"Incomplete vector coverage: {kind}")
    return {"passed": True, "count": len(ids), "kinds": dict(kinds), "relations": dict(relations),
            "states": dict(states), "documents": len(docs), "source_paths_checked": len(paths),
            "fingerprints": {name: file_hash(root / name) for name in INPUTS},
            "seconds": round(time.perf_counter() - started, 2)}


def check_mapping(client, index):
    require(bool(re.fullmatch(r"[a-z0-9_-]+", index)), "Invalid index name")
    actual = client.call("GET", f"/{index}/_mapping")[index]["mappings"]
    expected = definition()["mappings"]
    require(actual.get("properties") == expected["properties"] and actual.get("dynamic") == "strict",
            "Existing mapping differs; use a new index version instead of overwriting")
    settings = client.call("GET", f"/{index}/_settings")[index]["settings"]["index"]
    require(settings.get("knn") == "true" and settings["number_of_shards"] == "1", "Wrong index settings")
    require(settings.get("analysis") == definition()["settings"]["analysis"], "Analyzer differs")
    return actual


def create(client, index):
    existing = client.call("GET", f"/{index}", missing_ok=True)
    if existing is not None:
        check_mapping(client, index)
        return {"created": False, "already_exists": True, "index": index}
    response = client.call("PUT", f"/{index}", definition())
    require(response.get("acknowledged"), "Index creation not acknowledged")
    check_mapping(client, index)
    return {"created": True, "index": index}


def bulk_rows(client, index, rows, vectors):
    lines = []
    for row in rows:
        source = project(row)
        source["vector"] = vectors[row["kind"]][row["embedding_row_index"]].tolist()
        lines.extend((json.dumps({"index": {"_index": index, "_id": row["chunk_id"]}}),
                      json.dumps(source, ensure_ascii=False, separators=(",", ":"), allow_nan=False)))
    payload = ("\n".join(lines) + "\n").encode()
    for attempt in range(3):
        result = client.call("POST", "/_bulk", payload)
        items = [item["index"] for item in result.get("items", [])]
        require(len(items) == len(rows), "Incomplete bulk response")
        failed = [item for item in items if item.get("status", 500) >= 300]
        if not failed:
            return
        if attempt < 2 and all(item["status"] in (429, 502, 503, 504) for item in failed):
            time.sleep(attempt + 1)  # Same _id: bounded retry cannot create duplicates.
            continue
        raise RuntimeError("Bulk failed: " + json.dumps(failed[:3], ensure_ascii=False)[:1800])


def import_full(client, root, index, run, batch_size):
    preflight = validate(root)
    write_json(run / "preflight.json", preflight)
    create(client, index)
    require(not client.call("GET", f"/{index}/_alias")[index]["aliases"],
            "Refusing to import into a published index; build a new version first")
    vectors = {kind: np.load(root / f"import/{kind}_embeddings.npy", mmap_mode="r", allow_pickle=False)
               for kind in ("article", "source")}
    started, done = time.perf_counter(), 0
    previous = client.call("GET", f"/{index}/_settings")[index]["settings"]["index"].get("refresh_interval", "1s")
    client.call("PUT", f"/{index}/_settings", {"index": {"refresh_interval": "-1"}})
    try:
        batch = []
        for row in read_jsonl(root / "corpus/manifest/chunks.jsonl"):
            batch.append(row)
            if len(batch) == batch_size:
                bulk_rows(client, index, batch, vectors)
                done += len(batch)
                batch = []
                if done % 2000 == 0:
                    print(f"Imported {done}/{preflight['count']}", flush=True)
        if batch:
            bulk_rows(client, index, batch, vectors)
            done += len(batch)
        client.call("POST", f"/{index}/_refresh")
        count = client.call("GET", f"/{index}/_count")["count"]
        require(count == done == preflight["count"], "Post-import count mismatch")
        require(all(file_hash(root / name) == digest for name, digest in preflight["fingerprints"].items()),
                "Input changed during import; do not activate")
        result = {"passed": True, "index": index, "count": count, "bulk_errors": 0,
                  "batch_size": batch_size, "seconds": round(time.perf_counter() - started, 2),
                  "fingerprints": preflight["fingerprints"]}
        write_json(run / "import_result.json", result)
        return result
    finally:
        client.call("PUT", f"/{index}/_settings", {"index": {"refresh_interval": previous}})


def search_response(client, index, body):
    response = client.call("POST", f"/{index}/_search", body)
    require(not response.get("timed_out") and response.get("_shards", {}).get("failed", 0) == 0,
            "Search incomplete/timed out")
    return response


def bm25_body(text, filters=(), size=50):
    must = {"multi_match": {"query": text, "fields": ["chunk_text^5", "document_name^3", "section_path^2", "article_no^4"],
                            "type": "best_fields", "operator": "or"}}
    query = {"must": [must], "filter": list(filters)}
    match = re.search(r"第[一二三四五六七八九十百千万零〇两0-9]+条", text)
    if match:
        query["should"] = [{"term": {"article_no.keyword": {"value": match.group(), "boost": 8}}}]
    return {"size": size, "_source": {"excludes": ["vector", "embed_text"]}, "query": {"bool": query}}


def vector_body(vector, filters=(), size=50):
    array = np.asarray(vector, dtype=np.float32)
    require(array.shape == (1024,) and np.isfinite(array).all() and np.linalg.norm(array) > 0,
            "Query vector must be 1024 finite values, not zero")
    array = array / np.linalg.norm(array)
    # Lucene uses k to size its HNSW search queue. Keep a wider search than
    # the 50 candidates returned to RRF; full-corpus tests exposed misses at k=50.
    knn = {"vector": array.tolist(), "k": max(size, 200)}
    if filters:
        knn["filter"] = {"bool": {"filter": list(filters)}}
    return {"size": size, "_source": {"excludes": ["vector", "embed_text"]}, "query": {"knn": {"vector": knn}}}


def rrf(bm25_hits, vector_hits, size=10, k=60):
    merged = {}
    for channel, hits in (("bm25", bm25_hits), ("vector", vector_hits)):
        for rank, hit in enumerate(hits, 1):
            row = merged.setdefault(hit["_id"], {"chunk": hit["_source"], "rrf_score": 0.0})
            row[channel + "_rank"] = rank
            row[channel + "_score"] = hit["_score"]
            row["rrf_score"] += 1 / (k + rank)
    return sorted(merged.values(), key=lambda row: (-row["rrf_score"], row["chunk"]["chunk_id"]))[:size]


def hybrid(client, index, text, vector, filters=(), candidates=50, size=10):
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(search_response, client, index, bm25_body(text, filters, candidates))
        b = pool.submit(search_response, client, index, vector_body(vector, filters, candidates))
        bm, ve = a.result(), b.result()
    return {"text": text, "filters": list(filters), "candidate_count_per_channel": candidates,
            "knn_search_k": max(candidates, 200),
            "bm25_server_ms": bm["took"], "vector_server_ms": ve["took"],
            "retrieval_wall_ms": round((time.perf_counter() - started) * 1000, 2),
            "bm25": bm["hits"]["hits"], "vector": ve["hits"]["hits"],
            "results": rrf(bm["hits"]["hits"], ve["hits"]["hits"], size)}


def follow_version(client, index, chunk_id):
    response = search_response(client, index, {"size": 1, "_source": {"excludes": ["vector", "embed_text"]},
                                              "query": {"ids": {"values": [chunk_id]}}})
    require(bool(response["hits"]["hits"]), "Chunk not found")
    row = response["hits"]["hits"][0]["_source"]
    relation, target, parent = (row.get(key) for key in VERSION_FIELDS)
    if relation == "unresolved" or not target:
        return {"status": "unresolved", "original": row, "targets": []}
    if relation == "amends" or not parent:
        return {"status": "document_research_required", "original": row, "target_document_id": target,
                "targets": [], "reason": "Document relationship only; re-search within target, do not substitute the whole law."}
    filters = [{"term": {"document_id": target}}, {"term": {"parent_id": parent}},
               {"term": {"kind": "article"}}, {"term": {"is_current": True}}]
    body = {"size": 200, "_source": {"excludes": ["vector", "embed_text"]}, "track_total_hits": True,
            "query": {"bool": {"filter": filters}}, "sort": [{"chunk_index": "asc"}, {"chunk_id": "asc"}]}
    hits = []
    while True:
        response = search_response(client, index, body)
        page = response["hits"]["hits"]
        hits.extend(hit["_source"] for hit in page)
        if len(hits) >= response["hits"]["total"]["value"] or not page:
            break
        body["search_after"] = page[-1]["sort"]
    require(bool(hits), "Version target missing from index")
    require(len(hits) == hits[0]["chunk_count"] and [h["chunk_index"] for h in hits] == list(range(len(hits))),
            "Incomplete target article; do not return partial text as a full article")
    def plain(text):
        text = re.sub(r"\s+", "", text)
        return re.sub(r"^第[一二三四五六七八九十百千万零〇两0-9]+条", "", text)
    similarity = SequenceMatcher(None, plain(row["chunk_text"]),
        plain("".join(hit["chunk_text"] for hit in hits)), autojunk=False).ratio()
    if relation == "superseded_by" and similarity < .8:
        # Guard an observed numbering shift. Low similarity is a review signal,
        # never proof the legal link is wrong (long split articles can also trigger it).
        return {"status": "candidate_requires_review", "original": row, "targets": [],
                "configured_candidates": hits, "text_similarity": round(similarity, 4),
                "target_document_id": target,
                "reason": "Linked article text differs substantially; re-search target document and verify numbering. No automatic substitution."}
    return {"status": "current_candidate", "original": row, "targets": hits,
            "text_similarity": round(similarity, 4),
            "note": "Existing same-number relationship is a candidate, not a legal equivalence guarantee."}


def verify(client, root, index, run):
    started = time.perf_counter()
    check_mapping(client, index)
    expected, examples = {}, []
    for i, row in enumerate(read_jsonl(root / "corpus/manifest/chunks.jsonl")):
        expected[row["chunk_id"]] = source_hash(row)
        if i % 3000 == 0:
            examples.append(row)
    scroll_id, seen = None, set()
    try:
        response = client.call("POST", f"/{index}/_search?scroll=2m", {
            "size": 1000, "sort": ["_doc"], "_source": {"excludes": ["vector"]}, "query": {"match_all": {}}})
        while True:
            require(not response.get("timed_out") and response["_shards"]["failed"] == 0, "Incomplete verification scan")
            scroll_id = response["_scroll_id"]
            hits = response["hits"]["hits"]
            if not hits:
                break
            for hit in hits:
                cid = hit["_id"]
                require(cid in expected and cid not in seen and source_hash(hit["_source"]) == expected[cid],
                        f"Indexed source mismatch: {cid}")
                seen.add(cid)
            response = client.call("POST", "/_search/scroll", {"scroll": "2m", "scroll_id": scroll_id})
    finally:
        if scroll_id:
            client.call("DELETE", "/_search/scroll", {"scroll_id": [scroll_id]})
    require(len(seen) == len(expected), "Missing indexed chunks")
    vectors = {kind: np.load(root / f"import/{kind}_embeddings.npy", mmap_mode="r", allow_pickle=False)
               for kind in ("article", "source")}
    actual = client.call("POST", f"/{index}/_mget", {"ids": [r["chunk_id"] for r in examples]})["docs"]
    for row, hit in zip(examples, actual):
        require(hit.get("found") and np.array_equal(np.asarray(hit["_source"]["vector"], dtype=np.float32),
                vectors[row["kind"]][row["embedding_row_index"]]), f"Indexed vector mismatch: {row['chunk_id']}")
    health = client.call("GET", f"/_cluster/health/{index}?wait_for_status=green&timeout=60s")
    require(health["status"] == "green" and not health["timed_out"], "Index not green")
    result = {"passed": True, "index": index, "source_rows_compared": len(seen),
              "indexed_vectors_exactly_compared": len(examples), "health": health["status"],
              "seconds": round(time.perf_counter() - started, 2)}
    write_json(run / "verification.json", result)
    return result


def activate(client, root, index, run):
    # Re-verify actual index instead of trusting a stale success file.
    verified = verify(client, root, index, run)
    actions = []
    for alias in (READ_ALIAS, WRITE_ALIAS):
        existing = client.call("GET", f"/_alias/{alias}", missing_ok=True) or {}
        require(not (set(existing) - {index}), f"Alias {alias} already points elsewhere; no automatic cutover")
        action = {"index": index, "alias": alias}
        if alias == WRITE_ALIAS:
            action["is_write_index"] = True
        actions.append({"add": action})
    result = client.call("POST", "/_aliases", {"actions": actions})
    require(result.get("acknowledged"), "Alias publication not acknowledged")
    return {"passed": True, "index": index, "aliases": [READ_ALIAS, WRITE_ALIAS], "verification": verified}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["validate", "create", "import", "verify", "activate", "search", "follow", "status"])
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--url", default="https://localhost:19200")
    parser.add_argument("--ca-file")
    parser.add_argument("--index", default=INDEX)
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument("--text")
    parser.add_argument("--vector-file", type=Path)
    parser.add_argument("--vector-row", type=int, default=0)
    parser.add_argument("--current", action="store_true")
    parser.add_argument("--kind", choices=["article", "source"])
    parser.add_argument("--base-only", action="store_true")
    parser.add_argument("--chunk-id")
    args = parser.parse_args()
    root = args.root.resolve()
    require(re.fullmatch(r"[a-z0-9_-]+", args.index), "Invalid index name")
    require(1 <= args.batch_size <= 500, "batch-size must be 1..500")
    run = (args.run_dir or (root / ".ai-tmp" / ("index_" + datetime.now().strftime("%Y%m%d_%H%M%S")))).resolve()
    require(run.is_relative_to(root / ".ai-tmp"), "Run directory must be under corpus root/.ai-tmp")
    run.mkdir(parents=True, exist_ok=True)
    client = None if args.command == "validate" else Client(root, args.url, args.ca_file)
    try:
        if args.command == "validate":
            result = validate(root)
        elif args.command == "create":
            result = create(client, args.index)
        elif args.command == "import":
            result = import_full(client, root, args.index, run, args.batch_size)
        elif args.command == "verify":
            result = verify(client, root, args.index, run)
        elif args.command == "activate":
            result = activate(client, root, args.index, run)
        elif args.command == "status":
            result = {"health": client.call("GET", "/_cluster/health"),
                      "indices": client.call("GET", "/_cat/indices/maritime_law*?format=json"),
                      "aliases": client.call("GET", "/_alias/maritime_law*", missing_ok=True)}
        elif args.command == "follow":
            require(args.chunk_id, "--chunk-id required")
            result = follow_version(client, args.index, args.chunk_id)
        else:
            require(args.text, "--text required")
            filters = []
            for field, value in (("is_current", True if args.current else None), ("kind", args.kind),
                                 ("document_role", "base_act" if args.base_only else None)):
                if value is not None:
                    filters.append({"term": {field: value}})
            if args.vector_file:
                vectors = np.load(args.vector_file, allow_pickle=False)
                vector = vectors if vectors.ndim == 1 else vectors[args.vector_row]
                result = hybrid(client, args.index, args.text, vector, filters)
            else:
                result = search_response(client, args.index, bm25_body(args.text, filters, 10))
        write_json(run / (args.command + "_result.json"), result)
        print(json.dumps({"command": args.command, "result_file": str(run / (args.command + "_result.json")),
                          "passed": result.get("passed", True), "count": result.get("count")}, ensure_ascii=False))
    except Exception as exc:
        write_json(run / (args.command + "_error.json"), {"command": args.command, "error": str(exc)})
        raise


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    main()
