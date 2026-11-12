"""Public artifact metadata shared by the scheduler projection and Workbench."""

from typing import Literal

from pydantic import Field

from backend.contracts.schemas.core import Contract


class ArtifactReference(Contract):
    """A downloadable file produced by one completed work Run.

    The storage path stays private to the execution adapter. Clients receive
    only a stable id and the authorized download URL.
    """

    artifact_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    media_type: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)
    sha256: str = Field(min_length=1)
    version: int = Field(default=1, ge=1)
    kind: Literal["document", "preview", "file"] = "file"
    preview_artifact_id: str | None = None
    preview_of_artifact_id: str | None = None
    download_url: str | None = None

