"""Transaction-level Xiuxian behaviour with the migrated schema."""
from __future__ import annotations
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import sqlite3
from types import SimpleNamespace
import unittest

from plux_plugins.xiuxian.application.single_player import handle_command
from plux_plugins.xiuxian.domain.commands import parse_command
from plux_plugins.xiuxian.domain.config import DEFAULT_GAME_CONFIG
from plux_plugins.xiuxian.persistence.schema import GAME_SQL, DUNGEON_SQL


class FixedRng:
    def __init__(self, *rolls):
        self.rolls = list(rolls)

    def randrange(self, stop):
        value = self.rolls.pop(0) if self.rolls else 0
        assert 0 <= value < stop
        return value

    def choice(self, values):
        return values[0]


class CoreTests(unittest.TestCase):
    account = "test_bot"
    group = "test@chatroom"

    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(GAME_SQL + DUNGEON_SQL)
        self.now = datetime(2026, 10, 8, tzinfo=timezone.utc).timestamp()
        self.sequence = 0

    def tearDown(self):
        self.db.close()

    def run_command(self, text, *, player="alice", event_key=None, game_config=None,
                    mention_state="none", mentioned_ids=(), runtime_issue=None,
                    observed_at=None, rng=None):
        self.sequence += 1
        message = SimpleNamespace(
            content=text, message_kind="text", conversation_id=self.group,
            sender_id=player, message_id_candidate=str(10000 + self.sequence),
            mention_state=mention_state, mentioned_ids=mentioned_ids,
            observed_at_ms=int((self.now if observed_at is None else observed_at) * 1000))
        context = SimpleNamespace(
            store=self.db, account_id=self.account, conversation_id=self.group,
            user_id=player, event_key=event_key or f"event:{self.sequence}",
            message=message, game_config=game_config or DEFAULT_GAME_CONFIG, now=self.now,
            allowed_targets=frozenset({self.group}), is_admin=False,
            runtime_issue=runtime_issue,
            member_name=lambda group, player_id: None,
            card=lambda kind, payload: (kind, payload))
        command = parse_command(message)
        assert command is not None
        return handle_command(command, context, rng=rng)

    def player(self, player="alice"):
        return self.db.execute(
            "SELECT * FROM game_players WHERE account_id=? AND group_id=? AND player_id=?",
            (self.account, self.group, player)).fetchone()

    def test_registration_daily_reward_and_duplicate_event(self):
        self.run_command("#修仙 青玄")
        self.assertEqual(100, self.player()["spirit_stones"])
        self.run_command("#修炼")
        self.assertEqual((50, 200), (self.player()["cultivation"],
                                     self.player()["spirit_stones"]))
        self.assertIn("今日", self.run_command("#修炼"))
        self.assertEqual(2, self.db.execute("SELECT COUNT(*) FROM game_actions").fetchone()[0])
        self.assertIsNone(self.run_command("#修炼", event_key="event:2"))
        self.assertEqual((50, 200), (self.player()["cultivation"],
                                     self.player()["spirit_stones"]))

    def test_shop_debit_item_and_scope(self):
        self.run_command("#修仙 青玄")
        result = self.run_command("#购买 替身草人")
        self.assertIn("替身草人", result)
        self.assertEqual(12, self.player()["spirit_stones"])
        item = self.db.execute(
            "SELECT template_id,owner_player_id,state FROM game_items WHERE template_id=?",
            ("tishen_caoren",)).fetchone()
        self.assertEqual(("tishen_caoren", "alice", "held"),
                         (item["template_id"], item["owner_player_id"], item["state"]))
        self.assertEqual(1, self.db.execute(
            "SELECT COUNT(*) FROM game_shop_purchases").fetchone()[0])

    def test_pvp_invitation_reserves_wagers_only_on_accept(self):
        self.run_command("#修仙 青玄", player="alice")
        self.run_command("#修仙 清霜", player="bob")
        self.run_command("#修炼", player="alice")
        self.run_command("#修炼", player="bob")
        invite = self.run_command(
            "#决斗 @清霜\u2005 10", player="alice",
            mention_state="explicit_other", mentioned_ids=("bob",))
        self.assertIn("战书", invite)
        self.assertEqual((200, 200), (
            self.player("alice")["spirit_stones"],
            self.player("bob")["spirit_stones"]))
        duel = self.db.execute("SELECT * FROM game_pvp_duels").fetchone()
        self.assertEqual(("inviting", 0), (duel["state"], duel["escrowed"]))
        self.run_command("#接受决斗", player="bob")
        duel = self.db.execute("SELECT * FROM game_pvp_duels").fetchone()
        self.assertIn(duel["state"], ("fighting", "settled"))
        total = (self.player("alice")["spirit_stones"] +
                 self.player("bob")["spirit_stones"])
        self.assertEqual(400, total + (20 if duel["state"] == "fighting" else 0))

    def test_pvp_timeout_waits_while_receiver_is_catching_up(self):
        self.run_command("#修仙 青玄", player="alice")
        self.run_command("#修仙 清霜", player="bob")
        self.run_command("#修炼", player="alice")
        self.run_command("#修炼", player="bob")
        self.run_command(
            "#决斗 @清霜\u2005 10", player="alice",
            mention_state="explicit_other", mentioned_ids=("bob",))
        self.now += 61
        reply = self.run_command("#接受决斗", player="bob",
                                 runtime_issue="receiver_catching_up")
        self.assertNotIn("已超时", str(reply))
        duel = self.db.execute("SELECT state FROM game_pvp_duels").fetchone()
        self.assertIn(duel["state"], ("fighting", "settled"))

    def test_captured_pvp_acceptance_precedes_wall_clock_timeout(self):
        self.run_command("#修仙 青玄", player="alice")
        self.run_command("#修仙 清霜", player="bob")
        self.run_command("#修炼", player="alice")
        self.run_command("#修炼", player="bob")
        self.run_command(
            "#决斗 @清霜\u2005 10", player="alice",
            mention_state="explicit_other", mentioned_ids=("bob",))
        captured = self.now + 59
        self.now += 61
        reply = self.run_command("#接受决斗", player="bob",
                                 observed_at=captured)
        self.assertNotIn("已超时", str(reply))
        duel = self.db.execute("SELECT state FROM game_pvp_duels").fetchone()
        self.assertIn(duel["state"], ("fighting", "settled"))

    def test_failed_self_cultivation_still_starts_cooldown(self):
        rules = replace(DEFAULT_GAME_CONFIG, self_cultivation_success_percent=10)
        self.run_command("#修仙 青玄")
        self.run_command("#自主修炼", game_config=rules, rng=FixedRng(99))
        self.assertEqual(0, self.player()["cultivation"])
        self.assertEqual(1, self.db.execute(
            "SELECT COUNT(*) FROM game_actions WHERE action_kind='self_cultivate'").fetchone()[0])
        self.run_command("#修炼", game_config=rules)
        self.now += 3599
        self.assertIn("尚需 1 秒", self.run_command(
            "#自主修炼", game_config=rules, rng=FixedRng(0)))
        self.assertEqual(50, self.player()["cultivation"])
        self.now += 1
        self.run_command("#自主修炼", game_config=rules, rng=FixedRng(0))
        self.assertEqual(55, self.player()["cultivation"])
        self.assertEqual(2, self.db.execute(
            "SELECT COUNT(*) FROM game_actions WHERE action_kind='self_cultivate'").fetchone()[0])

    def test_mining_cooldown_and_deterministic_reward(self):
        self.run_command("#修仙 青玄")
        self.assertIn("+12", self.run_command("#采矿", rng=FixedRng(2, 50)))
        self.assertEqual(112, self.player()["spirit_stones"])
        self.assertIn("尚需 3600 秒", self.run_command("#采矿"))
        self.now += 1800
        self.assertIn("尚需 1800 秒", self.run_command("#采矿"))
        self.now += 1800
        self.assertIn("+13", self.run_command("#开采", rng=FixedRng(3, 50)))
        self.assertEqual(125, self.player()["spirit_stones"])

    def test_devil_resigns_above_historical_peak(self):
        self.run_command("#修仙 青玄")
        self.db.execute("""UPDATE game_players SET realm='foundation',
            cultivation=100, spirit_stones=1000, devil_max_contract_tier=2
            WHERE account_id=? AND group_id=? AND player_id='alice'""",
            (self.account, self.group))
        status = self.run_command("#魔契")
        self.assertIn("历史最高签订：第 2 层", status)
        self.assertIn("再次签约（第 3 层）", status)
        self.run_command("#签订魔契")
        row = self.player()
        self.assertEqual((3, 3, 25, 2200), tuple(row[key] for key in (
            "devil_contract_tier", "devil_max_contract_tier",
            "cultivation", "spirit_stones")))
        self.assertEqual(1, self.db.execute(
            "SELECT COUNT(*) FROM game_actions WHERE action_kind='devil_sign'").fetchone()[0])

    def test_shop_consumable_and_group_daily_stock(self):
        self.run_command("#修仙 青玄")
        self.db.execute("""UPDATE game_players SET spirit_stones=1000, cultivation=10
            WHERE account_id=? AND group_id=? AND player_id='alice'""",
            (self.account, self.group))
        for index in range(5):
            text = self.run_command("#购买 凝气丹" if index == 0 else "#购买 2")
            self.assertIn("购买成功", text)
        self.assertEqual((110, 250), (
            self.player()["cultivation"], self.player()["spirit_stones"]))
        self.assertIn("库存已售罄", self.run_command("#购买 凝气丹"))
        self.assertEqual(250, self.player()["spirit_stones"])

    def test_pvp_escrow_settlement_is_idempotent(self):
        from plux_plugins.xiuxian.persistence.game import GameRepository
        self.run_command("#修仙 青玄", player="alice")
        self.run_command("#修仙 清霜", player="bob")
        self.db.execute("UPDATE game_players SET spirit_stones=200")
        repo = GameRepository(self.db, self.account, self.group, "alice")
        now_iso = datetime.fromtimestamp(self.now, timezone.utc).isoformat()
        duel_id = repo.create_pvp_duel("alice", "bob", 50, now_iso)
        self.assertEqual((200, 200), (
            self.player("alice")["spirit_stones"], self.player("bob")["spirit_stones"]))
        repo.reserve_pvp_wagers(duel_id)
        self.assertEqual((150, 150), (
            self.player("alice")["spirit_stones"], self.player("bob")["spirit_stones"]))
        self.assertTrue(repo.finish_pvp_duel(duel_id, "settled", now_iso, winner_id="alice"))
        self.assertFalse(repo.finish_pvp_duel(duel_id, "settled", now_iso, winner_id="alice"))
        self.assertEqual((250, 150), (
            self.player("alice")["spirit_stones"], self.player("bob")["spirit_stones"]))

    def test_pvp_capture_after_deadline_expires_invitation(self):
        self.run_command("#修仙 青玄", player="alice")
        self.run_command("#修仙 清霜", player="bob")
        self.run_command("#修炼", player="alice")
        self.run_command("#修炼", player="bob")
        self.run_command(
            "#决斗 @清霜\u2005 10", player="alice",
            mention_state="explicit_other", mentioned_ids=("bob",))
        captured = self.now + 61
        self.now += 62
        reply = self.run_command("#接受决斗", player="bob", observed_at=captured)
        self.assertIn("已超时", reply)
        duel = self.db.execute("SELECT state FROM game_pvp_duels").fetchone()
        self.assertEqual("cancelled", duel["state"])
        self.assertEqual((200, 200), (
            self.player("alice")["spirit_stones"],
            self.player("bob")["spirit_stones"]))

    def test_shop_prop_targets_verified_member_and_is_consumed_once(self):
        self.run_command("#修仙 青玄", player="alice")
        self.run_command("#修仙 清霜", player="bob")
        self.assertIn("购买成功", self.run_command("#购买 扰心符", player="alice"))
        self.assertEqual(85, self.player("alice")["spirit_stones"])
        result = self.run_command("#使用 扰心符 @清霜\u2005", player="alice",
                                  mention_state="explicit_other",
                                  mentioned_ids=("bob",))
        self.assertIn("暗算得手", result)
        self.assertEqual(0, self.db.execute(
            "SELECT COUNT(*) FROM game_player_props WHERE player_id='alice'").fetchone()[0])
        debuff = self.db.execute(
            "SELECT target_player_id,caster_player_id,debuff_kind FROM game_player_debuffs").fetchone()
        self.assertEqual(("bob", "alice", "raoxin_fu"), tuple(debuff))
        self.assertIn("并未找到", self.run_command(
            "#使用 扰心符 @清霜\u2005", player="alice",
            mention_state="explicit_other", mentioned_ids=("bob",)))

    def test_pvp_round_confirmation_uses_capture_time(self):
        from plux_plugins.xiuxian.persistence.game import GameRepository
        self.run_command("#修仙 青玄", player="alice")
        self.run_command("#修仙 清霜", player="bob")
        repo = GameRepository(self.db, self.account, self.group, "alice")
        started = datetime.fromtimestamp(self.now, timezone.utc)
        duel_id = repo.create_pvp_duel("alice", "bob", 10, started.isoformat())
        repo.reserve_pvp_wagers(duel_id)
        state = {"fighter1": {"name": "青玄", "player_id": "alice"},
                 "fighter2": {"name": "清霜", "player_id": "bob"},
                 "p1_confirmed": False, "p2_confirmed": False}
        deadline = (started + timedelta(seconds=60)).isoformat()
        repo.update_pvp_duel_round_state(duel_id, 1, json.dumps(state), deadline)
        captured = self.now + 59
        self.now += 61
        reply = self.run_command("#继续决斗", player="alice", observed_at=captured)
        self.assertIn("已确认继续决斗", str(reply))
        duel = repo.get_pvp_duel(duel_id)
        self.assertEqual("fighting", duel["state"])
        self.assertTrue(json.loads(duel["round_state_json"])["p1_confirmed"])
        self.assertEqual((90, 90), (
            self.player("alice")["spirit_stones"], self.player("bob")["spirit_stones"]))

    def test_profile_card_is_a_snapshot_created_without_render_io(self):
        self.run_command("#修仙 青玄")
        kind, payload = self.run_command("#修仙", game_config=replace(
            DEFAULT_GAME_CONFIG, visual_cards_enabled=True))
        self.assertEqual("profile", kind)
        self.assertEqual("青玄", payload["player"]["dao_name"])
        self.assertIsInstance(payload["inventory"], list)


if __name__ == "__main__":
    unittest.main()

