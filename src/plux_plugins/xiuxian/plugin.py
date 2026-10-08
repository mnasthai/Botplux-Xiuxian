"""Thin Plux entrypoint; game rules and storage stay inside the plugin."""
from __future__ import annotations
from dataclasses import asdict
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Mapping
import json

from plux.api import (AssetRef, ConfigurationError, EventSpec, MemberRef, Outcome,
                      Plugin, PluginManifest, ReplyIntent, ScheduleSpec, StateSnapshot,
                      TaskIntent, TaskSpec, TextMessage)
from .application.context import GameContext, Message
from .application.single_player import handle_command
from .application import duels
from .application.dungeon import service as dungeon
from .domain.commands import parse_command
from .domain.catalog import CATALOG
from .domain.shop import PROP_TEMPLATES
from .persistence.schema import MIGRATIONS
from .presentation.models import CardPlan, Reply, ReplyImage, plain
from .settings import build_rules, validate_config

def _digest(value) -> str:
    return sha256(json.dumps(plain(value), sort_keys=True, ensure_ascii=False,
                            separators=(",", ":")).encode("utf-8")).hexdigest()

def _items(result):
    if result is None:
        return ()
    if isinstance(result, (tuple, list)):
        return tuple(item for value in result for item in _items(value))
    return (result,)

def _text_chunks(text: str, maximum: int = 14000):
    if not text.strip():
        return ()
    chunks, current, size = [], [], 0
    for character in text:
        length = len(character.encode("utf-8"))
        if size + length > maximum:
            chunks.append("".join(current))
            current, size = [], 0
        current.append(character)
        size += length
    if current:
        chunks.append("".join(current))
    return tuple(chunks)

