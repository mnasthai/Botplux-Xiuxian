"""Scoped duel persistence; every call uses the runner's existing transaction."""
from uuid import uuid4


ACTIVE = ('inviting', 'supporting', 'playing')


class DuelStore:
    def __init__(self, context, group_id=None):
        self.context = context
        self.db = context.store
        self.account = context.account_id
        self.group = group_id or context.conversation_id
        self.scope = (self.account, self.group)

    def active(self):
        return self.db.execute("""SELECT * FROM game_duels WHERE account_id=? AND group_id=?
            AND state IN ('inviting','supporting','playing')""", self.scope).fetchone()

    def get(self, duel_id):
        return self.db.execute('SELECT * FROM game_duels WHERE account_id=? AND group_id=? AND duel_id=?',
                               (*self.scope, duel_id)).fetchone()

    def insert(self, challenger, challenged, rules_version, rules_json, session, now):
        for _ in range(5):
            duel_id = 'D' + uuid4().hex[:8].upper()
            inserted = self.db.execute("""INSERT INTO game_duels
                (duel_id,account_id,group_id,challenger_id,challenged_id,state,rules_version,
                 rules_json,observer_session_id,created_at)
                VALUES(?,?,?,?,?,'inviting',?,?,?,?) ON CONFLICT(duel_id) DO NOTHING""",
                (duel_id, *self.scope, challenger, challenged, rules_version, rules_json, session, now))
            if inserted.rowcount:
                return self.get(duel_id)
        raise RuntimeError('could not allocate a duel identifier')

    def update(self, duel, **values):
        allowed = {'state', 'phase_sequence', 'rules_version', 'rules_json', 'lightning_position',
                   'next_turn', 'current_player_id', 'prompt_request_id', 'prompt_wait_started_at',
                   'phase_opened_at', 'phase_deadline_at', 'counted_on', 'winner_player_id',
                   'loser_player_id', 'loot_item_id', 'final_reason'}
        if not values or values.keys() - allowed:
            raise ValueError('unsupported duel update')
        result = self.db.execute(f"""UPDATE game_duels SET {','.join(f'{k}=?' for k in values)}
            WHERE account_id=? AND group_id=? AND duel_id=? AND state=? AND phase_sequence=?
                AND prompt_request_id IS ?""",
            (*values.values(), *self.scope, duel['duel_id'], duel['state'],
             duel['phase_sequence'], duel['prompt_request_id']))
        if result.rowcount != 1:
            raise RuntimeError('duel phase changed during operation')
        return self.get(duel['duel_id'])

    def invitation_stats(self, player, day_start, day_end):
        return self.db.execute("""SELECT
            sum(CASE WHEN julianday(created_at)>=julianday(?) AND julianday(created_at)<julianday(?)
                THEN 1 ELSE 0 END) AS today_count, max(julianday(created_at)) AS last_day
            FROM game_duels WHERE account_id=? AND group_id=? AND challenger_id=?""",
            (day_start, day_end, *self.scope, player)).fetchone()

    def counted(self, player, day, other=None):
        sql = """SELECT count(*) FROM game_duels WHERE account_id=? AND group_id=? AND counted_on=?
            AND state IN ('supporting','playing','settled') AND (challenger_id=? OR challenged_id=?)"""
        args = (*self.scope, day, player, player)
        if other is not None:
            sql += ' AND (challenger_id=? OR challenged_id=?)'
            args += (other, other)
        return self.db.execute(sql, args).fetchone()[0]

    def history(self, player):
        return self.db.execute("""SELECT d.* FROM game_duels d WHERE account_id=? AND group_id=?
            AND state IN ('settled','cancelled') AND (challenger_id=? OR challenged_id=? OR EXISTS(
                SELECT 1 FROM game_supports s WHERE s.duel_id=d.duel_id AND s.supporter_id=?))
            ORDER BY created_at DESC,duel_id DESC LIMIT 5""", (*self.scope, player, player, player)).fetchall()

    def prompt(self, duel):
        """Read the framework receipt through the current unit of work."""
        key = duel['prompt_request_id']
        return self.context.receipt(key) if key else None

    def cancel_prompt(self, duel, now):
        key = duel['prompt_request_id']
        if key:
            self.context.cancel_reply(key)

    def transfer(self, item, winner):
        changed = self.db.execute("""UPDATE game_items SET owner_player_id=? WHERE account_id=?
            AND group_id=? AND item_id=? AND owner_player_id=? AND state='held'""",
            (winner, *self.scope, item['item_id'], item['owner_player_id']))
        if changed.rowcount != 1:
            raise RuntimeError('loot ownership changed during settlement')

    def retire(self, item):
        changed = self.db.execute("""UPDATE game_items SET state='retired',owner_player_id=NULL WHERE account_id=?
            AND group_id=? AND item_id=? AND owner_player_id=? AND state='held'""",
            (*self.scope, item['item_id'], item['owner_player_id']))
        if changed.rowcount != 1:
            raise RuntimeError('loot ownership changed during settlement')

    def record_action(self, player, event_key, message_id, kind, data, now, duel):
        self.db.execute("""INSERT INTO game_actions(account_id,group_id,player_id,event_key,message_id,
            action_kind,resource_json,created_at,duel_id,phase_sequence) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (*self.scope, player, event_key, message_id, 'duel_' + kind, data, now,
             duel['duel_id'] if duel else None, duel['phase_sequence'] if duel else None))
