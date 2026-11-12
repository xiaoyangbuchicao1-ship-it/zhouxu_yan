"""Resident query encoder matching the delivered corpus's CLS/L2 contract."""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor

from backend.config.settings import Settings
from backend.contracts.schemas.core import PortError


class BgeM3Encoder:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rag-encoder")
        self.model = self.tokenizer = None

    def _encode(self, text: str) -> list[float]:
        cfg = self.settings.rag
        if cfg.embedding_local_only:
            # transformers may otherwise query Hugging Face metadata while
            # loading a local tokenizer. The encoder must be deterministic and
            # offline when the configuration says local-only.
            os.environ["HF_HUB_OFFLINE"] = "1"
            os.environ["TRANSFORMERS_OFFLINE"] = "1"
        import torch
        from transformers import AutoModel, AutoTokenizer

        if self.model is None:
            torch.set_num_threads(cfg.embedding_threads)
            options = dict(
                local_files_only=cfg.embedding_local_only,
                revision=cfg.embedding_revision,
                trust_remote_code=False,
            )
            self.tokenizer = AutoTokenizer.from_pretrained(cfg.embedding_model, **options)
            self.model = AutoModel.from_pretrained(cfg.embedding_model, **options)
            self.model.eval().to(cfg.embedding_device)
        tokens = self.tokenizer(text, return_tensors="pt", truncation=False)
        if tokens["input_ids"].shape[1] > cfg.embedding_max_tokens:
            raise PortError(self.settings.error("rag_invalid_query"))
        with torch.inference_mode():
            result = self.model(**{
                key: value.to(cfg.embedding_device) for key, value in tokens.items()
            }).last_hidden_state[:, 0]
            result = torch.nn.functional.normalize(result.float(), p=2, dim=1)
        if result.shape != (1, cfg.embedding_dimensions) or not torch.isfinite(result).all():
            raise PortError(self.settings.error("rag_embedding_failed"))
        return result.cpu()[0].tolist()

    async def encode(self, text: str) -> list[float]:
        # Cancellation removes queued work; a running native inference cannot be
        # forcibly killed. A single worker bounds native inference concurrency.
        try:
            return await asyncio.wrap_future(self.executor.submit(self._encode, text))
        except PortError:
            raise
        except Exception:
            raise PortError(self.settings.error("rag_embedding_failed")) from None

    def close(self):
        self.executor.shutdown(wait=True, cancel_futures=True)
