"""Group-scoped game queries inside the plugin runner's transaction."""
from datetime import datetime, timezone
import json
from uuid import uuid4


class GameRepository:
    def __init__(self, store, account_id, group_id, player_id):
        self.store = store
        self.scope = (account_id, group_id)
        self.player_id = player_id

    def player(self, player_id: str | None = None):
        target = player_id or self.player_id
        return self.store.execute('''SELECT * FROM game_players
            WHERE account_id=? AND group_id=? AND player_id=?''', (*self.scope, target)).fetchone()

    def name_owner(self, key):
        return self.store.execute('''SELECT player_id FROM game_players
            WHERE account_id=? AND group_id=? AND dao_name_key=?''', (*self.scope, key)).fetchone()

    def create_player(self, name, key, stones, now):
        self.store.execute('''INSERT INTO game_players
            (account_id,group_id,player_id,dao_name,dao_name_key,spirit_stones,joined_at)
            VALUES(?,?,?,?,?,?,?)''', (*self.scope, self.player_id, name, key, stones, now))

    def update_player(self, **values):
        allowed = {'dao_name', 'dao_name_key', 'realm', 'cultivation', 'spirit_stones',
                   'last_cultivated_on', 'last_explored_on',
                   'devil_contract_tier', 'devil_max_contract_tier', 'devil_last_settled_on', 'devil_total_borrowed',
                   'devil_signed_on'}
        if not values or values.keys() - allowed:
            raise ValueError('unsupported player update')
        assignments = ','.join(f'{key}=?' for key in values)
        cursor = self.store.execute(f'''UPDATE game_players SET {assignments}
            WHERE account_id=? AND group_id=? AND player_id=?''',
            (*values.values(), *self.scope, self.player_id))
        if cursor.rowcount != 1:
            raise RuntimeError('player changed during game operation')

    def update_other_player(self, player_id: str, **values):
        allowed = {'dao_name', 'dao_name_key', 'realm', 'cultivation', 'spirit_stones',
                   'last_cultivated_on', 'last_explored_on',
                   'devil_contract_tier', 'devil_max_contract_tier', 'devil_last_settled_on', 'devil_total_borrowed',
                   'devil_signed_on'}
        if not values or values.keys() - allowed:
            raise ValueError('unsupported player update')
        assignments = ','.join(f'{key}=?' for key in values)
        cursor = self.store.execute(f'''UPDATE game_players SET {assignments}
            WHERE account_id=? AND group_id=? AND player_id=?''',
            (*values.values(), *self.scope, player_id))
        return cursor.rowcount

    def inventory(self):
        return self.store.execute('''SELECT * FROM game_items
            WHERE account_id=? AND group_id=? AND owner_player_id=? AND state='held'
            ORDER BY created_at,item_id''', (*self.scope, self.player_id)).fetchall()

    def item(self, item_id):
        return self.store.execute('''SELECT i.*,p.dao_name AS owner_name FROM game_items i
            LEFT JOIN game_players p ON p.account_id=i.account_id AND p.group_id=i.group_id
                AND p.player_id=i.owner_player_id
            WHERE i.account_id=? AND i.group_id=? AND i.item_id=?''', (*self.scope, item_id)).fetchone()

    def rare_items(self):
        rows = self.store.execute('''SELECT i.*,p.dao_name AS owner_name FROM game_items i
            LEFT JOIN game_players p ON p.account_id=i.account_id AND p.group_id=i.group_id
                AND p.player_id=i.owner_player_id
            WHERE i.account_id=? AND i.group_id=? AND i.rarity IN ('ancient','treasure')''',
            self.scope).fetchall()
        return {row['template_id']: row for row in rows}

    def grant_item(self, template, now):
        if template.rarity in ('ancient', 'treasure'):
            old = self.store.execute('''SELECT item_id,state FROM game_items
                WHERE account_id=? AND group_id=? AND template_id=?''',
                (*self.scope, template.id)).fetchone()
            if old:
                if old['state'] != 'pool':
                    raise RuntimeError('rare item is no longer available')
                self.store.execute("UPDATE game_items SET state='held',owner_player_id=? WHERE item_id=?",
                                   (self.player_id, old['item_id']))
                return old['item_id']
        for _ in range(5):
            item_id = 'F' + uuid4().hex[:8].upper()
            inserted = self.store.execute('''INSERT INTO game_items
                (item_id,account_id,group_id,template_id,rarity,owner_player_id,state,created_at)
                VALUES(?,?,?,?,?,?,'held',?) ON CONFLICT(item_id) DO NOTHING''',
                (item_id, *self.scope, template.id, template.rarity, self.player_id, now))
            if inserted.rowcount:
                return item_id
        raise RuntimeError('could not allocate an unused item identifier')

    def return_item(self, item):
        state = 'pool' if item['rarity'] in ('ancient', 'treasure') else 'retired'
        result = self.store.execute('''UPDATE game_items SET state=?,owner_player_id=NULL
            WHERE item_id=? AND account_id=? AND group_id=? AND owner_player_id=? AND state='held' ''',
            (state, item['item_id'], *self.scope, self.player_id))
        if result.rowcount != 1:
            raise RuntimeError('item ownership changed during offering')

    def strip_all_items(self):
        """Strip all held items from this player upon dao heart collapse."""
        self.store.execute('''UPDATE game_items SET state='pool',owner_player_id=NULL
            WHERE account_id=? AND group_id=? AND owner_player_id=? AND state='held'
            AND rarity IN ('ancient','treasure')''', (*self.scope, self.player_id))
        self.store.execute('''UPDATE game_items SET state='retired',owner_player_id=NULL
            WHERE account_id=? AND group_id=? AND owner_player_id=? AND state='held'
            AND rarity NOT IN ('ancient','treasure')''', (*self.scope, self.player_id))

    def duel(self, *, include_invitation=False):
        states = "('inviting','supporting','playing')" if include_invitation else "('supporting','playing')"
        return self.store.execute(f'''SELECT duel_id,state FROM game_duels
            WHERE account_id=? AND group_id=? AND (challenger_id=? OR challenged_id=?)
                AND state IN {states} LIMIT 1''', (*self.scope, self.player_id, self.player_id)).fetchone()

    def ranking(self):
        return self.store.execute('''SELECT dao_name,realm,cultivation FROM game_players
            WHERE account_id=? AND group_id=?
            ORDER BY CASE realm WHEN 'nascent' THEN 4 WHEN 'core' THEN 3
                WHEN 'foundation' THEN 2 ELSE 1 END DESC,
                cultivation DESC,joined_at,player_id LIMIT 10''', self.scope).fetchall()

    def ranking_count(self):
        """Return the number of cultivators in this account and group scope."""
        return self.store.execute('''SELECT count(*) FROM game_players
            WHERE account_id=? AND group_id=?''', self.scope).fetchone()[0]

    def action(self, event_key, message_id):
        row = self.store.execute('''SELECT * FROM game_actions WHERE account_id=? AND event_key=?''',
                                 (self.scope[0], event_key)).fetchone()
        if row is None and message_id is not None:
            row = self.store.execute('''SELECT * FROM game_actions
                WHERE account_id=? AND group_id=? AND player_id=? AND message_id=?''',
                (*self.scope, self.player_id, message_id)).fetchone()
        return row

    def latest_action(self, kind):
        """Return this player's most recent action of one kind in this group."""
        return self.store.execute('''SELECT created_at FROM game_actions
            WHERE account_id=? AND group_id=? AND player_id=? AND action_kind=?
            ORDER BY created_at DESC LIMIT 1''', (*self.scope, self.player_id, kind)).fetchone()

    def record_action(self, event_key, kind, message_id, resource_json, now):
        self.store.execute('''INSERT INTO game_actions
            (account_id,group_id,player_id,event_key,action_kind,message_id,resource_json,created_at)
            VALUES(?,?,?,?,?,?,?,?)''',
            (*self.scope, self.player_id, event_key, kind, message_id, resource_json, now))

    def shop_purchase_count_today(self, day: str, item_id: str) -> int:
        row = self.store.execute('''SELECT amount FROM game_shop_purchases
            WHERE account_id=? AND group_id=? AND player_id=? AND item_id=? AND purchase_day=?''',
            (*self.scope, self.player_id, item_id, day)).fetchone()
        return row['amount'] if row else 0

    def shop_group_purchase_count_today(self, day: str, item_id: str) -> int:
        row = self.store.execute('''SELECT coalesce(sum(amount), 0) FROM game_shop_purchases
            WHERE account_id=? AND group_id=? AND item_id=? AND purchase_day=?''',
            (*self.scope, item_id, day)).fetchone()
        return row[0] if row else 0

    def record_shop_purchase(self, day: str, item_id: str, count: int = 1, now_iso: str | None = None) -> None:
        now_val = now_iso or datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        self.store.execute('''INSERT INTO game_shop_purchases
            (account_id,group_id,player_id,item_id,purchase_day,amount,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(account_id,group_id,player_id,item_id,purchase_day)
            DO UPDATE SET amount=amount+excluded.amount, updated_at=excluded.updated_at''',
            (*self.scope, self.player_id, item_id, day, count, now_val, now_val))

    def shop_purchases_today(self, day: str) -> dict[str, int]:
        rows = self.store.execute('''SELECT item_id,amount FROM game_shop_purchases
            WHERE account_id=? AND group_id=? AND player_id=? AND purchase_day=?''',
            (*self.scope, self.player_id, day)).fetchall()
        return {row['item_id']: row['amount'] for row in rows}

    def shop_group_purchases_today(self, day: str) -> dict[str, int]:
        rows = self.store.execute('''SELECT item_id, coalesce(sum(amount), 0) FROM game_shop_purchases
            WHERE account_id=? AND group_id=? AND purchase_day=? GROUP BY item_id''',
            (*self.scope, day)).fetchall()
        return {row[0]: row[1] for row in rows}

    def clear_self_cultivate_cooldown(self) -> None:
        self.store.execute('''UPDATE game_actions SET action_kind='self_cultivate_cleared'
            WHERE account_id=? AND group_id=? AND player_id=? AND action_kind='self_cultivate' ''',
            (*self.scope, self.player_id))

    def props(self, player_id: str | None = None) -> list[dict]:
        target = player_id or self.player_id
        return self.store.execute('''SELECT * FROM game_player_props
            WHERE account_id=? AND group_id=? AND player_id=?
            ORDER BY created_at, prop_id''', (*self.scope, target)).fetchall()

    def prop_count(self, player_id: str | None = None) -> int:
        target = player_id or self.player_id
        row = self.store.execute('''SELECT count(*) FROM game_player_props
            WHERE account_id=? AND group_id=? AND player_id=?''', (*self.scope, target)).fetchone()
        return row[0] if row else 0

    def grant_prop(self, template_id: str, now: str) -> str:
        for _ in range(5):
            prop_id = 'P' + uuid4().hex[:8].upper()
            inserted = self.store.execute('''INSERT INTO game_player_props
                (prop_id,account_id,group_id,player_id,template_id,created_at)
                VALUES(?,?,?,?,?,?) ON CONFLICT(prop_id) DO NOTHING''',
                (prop_id, *self.scope, self.player_id, template_id, now))
            if inserted.rowcount:
                return prop_id
        raise RuntimeError('could not allocate an unused prop identifier')

    def consume_prop(self, prop_id: str, player_id: str | None = None) -> bool:
        target = player_id or self.player_id
        cursor = self.store.execute('''DELETE FROM game_player_props
            WHERE prop_id=? AND account_id=? AND group_id=? AND player_id=?''',
            (prop_id, *self.scope, target))
        return cursor.rowcount > 0

    def find_player_prop(self, query: str) -> dict | None:
        props = self.props()
        cleaned = query.strip().lower()
        for p in props:
            if p['prop_id'].lower() == cleaned or p['template_id'].lower() == cleaned:
                return p
        return None

    def debuffs(self, target_player_id: str | None = None, *, today: str | None = None) -> list[dict]:
        target = target_player_id or self.player_id
        if today is not None:
            # Sanling Chen lasts until the end of the Beijing day it was applied.
            self.store.execute('''DELETE FROM game_player_debuffs
                WHERE account_id=? AND group_id=? AND target_player_id=?
                    AND debuff_kind='sanling_chen'
                    AND date(created_at, '+8 hours') < ?''', (*self.scope, target, today))
        return self.store.execute('''SELECT * FROM game_player_debuffs
            WHERE account_id=? AND group_id=? AND target_player_id=?
            ORDER BY created_at''', (*self.scope, target)).fetchall()

    def add_debuff(self, target_id: str, caster_id: str, debuff_kind: str, now: str) -> str:
        debuff_id = 'B' + uuid4().hex[:8].upper()
        self.store.execute('''INSERT INTO game_player_debuffs
            (debuff_id,account_id,group_id,target_player_id,caster_player_id,debuff_kind,created_at)
            VALUES(?,?,?,?,?,?,?)''',
            (debuff_id, *self.scope, target_id, caster_id, debuff_kind, now))
        return debuff_id

    def consume_debuff(self, debuff_id: str) -> None:
        self.store.execute('''DELETE FROM game_player_debuffs WHERE debuff_id=?''', (debuff_id,))

    def clear_debuffs(self, target_player_id: str | None = None) -> int:
        target = target_player_id or self.player_id
        cursor = self.store.execute('''DELETE FROM game_player_debuffs
            WHERE account_id=? AND group_id=? AND target_player_id=?''',
            (*self.scope, target))
        return cursor.rowcount

    def create_pvp_duel(self, challenger_id: str, challenged_id: str, wager: int, now: str) -> str:
        for _ in range(5):
            duel_id = 'V' + uuid4().hex[:8].upper()
            inserted = self.store.execute('''INSERT INTO game_pvp_duels
                (duel_id,account_id,group_id,challenger_id,challenged_id,wager,state,created_at)
                VALUES(?,?,?,?,?,?,'inviting',?) ON CONFLICT(duel_id) DO NOTHING''',
                (duel_id, *self.scope, challenger_id, challenged_id, wager, now))
            if inserted.rowcount:
                return duel_id
        raise RuntimeError('could not allocate an unused pvp duel identifier')

    def get_pvp_duel(self, duel_id: str) -> dict | None:
        return self.store.execute('''SELECT * FROM game_pvp_duels
            WHERE duel_id=? AND account_id=? AND group_id=?''',
            (duel_id, *self.scope)).fetchone()

    def get_pending_pvp_duel_for_target(self, target_id: str) -> dict | None:
        return self.store.execute('''SELECT * FROM game_pvp_duels
            WHERE account_id=? AND group_id=? AND challenged_id=? AND state='inviting'
            ORDER BY created_at DESC LIMIT 1''',
            (*self.scope, target_id)).fetchone()

    def get_active_pvp_duel_for_player(self, player_id: str) -> dict | None:
        return self.store.execute('''SELECT * FROM game_pvp_duels
            WHERE account_id=? AND group_id=? AND (challenger_id=? OR challenged_id=?) AND state IN ('inviting', 'fighting')
            ORDER BY created_at DESC LIMIT 1''',
            (*self.scope, player_id, player_id)).fetchone()

    def traditional_duel_for_player(self, player_id: str):
        return self.store.execute('''SELECT duel_id FROM game_duels
            WHERE account_id=? AND group_id=? AND (challenger_id=? OR challenged_id=?)
              AND state IN ('inviting','supporting','playing') LIMIT 1''',
            (*self.scope, player_id, player_id)).fetchone()

    def get_fighting_pvp_duel_for_player(self, player_id: str) -> dict | None:
        return self.store.execute('''SELECT * FROM game_pvp_duels
            WHERE account_id=? AND group_id=? AND (challenger_id=? OR challenged_id=?) AND state='fighting'
            ORDER BY created_at DESC LIMIT 1''',
            (*self.scope, player_id, player_id)).fetchone()

    def reserve_pvp_wagers(self, duel_id: str) -> None:
        duel = self.get_pvp_duel(duel_id)
        if duel is None or duel['state'] != 'inviting' or duel['escrowed']:
            raise RuntimeError('pvp invitation changed before wager reservation')
        wager = int(duel['wager'])
        for player_id in (duel['challenger_id'], duel['challenged_id']):
            result = self.store.execute('''UPDATE game_players SET spirit_stones=spirit_stones-?
                WHERE account_id=? AND group_id=? AND player_id=? AND spirit_stones>=?''',
                (wager, *self.scope, player_id, wager))
            if result.rowcount != 1:
                raise RuntimeError('pvp player balance changed before wager reservation')
        result = self.store.execute('''UPDATE game_pvp_duels SET escrowed=1, state='fighting'
            WHERE duel_id=? AND account_id=? AND group_id=? AND state='inviting' AND escrowed=0''',
            (duel_id, *self.scope))
        if result.rowcount != 1:
            raise RuntimeError('pvp invitation changed before wager reservation')

    def finish_pvp_duel(self, duel_id: str, state: str, now: str, *,
                        winner_id: str | None = None, surrendered_id: str | None = None,
                        timeout_notice: dict | None = None) -> bool:
        duel = self.get_pvp_duel(duel_id)
        if duel is None or duel['state'] not in ('inviting', 'fighting'):
            return False
        wager = int(duel['wager'])
        if state == 'settled' and (duel['state'] != 'fighting' or not duel['escrowed']):
            raise RuntimeError('cannot settle a duel without reserved wagers')
        result = self.store.execute('''UPDATE game_pvp_duels
            SET state=?, winner_id=?, surrendered_id=?, settled_at=?, timeout_notice_json=?
            WHERE duel_id=? AND account_id=? AND group_id=? AND state=?''',
            (state, winner_id, surrendered_id, now,
             json.dumps(timeout_notice, ensure_ascii=False) if timeout_notice else None,
             duel_id, *self.scope, duel['state']))
        if result.rowcount != 1:
            raise RuntimeError('pvp duel changed during settlement')
        if duel['escrowed']:
            if state == 'settled':
                penalty = int(wager * 0.8) if surrendered_id else wager
                awards = {winner_id: wager + penalty,
                          surrendered_id or (duel['challenged_id'] if winner_id == duel['challenger_id'] else duel['challenger_id']): wager - penalty}
            else:
                awards = {duel['challenger_id']: wager, duel['challenged_id']: wager}
            for player_id, amount in awards.items():
                result = self.store.execute('''UPDATE game_players SET spirit_stones=spirit_stones+?
                    WHERE account_id=? AND group_id=? AND player_id=?''', (amount, *self.scope, player_id))
                if result.rowcount != 1:
                    raise RuntimeError('pvp player missing during settlement')
        return True

    def update_pvp_duel_status(self, duel_id: str, state: str, winner_id: str | None = None, settled_at: str | None = None, surrendered_id: str | None = None) -> None:
        self.store.execute('''UPDATE game_pvp_duels
            SET state=?, winner_id=?, settled_at=?, surrendered_id=?
            WHERE duel_id=? AND account_id=? AND group_id=?''',
            (state, winner_id, settled_at, surrendered_id, duel_id, *self.scope))

    def update_pvp_duel_round_state(self, duel_id: str, current_round: int, round_state_json: str, round_deadline_at: str, state: str = 'fighting') -> None:
        self.store.execute('''UPDATE game_pvp_duels
            SET current_round=?, round_state_json=?, round_deadline_at=?, state=?
            WHERE duel_id=? AND account_id=? AND group_id=?''',
            (current_round, round_state_json, round_deadline_at, state, duel_id, *self.scope))

    def count_daily_pvp_duels(self, p1: str, p2: str, date_prefix: str) -> int:
        row = self.store.execute('''SELECT COUNT(*) FROM game_pvp_duels
            WHERE account_id=? AND group_id=? AND state='settled'
            AND ((challenger_id=? AND challenged_id=?) OR (challenger_id=? AND challenged_id=?))
            AND created_at LIKE ?''',
            (*self.scope, p1, p2, p2, p1, f"{date_prefix}%")).fetchone()
        return int(row[0]) if row else 0

    def get_player_last_duel_time(self, player_id: str) -> str | None:
        row = self.store.execute('''SELECT created_at FROM game_duels
            WHERE account_id=? AND group_id=? AND (challenger_id=? OR challenged_id=?)
            AND state IN ('settled','playing','cancelled','supporting')
            ORDER BY created_at DESC LIMIT 1''',
            (*self.scope, player_id, player_id)).fetchone()
        return str(row[0]) if row and row[0] else None

    def increment_refuse_duel_count(self, player_id: str) -> int:
        self.store.execute('''UPDATE game_players
            SET consecutive_refuse_duel_count = consecutive_refuse_duel_count + 1
            WHERE account_id=? AND group_id=? AND player_id=?''',
            (*self.scope, player_id))
        row = self.store.execute('''SELECT consecutive_refuse_duel_count FROM game_players
            WHERE account_id=? AND group_id=? AND player_id=?''',
            (*self.scope, player_id)).fetchone()
        return int(row[0]) if row and row[0] is not None else 1

    def reset_refuse_duel_count(self, player_id: str) -> None:
        self.store.execute('''UPDATE game_players
            SET consecutive_refuse_duel_count = 0
            WHERE account_id=? AND group_id=? AND player_id=?''',
            (*self.scope, player_id))

    def clean_expired_pvp_duels(self, now_iso: str, timeout_seconds: int = 60) -> list[dict]:
        now_dt = None
        if now_iso:
            try:
                clean = now_iso.replace('Z', '+00:00') if 'Z' in now_iso else now_iso
                now_dt = datetime.fromisoformat(clean)
                if now_dt.tzinfo is None:
                    now_dt = now_dt.replace(tzinfo=timezone.utc)
            except Exception:
                now_dt = None

        if not now_dt:
            return []

        expired = []

        # 1. Clean inviting duels
        inviting_rows = self.store.execute('''SELECT * FROM game_pvp_duels
            WHERE account_id=? AND group_id=? AND state='inviting' ''',
            self.scope).fetchall()

        for row in inviting_rows:
            created_raw = row['created_at']
            try:
                c_clean = created_raw.replace('Z', '+00:00') if 'Z' in created_raw else created_raw
                c_dt = datetime.fromisoformat(c_clean)
                if c_dt.tzinfo is None:
                    c_dt = c_dt.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
            if (now_dt - c_dt).total_seconds() > timeout_seconds:
                target = self.player(row['challenged_id'])
                challenger = self.player(row['challenger_id'])
                count = int(target['consecutive_refuse_duel_count']) + 1 if target else 1
                target_name = target['dao_name'] if target else '受邀者'
                challenger_name = challenger['dao_name'] if challenger else '发起者'
                dormant = (f'⚠️ 【{target_name}】已连续 {count} 次拒绝或未响应斗法/决斗，战意溃散自晦，至宝威能沉眠，整体攻击力减少 60%！'
                           f'唯有参与一次【#斗法】方可唤醒解除（参与决斗无法移除沉眠）。'
                           if count >= 3 else f'（连续拒绝/未响应 {count}/3 次）')
                notice = {'reason': 'invite_timeout', 'mention_ids': [row['challenged_id'], row['challenger_id']],
                          'text': f'⏳【仙道决斗 · 邀请超时】\n【{target_name}】超过 60 秒未应答【{challenger_name}】的决斗邀请，擂台作罢自动撤销！\n{dormant}'}
                self.finish_pvp_duel(row['duel_id'], 'cancelled', now_iso, timeout_notice=notice)
                new_cnt = self.increment_refuse_duel_count(row['challenged_id'])
                item = dict(row)
                item['type'] = 'invite_timeout'
                item['consecutive_refuse_duel_count'] = new_cnt
                expired.append(item)

        # 2. Clean fighting duels where round confirmation timed out
        fighting_rows = self.store.execute('''SELECT * FROM game_pvp_duels
            WHERE account_id=? AND group_id=? AND state='fighting' ''',
            self.scope).fetchall()

        for row in fighting_rows:
            deadline_raw = row['round_deadline_at']
            if not deadline_raw:
                continue
            try:
                d_clean = deadline_raw.replace('Z', '+00:00') if 'Z' in deadline_raw else deadline_raw
                d_dt = datetime.fromisoformat(d_clean)
                if d_dt.tzinfo is None:
                    d_dt = d_dt.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
            if now_dt <= d_dt:
                continue
            state_data = json.loads(row['round_state_json']) if row['round_state_json'] else {}
            p1_ok = bool(state_data.get('p1_confirmed'))
            p2_ok = bool(state_data.get('p2_confirmed'))
            p1, p2 = row['challenger_id'], row['challenged_id']
            current_round = int(row['current_round'] or 1)
            if p1_ok != p2_ok:
                winner_id, loser_id = (p1, p2) if p1_ok else (p2, p1)
                winner = self.player(winner_id)
                loser = self.player(loser_id)
                penalty = int(int(row['wager']) * 0.8)
                winner_name = winner['dao_name'] if winner else '胜者'
                loser_name = loser['dao_name'] if loser else '弃权者'
                notice = {'reason': 'round_timeout', 'mention_ids': [loser_id, winner_id],
                          'text': f'⏳【仙道决斗 · 确认超时】\n【{loser_name}】在决斗第 {current_round} 轮超过 60 秒未确认【#继续决斗】，视为认输弃权！\n胜者【{winner_name}】获得对方 80% 押注（+{penalty} 灵石），擂台决斗结束！'}
                self.finish_pvp_duel(row['duel_id'], 'settled', now_iso, winner_id=winner_id,
                                     surrendered_id=loser_id, timeout_notice=notice)
                expired.append({'type': 'surrender_timeout', 'winner_id': winner_id,
                                'loser_id': loser_id, 'duel': dict(row), 'penalty': penalty})
            else:
                notice = {'reason': 'round_timeout', 'mention_ids': [p1, p2],
                          'text': f'⏳【仙道决斗 · 双方超时】\n双方在决斗第 {current_round} 轮均超过 60 秒未确认【#继续决斗】，擂台作罢自动撤销，押注全额退回！'}
                self.finish_pvp_duel(row['duel_id'], 'cancelled', now_iso, timeout_notice=notice)
                expired.append({'type': 'both_timeout', 'duel': dict(row)})

        return expired
