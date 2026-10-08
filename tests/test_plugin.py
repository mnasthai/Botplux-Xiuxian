"""Offline integration of the independent plugin with the actual Plux runtime."""
from __future__ import annotations
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
import ast
import json
import tempfile
import unittest

from plux.api import (AssetRef, ConfigurationError, ConnectionSnapshot, ContentQuality, HistoryQuery,
                      MemberRef, MessageIdentity, ReplyIntent, TextMessage)
from plux.runtime.bootstrap import Application, validate_plugins
from plux.runtime.config import (ObserverConfig, PluginEntry, PolicyConfig,
                                 RuntimeConfig, RuntimePaths)
from plux_plugins.xiuxian.domain.catalog import CATALOG
from plux_plugins.xiuxian.persistence.game import GameRepository
from plux_plugins.xiuxian.presentation.models import CardPlan
from plux_plugins.xiuxian.settings import validate_config

ACCOUNT = "bot"
GROUP = "group@chatroom"

def configuration(root, *, visual=False, groups=(GROUP,), policy=None, rules=None):
    paths = RuntimePaths(root, root / "platform.sqlite3", root / "observer-4.1.13.12.jsonl",
                         root / "inbound", root / "outbound", root / "staging", root / "cache")
    settings = {"account": ACCOUNT, "groups": list(groups), "admin_ids": [],
                "rules": {"duel_enabled": True, "visual_cards_enabled": visual, **(rules or {})}}
    entry = PluginEntry("plux_plugins.xiuxian:XiuxianPlugin", config=settings)
    return RuntimeConfig(root / "plux.toml", paths,
        policy or PolicyConfig(ACCOUNT, (GROUP,), group_requires_mention=False, allow_unknown_history=True),
        ObserverConfig(enabled=False), (entry,))

class PluginIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.apps = []
        self.patches = []
        self.seq = 0

    def tearDown(self):
        for app in reversed(self.apps):
            app.stop()
        for item in reversed(self.patches):
            item.stop()
        self.directory.cleanup()

    def app(self, config=None, session="s1"):
        app = Application(config or configuration(self.root)).prepare()
        connection = ConnectionSnapshot(account=ACCOUNT, native_session=session, phase="read_only")
        item = patch.object(app.observer, "connection", return_value=connection)
        item.start()
        self.patches.append(item)
        self.apps.append(app)
        app.start(background=False)
        return app

    def ingest(self, app, text, actor="p1", *, mentions=(), ingestion="new", history="realtime"):
        self.seq += 1
        raw = json.dumps({
            "kind": "item", "schema_version": 2, "session_id": "s1", "seq": self.seq,
            "call_id": self.seq, "index": 0,
            "observed_unix_ms": int(datetime.now(timezone.utc).timestamp() * 1000),
            "from": GROUP, "to": ACCOUNT, "content": actor + ":\n" + text, "msg_type": 1,
            "content_read": {"status": "ok"}, "from_read": {"status": "ok"},
            "to_read": {"status": "ok"}, "source_read": {"status": "ok"},
            "msg_source": ("<msgsource><atuserlist><![CDATA[" + ",".join(mentions) + "]]></atuserlist></msgsource>") if mentions else "<msgsource></msgsource>",
        }, ensure_ascii=False).encode("utf-8") + b"\n"
        message = app.messages.ingest(raw, "fixture:" + str(self.seq), 0, len(raw),
                                     ingestion_status=ingestion, history_status=history)
        self.assertIsInstance(message, TextMessage)
        return message

    def execute(self, app, text, actor="p1", **kwargs):
        message = self.ingest(app, text, actor, **kwargs)
        self.assertTrue(app.executor.process(message, app.messages))
        return message

    def rows(self, app, query, parameters=()):
        with app.database.transaction() as uow:
            return uow.execute(query, parameters).fetchall()

    def test_metadata_validation_does_not_open_database(self):
        loaded = validate_plugins(configuration(self.root))
        self.assertEqual(loaded[0].manifest.plugin_id, "xiuxian")
        self.assertFalse((self.root / "platform.sqlite3").exists())

    def test_register_cultivate_duplicate_and_restart(self):
        app = self.app()
        created = self.execute(app, "#修仙 青玄")
        self.execute(app, "#修炼")
        self.assertTrue(app.executor.process(created, app.messages))
        self.assertEqual(tuple(self.rows(app, "SELECT cultivation,spirit_stones FROM game_players")[0]), (50, 200))
        self.assertEqual(self.rows(app, "SELECT COUNT(*) FROM plux_replies")[0][0], 2)
        app.stop()
        restarted = self.app()
        self.assertTrue(restarted.executor.process(created, restarted.messages))
        self.assertEqual(self.rows(restarted, "SELECT COUNT(*) FROM game_players")[0][0], 1)
        self.assertEqual(self.rows(restarted, "SELECT COUNT(*) FROM plux_replies")[0][0], 2)

    def test_reply_failure_rolls_back_character_and_retries(self):
        app = self.app()
        message = self.ingest(app, "#修仙 青玄")
        with patch.object(app.messages, "allowed_targets", frozenset()):
            self.assertFalse(app.executor.process(message, app.messages))
        self.assertEqual(self.rows(app, "SELECT COUNT(*) FROM game_players")[0][0], 0)
        self.assertEqual(self.rows(app, "SELECT COUNT(*) FROM plux_replies")[0][0], 0)
        self.assertTrue(app.executor.process(message, app.messages))
        self.assertEqual(self.rows(app, "SELECT COUNT(*) FROM game_players")[0][0], 1)

    def test_command_events_apply_framework_quality_and_mention_policy(self):
        policy = PolicyConfig(ACCOUNT, (GROUP,), group_requires_mention=True, allow_unknown_history=False)
        app = self.app(configuration(self.root, policy=policy))
        for index, quality in enumerate((
            ContentQuality(history_status="realtime", ingestion_status="backlog", mentions_status="explicit_self"),
            ContentQuality(history_status="unknown", ingestion_status="new", mentions_status="explicit_self"),
            ContentQuality(status="truncated", history_status="realtime", ingestion_status="new", mentions_status="explicit_self"),
            ContentQuality(history_status="realtime", ingestion_status="new", mentions_status="none"),
        )):
            source = TextMessage(event_key=f"bad:{index}", identity=MessageIdentity(ACCOUNT, GROUP, "p1", "s1"),
                observed_at=datetime.now(timezone.utc), quality=quality, text="#修仙 青玄",
                mentions=(MemberRef(ACCOUNT, ACCOUNT, GROUP),))
            self.assertTrue(app.executor.process(source))
        self.assertEqual(self.rows(app, "SELECT COUNT(*) FROM game_players")[0][0], 0)
        self.execute(app, "@机器人\u2005#修仙 青玄", mentions=(ACCOUNT,))
        self.assertEqual(self.rows(app, "SELECT COUNT(*) FROM game_players")[0][0], 1)

    def test_opponent_identity_uses_verified_native_mentions(self):
        app = self.app()
        self.execute(app, "#修仙 青玄")
        self.execute(app, "#修仙 白羽", "p2")
        with app.database.transaction() as uow:
            for actor in ("p1", "p2"):
                GameRepository(uow, ACCOUNT, GROUP, actor).grant_item(CATALOG["qingfeng_jian"], datetime.now(timezone.utc).isoformat())
        self.execute(app, "#斗法 @白羽\u2005", mentions=("p2",))
        row = self.rows(app, "SELECT challenger_id,challenged_id,prompt_request_id FROM game_duels")[0]
        self.assertEqual(tuple(row[:2]), ("p1", "p2"))
        self.assertIsNotNone(row["prompt_request_id"])
        self.assertEqual(self.rows(app, "SELECT COUNT(*) FROM plux_replies WHERE reply_key=?", (row["prompt_request_id"],))[0][0], 1)

    def test_rejected_duel_preserves_already_attempted_prompt(self):
        app = self.app()
        self.execute(app, "#修仙 青玄")
        self.execute(app, "#修仙 白羽", "p2")
        with app.database.transaction() as uow:
            for actor in ("p1", "p2"):
                GameRepository(uow, ACCOUNT, GROUP, actor).grant_item(CATALOG["qingfeng_jian"], datetime.now(timezone.utc).isoformat())
        self.execute(app, "#斗法 @白羽\u2005", mentions=("p2",))
        key = self.rows(app, "SELECT prompt_request_id FROM game_duels")[0][0]
        # A stage opens only after its corresponding attempt is accepted.
        with app.database.transaction() as uow:
            reference = app.services["xiuxian"].messages.reply_reference(key, uow)
            stamp = uow.execute("SELECT prompt_wait_started_at FROM game_duels").fetchone()[0]
            uow.execute("UPDATE plux_replies SET status='accepted' WHERE request_id=?", (reference.request_id,))
            uow.execute("INSERT INTO plux_delivery_attempts(attempt_id,request_id,status,started_at,finished_at) VALUES(?,?,'accepted',?,?)",
                        ("a1", reference.request_id, stamp, stamp))
        self.execute(app, "#拒绝斗法", "p2")
        self.assertEqual(tuple(self.rows(app, "SELECT state,final_reason FROM game_duels")[0]), ("cancelled", "declined"))
        # Already attempted prompts are never rewritten to cancelled.
        self.assertEqual(self.rows(app, "SELECT status FROM plux_replies WHERE reply_key=?", (key,))[0][0], "accepted")

    def test_public_reply_queries_and_cancellation_share_current_transaction(self):
        app = self.app()
        messages = app.services["xiuxian"].messages
        other = app.messages.for_plugin("other")
        with app.database.transaction() as uow:
            reference = messages.enqueue(ReplyIntent("cancel-me", ACCOUNT, GROUP, "s1", text="prompt"), uow)
            self.assertEqual(messages.reply_reference("cancel-me", uow), reference)
            self.assertIsNone(other.reply_reference("cancel-me", uow))
            self.assertFalse(other.cancel(reference.request_id, uow))
            self.assertEqual(messages.receipt(reference.request_id, uow).status, "queued")
            self.assertEqual(messages.member(MemberRef(ACCOUNT, "p1", GROUP), uow).source, "missing")
            self.assertEqual(messages.history(HistoryQuery(ACCOUNT, GROUP), uow).items, ())
            self.assertTrue(messages.cancel(reference.request_id, uow))
            self.assertFalse(messages.cancel(reference.request_id, uow))
            self.assertEqual(messages.receipt(reference.request_id, uow).status, "cancelled")

    def test_cancel_media_releases_reference_and_rolls_back_with_caller(self):
        from io import BytesIO
        app = self.app()
        data = app.services["xiuxian"].data
        messages = app.services["xiuxian"].messages
        asset = data.assets.publish(data.assets.stage(BytesIO(b"image-fixture"), kind="image"))
        with app.database.transaction() as uow:
            reference = messages.enqueue(ReplyIntent("media", ACCOUNT, GROUP, "s1", asset=asset), uow)
        with self.assertRaises(RuntimeError):
            with app.database.transaction() as uow:
                self.assertTrue(messages.cancel(reference.request_id, uow))
                raise RuntimeError("abort")
        self.assertEqual(messages.receipt(reference.request_id).status, "queued")
        with app.database.transaction() as uow:
            self.assertTrue(messages.cancel(reference.request_id, uow))
        self.assertEqual(messages.receipt(reference.request_id).status, "cancelled")

    def test_dungeon_state_survives_actual_application_restart(self):
        app = self.app()
        self.execute(app, "#修仙 青玄")
        self.execute(app, "#副本创建")
        row = self.rows(app, "SELECT run_id,code FROM dungeon_runs")[0]
        self.assertEqual(self.rows(app, "SELECT COUNT(*) FROM dungeon_run_state")[0][0], 1)
        app.stop()
        restarted = self.app()
        self.execute(restarted, "#修仙 白羽", "p2")
        self.execute(restarted, "#副本加入 " + row["code"], "p2")
        self.assertEqual(self.rows(restarted, "SELECT COUNT(*) FROM dungeon_run_members WHERE run_id=?", (row["run_id"],))[0][0], 2)

    def test_profile_renders_after_transaction_and_commits_asset_once(self):
        app = self.app(configuration(self.root, visual=True))
        self.execute(app, "#修仙 青玄")
        source = self.execute(app, "#修仙")
        task_key = "card:" + source.event_key + ":reply:0"
        self.assertEqual(self.rows(app, "SELECT COUNT(*) FROM plux_assets")[0][0], 0)
        for _ in range(5):
            app.tasks.tick()
            task = app.services["xiuxian"].tasks.get(task_key)
            if task and task["status"] == "committed":
                break
        self.assertEqual(task["status"], "committed")
        asset = AssetRef(**task["result"]["asset"])
        image_path = app.services["xiuxian"].data.assets.resolve(asset)
        from PIL import Image
        with Image.open(image_path) as image:
            self.assertEqual(image.size, (1080, 1440))
            self.assertEqual(image.format, "PNG")
        self.assertEqual(self.rows(app, "SELECT COUNT(*) FROM plux_replies WHERE kind='image'")[0][0], 1)
        self.assertTrue(app.executor.process(source, app.messages))
        self.assertEqual(self.rows(app, "SELECT COUNT(*) FROM plux_replies WHERE kind='image'")[0][0], 1)

    def test_active_games_keep_their_rules_on_restart(self):
        app = self.app()
        self.execute(app, "#修仙 青玄")
        self.execute(app, "#副本创建")
        previous = app.loaded[0].plugin.rules_snapshot.ref.version
        app.stop()
        config = configuration(self.root, rules={"cultivation_reward": 75})
        restarted = self.app(config)
        plugin = restarted.loaded[0].plugin
        self.assertEqual(plugin.rules.cultivation_reward, 50)
        self.assertEqual(plugin.rules_snapshot.ref.version, previous)

    def test_pending_inputs_postpone_poll_expiration(self):
        app = self.app()
        self.execute(app, "#修仙 青玄")
        self.execute(app, "#副本创建")
        self.ingest(app, "#副本", "p1")
        with app.database.transaction() as uow:
            uow.execute("UPDATE dungeon_run_state SET last_progress=0")
        self.assertTrue(app.tasks.tick())
        self.assertEqual(self.rows(app, "SELECT state FROM dungeon_runs")[0][0], "gathering")

