"""Bind a game call to public platform services and its current transaction."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from plux.api import MemberRef, TextMessage
from ..presentation.models import CardPlan, ReplyImage

@dataclass(frozen=True)
class Message:
    content: str
    conversation_id: str
    sender_id: str | None
    event_key: str
    session_id: str | None
    observed_at_ms: int
    mentioned_ids: tuple[str, ...] = ()
    mention_state: str = "none"
    message_id_candidate: str | None = None
    message_kind: str = "text"
    history_state: str = "unknown"
    @classmethod
    def from_platform(cls, source: TextMessage):
        others = tuple(ref.member_id for ref in source.mentions
                       if ref.member_id != source.identity.account)
        self_mentioned = any(ref.member_id == source.identity.account for ref in source.mentions)
        verified = source.quality.mentions_status in ("explicit_self", "explicit_other")
        state = ("explicit_other" if others else "explicit_self" if self_mentioned else "none") if verified else "unknown"
        return cls(source.text, source.identity.conversation, source.identity.actor,
                   source.event_key, source.identity.native_session,
                   int(source.observed_at.timestamp() * 1000),
                   others if others else tuple(ref.member_id for ref in source.mentions),
                   state, history_state=source.quality.history_status)

@dataclass
class GameContext:
    store: Any
    services: Any
    game_config: Any
    account_id: str
    allowed_targets: tuple[str, ...]
    now: float
    conversation_id: str | None = None
    user_id: str | None = None
    event_key: str | None = None
    message: Message | None = None
    connection_id: str | None = None
    previous_poll_at: float | None = None
    runtime_issue: str | None = None
    receiver_pending_since: float | None = None
    is_admin: bool = False
    plugin_name: str = "xiuxian"
    @property
    def game_group_whitelist(self):
        return self.allowed_targets
    def member_name(self, group: str, player: str) -> str | None:
        value = self.services.messages.member(MemberRef(self.account_id, player, group), self.store)
        return value.display_name if value.source == "native_group_member" else None
    def get_message(self, event_key: str):
        return self.services.messages.get(event_key, self.store)
    def receipt(self, logical_key: str):
        reference = self.services.messages.reply_reference(logical_key, self.store)
        return self.services.messages.receipt(reference.request_id, self.store) if reference else None
    def cancel_reply(self, logical_key: str) -> bool:
        reference = self.services.messages.reply_reference(logical_key, self.store)
        return self.services.messages.cancel(reference.request_id, self.store) if reference else False
    def reply_request_id(self, key: str) -> str:
        return key
    def card(self, kind: str, payload: dict):
        return ReplyImage(CardPlan(kind, payload))
