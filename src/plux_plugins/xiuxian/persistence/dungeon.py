"""Durable dungeon economy and transactional run snapshots."""

from __future__ import annotations

import json
from uuid import uuid4


def active_run(store, account_id, group_id, player_id):
    """Any gathering or departed run reserves a member across PvP modes."""
    return store.execute('''SELECT r.* FROM dungeon_runs r
        JOIN dungeon_run_members m ON m.run_id=r.run_id
        WHERE r.account_id=? AND r.group_id=? AND m.player_id=?
          AND r.state IN ('gathering','active')
        ORDER BY r.created_at DESC LIMIT 1''',
        (account_id, group_id, player_id)).fetchone()


def by_code(store, account_id, group_id, code):
    return store.execute('''SELECT * FROM dungeon_runs
        WHERE account_id=? AND group_id=? AND code=? AND state='gathering' ''',
        (account_id, group_id, code)).fetchone()


def new_run(store, account, group, leader, code, mode, now, state):
    run_id = 'T' + uuid4().hex.upper()
    store.execute('''INSERT INTO dungeon_runs
        (run_id,code,account_id,group_id,leader_id,member_ids_json,state,mode,created_at)
        VALUES(?,?,?,?,?,?,'gathering',?,?)''',
        (run_id, code, account, group, leader, json.dumps([leader]), mode, now))
    store.execute('''INSERT INTO dungeon_run_members
        (run_id,account_id,group_id,player_id) VALUES(?,?,?,?)''',
        (run_id, account, group, leader))
    save_state(store, run_id, state, now)
    return run_id


def save_state(store, run_id, state, last_progress, deadline=None):
    store.execute('''INSERT INTO dungeon_run_state(run_id,state_json,last_progress,phase_deadline)
        VALUES(?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET
        state_json=excluded.state_json,last_progress=excluded.last_progress,
        phase_deadline=excluded.phase_deadline''',
        (run_id, json.dumps(state, ensure_ascii=False, separators=(',', ':')),
         last_progress, deadline))


def load_state(store, run_id):
    row = store.execute('SELECT * FROM dungeon_run_state WHERE run_id=?',
                        (run_id,)).fetchone()
    return (json.loads(row['state_json']), row['last_progress'], row['phase_deadline']) if row else None


def remove_state(store, run_id):
    store.execute('DELETE FROM dungeon_run_state WHERE run_id=?', (run_id,))


def profile(store, account, group, player):
    store.execute('''INSERT OR IGNORE INTO dungeon_profiles(account_id,group_id,player_id)
        VALUES(?,?,?)''', (account, group, player))
    return store.execute('''SELECT * FROM dungeon_profiles
        WHERE account_id=? AND group_id=? AND player_id=?''',
        (account, group, player)).fetchone()


def roots(store, account, group, player):
    return store.execute('''SELECT root_id,equipped_slot FROM dungeon_roots
        WHERE account_id=? AND group_id=? AND player_id=? ORDER BY root_id''',
        (account, group, player)).fetchall()


def daily_departures(store, account, group, player, day):
    return store.execute('''SELECT COUNT(*) FROM dungeon_run_members m
        JOIN dungeon_runs r ON r.run_id=m.run_id
        WHERE m.account_id=? AND m.group_id=? AND m.player_id=?
          AND r.departure_day=? AND m.reward_eligible=1''',
        (account, group, player, day)).fetchone()[0]


def check_action(store, account, event_key, message_id, group, player, fingerprint):
    row = store.execute('''SELECT * FROM dungeon_actions
        WHERE account_id=? AND (event_key=? OR
            (group_id=? AND player_id=? AND message_id=?))''',
        (account, event_key, group, player, message_id)).fetchone()
    if row is None:
        return 'new'
    if (row['message_id'] == message_id and row['group_id'] == group and row['player_id'] == player
            and row['fingerprint'] == fingerprint):
        return 'repeat'
    return 'conflict'


def record_action(store, account, event_key, message_id, group, player, fingerprint, kind):
    store.execute('''INSERT INTO dungeon_actions
        (account_id,event_key,message_id,group_id,player_id,fingerprint,command_kind)
        VALUES(?,?,?,?,?,?,?)''',
        (account, event_key, message_id, group, player, fingerprint, kind))
