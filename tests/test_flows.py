"""Focused recovery and escrow checks for the migrated long-running flows."""
from __future__ import annotations

from datetime import datetime, timezone
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import sqlite3
import random
import tempfile
import unittest

from plux.api import Attempt, Receipt
from plux_plugins.xiuxian.application import duels
from plux_plugins.xiuxian.application.dungeon import service as dungeon_service
from plux_plugins.xiuxian.application.dungeon import combat, content, patterns
from plux_plugins.xiuxian.domain.config import GameConfig
from plux_plugins.xiuxian.domain.commands import Command
from plux_plugins.xiuxian.persistence import dungeon
from plux_plugins.xiuxian.persistence.duels import DuelStore
from plux_plugins.xiuxian.persistence.schema import MIGRATIONS
from plux_plugins.xiuxian.persistence.supports import SupportLedger


ACCOUNT = "bot"
GROUP = "group@chatroom"


def database(path: str = ":memory:") -> sqlite3.Connection:
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    for statement in MIGRATIONS[0].statements:
        db.execute(statement)
    return db


def players(db: sqlite3.Connection, *names: str) -> None:
    for name in names:
        db.execute(
            "INSERT INTO game_players(account_id,group_id,player_id,dao_name,dao_name_key)"
            " VALUES(?,?,?,?,?)", (ACCOUNT, GROUP, name, name + "修", name)
        )


def context(db: sqlite3.Connection, *, now: float = 1_700_000_000,
            session: str = "session-1", receipt=None):
    return SimpleNamespace(
        store=db, account_id=ACCOUNT, conversation_id=GROUP, user_id="p1",
        event_key="event-1", now=now, message=None,
        game_config=GameConfig(duel_enabled=True), connection_id=session,
        previous_poll_at=now - 1, runtime_issue=None, receiver_pending_since=None,
        allowed_targets={GROUP}, member_name=lambda group, player: player,
        reply_request_id=lambda key: key, receipt=receipt or (lambda key: None),
        cancel_reply=lambda key: None,
    )


class EventIdentityTests(unittest.TestCase):
    def test_committed_event_key_is_stable_action_identity(self):
        message = SimpleNamespace(message_id_candidate=None, event_key="session:42")
        self.assertEqual(duels._message_id(message), "session:42")
        self.assertEqual(dungeon_service._message_id(message), "session:42")


class SupportLedgerTests(unittest.TestCase):
    def test_paid_settlement_is_once_only(self):
        db = database()
        try:
            players(db, "p1", "p2", "p3", "p4")
            ctx = context(db)
            duel = DuelStore(ctx).insert(
                "p1", "p2", 1,
                '{"support_minimum":10,"support_maximum":100}',
                "session-1", duels._stamp(ctx.now),
            )
            ledger = SupportLedger(ctx, GROUP)
            ledger.register(duel, "p3", "p1", 20, duels._stamp(ctx.now))
            ledger.register(duel, "p4", "p2", 10, duels._stamp(ctx.now))
            first = ledger.settle(duel, winner="p1")
            second = ledger.settle(duel, winner="p1")
            self.assertEqual([row["payout"] for row in first], [30, 0])
            self.assertEqual([row["payout"] for row in second], [30, 0])
            balances = dict(db.execute(
                "SELECT player_id,spirit_stones FROM game_players"
                " WHERE player_id IN ('p3','p4')"
            ).fetchall())
            self.assertEqual(balances, {"p3": 110, "p4": 90})
        finally:
            db.close()

    def test_refund_is_once_only(self):
        db = database()
        try:
            players(db, "p1", "p2", "p3")
            ctx = context(db)
            duel = DuelStore(ctx).insert(
                "p1", "p2", 1,
                '{"support_minimum":10,"support_maximum":100}',
                "session-1", duels._stamp(ctx.now),
            )
            ledger = SupportLedger(ctx, GROUP)
            ledger.register(duel, "p3", "p1", 40, duels._stamp(ctx.now))
            ledger.settle(duel, refund=True)
            ledger.settle(duel, refund=True)
            row = db.execute("SELECT spirit_stones FROM game_players WHERE player_id='p3'").fetchone()
            self.assertEqual(row[0], 100)
            row = db.execute("SELECT settlement_state,payout FROM game_supports").fetchone()
            self.assertEqual(tuple(row), ("refunded", 40))
        finally:
            db.close()