class XiuxianPlugin(Plugin):
    manifest = PluginManifest(
        "xiuxian", version="0.1.0",
        capabilities=frozenset({"transactions", "catalogs", "snapshots", "assets",
                                "tasks", "schedules", "messages", "directory", "delivery"}),
        config_validator=validate_config, migrations=MIGRATIONS,
        resources=(Path(__file__).parent / "application/dungeon/content_catalog.json",),
    )

    def __init__(self, services):
        super().__init__(services)
        self.settings = services.config
        self.rules = None
        self.rules_snapshot = None
        self.previous_poll = None
        self.pending_since = None

    def register(self, registry):
        # Chinese commands and verified display mentions use the game's parser.
        registry.event(EventSpec("commands", self.command, TextMessage,
            predicate=lambda source: parse_command(Message.from_platform(source)) is not None,
            mode="atomic", requires_command_policy=True))
        registry.task(TaskSpec("poll", lambda payload, context: payload, self.poll,
            idempotent=True, max_attempts=3, timeout_seconds=10))
        registry.schedule(ScheduleSpec("advance", "poll", 1, missed_policy="coalesce"))
        registry.task(TaskSpec("render-card", self.render_card, self.commit_card,
            idempotent=True, max_attempts=3, timeout_seconds=60))

    def _content_digest(self):
        from .application.dungeon.content import _RAW
        return _digest({"artifacts": {key: asdict(value) for key, value in CATALOG.items()},
                        "props": {key: asdict(value) for key, value in PROP_TEMPLATES.items()}, "dungeon": _RAW})

    def _active(self, uow):
        account = self.settings["account"]
        return any(uow.execute(sql, (account,)).fetchone()[0] for sql in (
            "SELECT COUNT(*) FROM game_duels WHERE account_id=? AND state IN ('inviting','supporting','playing')",
            "SELECT COUNT(*) FROM game_pvp_duels WHERE account_id=? AND state IN ('inviting','fighting')",
            "SELECT COUNT(*) FROM dungeon_runs WHERE account_id=? AND state IN ('gathering','active')",
        ))

    def start(self):
        content_digest = self._content_digest()
        requested = self.settings["rules"]
        version = str(requested["rules_version"]) + "-" + _digest(requested)[:16]
        catalog = self.services.data.catalogs.load("rules", version=version, default=requested,
            validator=lambda raw: asdict(build_rules(raw)))
        with self.services.data.database.transaction() as uow:
            binding = self.services.data.snapshots.get("rules-binding", uow)
            active = self._active(uow)
        if active and binding:
            if binding.data["content_digest"] != content_digest:
                raise ConfigurationError("active games require their installed content version")
            if binding.catalog.version != catalog.ref.version:
                catalog = self.services.data.catalogs.get("rules", binding.catalog.version)
                self.services.logger.warning("Active games retain their previous rules until they finish; restart to apply new rules.")
        self.rules_snapshot = catalog
        self.rules = build_rules(catalog.data)
        if self.rules.visual_cards_enabled:
            # Fail during startup if an explicitly enabled presentation cannot run.
            try:
                from .presentation import renderer
            except ImportError as error:
                raise ConfigurationError("visual cards require the xiuxian visual extra (Pillow)") from error
            if not all(path.is_file() for path in (
                renderer.PROFILE_TEMPLATE, renderer.TEMPLATES_DIR / "bg_ranking.png",
                renderer.TEMPLATES_DIR / "bg_duel.png")):
                raise ConfigurationError("xiuxian visual resources are missing")
        if not active or binding is None:
            with self.services.data.database.transaction() as uow:
                self.services.data.snapshots.put(StateSnapshot(
                    "rules-binding", 1, 0, {"content_digest": content_digest},
                    catalog=catalog.ref), expected_revision=binding.revision if binding else None, uow=uow)
        self.previous_poll = self.services.clock.now().timestamp()
        with self.services.data.database.transaction() as uow:
            context = self._context(uow, event_key="startup")
            result = self._lifecycle("on_start", context)
            self._save(self._outcome(result, context), uow)

    def _context(self, uow, *, source=None, event_key=None):
        now = self.services.clock.now().timestamp()
        health = self.services.messages.input_status(uow, exclude_event_key=source.event_key if source else None)
        pending = bool(health["pending_count"] or health["source_unread"])
        if pending and self.pending_since is None:
            self.pending_since = now
        if not pending:
            self.pending_since = None
        connection = self.services.messages.connection()
        internal = Message.from_platform(source) if source else None
        return GameContext(
            store=uow, services=self.services, game_config=self.rules,
            account_id=self.settings["account"], allowed_targets=self.settings["groups"],
            now=now, conversation_id=source.identity.conversation if source else None,
            user_id=source.identity.actor if source else None,
            event_key=source.event_key if source else event_key, message=internal,
            connection_id=connection.native_session or (source.identity.native_session if source else None),
            previous_poll_at=self.previous_poll,
            runtime_issue="receiver_catching_up" if pending else None,
            receiver_pending_since=self.pending_since,
            is_admin=bool(source and source.identity.actor in self.settings["admin_ids"]),
        )

    def _lifecycle(self, name, context):
        result = []
        for module in (duels, dungeon):
            result.extend(_items(getattr(module, name)(context)))
        return result

    def command(self, source, call):
        if (source.identity.account != self.settings["account"]
                or source.identity.conversation not in self.settings["groups"]):
            return Outcome.noop()
        context = self._context(call.uow, source=source)
        result = self._lifecycle("on_before_messages", context)
        result.extend(_items(handle_command(parse_command(context.message), context)))
        return self._outcome(result, context)

    def poll(self, result, call):
        context = self._context(call.uow, event_key=call.event_key)
        results = self._lifecycle("on_poll", context)
        self.previous_poll = context.now
        return self._outcome(results, context)

    def _outcome(self, result, context):
        replies, tasks = [], []
        for index, value in enumerate(_items(result)):
            value = Reply(value) if isinstance(value, str) else value
            if not isinstance(value, (Reply, ReplyImage)):
                raise ConfigurationError("unsupported xiuxian reply type")
            target = value.target_id or context.conversation_id
            if target not in self.settings["groups"]:
                raise ConfigurationError("xiuxian reply target is outside its scope")
            session = context.connection_id
            key = value.request_key or f"{context.event_key}:reply:{index}"
            ttl = value.expires_in or 300
            if isinstance(value, ReplyImage):
                if not isinstance(value.image_path, CardPlan):
                    raise ConfigurationError("cards must be immutable render plans")
                tasks.append(TaskIntent("card:" + key, "render-card", {
                    "kind": value.image_path.kind, "snapshot": plain(value.image_path.payload),
                    "account": context.account_id, "target": target, "session": session,
                    "reply_key": key, "event_key": context.event_key, "ttl": ttl,
                }, catalog=self.rules_snapshot.ref))
            else:
                for part, text in enumerate(_text_chunks(value.text)):
                    replies.append(ReplyIntent(key if part == 0 else f"{key}:part:{part}",
                        context.account_id, target, session, text=text,
                        mentions=tuple(MemberRef(context.account_id, member, target) for member in value.mention_ids),
                        ttl_seconds=ttl, source_event_key=context.event_key))
        return Outcome.success(replies=tuple(replies), tasks=tuple(tasks))

    def _save(self, outcome, uow):
        for reply in outcome.replies:
            self.services.messages.enqueue(reply, uow)
        for task in outcome.tasks:
            self.services.tasks.enqueue(task, uow)

    def render_card(self, payload, call):
        key = "render:" + call.event_key
        saved = self.services.data.snapshots.get(key)
        if saved:
            self.services.data.assets.resolve(AssetRef(**saved.data["asset"]))
            return plain(saved.data)
        from .presentation import renderer
        snapshot = payload["snapshot"]
        if payload["kind"] == "profile":
            image = renderer.render_profile_card(snapshot["player"], snapshot["inventory"],
                snapshot["daily_info"], rules=snapshot["rules"])
        elif payload["kind"] == "ranking":
            image = renderer.render_ranking_card(snapshot["players"], total_count=snapshot["total_count"])
        elif payload["kind"] == "duel":
            image = renderer.render_duel_card(**snapshot)
        else:
            raise ConfigurationError("unknown xiuxian card kind")
        staged = self.services.data.assets.stage(BytesIO(image), kind="image")
        asset = self.services.data.assets.publish(staged)
        result = dict(payload, asset=asdict(asset))
        with self.services.data.database.transaction() as uow:
            self.services.data.snapshots.put(StateSnapshot(key, 1, 0, result, assets=(asset,)),
                                            expected_revision=None, uow=uow)
        return result

    def commit_card(self, result, call):
        asset = AssetRef(**result["asset"])
        key = "render:" + call.event_key
        snapshot = self.services.data.snapshots.get(key, call.uow)
        outcome = Outcome.success(replies=(ReplyIntent(
            result["reply_key"], result["account"], result["target"], result["session"],
            asset=asset, ttl_seconds=result["ttl"], source_event_key=result["event_key"]),))
        # The returned reply will retain the asset in this same commit.
        if snapshot:
            self.services.data.snapshots.delete(key, expected_revision=snapshot.revision, uow=call.uow)
        return outcome