class BoundaryTests(unittest.TestCase):
    def test_card_plan_copies_and_freezes_nested_snapshot(self):
        original = {"players": [{"name": "青玄"}]}
        plan = CardPlan("ranking", original)
        original["players"][0]["name"] = "changed"
        self.assertEqual(plan.payload["players"][0]["name"], "青玄")
        with self.assertRaises(TypeError):
            plan.payload["players"][0]["name"] = "no"

    def test_configuration_rejects_typo_and_invalid_rules(self):
        with self.assertRaises(ConfigurationError):
            validate_config({"accounts": "bot"})
        with self.assertRaises(ConfigurationError):
            validate_config({"rules": {"self_cultivation_success_percent": 101}})
        with self.assertRaises(ConfigurationError):
            validate_config({"groups": [GROUP]})

    def test_plugin_imports_only_public_framework_api(self):
        package = Path(__file__).parents[1] / "src/plux_plugins/xiuxian"
        for path in package.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            for node in ast.walk(tree):
                modules = [entry.name for entry in node.names] if isinstance(node, ast.Import) else [node.module or ""] if isinstance(node, ast.ImportFrom) and not node.level else []
                for module in modules:
                    self.assertFalse(module.startswith("wechat_receiver"), str(path))
                    if module == "plux" or module.startswith("plux."):
                        self.assertEqual(module, "plux.api", str(path))

if __name__ == "__main__":
    unittest.main()
