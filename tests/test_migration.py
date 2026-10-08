from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from plux_plugins.xiuxian.persistence.migration import MigrationError, migrate
from plux_plugins.xiuxian.persistence.schema import MIGRATIONS


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "legacy.sqlite3"
        self.target = self.root / "game.sqlite3"
        with closing(sqlite3.connect(self.source)) as db, db:
            for migration in MIGRATIONS:
                for statement in migration.statements:
                    db.execute(statement)
            db.executemany("""INSERT INTO game_players
                (account_id,group_id,player_id,dao_name,dao_name_key,
                 spirit_stones,devil_contract_tier,devil_max_contract_tier)
                VALUES ('wx','group',?,?,?,?,?,?)""",
                [('a','甲修','甲修',85,1,3),
                 ('b','乙修','乙修',90,0,0),
                 ('c','丙修','丙修',80,0,0)])
            db.execute("""INSERT INTO game_items
                (item_id,account_id,group_id,template_id,rarity,owner_player_id,state)
                VALUES ('item1','wx','group','qingfeng_jian','artifact','a','held')""")
            db.execute("""INSERT INTO game_player_props
                (prop_id,account_id,group_id,player_id,template_id)
                VALUES ('prop1','wx','group','a','raoxin_fu')""")
            db.execute("""INSERT INTO dungeon_profiles
                (account_id,group_id,player_id,class_id)
                VALUES ('wx','group','a','C01')""")
            db.execute("""INSERT INTO dungeon_roots
                (account_id,group_id,player_id,root_id)
                VALUES ('wx','group','a','R01')""")
            db.execute("""INSERT INTO game_duels
                (duel_id,account_id,group_id,challenger_id,challenged_id,
                 state,rules_version,rules_json)
                VALUES ('D1','wx','group','a','b','supporting',1,'{}')""")
            db.execute("""INSERT INTO game_supports
                (duel_id,account_id,group_id,supporter_id,supported_player_id,amount)
                VALUES ('D1','wx','group','c','a',20)""")
            db.execute("""INSERT INTO game_pvp_duels
                (duel_id,account_id,group_id,challenger_id,challenged_id,
                 wager,state,escrowed)
                VALUES ('V1','wx','group','a','b',10,'fighting',1)""")
            db.execute("""INSERT INTO dungeon_runs
                (run_id,code,account_id,group_id,leader_id,member_ids_json,
                 state,mode,created_at,departure_day)
                VALUES ('T1','AAA','wx','group','a','["a"]','active','正式',123.0,'2026-10-08')""")
            db.execute("""INSERT INTO dungeon_run_members
                (run_id,account_id,group_id,player_id,reward_eligible)
                VALUES ('T1','wx','group','a',1)""")
            db.execute("""INSERT INTO dungeon_run_state
                VALUES ('T1','{"phase":"fighting"}',123.0,456.0)""")
            db.execute("""INSERT INTO dungeon_notices
                VALUES ('notice1','wx','group','T1','old notification',0)""")
            db.execute("""CREATE TABLE ai_jobs(
                account_id TEXT,conversation_id TEXT,user_id TEXT,
                cost INTEGER,state TEXT,refunded INTEGER,question TEXT)""")
            db.execute("""INSERT INTO ai_jobs VALUES
                ('wx','group','a',5,'queued',0,'PRIVATE_CHAT_TEXT')""")
            db.execute("CREATE TABLE messages(text TEXT)")
            db.execute("INSERT INTO messages VALUES ('PRIVATE_CHAT_TEXT')")

    def test_game_only_migration_reconciles_reserved_currency(self):
        dry = migrate(self.source, self.target, dry_run=True)
        self.assertFalse(self.target.exists())
        self.assertEqual(dry["source_stones"], 255)
        self.assertEqual(dry["target_stones"], 300)
        self.assertEqual(dry["refund_stones"], 45)
        self.assertNotIn("PRIVATE_CHAT_TEXT", json.dumps(dry))
        actual = migrate(self.source, self.target)
        self.assertTrue(actual["published"])
        with closing(sqlite3.connect(self.target)) as db, db:
            self.assertEqual(db.execute(
                "SELECT spirit_stones FROM game_players WHERE player_id='a'").fetchone()[0], 100)
            self.assertEqual(db.execute(
                "SELECT spirit_stones FROM game_players WHERE player_id='b'").fetchone()[0], 100)
            self.assertEqual(db.execute(
                "SELECT spirit_stones FROM game_players WHERE player_id='c'").fetchone()[0], 100)
            self.assertEqual(db.execute(
                "SELECT state FROM game_duels").fetchone()[0], "cancelled")
            self.assertEqual(db.execute(
                "SELECT settlement_state,payout FROM game_supports").fetchone(), ("refunded",20))
            self.assertEqual(db.execute(
                "SELECT state,escrowed FROM game_pvp_duels").fetchone(), ("cancelled",0))
            self.assertEqual(db.execute(
                "SELECT state,reason FROM dungeon_runs").fetchone(),
                ("finished","plux_migration_cancelled"))
            self.assertEqual(db.execute(
                "SELECT reward_eligible FROM dungeon_run_members").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM game_items").fetchone()[0],1)
            tables = {row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertNotIn("messages", tables)
            self.assertNotIn("ai_jobs", tables)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM dungeon_notices").fetchone()[0],0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM dungeon_run_state").fetchone()[0],0)

    def _assert_unknown_content_is_rejected(self, table):
        for dry_run in (True, False):
            with self.subTest(table=table, dry_run=dry_run):
                with self.assertRaisesRegex(MigrationError, table):
                    migrate(self.source, self.target, dry_run=dry_run)
                self.assertFalse(self.target.exists())

    def test_unknown_artifact_template_fails_preflight(self):
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute("UPDATE game_items SET template_id='unknown_artifact'")
        self._assert_unknown_content_is_rejected("game_items")

    def test_unknown_prop_template_fails_preflight(self):
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute("UPDATE game_player_props SET template_id='unknown_prop'")
        self._assert_unknown_content_is_rejected("game_player_props")

    def test_unknown_root_fails_preflight(self):
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute("UPDATE dungeon_roots SET root_id='unknown_root'")
        self._assert_unknown_content_is_rejected("dungeon_roots")

    def test_unknown_class_fails_preflight(self):
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute("UPDATE dungeon_profiles SET class_id='unknown_class'")
        self._assert_unknown_content_is_rejected("dungeon_profiles")

    def test_legacy_profile_without_class_column_uses_schema_default(self):
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute("ALTER TABLE dungeon_profiles DROP COLUMN class_id")
        migrate(self.source, self.target)
        with closing(sqlite3.connect(self.target)) as db:
            self.assertEqual(
                db.execute("SELECT class_id FROM dungeon_profiles").fetchone()[0],
                "C01")

    def test_existing_destination_is_rejected(self):
        self.target.write_bytes(b"untouched")
        with self.assertRaises(MigrationError):
            migrate(self.source, self.target)
        self.assertEqual(self.target.read_bytes(), b"untouched")

    def test_missing_legacy_contract_column_recovers_action_history(self):
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute("ALTER TABLE game_players DROP COLUMN devil_max_contract_tier")
            db.execute("""INSERT INTO game_actions
                (event_key,account_id,group_id,player_id,action_kind,resource_json)
                VALUES ('event1','wx','group','a','devil_sign',
                  '{"changes":{"devil_contract_tier":3}}')""")
        migrate(self.source, self.target)
        with closing(sqlite3.connect(self.target)) as db, db:
            self.assertEqual(db.execute("""SELECT devil_contract_tier,
                devil_max_contract_tier FROM game_players WHERE player_id='a'""")
                .fetchone(), (1,3))

    def test_framework_can_apply_plugin_migration_to_published_game(self):
        from plux.adapters.sqlite import SqliteDatabase
        migrate(self.source, self.target)
        database = SqliteDatabase(self.target)
        try:
            database.migrate("xiuxian", MIGRATIONS)
            database.migrate("xiuxian", MIGRATIONS)
        finally:
            database.close()
        with closing(sqlite3.connect(self.target)) as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM plux_migrations WHERE namespace='xiuxian'"
            ).fetchone()[0], 1)

    def test_legacy_pvp_without_escrow_column_is_cancelled_without_refund(self):
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute("ALTER TABLE game_pvp_duels DROP COLUMN escrowed")
            db.execute("""UPDATE game_players SET spirit_stones=spirit_stones+10
                          WHERE player_id IN ('a','b')""")
        result = migrate(self.source, self.target)
        self.assertEqual(result["refunded_pvp_wagers"], 0)
        self.assertEqual(result["source_stones"], 275)
        self.assertEqual(result["target_stones"], 300)
        with closing(sqlite3.connect(self.target)) as db:
            self.assertEqual(db.execute(
                "SELECT state,escrowed FROM game_pvp_duels").fetchone(),
                ("cancelled",0))

    def test_unmatched_charge_fails_without_publishing(self):
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute("""INSERT INTO ai_jobs VALUES
                ('wx','group','missing',10,'running',0,'PRIVATE_CHAT_TEXT')""")
        with self.assertRaises(MigrationError):
            migrate(self.source, self.target)
        self.assertFalse(self.target.exists())


if __name__ == "__main__":
    unittest.main()