"""Transactional spectator-support escrow and integer settlement."""
from __future__ import annotations

import json
import logging


_SQLITE_MAX = 2**63 - 1


class SupportError(ValueError):
    """A user-visible support rule violation detected before any write."""


class SupportLedger:
    def __init__(self, context, group_id):
        if not isinstance(group_id, str) or not group_id:
            raise ValueError('group_id must be a nonempty string')
        self.db = context.store
        self.account = context.account_id
        self.group = group_id
        self.scope = (self.account, self.group)

    def _check_duel(self, duel):
        if (duel is None or duel['account_id'] != self.account
                or duel['group_id'] != self.group or not duel['duel_id']):
            raise SupportError('这场斗法不属于当前群，不能登记或结算支持。')

    @staticmethod
    def _limits(duel):
        try:
            rules = json.loads(duel['rules_json'])
            minimum, maximum = rules['support_minimum'], rules['support_maximum']
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise SupportError('本场斗法的支持规则快照无效。') from exc
        if (type(minimum) is not int or type(maximum) is not int
                or minimum <= 0 or minimum > maximum):
            raise SupportError('本场斗法的支持规则快照无效。')
        return minimum, maximum

    def register(self, duel, supporter_id, target_id, amount, now_iso):
        """Deduct one support stake after all user-facing checks pass."""
        self._check_duel(duel)
        minimum, maximum = self._limits(duel)
        participants = (duel['challenger_id'], duel['challenged_id'])
        if target_id not in participants:
            raise SupportError('只能支持本场斗法的两名参战者之一。')
        if supporter_id in participants:
            raise SupportError('参战者不能支持自己的这场斗法。')
        if type(amount) is not int:
            raise SupportError('支持金额必须是十进制整数。')
        if not minimum <= amount <= maximum:
            raise SupportError(f'每次支持金额须为 {minimum}～{maximum} 灵石。')
        if not isinstance(now_iso, str) or not now_iso:
            raise SupportError('未能确认支持登记时间，请重新发送。')
        player = self.db.execute("""SELECT spirit_stones FROM game_players
            WHERE account_id=? AND group_id=? AND player_id=?""",
            (*self.scope, supporter_id)).fetchone()
        if player is None:
            raise SupportError('请先发送 #修仙 道号 创建修士，再参与围观支持。')
        duplicate = self.db.execute("""SELECT 1 FROM game_supports
            WHERE account_id=? AND group_id=? AND duel_id=? AND supporter_id=?""",
            (*self.scope, duel['duel_id'], supporter_id)).fetchone()
        if duplicate is not None:
            raise SupportError('每人每场只能支持一次，不能追加、撤回或换边。')
        if player['spirit_stones'] < amount:
            raise SupportError('灵石不足，本次支持未登记。')

        changed = self.db.execute("""UPDATE game_players SET spirit_stones=spirit_stones-?
            WHERE account_id=? AND group_id=? AND player_id=? AND spirit_stones>=?""",
            (amount, *self.scope, supporter_id, amount))
        if changed.rowcount != 1:
            raise RuntimeError('support balance changed during registration')
        self.db.execute("""INSERT INTO game_supports
            (duel_id,account_id,group_id,supporter_id,supported_player_id,amount,created_at)
            VALUES(?,?,?,?,?,?,?)""",
            (duel['duel_id'], *self.scope, supporter_id, target_id, amount, now_iso))
        return self.db.execute("""SELECT rowid AS support_order,* FROM game_supports
            WHERE account_id=? AND group_id=? AND duel_id=? AND supporter_id=?""",
            (*self.scope, duel['duel_id'], supporter_id)).fetchone()

    def rows(self, duel):
        self._check_duel(duel)
        return self.db.execute("""SELECT s.rowid AS support_order,s.*,p.dao_name AS supporter_name
            FROM game_supports s JOIN game_players p
              ON p.account_id=s.account_id AND p.group_id=s.group_id
             AND p.player_id=s.supporter_id
            WHERE s.account_id=? AND s.group_id=? AND s.duel_id=?
            ORDER BY s.rowid""", (*self.scope, duel['duel_id'])).fetchall()

    def totals(self, duel):
        self._check_duel(duel)
        return self._totals_from_rows(duel, self.rows(duel))

    @staticmethod
    def _totals_from_rows(duel, rows):
        totals = {duel['challenger_id']: 0, duel['challenged_id']: 0}
        for row in rows:
            if row['supported_player_id'] not in totals:
                raise SupportError('本场斗法存在目标异常的支持记录，无法安全结算。')
            if type(row['amount']) is not int or row['amount'] <= 0:
                raise SupportError('本场斗法存在金额异常的支持记录，无法安全结算。')
            totals[row['supported_player_id']] += row['amount']
        return totals

    def settle(self, duel, winner=None, refund=False, *, rng=None):
        """Settle every pending row once; never changes duel or item state."""
        self._check_duel(duel)
        rows = self.rows(duel)
        if not rows:
            return []
        states = {row['settlement_state'] for row in rows}
        if states == {'paid'} or states == {'refunded'}:
            return rows
        if states != {'pending'}:
            logging.warning('Mixed support settlement state for duel %s: %s',
                            duel['duel_id'], sorted(states))
            raise SupportError('本场围观支持结算状态异常，未重复支付。')

        participants = (duel['challenger_id'], duel['challenged_id'])
        if not refund and winner is not None and winner not in participants:
            raise SupportError('本场斗法胜者信息无效，无法安全结算支持。')
        totals = self._totals_from_rows(duel, rows)
        refund_all = bool(refund or winner is None or any(totals[player] == 0 for player in participants))
        payouts = {}
        state = 'refunded' if refund_all else 'paid'
        if refund_all:
            payouts = {row['supporter_id']: row['amount'] for row in rows}
        else:
            winner_pool = totals[winner]
            loser_pool = totals[participants[1] if winner == participants[0] else participants[0]]
            winners = []
            distributed = 0
            for row in rows:
                if row['supported_player_id'] == winner:
                    share, remainder = divmod(loser_pool * row['amount'], winner_pool)
                    payouts[row['supporter_id']] = row['amount'] + share
                    winners.append((remainder, row['support_order'], row['supporter_id']))
                    distributed += share
                else:
                    payouts[row['supporter_id']] = 0
            leftover = loser_pool - distributed
            for _, _, supporter in sorted(winners, key=lambda item: (-item[0], item[1]))[:leftover]:
                payouts[supporter] += 1

            if rng is not None:
                for row in rows:
                    if row['supported_player_id'] == winner and payouts.get(row['supporter_id'], 0) > 0:
                        has_sword = self.db.execute("""SELECT 1 FROM game_items
                            WHERE account_id=? AND group_id=? AND owner_player_id=?
                              AND state='held' AND template_id IN ('qingfeng_jian', 'songwen_jian')
                            LIMIT 1""", (*self.scope, row['supporter_id'])).fetchone()
                        if has_sword and rng.randrange(100) < 25:
                            payouts[row['supporter_id']] *= 2

        for row in rows:
            payout = payouts[row['supporter_id']]
            balance = self.db.execute("""SELECT spirit_stones FROM game_players
                WHERE account_id=? AND group_id=? AND player_id=?""",
                (*self.scope, row['supporter_id'])).fetchone()
            if (balance is None or type(payout) is not int or payout < 0 or payout > _SQLITE_MAX
                    or balance['spirit_stones'] > _SQLITE_MAX - payout):
                raise SupportError('支持结算金额超出安全范围，未进行支付。')
        for row in rows:
            payout = payouts[row['supporter_id']]
            if payout:
                changed = self.db.execute("""UPDATE game_players SET spirit_stones=spirit_stones+?
                    WHERE account_id=? AND group_id=? AND player_id=? AND spirit_stones<=?""",
                    (payout, *self.scope, row['supporter_id'], _SQLITE_MAX - payout))
                if changed.rowcount != 1:
                    raise RuntimeError('support balance changed during settlement')
            changed = self.db.execute("""UPDATE game_supports SET settlement_state=?,payout=?
                WHERE account_id=? AND group_id=? AND duel_id=? AND supporter_id=?
                  AND settlement_state='pending'""",
                (state, payout, *self.scope, duel['duel_id'], row['supporter_id']))
            if changed.rowcount != 1:
                raise RuntimeError('support row changed during settlement')
        return self.rows(duel)
