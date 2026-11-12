from __future__ import annotations

from typing import Protocol

from backend.contracts.modules.materials import MaterialInput, ParsedMaterial


class MaterialParser(Protocol):
    name: str

    def can_handle(self, material: MaterialInput) -> bool: ...

    def parse(self, material: MaterialInput) -> ParsedMaterial: ...
