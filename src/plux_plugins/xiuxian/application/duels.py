"""Persistent lightning duels driven by committed messages and Plux polls."""
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import math
import random

from ..presentation.models import Reply
from ..domain.catalog import CATALOG
from ..persistence.duels import ACTIVE, DuelStore
from ..persistence.game import GameRepository
from ..persistence.supports import SupportError, SupportLedger


KINDS = frozenset({'challenge', 'accept', 'reject', 'cancel', 'lightning', 'support', 'duel_status', 'history'})
_RANDOM = random.SystemRandom()
_BEIJING = timezone(timedelta(hours=8))
_REASONS = {
    'declined': '受邀者拒绝了本场斗法。',
    'withdrawn': '发起者撤销了本场邀请。',
    'invitation_timeout': '邀请已到期，本场无人应战。',
    'ineligible': '接受时双方已不满足斗法条件，本场邀请取消。',
}


def _stamp(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def _seconds(value):
    if not isinstance(value, str):
        return None
    try:
        date = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return date.timestamp() if date.tzinfo is not None else None
    except (ValueError, OverflowError, OSError):
        return None


def _message_id(message):
    value = message.message_id_candidate
    if (isinstance(value, str) and len(value) <= 20 and value.isascii()
            and value.isdecimal() and 0 < int(value) < 2**64):
        return str(int(value))
    # Plux exposes the committed event identity instead of a native numeric ID.
    event_key = getattr(message, 'event_key', None)
    return event_key if isinstance(event_key, str) and 0 < len(event_key) <= 512 else None


def _rules(config):
    return json.dumps(asdict(config), ensure_ascii=True, separators=(',', ':'))


class _Duel:
    def __init__(self, context, group=None, rng=None):
        self.ctx = context
        self.repo = DuelStore(context, group)
        self.group = self.repo.group
        self.supports = SupportLedger(context, self.group)
        self.now = _stamp(context.now)
        self.today = datetime.fromtimestamp(context.now, _BEIJING).date().isoformat()
        self.rng = rng if rng is not None else _RANDOM
        self.changed = False

    def player_repo(self, player):
        return GameRepository(self.ctx.store, self.ctx.account_id, self.group, player)

    def name(self, player):
        row = self.player_repo(player).player()
        return row['dao_name'] if row else '修士'

    def mention(self, player):
        """Use verified group member data for the visible mention name."""
        nickname = self.ctx.member_name(self.group, player)
        nickname = nickname.strip() if isinstance(nickname, str) else ''
        return '@' + (nickname or self.name(player)) + '\u2005'

    def unsafe(self, duel=None):
        rules = json.loads(duel['rules_json']) if duel else asdict(self.ctx.game_config)
        issue = getattr(self.ctx, 'runtime_issue', None)
        if issue:
            if not issue.startswith('receiver_catching_up'):
                return 'runtime'
            pending_since = getattr(self.ctx, 'receiver_pending_since', None)
            if (pending_since is None or not
                    0 <= self.ctx.now - pending_since <= rules['poll_stall_seconds']):
                return 'receiver_stalled'
        if not self.ctx.connection_id:
            return 'connection'
        if duel and duel['observer_session_id'] != self.ctx.connection_id:
            return 'session_changed'
        previous = self.ctx.previous_poll_at
        if previous is not None and not 0 <= self.ctx.now - previous <= rules['poll_stall_seconds']:
            return 'poll_stalled'
        if duel and self.ctx.now < (_seconds(duel['created_at']) or self.ctx.now):
            return 'clock_changed'
        if self.group not in self.ctx.allowed_targets:
            return 'target_disabled'
        if not self.ctx.game_config.duel_enabled:
            return 'duel_disabled'
        return None

    def phase(self, duel, state, text, mentions, **values):
        sequence = duel['phase_sequence'] + (duel['prompt_request_id'] is not None)
        key = f"duel:{duel['duel_id']}:phase:{sequence}"
        rules = json.loads(values.get('rules_json', duel['rules_json']))
        self.repo.update(duel, state=state, phase_sequence=sequence,
            prompt_request_id=self.ctx.reply_request_id(key), prompt_wait_started_at=self.now,
            phase_opened_at=None, phase_deadline_at=None, **values)
        self.changed = True
        return Reply(f"⚔️【斗法 {duel['duel_id']}】\n\n{text}", mention_ids=tuple(mentions),
                     target_id=self.group, request_key=key, expires_in=rules['prompt_timeout_seconds'])

    def finish(self, duel, reason, *, winner=None, loser=None, loot=None, cultivation_loss=None,
               extra_loot=None, zhenhun_triggered=False, longwen_triggered=False,
               taixu_triggered=False, qingshuang_triggered=False):
        if reason.startswith('technical:'):
            # Record the health category without raw error paths or message text.
            issue = (getattr(self.ctx, 'runtime_issue', None) or 'none').partition(':')[0]
            logging.warning('Duel %s cancelled (%s; runtime=%s)', duel['duel_id'], reason, issue)
        self.repo.cancel_prompt(duel, self.now)
        if winner is None:
            self.supports.settle(duel, refund=True)
            if reason in ('declined', 'invitation_timeout'):
                self.ctx.store.execute(
                    "UPDATE game_players SET consecutive_refuse_duel_count = "
                    "consecutive_refuse_duel_count + 1 "
                    "WHERE account_id=? AND group_id=? AND player_id=?",
                    (self.ctx.account_id, self.group, duel['challenged_id']),
                )
        else:
            self.ctx.store.execute(
                "UPDATE game_players SET consecutive_refuse_duel_count = 0 "
                "WHERE account_id=? AND group_id=? AND player_id IN (?, ?)",
                (self.ctx.account_id, self.group,
                 duel['challenger_id'], duel['challenged_id']),
            )
        changes = {}
        if cultivation_loss is not None or extra_loot is not None or zhenhun_triggered or longwen_triggered or taixu_triggered or qingshuang_triggered:
            rules = json.loads(duel['rules_json'])
            rules['settlement'] = {
                'cultivation_loss': cultivation_loss if cultivation_loss is not None else 0,
                'extra_loot': extra_loot,
                'zhenhun_triggered': zhenhun_triggered,
                'longwen_triggered': longwen_triggered,
                'taixu_triggered': taixu_triggered,
                'qingshuang_triggered': qingshuang_triggered,
            }
            changes['rules_json'] = json.dumps(rules, ensure_ascii=True, separators=(',', ':'))
        self.changed = True
        return self.repo.update(duel, state='settled' if winner else 'cancelled', final_reason=reason,
            winner_player_id=winner, loser_player_id=loser, loot_item_id=loot,
            prompt_request_id=self.ctx.reply_request_id(f"duel:{duel['duel_id']}:result"),
            prompt_wait_started_at=None, phase_opened_at=None, phase_deadline_at=None, **changes)

    def result_text(self, duel, page=1):
        header = f"⚔️【斗法 {duel['duel_id']}】\n\n"
        reason = duel['final_reason'] or ''
        if duel['state'] == 'cancelled':
            text = _REASONS.get(reason, '本场斗法因运行中断或提示异常而取消。')
            dormant_tip = ''
            challenged_id = dict(duel).get('challenged_id') if duel else None
            if reason in ('declined', 'invitation_timeout') and challenged_id:
                ch_row = self.ctx.store.execute(
                    "SELECT consecutive_refuse_duel_count FROM game_players WHERE account_id=? AND group_id=? AND player_id=?",
                    (self.ctx.account_id, self.group, challenged_id)
                ).fetchone()
                cnt = int(ch_row[0]) if ch_row and ch_row[0] is not None else 0
                if cnt >= 3:
                    dormant_tip = (
                        f"\n\n⚠️【至宝自晦】受邀方已连续 {cnt} 次拒绝或未响应斗法/决斗，战意溃散自晦，"
                        f"触发至宝沉眠，整体攻击力减少 60%！唯有参与一次【#斗法】方可唤醒解除！"
                    )
                elif cnt > 0:
                    dormant_tip = f"\n（受邀方连续拒绝/未响应 {cnt}/3 次，达 3 次将触发至宝沉眠）"
            return header + '↩️ ' + text + '\n双方未损失法宝或修为，本场不计斗法次数。' + dormant_tip + self.support_summary(duel, page)
        winner, loser = self.name(duel['winner_player_id']), self.name(duel['loser_player_id'])
        item = self.player_repo(duel['winner_player_id']).item(duel['loot_item_id'])
        treasure = CATALOG[item['template_id']].name if item else '法宝'
        if reason == 'timeout':
            text = (f'⏳【引雷超时】\n\n☁️ 雷云翻涌，法诀迟迟未起。\n'
                    f'{loser}未在时限内引雷，本场判负！')
        else:
            text = ('☁️ 🌩️ ☁️\n⚡ ⚡ ⚡\n💥 😵 💥\n\n'
                    f'【紫霄神雷 · 落！】\n\n{loser}引雷在身，护体灵光应声而碎，斗法落败！')
        if item and item['template_id'] == 'tishen_caoren':
            text += f'\n\n🏆 {winner}获胜\n🎋【替身草人】替主挡灾应声碎裂！'
        else:
            text += f'\n\n🏆 {winner}获胜\n⚔️【{treasure}】归{winner}所有'

        settlement = json.loads(duel['rules_json']).get('settlement', {})
        if settlement.get('taixu_triggered'):
            text += f'\n🪡【太虚神针 · 破气】神针无视草人穿透护体罡气，强行夺走真实法宝！'
        extra_loot = settlement.get('extra_loot')
        if extra_loot:
            text += f'\n👻【万魂幡 · 噬魂摄宝】阴风卷动，从秘境额外卷出普通法器【{extra_loot["name"]}】归{winner}所有！'

        loss = settlement.get('cultivation_loss')
        if settlement.get('longwen_triggered'):
            text += f'\n🥁【龙纹鼓 · 龙吟】龙魂长啸护体，{loser}今日首战免除修为扣除！'
        elif settlement.get('zhenhun_triggered'):
            text += f'\n🔔【镇魂铃 · 定魄】神铃清响镇住神魂，{loser}免除 20 点修为扣除！'
        elif loss is not None:
            extra_frost_tip = '（含青霜剑【霜刃】追加 -10 修为）' if settlement.get('qingshuang_triggered') else ''
            text += f'\n📉 {loser}损失 {loss} 修为{extra_frost_tip}，胜者修为不变。'
        return header + text + self.support_summary(duel, page)

    def support_summary(self, duel, page=1):
        rows = self.supports.rows(duel)
        if not rows:
            return '\n\n💎 本场没有围观支持。'
        pages = (len(rows) + 9) // 10
        if not 1 <= page <= pages:
            return f'\n\n👉 围观明细共 {pages} 页，请使用 #战绩 {duel["duel_id"]} 页码 查询。'
        refunded = all(row['settlement_state'] == 'refunded' for row in rows)
        label = '↩️ 围观支持已全部原额退还' if refunded else '💎 围观支持结算'
        lines = [f'\n\n{label}（第 {page}/{pages} 页）：']
        if refunded:
            if duel['state'] == 'cancelled':
                lines.append('本场斗法取消，支持原额退还。')
            elif duel['final_reason'] == 'timeout':
                lines.append('本场因引雷超时结束，支持原额退还。')
            elif len({row['supported_player_id'] for row in rows}) == 1:
                lines.append('本场仅一方收到支持，支持原额退还。')
        for row in rows[(page - 1) * 10:page * 10]:
            payout = row['payout']
            net = payout - row['amount']
            change = f'净赚 {net}' if net > 0 else f'损失 {-net}' if net < 0 else '收回本金'
            lines.append(f"💎 {row['supporter_name']}：支持 {row['amount']}｜到账 {payout}（{change}）")
        if page < pages:
            lines.append(f'👉 其余 {len(rows) - page * 10} 人：#战绩 {duel["duel_id"]} {page + 1}')
        return '\n'.join(lines)

    def result_reply(self, duel):
        if not self.ctx.connection_id or self.group not in self.ctx.allowed_targets:
            return None
        if self.ctx.receipt(duel['prompt_request_id']) is not None:
            return None
        return Reply(self.result_text(duel), target_id=self.group,
                     request_key=f"duel:{duel['duel_id']}:result")

    def card_support_summary(self, duel):
        """Return a compact, post-settlement spectator summary for a card."""
        rows = self.supports.rows(duel)
        if not rows:
            return '本场无围观灵石支持'
        total = sum(row['amount'] for row in rows)
        if all(row['settlement_state'] == 'refunded' for row in rows):
            return f'围观支持：{len(rows)} 人｜{total} 灵石已原额退还'
        winner = duel['winner_player_id']
        correct = [row for row in rows if row['supported_player_id'] == winner]
        net = sum(row['payout'] - row['amount'] for row in correct)
        return f'围观支持：{len(rows)} 人｜支持胜方 {len(correct)} 人｜支持者净收益 +{net} 灵石'

    def synchronize(self, duel):
        """Confirm a prompt or cancel unsafe play; never penalize a timeout here."""
        if duel['state'] not in ACTIVE:
            return duel
        problem = self.unsafe(duel)
        if problem:
            return self.finish(duel, 'technical:' + problem)
        prompt = self.repo.prompt(duel)
        if prompt is None:
            return self.finish(duel, 'technical:missing_prompt')
        if prompt.status in ('rejected', 'expired', 'cancelled', 'unknown'):
            return self.finish(duel, 'technical:prompt_failed')
        if duel['phase_opened_at']:
            return duel
        rules = json.loads(duel['rules_json'])
        wait = _seconds(duel['prompt_wait_started_at'])
        if wait is None:
            return self.finish(duel, 'technical:missing_prompt_time')
        if prompt.status == 'accepted':
            accepted = [attempt for attempt in prompt.attempts
                        if attempt.status == 'accepted']
            if len(accepted) != 1:
                return self.finish(duel, 'technical:invalid_receipt')
            attempt = accepted[0]
            completed = attempt.finished_at.timestamp() if attempt.finished_at else None
            started = attempt.started_at.timestamp()
            if (completed is None or not wait <= started <= completed <= self.ctx.now
                    or completed >= wait + rules['prompt_timeout_seconds']):
                return self.finish(duel, 'technical:invalid_submission_time')
            duration = rules[{'inviting': 'invitation_timeout_seconds',
                              'supporting': 'support_timeout_seconds', 'playing': 'turn_timeout_seconds'}[duel['state']]]
            return self.repo.update(duel, phase_opened_at=_stamp(completed),
                                    phase_deadline_at=_stamp(completed + duration))
        if self.ctx.now >= wait + rules['prompt_timeout_seconds']:
            return self.finish(duel, 'technical:prompt_timeout')
        return duel

    def eligible_pair(self, challenger, challenged, rules):
        for player in (challenger, challenged):
            repo = self.player_repo(player)
            if repo.player() is None:
                return '👉 双方都须先发送 #修仙 道号 创建修士。'
            inv = repo.inventory()
            limit = rules.duel_inventory_limit + (2 if any(i['template_id'] == 'qiankun_ding' for i in inv) else 0)
            if not 1 <= len(inv) <= limit:
                return f'🎒 双方须各持有 1～{limit} 件法宝，先寻宝或献宝整理后再来。'
            if self.repo.counted(player, self.today) >= rules.daily_duel_limit:
                return '⏳ 有一方已达到今日斗法次数上限。'
        if self.repo.counted(challenger, self.today, challenged) >= rules.pair_daily_duel_limit:
            return '⏳ 你们两人今日的相互斗法次数已达上限。'
        return None

    def challenge(self, command):
        user, target = self.ctx.user_id, command.target_id
        message = self.ctx.message
        if (message.mention_state != 'explicit_other' or tuple(message.mentioned_ids) != (target,)
                or not target or target in (user, self.ctx.account_id, 'notify@all')):
            return '👉 请从群成员列表真正 @ 一名其他修士，不能挑战自己或机器人。'
        if self.repo.active():
            return '⏳ 本群已有邀请或斗法进行中，请等本场结束。'
        from ..persistence.dungeon import active_run
        for player_id in (user, target):
            if active_run(self.ctx.store, self.ctx.account_id, self.group, player_id):
                return '⏳ 参战者正在副本队伍中，请先结束或退出副本。'
            if GameRepository(self.ctx.store, self.ctx.account_id, self.group, player_id).get_active_pvp_duel_for_player(player_id):
                return '⏳ 参战者已有仙道决斗邀请或对局，请先结束该场决斗。'
        rules = self.ctx.game_config
        error = self.eligible_pair(user, target, rules)
        if error:
            return error
        midnight = datetime.fromtimestamp(self.ctx.now, _BEIJING).replace(hour=0, minute=0, second=0, microsecond=0)
        stats = self.repo.invitation_stats(user, _stamp(midnight.timestamp()),
                                            _stamp((midnight + timedelta(days=1)).timestamp()))
        if (stats['today_count'] or 0) >= rules.daily_invitation_limit:
            return '⏳ 今日发起邀请次数已达上限，明日再来。'
        if stats['last_day'] is not None:
            last = (stats['last_day'] - 2440587.5) * 86400
            if self.ctx.now - last < rules.invitation_cooldown_seconds - 0.0001:
                return f'⏳ 发起邀请需间隔 {rules.invitation_cooldown_seconds} 秒，请稍后再试。'
        duel = self.repo.insert(user, target, rules.rules_version, _rules(rules), self.ctx.connection_id, self.now)
        text = (f'{self.name(user)}发起斗法！\n\n'
                # f'败者将随机失去一件法宝并扣 {rules.duel_loss_cultivation} 修为（最低为 0），胜者不加修为。\n'
                f'👉 {self.mention(target)}\n'
                f'请在 {rules.invitation_timeout_seconds} 秒内发送【#接受斗法】或【#拒绝斗法】。')
        return self.phase(duel, 'inviting', text, (target,))

    def act(self, command, duel):
        if duel is None:
            return '⚔️ 本群当前没有待处理的斗法。'
        if duel['state'] not in ACTIVE:
            return '⚔️ 本场斗法已结束。'
        opened, deadline = _seconds(duel['phase_opened_at']), _seconds(duel['phase_deadline_at'])
        captured = self.ctx.message.observed_at_ms
        if type(captured) is not int or captured < 0:
            return '⚠️ 未能确认这条指令的捕获时间，请重新发送。'
        captured /= 1000
        if opened is None:
            return '⏳ 本阶段提示尚未确认发出，请等待提示后再操作。'
        if deadline is None or not opened <= captured < deadline or captured > self.ctx.now:
            return '⏳ 这条指令不在当前阶段的有效时间内，未推进对局。'
        user, kind = self.ctx.user_id, command.kind
        if kind == 'support':
            if duel['state'] != 'supporting':
                return '⏳ 围观支持窗口尚未开放或已经封盘，本次未扣灵石。'
            if (self.ctx.message.mention_state != 'explicit_other'
                    or tuple(self.ctx.message.mentioned_ids) != (command.target_id,)):
                return '👉 请从群成员列表真正 @ 一名参战者。'
            try:
                self.supports.register(duel, user, command.target_id, command.amount, self.now)
            except SupportError as exc:
                return '⚠️ ' + str(exc)
            self.changed = True
            totals = self.supports.totals(duel)
            return (f'💎【斗法 {duel["duel_id"]} · 围观支持】\n\n'
                    f'{self.name(user)}支持{self.name(command.target_id)}\n'
                    f'💎 已扣除 {command.amount} 灵石\n\n'
                    f'双方支持：{self.name(duel["challenger_id"])}：{totals[duel["challenger_id"]]} 灵石｜'
                    f'{self.name(duel["challenged_id"])}：{totals[duel["challenged_id"]]} 灵石')
        if kind in ('accept', 'reject', 'cancel'):
            if duel['state'] != 'inviting':
                return '⚔️ 邀请已经结束，接受后的斗法不能主动撤销。'
            owner = duel['challenger_id'] if kind == 'cancel' else duel['challenged_id']
            if user != owner:
                return '⚠️ 这项操作只能由该邀请对应的玩家执行。'
            if kind != 'accept':
                duel = self.finish(duel, 'withdrawn' if kind == 'cancel' else 'declined')
                return self.result_reply(duel)
            rules = self.ctx.game_config
            from ..persistence.dungeon import active_run
            for player_id in (duel['challenger_id'], duel['challenged_id']):
                if active_run(self.ctx.store, self.ctx.account_id, self.group, player_id):
                    return '⏳ 参战者正在副本队伍中，暂不能接受斗法。'
                if GameRepository(self.ctx.store, self.ctx.account_id, self.group, player_id).get_active_pvp_duel_for_player(player_id):
                    return '⏳ 参战者已有仙道决斗邀请或对局，暂不能接受斗法。'
            error = self.eligible_pair(duel['challenger_id'], duel['challenged_id'], rules)
            if error:
                return self.result_reply(self.finish(duel, 'ineligible'))
            snapshot = asdict(rules)
            snapshot['inventory'] = {p: sorted(i['item_id'] for i in self.player_repo(p).inventory())
                                     for p in (duel['challenger_id'], duel['challenged_id'])}
            # Preselect one uniform candidate per possible loser while their
            # bags are locked. Settlement retries then never redraw the loot.
            def _choose_loot(player_id):
                inv = self.player_repo(player_id).inventory()
                straws = [item for item in inv if item['template_id'] == 'tishen_caoren']
                if straws:
                    return straws[0]['item_id']
                return self.rng.choice(inv)['item_id']

            snapshot['loot_by_player'] = {p: _choose_loot(p)
                                          for p in (duel['challenger_id'], duel['challenged_id'])}
            text = ('☁️  ☁️  ☁️ \n🌩️ 雷云蓄势 🌩️\n\n'
                    f"{self.mention(duel['challenger_id'])}与{self.mention(duel['challenged_id'])}的斗法已成立！\n\n"
                    f'⏳ 围观支持开放 {rules.support_timeout_seconds} 秒\n'
                    f'👉 发送【#支持 @参战者 金额】\n'
                    f'每人每场一次，限 {rules.support_minimum}～{rules.support_maximum} 灵石，参战者不能支持。\n\n'
                    f'💎 {self.name(duel["challenger_id"])}：0 灵石｜{self.name(duel["challenged_id"])}：0 灵石')
            return self.phase(duel, 'supporting', text, (duel['challenger_id'], duel['challenged_id']),
                lightning_position=self.rng.randint(1, 6), next_turn=1, current_player_id=duel['challenger_id'],
                counted_on=self.today, rules_version=rules.rules_version,
                rules_json=json.dumps(snapshot, ensure_ascii=True, separators=(',', ':')))
        if kind == 'lightning':
            if duel['state'] != 'playing':
                return '⏳ 尚未进入引雷回合，请等待开场提示。'
            if user != duel['current_player_id']:
                return '⏳ 还没有轮到你引雷，请等待自己的回合。'
            if duel['next_turn'] == duel['lightning_position']:
                return self.settle(duel, user, 'lightning')
            if not 1 <= duel['next_turn'] < duel['lightning_position'] <= 6:
                return self.result_reply(self.finish(duel, 'technical:invalid_turn'))
            other = duel['challenged_id'] if user == duel['challenger_id'] else duel['challenger_id']
            seconds = json.loads(duel['rules_json'])['turn_timeout_seconds']
            text = ('☁️  ⚡  ☁️\n✨ 有惊无险 ✨\n\n'
                    f'{self.name(user)}掐诀引雷！\n电光擦肩而过，雷声滚向云海深处。\n\n'
                    f'雷云未散，轮到下一位。\n\n👉 {self.mention(other)}\n'
                    f'请在 {seconds} 秒内发送【#引雷】')
            return self.phase(duel, 'playing', text, (other,), next_turn=duel['next_turn'] + 1, current_player_id=other)
        return None

    def settle(self, duel, loser, reason):
        winner = duel['challenged_id'] if loser == duel['challenger_id'] else duel['challenger_id']
        rules = json.loads(duel['rules_json'])
        items = {p: self.player_repo(p).inventory() for p in (winner, loser)}
        expected = rules.get('inventory', {})
        if (any(sorted(i['item_id'] for i in items[p]) != expected.get(p) for p in (winner, loser))
                or not items[loser]):
            return self.result_reply(self.finish(duel, 'technical:asset_mismatch'))
        selected = rules.get('loot_by_player', {}).get(loser)
        loot = next((item for item in items[loser] if item['item_id'] == selected), None)
        if loot is None:
            return self.result_reply(self.finish(duel, 'technical:missing_loot_choice'))
        self.supports.settle(duel, winner=winner, refund=reason == 'timeout', rng=self.rng)
        extra_loot = None
        taixu_triggered = False
        if loot['template_id'] == 'tishen_caoren' and any(item['template_id'] == 'taixu_shenzhen' for item in items[winner]):
            if self.rng.randrange(100) < 10:
                other_items = [i for i in items[loser] if i['template_id'] != 'tishen_caoren']
                if other_items:
                    loot = self.rng.choice(other_items)
                    taixu_triggered = True

        if loot['template_id'] == 'tishen_caoren':
            self.repo.retire(loot)
        else:
            self.repo.transfer(loot, winner)
            if any(item['template_id'] == 'wanhun_fan' for item in items[winner]) and self.rng.randrange(100) < 25:
                pool = [t for t in CATALOG.values() if t.rarity == 'artifact' and t.id != 'tishen_caoren']
                extra_template = self.rng.choice(pool)
                extra_item_id = self.player_repo(winner).grant_item(extra_template, self.now)
                extra_loot = {'template_id': extra_template.id, 'item_id': extra_item_id, 'name': extra_template.name}
        loser_repo = self.player_repo(loser)
        cultivation = loser_repo.player()['cultivation']
        base_loss = min(cultivation, rules.get('duel_loss_cultivation', 0))
        zhenhun = any(item['template_id'] == 'zhenhun_ling' for item in items[loser])
        longwen = any(item['template_id'] == 'longwen_gu' for item in items[loser])
        longwen_triggered = False
        if longwen:
            today_str = self.now[:10]
            prev_duels = self.ctx.store.execute(
                "SELECT count(*) FROM game_duels WHERE account_id=? AND group_id=? AND loser_player_id=? AND counted_on=? AND state='settled'",
                (self.repo.scope[0], self.group, loser, today_str)
            ).fetchone()[0]
            if prev_duels == 0:
                longwen_triggered = True

        zhenhun_triggered = False
        qingshuang_triggered = False
        if longwen_triggered:
            loss = 0
        elif reason == 'lightning' and zhenhun and self.rng.randrange(100) < 30:
            loss = 0
            zhenhun_triggered = True
        else:
            loss = base_loss
            if any(item['template_id'] == 'qingshuang_jian' for item in items[winner]) and cultivation > loss:
                extra_frost = min(cultivation - loss, 10)
                loss += extra_frost
                qingshuang_triggered = True
        loser_repo.update_player(cultivation=cultivation - loss)
        return self.result_reply(self.finish(duel, reason, winner=winner, loser=loser,
            loot=loot['item_id'], cultivation_loss=loss, extra_loot=extra_loot,
            zhenhun_triggered=zhenhun_triggered, longwen_triggered=longwen_triggered,
            taixu_triggered=taixu_triggered, qingshuang_triggered=qingshuang_triggered))

    def timeout(self, duel):
        # An on-time action may still be in the unread tail. Process it before
        # advancing a phase or taking assets for a timeout.
        if (getattr(self.ctx, 'runtime_issue', None) or '').startswith('receiver_catching_up'):
            return None
        deadline = _seconds(duel['phase_deadline_at'])
        if deadline is None or self.ctx.now < deadline:
            return None
        if duel['state'] == 'inviting':
            return self.result_reply(self.finish(duel, 'invitation_timeout'))
        if duel['state'] == 'playing':
            return self.settle(duel, duel['current_player_id'], 'timeout')
        if duel['state'] == 'supporting':
            name, other = self.name(duel['challenger_id']), self.name(duel['challenged_id'])
            seconds = json.loads(duel['rules_json'])['turn_timeout_seconds']
            text = ('☁️ ☁️ ☁️\n🌩️ 雷云已聚 🌩️\n\n'
                    f'{name}与{other}立于高天。\n紫霄神雷藏于云中，二人轮流引雷！\n\n'
                    f"👉 {self.mention(duel['challenger_id'])}\n请在 {seconds} 秒内发送【#引雷】")
            return self.phase(duel, 'playing', text, (duel['challenger_id'],))
        return None

    def status(self, duel):
        if duel is None:
            return '⚔️ 本群当前没有进行中的斗法。'
        names = f"{self.name(duel['challenger_id'])} · {self.name(duel['challenged_id'])}"
        if duel['state'] not in ACTIVE:
            return self.result_text(duel)
        stage = {'inviting': '等待应战', 'supporting': '雷云蓄势', 'playing': '引雷进行中'}[duel['state']]
        deadline = _seconds(duel['phase_deadline_at'])
        timing = '等待提示提交' if deadline is None else f'剩余 {max(0, math.ceil(deadline - self.ctx.now))} 秒'
        text = f"⚔️【斗法 {duel['duel_id']}】\n{names}\n⏳ {stage}｜{timing}"
        if duel['state'] == 'playing':
            text += f"\n👉 轮到：{self.name(duel['current_player_id'])}"
        totals = self.supports.totals(duel)
        text += (f'\n💎 支持：{self.name(duel["challenger_id"])} {totals[duel["challenger_id"]]} 灵石｜'
                 f'{self.name(duel["challenged_id"])} {totals[duel["challenged_id"]]} 灵石')
        return text

    def history(self, command):
        if command.argument:
            duel = self.repo.get(command.argument)
            if duel is None:
                return '⚔️ 本群没有这场斗法。'
            if duel['state'] in ACTIVE:
                return self.status(duel)
            return self.result_text(duel, command.page)
        rows = self.repo.history(self.ctx.user_id)
        if not rows:
            return '🏆 你在本群还没有已结束的斗法记录。'
        lines = ['🏆【最近五场战绩】']
        for row in rows:
            outcome = (f"胜者：{self.name(row['winner_player_id'])}" if row['state'] == 'settled' else '已取消')
            lines.append(f"{row['duel_id']} · {self.name(row['challenger_id'])} / {self.name(row['challenged_id'])} · {outcome}")
        return '\n'.join(lines) + '\n👉 详情：#战绩 对局编号'


def handle_command(command, context, *, rng=None):
    group, user, message = context.conversation_id, context.user_id, context.message
    if (not group or not group.endswith('@chatroom') or group not in context.allowed_targets
            or not user or not context.event_key or message is None):
        return None
    game = _Duel(context, rng=rng)
    if command.kind == 'history':
        return game.history(command)
    duel = game.repo.active()
    if command.kind == 'duel_status':
        return game.status(duel)
    if not context.game_config.duel_enabled:
        return '⏳ 引天雷斗法尚未开放，当前可修炼、寻宝和收藏法宝。'
    player = game.player_repo(user)
    if player.player() is None:
        return '👉 请先发送 #修仙 道号 创建修士。'
    identity = _message_id(message)
    if identity is None:
        return '⚠️ 未能确认这条消息的唯一标识，本次未操作，请重新发送指令。'
    fingerprint = hashlib.sha256(json.dumps([message.content, list(message.mentioned_ids)],
        ensure_ascii=True, separators=(',', ':')).encode()).hexdigest()
    old = player.action(context.event_key, identity)
    if old:
        if (old['group_id'] == group and old['player_id'] == user
                and json.loads(old['resource_json']).get('message_fingerprint') == fingerprint):
            return None
        logging.warning('Duel command identity conflict for %s', context.event_key)
        return '⚠️ 消息标识发生冲突，本次未执行，请重新发送指令。'
    original = duel
    if duel:
        duel = game.synchronize(duel)
    if duel and duel['state'] not in ACTIVE:
        reply = game.result_reply(duel)
    elif game.unsafe(duel):
        reply = '⚠️ 机器人当前的消息处理或连接状态不稳定，本次未发起或推进斗法。'
    elif command.kind == 'challenge':
        reply = game.challenge(command)
    else:
        reply = game.act(command, duel)
    record = original or game.repo.active()
    ignored = not game.changed
    if ignored and isinstance(reply, str):
        recent = context.store.execute("""SELECT resource_json,created_at FROM game_actions
            WHERE account_id=? AND group_id=? AND player_id=? AND action_kind=?
            ORDER BY created_at DESC LIMIT 1""",
            (context.account_id, group, user, 'duel_' + command.kind)).fetchone()
        if (recent and context.now - (_seconds(recent['created_at']) or 0) < 3
                and json.loads(recent['resource_json']).get('message_fingerprint') == fingerprint):
            reply = None
    game.repo.record_action(user, context.event_key, identity, command.kind,
        json.dumps({'message_fingerprint': fingerprint, 'ignored': ignored}, separators=(',', ':')),
        game.now, record)
    return reply


def _active(context):
    return context.store.execute("""SELECT * FROM game_duels WHERE account_id=?
        AND state IN ('inviting','supporting','playing') ORDER BY created_at,duel_id""",
        (context.account_id,)).fetchall()


def _notices(context, limit=3):
    if not context.connection_id or limit <= 0:
        return []
    rows = context.store.execute("""SELECT * FROM game_duels
        WHERE account_id=? AND state IN ('settled','cancelled')
          AND prompt_request_id IS NOT NULL
        ORDER BY created_at,duel_id""", (context.account_id,)).fetchall()
    replies = []
    for row in rows:
        if len(replies) >= limit:
            break
        reply = _Duel(context, row['group_id']).result_reply(row)
        if reply:
            replies.append(reply)
    return replies


def on_start(context):
    """Resume committed phases after a process restart."""
    for row in _active(context):
        _Duel(context, row['group_id']).synchronize(row)
    return _notices(context)


def on_before_messages(context, *, limit=3):
    for row in _active(context):
        _Duel(context, row['group_id']).synchronize(row)
    return _notices(context, limit)


def poll_pvp_duels(context, limit=3):
    """Settle timed-out PvP invitations and replay their durable notices."""
    now_iso = _stamp(context.now)
    if not (getattr(context, 'runtime_issue', None) or '').startswith('receiver_catching_up'):
        groups = context.store.execute(
            "SELECT DISTINCT group_id FROM game_pvp_duels WHERE account_id=? AND state IN ('inviting','fighting')",
            (context.account_id,)
        ).fetchall()
        for group in groups:
            GameRepository(context.store, context.account_id, group['group_id'], '').clean_expired_pvp_duels(now_iso)

    if not context.connection_id or limit <= 0:
        return []
    rows = context.store.execute(
        "SELECT duel_id,group_id,timeout_notice_json FROM game_pvp_duels "
        "WHERE account_id=? AND timeout_notice_json IS NOT NULL ORDER BY created_at,duel_id",
        (context.account_id,)
    ).fetchall()
    replies = []
    for row in rows:
        if len(replies) >= limit:
            break
        if row['group_id'] not in context.allowed_targets:
            continue
        notice = json.loads(row['timeout_notice_json'])
        key = f"pvp_duel:{row['duel_id']}:{notice['reason']}"
        if context.receipt(key) is not None:
            context.store.execute('UPDATE game_pvp_duels SET timeout_notice_json=NULL WHERE duel_id=?',
                                  (row['duel_id'],))
            continue
        replies.append(Reply(notice['text'], mention_ids=tuple(notice['mention_ids']),
                             target_id=row['group_id'], request_key=key))
    return replies


def on_poll(context, *, limit=3):
    replies = []
    for row in _active(context):
        game = _Duel(context, row['group_id'])
        row = game.synchronize(row)
        if row['state'] not in ACTIVE:
            continue
        if len(replies) < limit:
            reply = game.timeout(row)
            if reply:
                replies.append(reply)
    # Results just returned by timeout have not yet entered the reply queue.
    keys = {r.request_key for r in replies}
    for reply in _notices(context, limit):
        if len(replies) >= limit:
            break
        if reply.request_key not in keys:
            replies.append(reply)
            keys.add(reply.request_key)
    for reply in poll_pvp_duels(context, max(0, limit - len(replies))):
        if len(replies) >= limit:
            break
        if reply.request_key not in keys:
            replies.append(reply)
            keys.add(reply.request_key)
    return replies
