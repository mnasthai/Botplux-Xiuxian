"""Versioned Plux schema for the Xiuxian game.

The game DDL below is a frozen copy of receiver.games.schema.GAME_SCHEMA.
Source receiver modules are never imported by this plugin or the migrator.
"""
from __future__ import annotations

from plux.api import Migration

GAME_TABLES = (
    "game_players", "game_items", "game_duels", "game_supports",
    "game_actions", "game_shop_purchases", "game_player_props",
    "game_player_debuffs", "game_pvp_duels", "dungeon_profiles",
    "dungeon_roots", "dungeon_runs", "dungeon_run_members", "dungeon_actions",
)
# dungeon_notices are outbound notifications and intentionally never copied.
# dungeon_run_state was TEMP in the legacy receiver and cannot be recovered.
GAME_SQL = """
    CREATE TABLE IF NOT EXISTS game_players (
        account_id TEXT NOT NULL CHECK(length(account_id) > 0),
        group_id TEXT NOT NULL CHECK(length(group_id) > 0),
        player_id TEXT NOT NULL CHECK(length(player_id) > 0),
        dao_name TEXT NOT NULL CHECK(length(dao_name) BETWEEN 2 AND 12),
        dao_name_key TEXT NOT NULL CHECK(length(dao_name_key) > 0),
        realm TEXT NOT NULL DEFAULT 'qi' CHECK(realm IN ('qi','foundation','core','nascent')),
        cultivation INTEGER NOT NULL DEFAULT 0
            CHECK(typeof(cultivation) = 'integer' AND cultivation >= 0),
        spirit_stones INTEGER NOT NULL DEFAULT 100
            CHECK(typeof(spirit_stones) = 'integer' AND spirit_stones >= 0),
        last_cultivated_on TEXT,
        last_explored_on TEXT,
        devil_contract_tier INTEGER NOT NULL DEFAULT 0
            CHECK(typeof(devil_contract_tier) = 'integer' AND devil_contract_tier >= 0),
        devil_max_contract_tier INTEGER NOT NULL DEFAULT 0
            CHECK(typeof(devil_max_contract_tier) = 'integer' AND devil_max_contract_tier >= 0),
        devil_last_settled_on TEXT,
        devil_total_borrowed INTEGER NOT NULL DEFAULT 0
            CHECK(typeof(devil_total_borrowed) = 'integer' AND devil_total_borrowed >= 0),
        devil_signed_on TEXT,
        consecutive_refuse_duel_count INTEGER NOT NULL DEFAULT 0
            CHECK(typeof(consecutive_refuse_duel_count) = 'integer' AND consecutive_refuse_duel_count >= 0),
        joined_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        PRIMARY KEY(account_id, group_id, player_id),
        UNIQUE(account_id, group_id, dao_name_key)
    );
    CREATE TABLE IF NOT EXISTS game_items (
        item_id TEXT PRIMARY KEY CHECK(length(item_id) > 0),
        account_id TEXT NOT NULL CHECK(length(account_id) > 0),
        group_id TEXT NOT NULL CHECK(length(group_id) > 0),
        template_id TEXT NOT NULL CHECK(length(template_id) > 0),
        rarity TEXT NOT NULL CHECK(rarity IN ('artifact','spirit','ancient','treasure')),
        owner_player_id TEXT,
        state TEXT NOT NULL CHECK(state IN ('held','pool','retired')),
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        CHECK((state = 'held' AND owner_player_id IS NOT NULL) OR
              (state IN ('pool','retired') AND owner_player_id IS NULL)),
        UNIQUE(item_id, account_id, group_id),
        FOREIGN KEY(account_id, group_id, owner_player_id)
            REFERENCES game_players(account_id, group_id, player_id)
    );
    CREATE UNIQUE INDEX IF NOT EXISTS ux_game_rare_item_template
        ON game_items(account_id, group_id, template_id)
        WHERE rarity IN ('ancient','treasure');
    CREATE INDEX IF NOT EXISTS ix_game_items_owner
        ON game_items(account_id, group_id, owner_player_id) WHERE state = 'held';
    CREATE TABLE IF NOT EXISTS game_duels (
        duel_id TEXT PRIMARY KEY CHECK(length(duel_id) > 0),
        account_id TEXT NOT NULL CHECK(length(account_id) > 0),
        group_id TEXT NOT NULL CHECK(length(group_id) > 0),
        challenger_id TEXT NOT NULL,
        challenged_id TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('inviting','supporting','playing','settled','cancelled')),
        phase_sequence INTEGER NOT NULL DEFAULT 1
            CHECK(typeof(phase_sequence) = 'integer' AND phase_sequence > 0),
        rules_version INTEGER NOT NULL
            CHECK(typeof(rules_version) = 'integer' AND rules_version > 0),
        rules_json TEXT NOT NULL CHECK(json_valid(rules_json)),
        lightning_position INTEGER
            CHECK(lightning_position IS NULL OR
                  (typeof(lightning_position) = 'integer' AND lightning_position BETWEEN 1 AND 6)),
        next_turn INTEGER
            CHECK(next_turn IS NULL OR (typeof(next_turn) = 'integer' AND next_turn BETWEEN 1 AND 6)),
        current_player_id TEXT,
        observer_session_id TEXT,
        prompt_request_id TEXT,
        prompt_wait_started_at TEXT,
        phase_opened_at TEXT,
        phase_deadline_at TEXT,
        counted_on TEXT,
        winner_player_id TEXT,
        loser_player_id TEXT,
        loot_item_id TEXT,
        final_reason TEXT,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        CHECK(challenger_id <> challenged_id),
        CHECK(current_player_id IS NULL OR current_player_id IN (challenger_id, challenged_id)),
        CHECK(winner_player_id IS NULL OR winner_player_id IN (challenger_id, challenged_id)),
        CHECK(loser_player_id IS NULL OR loser_player_id IN (challenger_id, challenged_id)),
        CHECK(winner_player_id IS NULL OR loser_player_id IS NULL OR winner_player_id <> loser_player_id),
        UNIQUE(duel_id, account_id, group_id),
        FOREIGN KEY(account_id, group_id, challenger_id)
            REFERENCES game_players(account_id, group_id, player_id),
        FOREIGN KEY(account_id, group_id, challenged_id)
            REFERENCES game_players(account_id, group_id, player_id),
        FOREIGN KEY(account_id, group_id, current_player_id)
            REFERENCES game_players(account_id, group_id, player_id),
        FOREIGN KEY(account_id, group_id, winner_player_id)
            REFERENCES game_players(account_id, group_id, player_id),
        FOREIGN KEY(account_id, group_id, loser_player_id)
            REFERENCES game_players(account_id, group_id, player_id),
        FOREIGN KEY(loot_item_id, account_id, group_id)
            REFERENCES game_items(item_id, account_id, group_id)
    );
    CREATE UNIQUE INDEX IF NOT EXISTS ux_game_active_duel_group
        ON game_duels(account_id, group_id) WHERE state IN ('inviting','supporting','playing');
    CREATE INDEX IF NOT EXISTS ix_game_duels_player
        ON game_duels(account_id, group_id, challenger_id, challenged_id, counted_on);
    CREATE TABLE IF NOT EXISTS game_supports (
        duel_id TEXT NOT NULL,
        account_id TEXT NOT NULL CHECK(length(account_id) > 0),
        group_id TEXT NOT NULL CHECK(length(group_id) > 0),
        supporter_id TEXT NOT NULL,
        supported_player_id TEXT NOT NULL,
        amount INTEGER NOT NULL CHECK(typeof(amount) = 'integer' AND amount > 0),
        payout INTEGER CHECK(payout IS NULL OR (typeof(payout) = 'integer' AND payout >= 0)),
        settlement_state TEXT NOT NULL DEFAULT 'pending'
            CHECK(settlement_state IN ('pending','paid','refunded')),
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        PRIMARY KEY(duel_id, supporter_id),
        CHECK((settlement_state = 'pending' AND payout IS NULL) OR
              (settlement_state IN ('paid','refunded') AND payout IS NOT NULL)),
        FOREIGN KEY(duel_id, account_id, group_id)
            REFERENCES game_duels(duel_id, account_id, group_id),
        FOREIGN KEY(account_id, group_id, supporter_id)
            REFERENCES game_players(account_id, group_id, player_id),
        FOREIGN KEY(account_id, group_id, supported_player_id)
            REFERENCES game_players(account_id, group_id, player_id)
    );
    CREATE INDEX IF NOT EXISTS ix_game_supports_duel ON game_supports(duel_id, settlement_state);
    CREATE TABLE IF NOT EXISTS game_actions (
        event_key TEXT NOT NULL CHECK(length(event_key) > 0),
        account_id TEXT NOT NULL CHECK(length(account_id) > 0),
        group_id TEXT NOT NULL CHECK(length(group_id) > 0),
        player_id TEXT NOT NULL CHECK(length(player_id) > 0),
        action_kind TEXT NOT NULL CHECK(length(action_kind) > 0),
        duel_id TEXT,
        phase_sequence INTEGER
            CHECK(phase_sequence IS NULL OR (typeof(phase_sequence) = 'integer' AND phase_sequence > 0)),
        message_id TEXT,
        resource_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(resource_json)),
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        PRIMARY KEY(account_id, event_key),
        FOREIGN KEY(account_id, group_id, player_id)
            REFERENCES game_players(account_id, group_id, player_id),
        FOREIGN KEY(duel_id, account_id, group_id)
            REFERENCES game_duels(duel_id, account_id, group_id)
    );
    CREATE UNIQUE INDEX IF NOT EXISTS ux_game_actions_message
        ON game_actions(account_id, group_id, player_id, message_id)
        WHERE message_id IS NOT NULL AND message_id <> '';
    CREATE TABLE IF NOT EXISTS game_shop_purchases (
        account_id TEXT NOT NULL CHECK(length(account_id) > 0),
        group_id TEXT NOT NULL CHECK(length(group_id) > 0),
        player_id TEXT NOT NULL CHECK(length(player_id) > 0),
        item_id TEXT NOT NULL CHECK(length(item_id) > 0),
        purchase_day TEXT NOT NULL CHECK(length(purchase_day) > 0),
        amount INTEGER NOT NULL DEFAULT 1
            CHECK(typeof(amount) = 'integer' AND amount > 0),
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        PRIMARY KEY(account_id, group_id, player_id, item_id, purchase_day),
        FOREIGN KEY(account_id, group_id, player_id)
            REFERENCES game_players(account_id, group_id, player_id)
    );
    CREATE TABLE IF NOT EXISTS game_player_props (
        prop_id TEXT PRIMARY KEY CHECK(length(prop_id) > 0),
        account_id TEXT NOT NULL CHECK(length(account_id) > 0),
        group_id TEXT NOT NULL CHECK(length(group_id) > 0),
        player_id TEXT NOT NULL CHECK(length(player_id) > 0),
        template_id TEXT NOT NULL CHECK(length(template_id) > 0),
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        FOREIGN KEY(account_id, group_id, player_id)
            REFERENCES game_players(account_id, group_id, player_id)
    );
    CREATE INDEX IF NOT EXISTS ix_game_props_owner
        ON game_player_props(account_id, group_id, player_id);
    CREATE TABLE IF NOT EXISTS game_player_debuffs (
        debuff_id TEXT PRIMARY KEY CHECK(length(debuff_id) > 0),
        account_id TEXT NOT NULL CHECK(length(account_id) > 0),
        group_id TEXT NOT NULL CHECK(length(group_id) > 0),
        target_player_id TEXT NOT NULL CHECK(length(target_player_id) > 0),
        caster_player_id TEXT NOT NULL CHECK(length(caster_player_id) > 0),
        debuff_kind TEXT NOT NULL CHECK(length(debuff_kind) > 0),
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        FOREIGN KEY(account_id, group_id, target_player_id)
            REFERENCES game_players(account_id, group_id, player_id),
        FOREIGN KEY(account_id, group_id, caster_player_id)
            REFERENCES game_players(account_id, group_id, player_id)
    );
    CREATE INDEX IF NOT EXISTS ix_game_debuffs_target
        ON game_player_debuffs(account_id, group_id, target_player_id);
    CREATE TABLE IF NOT EXISTS game_pvp_duels (
        duel_id TEXT PRIMARY KEY CHECK(length(duel_id) > 0),
        account_id TEXT NOT NULL CHECK(length(account_id) > 0),
        group_id TEXT NOT NULL CHECK(length(group_id) > 0),
        challenger_id TEXT NOT NULL,
        challenged_id TEXT NOT NULL,
        wager INTEGER NOT NULL DEFAULT 0 CHECK(wager >= 0),
        state TEXT NOT NULL CHECK(state IN ('inviting','fighting','settled','rejected','cancelled')),
        escrowed INTEGER NOT NULL DEFAULT 0 CHECK(escrowed IN (0,1)),
        timeout_notice_json TEXT,
        current_round INTEGER NOT NULL DEFAULT 1,
        round_state_json TEXT,
        round_deadline_at TEXT,
        winner_id TEXT,
        surrendered_id TEXT,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        settled_at TEXT,
        FOREIGN KEY(account_id, group_id, challenger_id)
            REFERENCES game_players(account_id, group_id, player_id),
        FOREIGN KEY(account_id, group_id, challenged_id)
            REFERENCES game_players(account_id, group_id, player_id)
    );
    CREATE INDEX IF NOT EXISTS ix_game_pvp_duels_scope
        ON game_pvp_duels(account_id, group_id, state);
"""

