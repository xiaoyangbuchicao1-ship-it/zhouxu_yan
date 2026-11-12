"""Explicit case snapshot import. Never run writes during application startup.

Run: python -m backend.infrastructure.corpus.import_cases
Uses the same configured TLS/credentials as the law index, on a distinct index.
"""

import asyncio
import json
import re

import numpy as np

from backend.config.settings import load_settings
from backend.infrastructure.search.cases import CaseRetriever
from backend.infrastructure.search.opensearch import OpenSearchRetriever


async def publish(service):
    index = service.index
    alias = service.cfg.read_alias
    if not re.fullmatch(r"[a-z0-9_-]+", alias) or alias in {
        service.index,
        service.law_alias,
    }:
        raise ValueError("invalid_case_alias")
    existing = await service.client.get("/" + index)
    if existing.status_code == 404:
        properties = {
            key: {"type": "keyword"}
            for key in (
                "case_id",
                "chunk_id",
                "snapshot_id",
                "source_sha256",
                "text_sha256",
            )
        }
        properties.update(
            {key: {"type": "text", "analyzer": "standard"} for key in ("title", "text")}
        )
        properties.update({key: {"type": "integer"} for key in ("start", "end")})
        properties["embedding"] = {
            "type": "knn_vector",
            "dimension": 1024,
            "method": {"name": "hnsw", "engine": "lucene", "space_type": "cosinesimil"},
        }
        from backend.infrastructure.corpus.cases import text_hash

        await service.request(
            "PUT",
            "/" + index,
            json={
                "settings": {
                    "index": {
                        "knn": True,
                        "number_of_shards": 1,
                        "number_of_replicas": 0,
                    }
                },
                "mappings": {
                    "dynamic": "strict",
                    "_meta": {
                        "snapshot_id": service.source.version,
                        "manifest_sha256": text_hash(
                            json.dumps(service.source.manifest, sort_keys=True)
                        ),
                    },
                    "properties": properties,
                },
            },
        )
        vectors = np.load(service.source.directory / "vectors.npy", mmap_mode="r")
        if vectors.shape != (len(service.source.chunks), 1024):
            raise ValueError("vector_shape")
        for start in range(0, len(vectors), 100):
            lines = []
            for row, vector in zip(
                service.source.chunks[start : start + 100], vectors[start : start + 100]
            ):
                if not np.isfinite(vector).all() or not np.isclose(
                    np.linalg.norm(vector), 1, atol=1e-5
                ):
                    raise ValueError("vector_contract")
                lines.extend(
                    [
                        json.dumps({"create": {"_id": row["chunk_id"]}}),
                        json.dumps(
                            {**row, "embedding": vector.tolist()}, ensure_ascii=False
                        ),
                    ]
                )
            data = await service.request(
                "POST",
                f"/{index}/_bulk",
                content="\n".join(lines) + "\n",
                headers={"Content-Type": "application/x-ndjson"},
            )
            if data["errors"]:
                raise RuntimeError("partial_import_not_published")
        await service.request("POST", f"/{index}/_refresh")
    else:
        existing.raise_for_status()
    await service.validate_index()
    counted = await service.request("GET", f"/{index}/_count")
    if counted.get("_shards", {}).get("failed", 0) or counted["count"] != len(
        service.source.chunks
    ):
        raise ValueError("incomplete_index")
    current = await service.client.get("/_alias/" + alias)
    actions = []
    if current.status_code == 200:
        actions = [
            {"remove": {"index": name, "alias": alias}} for name in current.json()
        ]
    elif current.status_code != 404:
        current.raise_for_status()
    actions.append({"add": {"index": index, "alias": alias}})
    await service.request("POST", "/_aliases", json={"actions": actions})
    return {"index": index, "alias": alias, "documents": len(service.source.chunks)}


async def main():
    settings = load_settings()
    async with OpenSearchRetriever(settings, None) as connection:
        service = CaseRetriever(settings, None, connection.client)
        print(json.dumps(await publish(service)))


if __name__ == "__main__":
    asyncio.run(main())
