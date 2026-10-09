"""File metadata. Storage keys stay on the server side of this module."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID


def format_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class FileRecord:
    id: UUID
    name: str
    media_type: str
    size: int
    sha256: str
    status: str
    created_at: datetime
    purpose: str
    project_id: UUID | None
    base_id: UUID | None
    storage_key: str

    def public_dict(self) -> dict[str, object]:
        """FileInfo fields only. No host path and no storage key."""

        return {
            "id": str(self.id),
            "name": self.name,
            "mediaType": self.media_type,
            "size": self.size,
            "sha256": self.sha256,
            "status": self.status,
            "createdAt": format_timestamp(self.created_at),
        }
