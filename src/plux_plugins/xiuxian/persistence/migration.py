"""One-way, game-only migration from a legacy receiver SQLite database.

This module never imports the receiver. A consistent SQLite backup is inspected,
then copied into a fresh Plux game database. No source row is changed.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from typing import Any

from .schema import GAME_TABLES, MIGRATIONS

_SQLITE_MAX = 2**63 - 1
_ACTIVE_DUELS = ("inviting", "supporting", "playing")
_ACTIVE_PVP = ("inviting", "fighting")
_ACTIVE_RUNS = ("gathering", "active")
_REQUIRED = {
    "game_players": {"account_id", "group_id", "player_id", "dao_name",
                     "dao_name_key", "realm", "cultivation", "spirit_stones"},
    "game_items": {"item_id", "account_id", "group_id", "template_id", "rarity", "state"},
    "game_duels": {"duel_id", "account_id", "group_id", "challenger_id",
                   "challenged_id", "state", "rules_version", "rules_json"},
    "game_supports": {"duel_id", "account_id", "group_id", "supporter_id",
                      "supported_player_id", "amount"},
    "game_actions": {"account_id", "event_key", "group_id", "player_id", "action_kind"},
    "game_pvp_duels": {"duel_id", "account_id", "group_id", "challenger_id",
                       "challenged_id", "wager", "state"},
    "dungeon_runs": {"run_id", "code", "account_id", "group_id", "leader_id",
                      "member_ids_json", "state", "mode", "created_at"},
}

class MigrationError(ValueError):
    """Preflight or reconciliation failure. Neither database is changed."""

def _tables(db: sqlite3.Connection) -> set[str]:
    return {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}

def _columns(db: sqlite3.Connection, table: str) -> tuple[str, ...]:
    # table comes only from the fixed allowlist or a literal constant.
    return tuple(row[1] for row in db.execute(f'PRAGMA table_info("{table}")'))

def _count(db: sqlite3.Connection, table: str) -> int:
    return db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]

def _game_counts(db: sqlite3.Connection) -> dict[str, int]:
    tables = _tables(db)
    return {table: _count(db, table) if table in tables else 0
            for table in GAME_TABLES}

def _balance_total(db: sqlite3.Connection) -> int:
    return sum(row[0] for row in db.execute(
        "SELECT spirit_stones FROM game_players"))

def _check_content_ids(db: sqlite3.Connection, present: set[str]) -> None:
    # Load the plugin's current content definitions, never the legacy receiver.
    from plux_plugins.xiuxian.domain.catalog import CATALOG
    from plux_plugins.xiuxian.domain.shop import PROP_TEMPLATES
    from plux_plugins.xiuxian.application.dungeon.content import CLASSES, ROOTS

    references = (
        ("game_items", "template_id", CATALOG),
        ("game_player_props", "template_id", PROP_TEMPLATES),
        ("dungeon_roots", "root_id", ROOTS),
        ("dungeon_profiles", "class_id", CLASSES),
    )
    for table, column, catalog in references:
        if table not in present:
            continue
        if column not in _columns(db, table):
            # Older dungeon profiles lacked class_id and default to C01.
            if table == "dungeon_profiles":
                continue
            raise MigrationError(f"{table} lacks {column} needed for content validation")
        unknown = {row[0] for row in db.execute(
            f'SELECT DISTINCT "{column}" FROM "{table}"')} - set(catalog)
        if unknown:
            preview = sorted((repr(item) for item in unknown))[:8]
            raise MigrationError(
                f"{table}.{column} has {len(unknown)} unknown content ID(s): "
                + ", ".join(preview))


def _require_snapshot(db: sqlite3.Connection) -> None:
    if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        raise MigrationError("source snapshot failed SQLite quick_check")
    present = _tables(db)
    if "game_players" not in present:
        raise MigrationError("source has no game_players table")
    for table in GAME_TABLES:
        if table not in present:
            continue
        cols = set(_columns(db, table))
        missing = _REQUIRED.get(table, set()) - cols
        if missing:
            raise MigrationError(f"{table} lacks required columns: {sorted(missing)}")
    _check_content_ids(db, present)
    if "ai_jobs" in present:
        ai_cols = set(_columns(db, "ai_jobs"))
        required = {"account_id", "conversation_id", "user_id",
                    "cost", "state", "refunded"}
        if _count(db, "ai_jobs") and required - ai_cols:
            raise MigrationError("ai_jobs lacks fields needed to reconcile charged jobs")

def _create_schema(db: sqlite3.Connection) -> None:
    for migration in MIGRATIONS:
        for statement in migration.statements:
            db.execute(statement)

def _copy_game(snapshot: sqlite3.Connection, target: sqlite3.Connection) -> None:
    source_tables = _tables(snapshot)
    for table in GAME_TABLES:
        if table not in source_tables:
            continue
        from_cols = _columns(snapshot, table)
        to_cols = set(_columns(target, table))
        extra = set(from_cols) - to_cols
        if extra:
            raise MigrationError(f"{table} has unknown columns: {sorted(extra)}")
        cols = [column for column in from_cols if column in to_cols]
        names = ",".join(f'"{name}"' for name in cols)
        placeholders = ",".join("?" for _ in cols)
        read = snapshot.execute(f'SELECT {names} FROM "{table}"')
        while rows := read.fetchmany(1000):
            target.executemany(
                f'INSERT INTO "{table}" ({names}) VALUES ({placeholders})', rows)

def _recover_contract_tiers(snapshot: sqlite3.Connection,
                            target: sqlite3.Connection) -> None:
    if "devil_max_contract_tier" in _columns(snapshot, "game_players"):
        return
    target.execute("""UPDATE game_players SET devil_max_contract_tier=devil_contract_tier
                      WHERE devil_contract_tier>devil_max_contract_tier""")
    if "game_actions" not in _tables(snapshot):
        return
    for account, group, player, raw in target.execute(
            "SELECT account_id,group_id,player_id,resource_json FROM game_actions "
            "WHERE action_kind='devil_sign'"):
        try:
            payload = json.loads(raw)
            changes = payload.get("changes")
            tier = changes.get("devil_contract_tier") if isinstance(changes, dict) else None
        except (TypeError, ValueError, AttributeError):
            continue
        if type(tier) is int and 0 <= tier <= _SQLITE_MAX:
            target.execute("""UPDATE game_players SET devil_max_contract_tier=?
                 WHERE account_id=? AND group_id=? AND player_id=?
                   AND devil_max_contract_tier<?""",
                (tier, account, group, player, tier))

def _credit(target: sqlite3.Connection, account: str, group: str,
            player: str, amount: int) -> None:
    if type(amount) is not int or amount < 0 or amount > _SQLITE_MAX:
        raise MigrationError("invalid refund amount")
    changed = target.execute("""UPDATE game_players
        SET spirit_stones=spirit_stones+?
        WHERE account_id=? AND group_id=? AND player_id=?
          AND spirit_stones<=?""",
        (amount, account, group, player, _SQLITE_MAX - amount))
    if changed.rowcount != 1:
        raise MigrationError("refund player missing or balance would overflow")

def _reconcile(target: sqlite3.Connection, snapshot: sqlite3.Connection) -> dict[str, Any]:
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    now_real = datetime.now(timezone.utc).timestamp()
    report: dict[str, Any] = {
        "cancelled_duels": 0, "refunded_supports": 0,
        "cancelled_pvp": 0, "refunded_pvp_wagers": 0,
        "cancelled_dungeon_runs": 0, "refunded_ai_jobs": 0,
        "refund_stones": 0,
    }
    if "game_duels" in _tables(snapshot):
        report["cancelled_duels"] = target.execute("""UPDATE game_duels
            SET state='cancelled', final_reason='plux_migration_cancelled',
                observer_session_id=NULL, prompt_request_id=NULL,
                prompt_wait_started_at=NULL, phase_deadline_at=NULL
            WHERE state IN ('inviting','supporting','playing')""").rowcount
    if "game_supports" in _tables(snapshot):
        mixed = target.execute("""SELECT 1 FROM game_supports p
            WHERE p.settlement_state='pending' AND EXISTS(
             SELECT 1 FROM game_supports s WHERE s.duel_id=p.duel_id
              AND s.settlement_state<>'pending') LIMIT 1""").fetchone()
        if mixed:
            raise MigrationError("mixed support settlement state requires manual review")
        for duel_id, account, group, player, amount in target.execute(
                """SELECT duel_id,account_id,group_id,supporter_id,amount
                   FROM game_supports WHERE settlement_state='pending'""").fetchall():
            _credit(target, account, group, player, amount)
            changed = target.execute("""UPDATE game_supports SET
                    settlement_state='refunded',payout=?
                    WHERE duel_id=? AND supporter_id=? AND settlement_state='pending'""",
                    (amount, duel_id, player))
            if changed.rowcount != 1:
                raise MigrationError("support settlement changed during migration")
            report["refunded_supports"] += 1
            report["refund_stones"] += amount
    if "game_pvp_duels" in _tables(snapshot):
        for account, group, challenger, challenged, wager in target.execute(
                """SELECT account_id,group_id,challenger_id,challenged_id,wager
                   FROM game_pvp_duels
                   WHERE state IN ('inviting','fighting') AND escrowed=1""").fetchall():
            _credit(target, account, group, challenger, wager)
            _credit(target, account, group, challenged, wager)
            report["refunded_pvp_wagers"] += 2
            report["refund_stones"] += wager * 2
        report["cancelled_pvp"] = target.execute("""UPDATE game_pvp_duels
            SET state='cancelled',escrowed=0,settled_at=?,
                round_state_json=NULL,round_deadline_at=NULL,
                timeout_notice_json=NULL
            WHERE state IN ('inviting','fighting')""", (now_iso,)).rowcount
    if "dungeon_runs" in _tables(snapshot):
        report["cancelled_dungeon_runs"] = target.execute("""UPDATE dungeon_runs
            SET state='finished', settled_at=?, reason='plux_migration_cancelled'
            WHERE state IN ('gathering','active')""", (now_real,)).rowcount
        target.execute("""UPDATE dungeon_run_members SET reward_eligible=0
            WHERE run_id IN (SELECT run_id FROM dungeon_runs
             WHERE reason='plux_migration_cancelled')""")
    # Pending/started AI jobs were charged in the old receiver. We copy no AI
    # question or job row, only reimburse game currency that was not refunded.
    if "ai_jobs" in _tables(snapshot):
        for account, group, player, cost in snapshot.execute(
                """SELECT account_id,conversation_id,user_id,cost FROM ai_jobs
                   WHERE state IN ('queued','running') AND refunded=0 AND cost>0"""):
            _credit(target, account, group, player, cost)
            report["refunded_ai_jobs"] += 1
            report["refund_stones"] += cost
    return report

def _verify(snapshot: sqlite3.Connection, target: sqlite3.Connection,
            report: dict[str, Any]) -> None:
    if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        raise MigrationError("target failed SQLite quick_check")
    if target.execute("PRAGMA foreign_key_check").fetchone():
        raise MigrationError("target has invalid foreign key references")
    source_counts = _game_counts(snapshot)
    target_counts = _game_counts(target)
    if source_counts != target_counts:
        raise MigrationError("game row counts changed during migration")
    if _count(target, "dungeon_notices") or _count(target, "dungeon_run_state"):
        raise MigrationError("legacy notifications or transient run state were copied")
    if target.execute("""SELECT 1 FROM game_supports
            WHERE settlement_state='pending' LIMIT 1""").fetchone():
        raise MigrationError("support still pending")
    if _balance_total(target) != _balance_total(snapshot) + report["refund_stones"]:
        raise MigrationError("player balance reconciliation failed")
    if _count(target, "game_items") != source_counts["game_items"]:
        raise MigrationError("game item conservation failed")

def _snapshot(source: Path, destination_dir: Path) -> Path:
    if not source.is_file():
        raise MigrationError("source database does not exist")
    with tempfile.NamedTemporaryFile(prefix="plux-xiuxian-source-",
                                     suffix=".sqlite3", dir=destination_dir,
                                     delete=False) as temporary:
        snapshot_path = Path(temporary.name)
    try:
        with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro",
                                     uri=True, timeout=30)) as live:
            with closing(sqlite3.connect(snapshot_path)) as copy:
                live.backup(copy)
        return snapshot_path
    except BaseException:
        snapshot_path.unlink(missing_ok=True)
        raise

def migrate(source: str | Path, destination: str | Path,
            *, dry_run: bool = False) -> dict[str, Any]:
    """Inspect or publish a fresh game-only database. Refuse an existing target."""
    source_path = Path(source).resolve()
    target_path = Path(destination).resolve()
    if source_path == target_path or target_path.exists():
        raise MigrationError("destination must be a new path distinct from source")
    if not target_path.parent.is_dir():
        raise MigrationError("destination parent directory does not exist")
    snapshot_path = _snapshot(source_path, target_path.parent)
    pending_path: Path | None = None
    published = False
    try:
        with closing(sqlite3.connect(snapshot_path)) as snapshot:
            _require_snapshot(snapshot)
            with tempfile.NamedTemporaryFile(prefix="plux-xiuxian-target-",
                                             suffix=".sqlite3",
                                             dir=target_path.parent,
                                             delete=False) as temporary:
                pending_path = Path(temporary.name)
            with closing(sqlite3.connect(pending_path)) as target:
                target.execute("PRAGMA foreign_keys=ON")
                target.execute("BEGIN IMMEDIATE")
                try:
                    _create_schema(target)
                    _copy_game(snapshot, target)
                    _recover_contract_tiers(snapshot, target)
                    report = _reconcile(target, snapshot)
                    _verify(snapshot, target, report)
                    target.commit()
                except BaseException:
                    target.rollback()
                    raise
            counts = _game_counts(snapshot)
            with closing(sqlite3.connect(pending_path)) as verified:
                target_stones = _balance_total(verified)
            result = {
                "source_snapshot": "consistent_sqlite_backup",
                "mode": "inspect" if dry_run else "migrate",
                "game_rows": counts,
                "players": counts["game_players"],
                "items": counts["game_items"],
                "source_stones": _balance_total(snapshot),
                "target_stones": target_stones,
                **report,
            }
        if not dry_run:
            # Same-volume hard link publishes only if the name is still unused.
            # No existing destination is overwritten, including concurrent runs.
            os.link(pending_path, target_path)
            published = True
            result["published"] = True
        else:
            result["published"] = False
        return result
    finally:
        # Once the hard link exists the target is published. A transient
        # Windows handle on a staging file must not turn success into an
        # apparent failure that tempts an operator to publish it again.
        for temporary in (pending_path, snapshot_path):
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    if not published:
                        raise
                    result['staging_cleanup_pending'] = True

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Migrate only Xiuxian game data")
    sub = parser.add_subparsers(dest="command", required=True)
    inspect = sub.add_parser("inspect", help="read a consistent snapshot, no target")
    inspect.add_argument("--source", type=Path, required=True)
    migrate_command = sub.add_parser("migrate", help="create a new game database")
    migrate_command.add_argument("--source", type=Path, required=True)
    migrate_command.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "inspect":
            with tempfile.TemporaryDirectory(prefix="plux-xiuxian-inspect-") as temp:
                result = migrate(args.source, Path(temp) / "inspect.sqlite3",
                                 dry_run=True)
        else:
            result = migrate(args.source, args.destination)
    except (MigrationError, sqlite3.Error, OSError) as exc:
        parser.exit(2, f"migration failed: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())