class DuelRecoveryTests(unittest.TestCase):
    def test_challenge_uses_platform_event_identity(self):
        db = database()
        try:
            players(db, "p1", "p2")
            for item_id, owner in (("F101", "p1"), ("F102", "p2")):
                db.execute(
                    "INSERT INTO game_items"
                    "(item_id,account_id,group_id,template_id,rarity,owner_player_id,state)"
                    " VALUES(?,?,?,?,?,?,'held')",
                    (item_id, ACCOUNT, GROUP, "qingfeng_jian", "artifact", owner),
                )
            ctx = context(db)
            ctx.message = SimpleNamespace(
                message_id_candidate=None, event_key="event-1",
                content="#斗法 @p2", mentioned_ids=("p2",),
                mention_state="explicit_other", observed_at_ms=int(ctx.now * 1000),
            )
            result = duels.handle_command(Command("challenge", target_id="p2"), ctx)
            self.assertEqual(result.request_key.startswith("duel:"), True)
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM game_duels").fetchone()[0], 1,
            )
            self.assertIsNone(
                duels.handle_command(Command("challenge", target_id="p2"), ctx),
            )
        finally:
            db.close()

    def test_committed_prompt_receipt_resumes_phase_after_start(self):
        db = database()
        try:
            players(db, "p1", "p2")
            now = 1_700_000_000
            accepted = Receipt(
                "logical", "accepted",
                (Attempt(
                    "attempt-1", "accepted",
                    datetime.fromtimestamp(now - 4, timezone.utc),
                    datetime.fromtimestamp(now - 3, timezone.utc),
                ),),
            )
            ctx = context(db, now=now, receipt=lambda key: accepted)
            store = DuelStore(ctx)
            duel = store.insert(
                "p1", "p2", 1,
                '{"prompt_timeout_seconds":15,"invitation_timeout_seconds":60,"poll_stall_seconds":5}',
                "session-1", duels._stamp(now - 10),
            )
            store.update(
                duel, prompt_request_id="duel:" + duel["duel_id"] + ":phase:1",
                prompt_wait_started_at=duels._stamp(now - 5),
            )
            duels.on_start(ctx)
            row = store.get(duel["duel_id"])
            self.assertEqual(row["state"], "inviting")
            self.assertEqual(duels._seconds(row["phase_opened_at"]), now - 3)
            self.assertEqual(duels._seconds(row["phase_deadline_at"]), now + 57)
        finally:
            db.close()

    def test_result_notice_has_stable_key_until_receipt_exists(self):
        db = database()
        try:
            players(db, "p1", "p2")
            found = {}
            ctx = context(db, session="new-session",
                          receipt=lambda key: found.get(key))
            duel = DuelStore(ctx).insert(
                "p1", "p2", 1,
                '{"support_minimum":10,"support_maximum":100}',
                "old-session", duels._stamp(ctx.now - 10),
            )
            first = duels.on_before_messages(ctx)
            self.assertEqual(len(first), 1)
            key = f"duel:{duel['duel_id']}:result"
            self.assertEqual(first[0].request_key, key)
            self.assertEqual(duels.on_before_messages(ctx)[0].request_key, key)
            found[key] = object()
            self.assertEqual(duels.on_before_messages(ctx), [])
        finally:
            db.close()

    def test_session_change_refunds_support_once(self):
        db = database()
        try:
            players(db, "p1", "p2", "p3")
            ctx = context(db, session="new-session")
            store = DuelStore(ctx)
            duel = store.insert(
                "p1", "p2", 1,
                '{"support_minimum":10,"support_maximum":100}',
                "old-session", duels._stamp(ctx.now - 10),
            )
            SupportLedger(ctx, GROUP).register(
                duel, "p3", "p1", 20, duels._stamp(ctx.now - 4),
            )
            duels.on_before_messages(ctx)
            duels.on_before_messages(ctx)
            row = store.get(duel["duel_id"])
            self.assertEqual(row["state"], "cancelled")
            self.assertEqual(row["final_reason"], "technical:session_changed")
            self.assertEqual(
                db.execute("SELECT spirit_stones FROM game_players WHERE player_id='p3'").fetchone()[0],
                100,
            )
        finally:
            db.close()