DUNGEON_SQL = """
CREATE TABLE IF NOT EXISTS dungeon_profiles (
 account_id TEXT NOT NULL, group_id TEXT NOT NULL, player_id TEXT NOT NULL,
 class_id TEXT NOT NULL DEFAULT 'C01',
 starter_claimed INTEGER NOT NULL DEFAULT 0 CHECK(starter_claimed IN (0,1)),
 dust INTEGER NOT NULL DEFAULT 0 CHECK(dust >= 0),
 highest_night INTEGER NOT NULL DEFAULT 0 CHECK(highest_night BETWEEN 0 AND 3),
 PRIMARY KEY(account_id,group_id,player_id));
CREATE TABLE IF NOT EXISTS dungeon_roots (
 account_id TEXT NOT NULL, group_id TEXT NOT NULL, player_id TEXT NOT NULL,
 root_id TEXT NOT NULL,
 equipped_slot INTEGER CHECK(equipped_slot BETWEEN 1 AND 3),
 PRIMARY KEY(account_id,group_id,player_id,root_id),
 UNIQUE(account_id,group_id,player_id,equipped_slot));
CREATE TABLE IF NOT EXISTS dungeon_runs (
 run_id TEXT PRIMARY KEY, code TEXT NOT NULL,
 account_id TEXT NOT NULL, group_id TEXT NOT NULL,
 leader_id TEXT NOT NULL, member_ids_json TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('gathering','active','finished')),
 mode TEXT NOT NULL CHECK(mode IN ('正式','练习')),
 departure_day TEXT,
 highest_night INTEGER NOT NULL DEFAULT 0 CHECK(highest_night BETWEEN 0 AND 3),
 created_at REAL NOT NULL, departed_at REAL, settled_at REAL,
 reason TEXT,
 UNIQUE(account_id,group_id,code));
CREATE TABLE IF NOT EXISTS dungeon_run_members (
 run_id TEXT NOT NULL, account_id TEXT NOT NULL, group_id TEXT NOT NULL,
 player_id TEXT NOT NULL,
 reward_eligible INTEGER NOT NULL DEFAULT 0 CHECK(reward_eligible IN (0,1)),
 settled INTEGER NOT NULL DEFAULT 0 CHECK(settled IN (0,1)),
 cultivation_awarded INTEGER NOT NULL DEFAULT 0 CHECK(cultivation_awarded >= 0),
 stones_awarded INTEGER NOT NULL DEFAULT 0, dust_awarded INTEGER NOT NULL DEFAULT 0,
 root_awarded TEXT,
 PRIMARY KEY(run_id,player_id));
CREATE INDEX IF NOT EXISTS dungeon_runs_scope_state
 ON dungeon_runs(account_id,group_id,state);
CREATE INDEX IF NOT EXISTS dungeon_runs_departure
 ON dungeon_runs(account_id,group_id,departure_day);
CREATE TABLE IF NOT EXISTS dungeon_actions (
 account_id TEXT NOT NULL, event_key TEXT NOT NULL,
 message_id TEXT NOT NULL, group_id TEXT NOT NULL, player_id TEXT NOT NULL,
 fingerprint TEXT NOT NULL, command_kind TEXT NOT NULL,
 PRIMARY KEY(account_id,event_key),
 UNIQUE(account_id,group_id,player_id,message_id));
CREATE TABLE IF NOT EXISTS dungeon_notices (
 notice_id TEXT PRIMARY KEY, account_id TEXT NOT NULL,
 group_id TEXT NOT NULL, run_id TEXT NOT NULL,
 text TEXT NOT NULL, queued INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS dungeon_run_state (
 run_id TEXT PRIMARY KEY, state_json TEXT NOT NULL,
 last_progress REAL NOT NULL, phase_deadline REAL);
"""

def _statements(sql: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in sql.split(";") if part.strip())

MIGRATIONS = (Migration(1, _statements(GAME_SQL) + _statements(DUNGEON_SQL)),)
