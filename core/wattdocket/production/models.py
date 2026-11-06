"""Source adapter contract. Observation dates are never retrieval dates."""
from dataclasses import asdict, dataclass
from typing import Any, Protocol


class Fetcher(Protocol):
    def get_bytes(self, url: str) -> bytes: ...
    def get_json(self, url: str) -> Any: ...
    def post_json(self, url: str, payload: Any) -> Any: ...


@dataclass
class SourceResult:
    source_id: str
    label: str
    url: str
    data: Any
    record_count: int
    observation_start: str | None = None
    observation_end: str | None = None
    published_at: str | None = None
    data_vintage: str | None = None
    coverage: str = ""
    note: str = ""

    def as_dict(self) -> dict:
        return asdict(self)