class DungeonRecoveryTests(unittest.TestCase):
    def test_dungeon_notice_replays_until_logical_receipt_exists(self):
        db = database()
        try:
            found = {}
            ctx = context(db, receipt=lambda key: found.get(key))
            db.execute(
                "INSERT INTO dungeon_notices"
                "(notice_id,account_id,group_id,run_id,text)"
                " VALUES(?,?,?,?,?)",
                ("run-1:idle", ACCOUNT, GROUP, "run-1", "队伍已结束"),
            )
            first = dungeon_service.on_start(ctx)
            self.assertEqual(len(first), 1)
            self.assertEqual(first[0].request_key, "dungeon:run-1:idle")
            found[first[0].request_key] = object()
            self.assertEqual(dungeon_service.on_before_messages(ctx), [])
            self.assertEqual(
                db.execute("SELECT queued FROM dungeon_notices").fetchone()[0], 1,
            )
        finally:
            db.close()

    def test_run_state_survives_connection_restart(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = str(Path(temporary) / "xiuxian.db")
            db = database(path)
            try:
                players(db, "p1")
                state = {"code": "AB12", "phase": "gathering", "members": [{"player_id": "p1"}]}
                run_id = dungeon.new_run(
                    db, ACCOUNT, GROUP, "p1", "AB12", "正式", 1_700_000_000, state,
                )
                db.commit()
            finally:
                db.close()
            reopened = sqlite3.connect(path)
            reopened.row_factory = sqlite3.Row
            try:
                recovered, progress, deadline = dungeon.load_state(reopened, run_id)
                self.assertEqual(recovered, state)
                self.assertEqual(progress, 1_700_000_000)
                self.assertIsNone(deadline)
                ctx = context(reopened)
                dungeon_service.on_start(ctx)
                self.assertEqual(
                    reopened.execute("SELECT state FROM dungeon_runs WHERE run_id=?", (run_id,)).fetchone()[0],
                    "gathering",
                )
            finally:
                reopened.close()


    def test_dungeon_reward_settles_once_after_recovery(self):
        db = database()
        try:
            players(db, "p1")
            dungeon.profile(db, ACCOUNT, GROUP, "p1")
            state = {"code": "AB12", "phase": "explore",
                     "members": [{"player_id": "p1"}], "highest_night": 1}
            run_id = dungeon.new_run(
                db, ACCOUNT, GROUP, "p1", "AB12", "正式", 1_700_000_000, state,
            )
            db.execute(
                "UPDATE dungeon_runs SET state='active',departed_at=? WHERE run_id=?",
                (1_700_000_010, run_id),
            )
            db.execute(
                "UPDATE dungeon_run_members SET reward_eligible=1 WHERE run_id=?",
                (run_id,),
            )
            ctx = context(db, now=1_700_000_500)
            run = db.execute("SELECT * FROM dungeon_runs WHERE run_id=?", (run_id,)).fetchone()
            self.assertIsNotNone(
                dungeon_service._finish(ctx, run, state, "测试结算", random.Random(1)),
            )
            first = db.execute(
                "SELECT cultivation,spirit_stones FROM game_players WHERE player_id='p1'"
            ).fetchone()
            self.assertIsNone(
                dungeon_service._finish(ctx, run, state, "重复结算", random.Random(1)),
            )
            second = db.execute(
                "SELECT cultivation,spirit_stones FROM game_players WHERE player_id='p1'"
            ).fetchone()
            self.assertEqual(tuple(first), tuple(second))
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM dungeon_run_state WHERE run_id=?",
                           (run_id,)).fetchone()[0], 0,
            )
        finally:
            db.close()

    def test_dungeon_creation_and_join_continue_after_restart(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = str(Path(temporary) / "command.db")
            db = database(path)
            try:
                players(db, "p1", "p2")
                ctx = context(db)
                ctx.message = SimpleNamespace(
                    message_id_candidate=None, event_key="event-1", content="#副本创建",
                    observed_at_ms=int(ctx.now * 1000),
                )
                result = dungeon_service.handle_command(Command("dungeon_create"), ctx)
                self.assertIn("副本准备", result)
                run = db.execute("SELECT run_id,code FROM dungeon_runs").fetchone()
                db.commit()
            finally:
                db.close()
            reopened = sqlite3.connect(path)
            reopened.row_factory = sqlite3.Row
            try:
                ctx = context(reopened)
                dungeon_service.on_start(ctx)
                ctx.user_id = "p2"
                ctx.event_key = "event-2"
                ctx.message = SimpleNamespace(
                    message_id_candidate=None, event_key="event-2", content="#副本加入 " + run["code"],
                    observed_at_ms=int(ctx.now * 1000),
                )
                result = dungeon_service.handle_command(
                    Command("dungeon_join", run["code"]), ctx,
                )
                self.assertIn("新成员请准备", result)
                state, _, _ = dungeon.load_state(reopened, run["run_id"])
                self.assertEqual(
                    {member["player_id"] for member in state["members"]},
                    {"p1", "p2"},
                )
            finally:
                reopened.close()


class DungeonRulesTests(unittest.TestCase):
    def test_catalog_and_pattern_roster_are_available_from_new_package(self):
        content.validate_catalog()
        self.assertEqual(
            (len(content.CLASSES), len(content.ROOTS), len(content.ENEMIES),
             len(content.BOSSES), len(content.UPGRADES),
             len(content.EQUIPMENT), len(content.EXPLORATION_NODES)),
            (8, 30, 24, 6, 28, 6, 6),
        )
        self.assertEqual(
            set(patterns.PATTERNS),
            {f"M{i:02d}" for i in range(1, 25)}
            | {f"B{i:02d}" for i in range(1, 7)},
        )

    def test_combat_round_is_pure_and_json_restorable(self):
        actor = combat.create_member("p1", "p1修", "C01")
        battle = combat.start_battle([actor], "M01", rng=random.Random(3))
        before = deepcopy(battle)
        result, logs = combat.resolve_round(
            battle, {"p1": "attack"}, random.Random(4),
        )
        self.assertEqual(battle, before)
        self.assertEqual(json.loads(json.dumps(result)), result)
        self.assertIsInstance(logs, list)


class BacklogDeadlineTests(unittest.TestCase):
    def test_dungeon_idle_timeout_waits_for_unread_messages(self):
        db = database()
        try:
            players(db, "p1")
            state = {"code": "AB12", "phase": "gathering",
                     "members": [{"player_id": "p1"}],
                     "ready": [], "highest_night": 0}
            run_id = dungeon.new_run(
                db, ACCOUNT, GROUP, "p1", "AB12", "正式", 1_700_000_000, state,
            )
            ctx = context(db, now=1_700_001_000)
            ctx.runtime_issue = "receiver_catching_up"
            dungeon_service.on_poll(ctx)
            self.assertEqual(
                db.execute("SELECT state FROM dungeon_runs WHERE run_id=?", (run_id,)).fetchone()[0],
                "gathering",
            )
            ctx.runtime_issue = None
            dungeon_service.on_poll(ctx)
            self.assertEqual(
                db.execute("SELECT state FROM dungeon_runs WHERE run_id=?", (run_id,)).fetchone()[0],
                "finished",
            )
        finally:
            db.close()

    def test_last_on_time_dungeon_command_survives_processing_delay(self):
        db = database()
        try:
            players(db, "p1")
            state = {"code": "AB12", "phase": "gathering", "version": 1,
                     "token": "AB12.1", "leader": "p1",
                     "members": [{"player_id": "p1", "name": "p1修",
                                  "class_id": "C01", "roots": []}],
                     "ready": [], "highest_night": 0}
            run_id = dungeon.new_run(
                db, ACCOUNT, GROUP, "p1", "AB12", "正式", 1_700_000_000, state,
            )
            ctx = context(db, now=1_700_001_000)
            ctx.message = SimpleNamespace(
                message_id_candidate=None, event_key="last-on-time",
                content="#副本准备", observed_at_ms=1_700_000_100_000,
            )
            ctx.event_key = "last-on-time"
            result = dungeon_service.handle_command(Command("dungeon_ready"), ctx)
            self.assertIn("p1修", result)
            self.assertEqual(
                db.execute("SELECT state FROM dungeon_runs WHERE run_id=?", (run_id,)).fetchone()[0],
                "gathering",
            )
            recovered, _, _ = dungeon.load_state(db, run_id)
            self.assertEqual(recovered["ready"], ["p1"])
        finally:
            db.close()

    def test_pvp_poll_does_not_expire_invitation_during_backlog(self):
        db = database()
        try:
            players(db, "p1", "p2")
            db.execute(
                "INSERT INTO game_pvp_duels"
                "(duel_id,account_id,group_id,challenger_id,challenged_id,state)"
                " VALUES(?,?,?,?,?,'inviting')",
                ("PVP1", ACCOUNT, GROUP, "p1", "p2"),
            )
            ctx = context(db)
            ctx.runtime_issue = "receiver_catching_up"
            duels.poll_pvp_duels(ctx)
            self.assertEqual(
                db.execute("SELECT state FROM game_pvp_duels WHERE duel_id='PVP1'").fetchone()[0],
                "inviting",
            )
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()