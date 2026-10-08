"""Business output plans; rendering and transport happen after the transaction."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Mapping
from types import MappingProxyType

@dataclass(frozen=True)
class Reply:
    text: str
    mention_ids: tuple[str, ...] = ()
    target_id: str | None = None
    request_key: str | None = None
    expires_in: float | None = None
    def __str__(self):
        return self.text
    def __contains__(self, value):
        return value in self.text

def freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): freeze(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(freeze(item) for item in value)
    return value

def plain(value):
    if isinstance(value, Mapping):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain(item) for item in value]
    return value

@dataclass(frozen=True)
class CardPlan:
    kind: str
    payload: Mapping[str, Any]
    def __post_init__(self):
        object.__setattr__(self, "payload", freeze(self.payload))

@dataclass(frozen=True)
class ReplyImage:
    image_path: CardPlan
    target_id: str | None = None
    request_key: str | None = None
    expires_in: float | None = None

def reply_request_id(account_id: str, plugin_name: str, request_key: str) -> str:
    """A plugin-owned logical key; the platform owns native request identity."""
    return request_key
