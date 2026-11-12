"""Case retrieval on the existing authenticated OpenSearch/encoder lifetime."""

import asyncio
import json
import math
import re
from collections import defaultdict

from backend.config.settings import BACKEND_ROOT, Settings
from backend.infrastructure.corpus.cases import CaseSnapshot, CaseSourceError, text_hash


class CaseRetriever:
    def __init__(self, settings: Settings, encoder, client):
        from pathlib import Path

        self.cfg = settings.cases
        self.law_alias = settings.rag.read_alias
        self.encoder = encoder
        self.client = client
        path = Path(self.cfg.snapshot_dir)
        if not path.is_absolute():
            path = BACKEND_ROOT / path
        self.source = CaseSnapshot(
            path,
            {
                "model": settings.rag.embedding_model,
                "revision": settings.rag.embedding_revision,
                "pooling": "cls",
                "normalize": True,
                "dimensions": settings.rag.embedding_dimensions,
            },
        )
        self.index = self.source.version

    async def request(self, method, path, **kwargs):
        r = await self.client.request(method, path, **kwargs)
        r.raise_for_status()
        return r.json()

    async def validate_index(self):
        data = await self.request("GET", f"/{self.index}/_mapping")
        meta = data[self.index]["mappings"].get("_meta", {})
        if meta.get("snapshot_id") != self.source.version or meta.get(
            "manifest_sha256"
        ) != text_hash(json.dumps(self.source.manifest, sort_keys=True)):
            raise CaseSourceError("index_snapshot")

    async def search(self, query, limit=None, case_id=None):
        limit = min(limit or self.cfg.default_results, self.cfg.max_results)
        await self.validate_index()
        if re.fullmatch(r"HH\d+(?:-\d+)?", query.strip()) and case_id is None:
            case_id = query.strip()
        if case_id:
            rows = [row for row in self.source.chunks if row["case_id"] == case_id][:1]
            return self.pack(rows, {}, limit, "identity")
        if self.encoder is None:
            raise RuntimeError("case_encoder_unavailable")
        vector = await self.encoder.encode(query)
        if len(vector) != 1024 or not all(math.isfinite(v) for v in vector):
            raise CaseSourceError("encoder_contract")
        filters = [{"term": {"snapshot_id": self.source.version}}]
        queries = [
            {
                "bool": {
                    "must": [
                        {"multi_match": {"query": query, "fields": ["text", "title^3"]}}
                    ],
                    "filter": filters,
                }
            },
            {
                "knn": {
                    "embedding": {
                        "vector": vector,
                        "k": self.cfg.candidates,
                        "filter": {"bool": {"filter": filters}},
                    }
                }
            },
        ]

        async def hits(q):
            data = await self.request(
                "POST",
                f"/{self.index}/_search",
                json={
                    "size": self.cfg.candidates,
                    "query": q,
                    "_source": {"excludes": ["embedding"]},
                },
            )
            if data.get("timed_out") or data.get("_shards", {}).get("failed", 0):
                raise RuntimeError("partial_search")
            return data["hits"]["hits"]

        found = await asyncio.gather(*(hits(q) for q in queries))
        lanes = []
        scores = defaultdict(float)
        by_case = {}
        ranks = defaultdict(dict)
        for lane_no, hits_list in enumerate(found):
            lane = []
            seen = set()
            for hit in hits_list:
                row = hit["_source"]
                self.source.validate_chunk(row)
                if row != self.source.by_id.get(row["chunk_id"]):
                    raise CaseSourceError("index_body")
                cid = row["case_id"]
                if cid in seen:
                    continue
                seen.add(cid)
                lane.append(row)
                rank = len(lane)
                scores[cid] += 1 / (60 + rank)
                by_case.setdefault(cid, row)
                ranks[cid][str(lane_no)] = rank
            lanes.append(lane)
        ordered = []
        seen = set()
        for rank in range((limit + 1) // 2):
            for lane in reversed(lanes):
                if rank < len(lane) and lane[rank]["case_id"] not in seen:
                    row = lane[rank]
                    seen.add(row["case_id"])
                    ordered.append(row)
        for cid in sorted(scores, key=lambda c: (-scores[c], c)):
            if cid not in seen:
                ordered.append(by_case[cid])
                seen.add(cid)
        return self.pack(ordered, ranks, limit, "vector_reserved")

    def pack(self, rows, ranks, limit, method):
        items = []
        for row in rows[:limit]:
            end = min(row["end"], row["start"] + self.cfg.excerpt_chars)
            items.append(
                {
                    "case_id": row["case_id"],
                    "title": row["title"],
                    "snapshot_id": self.source.version,
                    "reference_id": f"{row['case_id']}@{self.source.version}:{row['start']}-{end}",
                    "source_sha256": row["source_sha256"],
                    "excerpt": row["text"][: end - row["start"]],
                    "start": row["start"],
                    "end": end,
                    "chunk_end": row["end"],
                    "lane_ranks": ranks.get(row["case_id"], {}),
                }
            )
        return {
            "status": "ok",
            "snapshot_id": self.source.version,
            "source_type": "local_case_material",
            "method": method,
            "items": items,
            "exhaustive": False,
            "law_effect": "not_verified",
        }

    def read(self, case_id, start=0, chars=None, snapshot_id=None):
        return self.source.read(
            case_id,
            start,
            min(chars or self.cfg.read_chars, self.cfg.max_read_chars),
            snapshot_id,
        )
