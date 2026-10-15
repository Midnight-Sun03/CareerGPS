from dataclasses import dataclass, field
from typing import Any


@dataclass
class FetchedOpportunity:
    external_id: str
    source_url: str
    listing_url: str = ""
    apply_url: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    raw_content: str = ""
    fields: dict[str, Any] = field(default_factory=dict)
    parser_version: str = "1"
    detail_http_status: int | None = None
    application_link_valid: bool | None = None
