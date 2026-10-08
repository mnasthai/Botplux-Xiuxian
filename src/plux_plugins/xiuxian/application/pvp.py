"""Cultivation use cases inside the caller's game transaction."""
from __future__ import annotations
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import math
import random
import sqlite3
import unicodedata
from ..domain.catalog import CATALOG, RARITY_NAMES, REALM_NAMES, format_artifact_help
from ..domain.artifact_effects import (breakthrough_costs, daily_cultivation_rewards,
    exploration_cost, self_cultivation_reward, self_cultivation_success_percent, template_ids)
from ..domain.config import REALMS, RARITIES
from ..domain.cooldowns import self_cultivation_remaining
from ..domain.help import MODULE_HELP_KINDS, format_help as _help_text, format_module_help, with_help_navigation
from ..domain.shop import find_shop_item, format_shop, format_shop_help, PROP_TEMPLATES
from ..domain.pvp import (apply_cheat_mode, calculate_fighter_stats, init_fighters_for_duel,
    simulate_duel_round, format_round_report, format_surrender_report, format_duel_report,
    format_pvp_help, format_pvp_stats, simulate_duel, REALM_ORDER)
from ..persistence.game import GameRepository
from ..presentation.models import Reply
from .character import _BEIJING, _SQLITE_MAX, _RANDOM, _CREATE_HINT, _message_id, _name, _val, _devil_highest_tier

class PvpUseCases:
    def _expire_pvp(self):
        # Commands are judged at capture time: the worker may drain the last
        # queued input after the wall-clock deadline and clear the backlog flag.
        if self.context.runtime_issue == 'receiver_catching_up':
            return []
        when = self.context.now
        message = self.context.message
        captured = getattr(message, 'observed_at_ms', None)
        if type(captured) is int and 0 < captured <= when * 1000:
            when = captured / 1000
        effective_now = datetime.fromtimestamp(when, timezone.utc).isoformat(
            timespec='microseconds')
        return self.repo.clean_expired_pvp_duels(effective_now)

    def pvp_stats(self):
        self._expire_pvp()
        self.player = self.repo.player()
        inv = self.repo.inventory()
        last_duel = self.repo.get_player_last_duel_time(self.player['player_id'])
        p_dict = dict(self.player) if self.player is not None else {}
        refuse_count = p_dict.get('consecutive_refuse_duel_count', 0) or 0
        return format_pvp_stats(
            self.player,
            inv,
            consecutive_refuse_count=refuse_count,
            last_duel_at=last_duel,
            now_iso=self.now,
        )

    def pvp_duel(self, target_id: str | None, wager: int | None):
        if not target_id:
            return "👉 用法：#决斗 @群友 金额（1～1000 的正整数，例如：#决斗 @百里 1）"
        if target_id == self.player['player_id']:
            return "⚠️ 你不能向自己发起决斗！"
        if wager is None or wager <= 0 or wager > 1000:
            return "⚠️ 决斗押注金额必须是 1～1000 的正整数！"

        self._expire_pvp()
        self.player = self.repo.player()

        from ..persistence.dungeon import active_run
        if any(active_run(self.context.store, *self.repo.scope, player_id)
               for player_id in (self.player['player_id'], target_id)):
            return '⏳ 参战者正在副本队伍中，请先结束或退出副本。'

        target_row = self.repo.store.execute(
            "SELECT * FROM game_players WHERE account_id=? AND group_id=? AND player_id=?",
            (*self.repo.scope, target_id)
        ).fetchone()
        if not target_row:
            return "⚠️ 目标尚未踏入仙途，无法向其发起决斗。"

        if self.player['cultivation'] < 15:
            return f"⚠️ 你的当前修为仅有 {self.player['cultivation']}，气血过于虚弱（需≥15），无法发起决斗！"
        if target_row['cultivation'] < 15:
            return f"⚠️ 【{target_row['dao_name']}】当前修为仅有 {target_row['cultivation']}，气血虚弱，无法应战！"

        if self.player['spirit_stones'] < wager:
            return f"⚠️ 你的灵石不足！当前拥有 {self.player['spirit_stones']} 灵石，不足以押注 {wager} 灵石。"
        if target_row['spirit_stones'] < wager:
            return f"⚠️ 对方灵石不足！【{target_row['dao_name']}】当前仅有 {target_row['spirit_stones']} 灵石，无法接受 {wager} 灵石的押注决斗。"

        active_self = self.repo.get_active_pvp_duel_for_player(self.player['player_id'])
        if active_self:
            if active_self['state'] == 'inviting':
                if active_self['challenger_id'] == self.player['player_id']:
                    created_raw = active_self['created_at']
                    rem_sec = 60
                    try:
                        clean_ts = created_raw.replace('Z', '+00:00') if 'Z' in created_raw else created_raw
                        c_dt = datetime.fromisoformat(clean_ts)
                        if c_dt.tzinfo is None:
                            c_dt = c_dt.replace(tzinfo=timezone.utc)
                        n_dt = datetime.fromisoformat(self.now.replace('Z', '+00:00'))
                        rem_sec = max(0, 60 - int((n_dt - c_dt).total_seconds()))
                    except Exception:
                        pass
                    return f"⚠️ 你已向群友发起了决斗邀请，等待应答中（剩余 {rem_sec} 秒）！如需撤回请发送【#取消决斗】。"
                else:
                    return "⚠️ 你当前有一场待接受的决斗邀请，请先发送【#接受决斗】或【#拒绝决斗】。"
            return "⚠️ 你当前正在决斗交锋中，请先完成当前战斗或发送【#投降】。"

        active_target = self.repo.get_active_pvp_duel_for_player(target_id)
        if active_target:
            return f"⚠️ 【{target_row['dao_name']}】当前正在决斗中或已有未决邀请，请稍后再试。"
        if self.repo.traditional_duel_for_player(self.player['player_id']):
            return "⚠️ 你当前已有斗法进行中，请先完成斗法。"
        if self.repo.traditional_duel_for_player(target_id):
            return f"⚠️ 【{target_row['dao_name']}】当前已有斗法进行中，请稍后再试。"

        daily_count = self.repo.count_daily_pvp_duels(self.player['player_id'], target_id, self.today)
        if daily_count >= 10:
            return f"⚠️ 你与【{target_row['dao_name']}】今日已完成 5 场决斗，仙道切磋点到为止，请明日再战！"

        duel_id = self.repo.create_pvp_duel(self.player['player_id'], target_id, wager, self.now)
        self.change = {'pvp_duel_id': duel_id, 'target_id': target_id, 'wager': wager}

        attacker_order = REALM_ORDER.get(self.player['realm'], 1)
        defender_order = REALM_ORDER.get(target_row['realm'], 1)
        attacker_realm_name = REALM_NAMES.get(self.player['realm'], self.player['realm'])
        defender_realm_name = REALM_NAMES.get(target_row['realm'], target_row['realm'])

        diff = abs(attacker_order - defender_order)
        if diff >= 2:
            if attacker_order > defender_order:
                gap_notice = (
                    f"⚠️【境界悬殊 · 降维施压】发起者境界远高于受邀者（跨 {diff} 阶）！\n"
                    f"强者已获【金盾】护体（固定免伤 15%），唯有受邀者掷出 19/20 大成功方可贯穿！此战极其凶险！\n\n"
                )
            else:
                gap_notice = (
                    f"⚠️【越级挑战 · 逆境伐仙】发起者境界远低于受邀者（跨 {diff} 阶）！\n"
                    f"对方拥有元罡【金盾】（固定免伤 15%），唯有天机骰掷出 19 或 20 点大成功方可破盾！请谨慎迎战！\n\n"
                )
        else:
            gap_notice = ""

        return (
            f"⚔️【仙道决斗 · 战书下达】\n"
            f"【{self.player['dao_name']}】（{attacker_realm_name}）向【{target_row['dao_name']}】（{defender_realm_name}）发起仙道决斗！\n"
            f"💎 押注对决：{wager} 灵石\n\n"
            f"{gap_notice}"
            # f"📜 规则声明：\n"
            # f"• 胜者赢取对方押注灵石，绝不伤及法宝，不扣除真实道基修为！\n"
            f"• 请【{target_row['dao_name']}】在 60 秒内发送【#接受决斗】应战，或发送【#拒绝决斗】。"
        )

    def accept_pvp_duel(self):
        expired = self._expire_pvp()
        self.player = self.repo.player()
        duel = self.repo.get_pending_pvp_duel_for_target(self.player['player_id'])
        if not duel:
            if any(item['type'] == 'invite_timeout' and item['challenged_id'] == self.player['player_id']
                   for item in expired):
                return '⏳ 该决斗邀请已超时，擂台已自动撤销。'
            return "⚔️ 你当前没有待接受的决斗邀请。"

        challenger_row = self.repo.store.execute(
            "SELECT * FROM game_players WHERE account_id=? AND group_id=? AND player_id=?",
            (*self.repo.scope, duel['challenger_id'])
        ).fetchone()
        if not challenger_row:
            self.repo.update_pvp_duel_status(duel['duel_id'], 'cancelled')
            return "⚠️ 发起人已离开，决斗取消。"

        wager = int(duel['wager'])
        if self.player['spirit_stones'] < wager:
            return f"⚠️ 你的灵石不足！当前拥有 {self.player['spirit_stones']} 灵石，不足以应战 {wager} 灵石的决斗。"
        if challenger_row['spirit_stones'] < wager:
            return f"⚠️ 发起者【{challenger_row['dao_name']}】当前灵石不足 {wager}，决斗无法进行。"
        if self.player['cultivation'] < 15 or challenger_row['cultivation'] < 15:
            return "⚠️ 参战方气血过于虚弱（需≥15），决斗无法进行。"
        if (self.repo.traditional_duel_for_player(self.player['player_id'])
                or self.repo.traditional_duel_for_player(challenger_row['player_id'])):
            return "⚠️ 参战方已有斗法进行中，决斗邀请暂不能接受。"

        from ..persistence.dungeon import active_run
        if any(active_run(self.context.store, *self.repo.scope, player_id)
               for player_id in (self.player['player_id'], challenger_row['player_id'])):
            return '⏳ 参战者正在副本队伍中，决斗邀请暂不能接受。'

        self.repo.reserve_pvp_wagers(duel['duel_id'])
        self.player = self.repo.player()

        inv1 = self.repo.store.execute(
            "SELECT * FROM game_items WHERE account_id=? AND group_id=? AND owner_player_id=? AND state='held'",
            (*self.repo.scope, challenger_row['player_id'])
        ).fetchall()
        inv2 = self.repo.store.execute(
            "SELECT * FROM game_items WHERE account_id=? AND group_id=? AND owner_player_id=? AND state='held'",
            (*self.repo.scope, self.player['player_id'])
        ).fetchall()

        last_duel1 = self.repo.get_player_last_duel_time(challenger_row['player_id'])
        last_duel2 = self.repo.get_player_last_duel_time(self.player['player_id'])
        p_dict1 = dict(challenger_row) if challenger_row is not None else {}
        p_dict2 = dict(self.player) if self.player is not None else {}
        refuse1 = p_dict1.get('consecutive_refuse_duel_count', 0) or 0
        refuse2 = p_dict2.get('consecutive_refuse_duel_count', 0) or 0

        fighter1 = calculate_fighter_stats(challenger_row, inv1, consecutive_refuse_count=refuse1, last_duel_at=last_duel1, now_iso=self.now)
        fighter2 = calculate_fighter_stats(self.player, inv2, consecutive_refuse_count=refuse2, last_duel_at=last_duel2, now_iso=self.now)

        p1_id = challenger_row['player_id']
        p2_id = self.player['player_id']
        fighter1['player_id'] = p1_id
        fighter2['player_id'] = p2_id
        fighter1['mention'] = self.mention(p1_id)
        fighter2['mention'] = self.mention(p2_id)

        prepared = json.loads(duel['round_state_json']) if duel['round_state_json'] else {}
        cheat_modes = prepared.get('cheat_modes', {})
        if p1_id in cheat_modes:
            choice = cheat_modes[p1_id]
            fighter1 = apply_cheat_mode(fighter1, choice['mode'], choice.get('dice'))
        if p2_id in cheat_modes:
            choice = cheat_modes[p2_id]
            fighter2 = apply_cheat_mode(fighter2, choice['mode'], choice.get('dice'))

        init_fighters_for_duel(fighter1, fighter2)

        # Run Round 1
        round_res = simulate_duel_round(fighter1, fighter2, 1, self.rng)

        if round_res['is_over']:
            # Round 1 knockout! Settle immediately (100% wager)
            winner_idx = round_res['winner']
            winner_id = challenger_row['player_id'] if winner_idx == 1 else self.player['player_id']
            loser_id = self.player['player_id'] if winner_idx == 1 else challenger_row['player_id']

            self.repo.finish_pvp_duel(duel['duel_id'], 'settled', self.now, winner_id=winner_id)
            self.change = {'duel_id': duel['duel_id'], 'winner_id': winner_id, 'wager': wager}
            self.player = self.repo.player()

            duel_res = {
                'winner': winner_idx,
                'rounds': 1,
                'logs': round_res['logs'],
                'final_hp1': fighter1['hp'],
                'final_hp2': fighter2['hp'],
            }
            report = format_duel_report(fighter1, fighter2, duel_res, wager)
            dormant_notes = []
            if fighter1.get('has_dormant_treasure'):
                dormant_notes.append(f"💤 战意未醒：【{fighter1['name']}】处于【至宝沉眠】（整体攻击力减少 60%），唯有参与【#斗法】方可彻底唤醒！")
            if fighter2.get('has_dormant_treasure'):
                dormant_notes.append(f"💤 战意未醒：【{fighter2['name']}】处于【至宝沉眠】（整体攻击力减少 60%），唯有参与【#斗法】方可彻底唤醒！")
            if dormant_notes:
                report += "\n\n" + "\n".join(dormant_notes)
            return report
        else:
            # Round 1 finished and both alive -> enter 'fighting' state and wait for confirmation
            clean_ts = self.now.replace('Z', '+00:00') if 'Z' in self.now else self.now
            now_dt = datetime.fromisoformat(clean_ts)
            if now_dt.tzinfo is None:
                now_dt = now_dt.replace(tzinfo=timezone.utc)
            deadline_dt = now_dt + timedelta(seconds=60)
            deadline_iso = deadline_dt.isoformat(timespec='microseconds').replace('+00:00', 'Z')

            state_data = {
                'fighter1': fighter1,
                'fighter2': fighter2,
                'p1_confirmed': False,
                'p2_confirmed': False,
            }
            self.repo.update_pvp_duel_round_state(
                duel['duel_id'],
                current_round=1,
                round_state_json=json.dumps(state_data, ensure_ascii=False),
                round_deadline_at=deadline_iso,
                state='fighting'
            )
            self.change = {'pvp_duel_fighting': duel['duel_id'], 'round': 1}
            self.player = self.repo.player()

            diff = abs(fighter1.get('realm_order', 1) - fighter2.get('realm_order', 1))
            prefix = ""
            if diff >= 2:
                prefix = "⚠️【境界悬殊】双方相差 2 阶及以上，强者获【金盾】固定免伤 15%（唯有 19/20 大成功可贯穿）！\n\n"

            round_rep = format_round_report(fighter1, fighter2, round_res, wager, remaining_seconds=60)
            return Reply(prefix + f"💎 双方各 {wager} 灵石押注已托管，当前可消费余额不含押注。\n" + round_rep,
                         mention_ids=(p1_id, p2_id))

    def pvp_cheat(self, mode: str, custom_dice: int | None):
        if not self.context.is_admin:
            return '⚠️ 仅管理员可以使用祈愿。'
        self._expire_pvp()
        duel = self.repo.get_active_pvp_duel_for_player(self.context.user_id)
        if not duel:
            return '⚔️ 你当前没有待应战或正在交锋的决斗。'

        if self.context.user_id == duel['challenger_id']:
            fighter_key = 'fighter1'
        elif self.context.user_id == duel['challenged_id']:
            fighter_key = 'fighter2'
        else:
            return '⚠️ 你不是本场决斗的参战修士。'

        state_data = json.loads(duel['round_state_json']) if duel['round_state_json'] else {}
        if duel['state'] == 'inviting':
            cheat_modes = state_data.setdefault('cheat_modes', {})
            # if self.context.user_id in cheat_modes:
            #     return '⚠️ 本场决斗已经预备开挂模式。'
            # Validate before writing the invitation so malformed modes never persist.
            prepared = apply_cheat_mode({'name': self.player['dao_name']}, mode, custom_dice)
            cheat_modes[self.context.user_id] = {'mode': mode, 'dice': custom_dice}
            self.repo.store.execute(
                "UPDATE game_pvp_duels SET round_state_json=? WHERE duel_id=? "
                "AND account_id=? AND group_id=? AND state='inviting'",
                (json.dumps(state_data, ensure_ascii=False), duel['duel_id'], *self.repo.scope),
            )
            self.change = {'duel_id': duel['duel_id'], 'cheat_mode': mode,
                           'player_id': self.context.user_id}
            return f"✨【{self.player['dao_name']}】已预备「{prepared['cheat_title']}」--> 开战时生效。"

        fighter = state_data.get(fighter_key)
        if not isinstance(fighter, dict) or fighter.get('player_id') != self.context.user_id:
            return '⚠️ 决斗状态异常'
        # if fighter.get('is_cheat'):
        #     return '⚠️ 本场决斗已经启用开挂模式。'

        state_data[fighter_key] = apply_cheat_mode(fighter, mode, custom_dice)
        self.repo.update_pvp_duel_round_state(
            duel['duel_id'],
            current_round=int(duel['current_round']),
            round_state_json=json.dumps(state_data, ensure_ascii=False),
            round_deadline_at=duel['round_deadline_at'],
        )
        self.change = {'duel_id': duel['duel_id'], 'cheat_mode': mode,
                       'player_id': self.context.user_id}
        return (f"✨【{state_data[fighter_key]['name']}】已启用「{state_data[fighter_key]['cheat_title']}」；"
                '从下一轮交锋起生效。')

    def continue_pvp_duel(self):
        self._expire_pvp()
        self.player = self.repo.player()
        duel = self.repo.get_fighting_pvp_duel_for_player(self.player['player_id'])
        if not duel:
            return "⚔️ 你当前没有正在进行中的决斗交锋。"

        deadline_raw = duel['round_deadline_at']
        d_dt = None
        now_dt = None
        if deadline_raw:
            try:
                d_clean = deadline_raw.replace('Z', '+00:00') if 'Z' in deadline_raw else deadline_raw
                d_dt = datetime.fromisoformat(d_clean)
                if d_dt.tzinfo is None:
                    d_dt = d_dt.replace(tzinfo=timezone.utc)
                now_clean = self.now.replace('Z', '+00:00') if 'Z' in self.now else self.now
                now_dt = datetime.fromisoformat(now_clean)
                if now_dt.tzinfo is None:
                    now_dt = now_dt.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                pass

        state_data = json.loads(duel['round_state_json']) if duel['round_state_json'] else {}
        fighter1 = state_data.get('fighter1', {})
        fighter2 = state_data.get('fighter2', {})
        p1_confirmed = bool(state_data.get('p1_confirmed'))
        p2_confirmed = bool(state_data.get('p2_confirmed'))
        wager = int(duel['wager'])
        current_round = int(duel['current_round'] or 1)

        is_p1 = (self.player['player_id'] == duel['challenger_id'])
        is_p2 = (self.player['player_id'] == duel['challenged_id'])

        if not (is_p1 or is_p2):
            return "⚠️ 你不是本场决斗的参战修士。"

        rem_sec = 60
        if d_dt and now_dt:
            rem_sec = max(1, int((d_dt - now_dt).total_seconds()))

        # If user already confirmed
        if (is_p1 and p1_confirmed) or (is_p2 and p2_confirmed):
            other_name = fighter2.get('name', '对手') if is_p1 else fighter1.get('name', '发起者')
            return f"⏳ 你已确认继续决斗，正在等待【{other_name}】确认（剩余 {rem_sec} 秒）..."

        if is_p1:
            p1_confirmed = True
        else:
            p2_confirmed = True

        state_data['p1_confirmed'] = p1_confirmed
        state_data['p2_confirmed'] = p2_confirmed

        if not (p1_confirmed and p2_confirmed):
            # Only one confirmed so far
            self.repo.update_pvp_duel_round_state(
                duel['duel_id'],
                current_round=current_round,
                round_state_json=json.dumps(state_data, ensure_ascii=False),
                round_deadline_at=duel['round_deadline_at'],
                state='fighting'
            )
            my_name = fighter1.get('name', '修士') if is_p1 else fighter2.get('name', '修士')
            other_name = fighter2.get('name', '对手') if is_p1 else fighter1.get('name', '发起者')
            other_id = duel['challenged_id'] if is_p1 else duel['challenger_id']
            other_mention = self.mention(other_id)
            return Reply(
                f"⚔️ 【{my_name}】已确认继续决斗！\n"
                f"⏳ 正在等待【{other_name}】确认（倒计时 {rem_sec} 秒），双方均确认后立即打响下一轮！\n"
                f"👉 请 {other_mention} 发送【#继续决斗】应战，或发送【#投降】认输（扣除 80% 押注）。",
                mention_ids=(other_id,)
            )

        # Both confirmed! Advance to next round
        next_round = current_round + 1
        round_res = simulate_duel_round(fighter1, fighter2, next_round, self.rng)
        state_data['fighter1'] = fighter1
        state_data['fighter2'] = fighter2
        state_data['p1_confirmed'] = False
        state_data['p2_confirmed'] = False

        if round_res['is_over']:
            # Battle concluded!
            win_idx = round_res['winner']
            winner_id = duel['challenger_id'] if win_idx == 1 else duel['challenged_id']
            loser_id = duel['challenged_id'] if win_idx == 1 else duel['challenger_id']

            self.repo.finish_pvp_duel(duel['duel_id'], 'settled', self.now, winner_id=winner_id)
            self.change = {'duel_id': duel['duel_id'], 'winner_id': winner_id, 'wager': wager}
            self.player = self.repo.player()

            duel_res = {
                'winner': win_idx,
                'rounds': next_round,
                'logs': round_res['logs'],
                'final_hp1': fighter1['hp'],
                'final_hp2': fighter2['hp'],
            }
            report = format_duel_report(fighter1, fighter2, duel_res, wager)
            dormant_notes = []
            if fighter1.get('has_dormant_treasure'):
                dormant_notes.append(f"💤 战意未醒：【{fighter1['name']}】处于【至宝沉眠】（整体攻击力减少 60%），唯有参与【#斗法】方可彻底唤醒！")
            if fighter2.get('has_dormant_treasure'):
                dormant_notes.append(f"💤 战意未醒：【{fighter2['name']}】处于【至宝沉眠】（整体攻击力减少 60%），唯有参与【#斗法】方可彻底唤醒！")
            if dormant_notes:
                report += "\n\n" + "\n".join(dormant_notes)
            return report
        else:
            # Battle continues!
            clean_ts = self.now.replace('Z', '+00:00') if 'Z' in self.now else self.now
            now_dt = datetime.fromisoformat(clean_ts)
            if now_dt.tzinfo is None:
                now_dt = now_dt.replace(tzinfo=timezone.utc)
            next_deadline_dt = now_dt + timedelta(seconds=60)
            next_deadline_iso = next_deadline_dt.isoformat(timespec='microseconds').replace('+00:00', 'Z')

            p1_id = duel['challenger_id']
            p2_id = duel['challenged_id']
            fighter1['player_id'] = p1_id
            fighter2['player_id'] = p2_id
            fighter1['mention'] = self.mention(p1_id)
            fighter2['mention'] = self.mention(p2_id)

            self.repo.update_pvp_duel_round_state(
                duel['duel_id'],
                current_round=next_round,
                round_state_json=json.dumps(state_data, ensure_ascii=False),
                round_deadline_at=next_deadline_iso,
                state='fighting'
            )
            self.change = {'pvp_duel_fighting': duel['duel_id'], 'round': next_round}
            self.player = self.repo.player()
            round_rep = format_round_report(fighter1, fighter2, round_res, wager, remaining_seconds=60)
            return Reply(round_rep, mention_ids=(p1_id, p2_id))

    def surrender_pvp_duel(self):
        self._expire_pvp()
        self.player = self.repo.player()
        duel = self.repo.get_fighting_pvp_duel_for_player(self.player['player_id'])
        if not duel:
            return "⚔️ 你当前没有正在进行中的决斗交锋。"

        is_p1 = (self.player['player_id'] == duel['challenger_id'])
        surrendered_id = self.player['player_id']
        winner_id = duel['challenged_id'] if is_p1 else duel['challenger_id']

        state_data = json.loads(duel['round_state_json']) if duel['round_state_json'] else {}
        fighter1 = state_data.get('fighter1', {})
        fighter2 = state_data.get('fighter2', {})
        surrendered_fighter = fighter1 if is_p1 else fighter2
        winner_fighter = fighter2 if is_p1 else fighter1

        wager = int(duel['wager'])
        penalty = int(wager * 0.8)

        self.repo.finish_pvp_duel(duel['duel_id'], 'settled', self.now,
                                  winner_id=winner_id, surrendered_id=surrendered_id)

        self.change = {'duel_id': duel['duel_id'], 'winner_id': winner_id, 'surrendered_id': surrendered_id, 'penalty': penalty}
        self.player = self.repo.player()

        return format_surrender_report(surrendered_fighter, winner_fighter, wager, penalty, is_timeout=False)

    def reject_pvp_duel(self):
        expired = self._expire_pvp()
        self.player = self.repo.player()
        duel = self.repo.get_pending_pvp_duel_for_target(self.player['player_id'])
        if not duel:
            if any(item['type'] == 'invite_timeout' and item['challenged_id'] == self.player['player_id']
                   for item in expired):
                return '⏳ 该决斗邀请已超时，擂台已自动撤销。'
            return "⚔️ 你当前没有待处理的决斗邀请。"

        self.repo.update_pvp_duel_status(duel['duel_id'], 'rejected')
        new_refuse_count = self.repo.increment_refuse_duel_count(self.player['player_id'])
        self.change = {'rejected_duel_id': duel['duel_id']}

        # Refresh self.player cache
        self.player = self.repo.player()

        challenger_row = self.repo.store.execute(
            "SELECT dao_name FROM game_players WHERE account_id=? AND group_id=? AND player_id=?",
            (*self.repo.scope, duel['challenger_id'])
        ).fetchone()
        challenger_name = challenger_row['dao_name'] if challenger_row else '发起者'

        if new_refuse_count >= 3:
            dormant_msg = (
                f"\n⚠️ 你已连续 {new_refuse_count} 次拒绝或未响应斗法/决斗，战意溃散自晦，"
                f"至宝威能沉眠，整体攻击力减少 60%！唯有参与一次【#斗法】方可唤醒解除（参与决斗无法移除沉眠）。"
            )
        else:
            dormant_msg = f"（连续拒绝/未响应 {new_refuse_count}/3 次）"

        return f"⚔️ 【{self.player['dao_name']}】拒绝了【{challenger_name}】的决斗邀请，擂台作罢。{dormant_msg}"

    def cancel_pvp_duel(self):
        self._expire_pvp()
        self.player = self.repo.player()
        duel = self.repo.store.execute(
            "SELECT * FROM game_pvp_duels WHERE account_id=? AND group_id=? AND challenger_id=? AND state='inviting' "
            "ORDER BY created_at DESC LIMIT 1",
            (*self.repo.scope, self.player['player_id'])
        ).fetchone()

        if not duel:
            active_fighting = self.repo.get_fighting_pvp_duel_for_player(self.player['player_id'])
            if active_fighting:
                return "⚠️ 决斗已经打响，无法直接取消！若欲认输请发送【#投降】止损。"
            return "⚔️ 你当前没有正在等待应战的决斗邀请。"

        self.repo.finish_pvp_duel(duel['duel_id'], 'cancelled', self.now)
        self.change = {'cancelled_pvp_duel_id': duel['duel_id']}

        challenged_row = self.repo.store.execute(
            "SELECT dao_name FROM game_players WHERE account_id=? AND group_id=? AND player_id=?",
            (*self.repo.scope, duel['challenged_id'])
        ).fetchone()
        challenged_name = challenged_row['dao_name'] if challenged_row else '对方'
        return f"⚔️ 【{self.player['dao_name']}】撤销了向【{challenged_name}】发起的仙道决斗邀请，邀请阶段未扣押注灵石。"

    def pvp_duel_status(self):
        self._expire_pvp()
        self.player = self.repo.player()
        duel = self.repo.get_active_pvp_duel_for_player(self.player['player_id'])
        if not duel:
            duel = self.repo.store.execute(
                "SELECT * FROM game_pvp_duels WHERE account_id=? AND group_id=? AND state IN ('inviting', 'fighting') "
                "ORDER BY created_at DESC LIMIT 1",
                self.repo.scope
            ).fetchone()

        if not duel:
            return "⚔️ 当前本群暂无进行中或等待应战的仙道决斗。发送【#决斗 @群友 金额】下达战书！"

        p1 = self.repo.player(duel['challenger_id'])
        p2 = self.repo.player(duel['challenged_id'])
        p1_name = p1['dao_name'] if p1 else '挑战者'
        p2_name = p2['dao_name'] if p2 else '受邀者'
        wager = duel['wager']
        now_dt = None
        try:
            now_dt = datetime.fromisoformat(self.now.replace('Z', '+00:00'))
            if now_dt.tzinfo is None:
                now_dt = now_dt.replace(tzinfo=timezone.utc)
        except Exception:
            pass

        if duel['state'] == 'inviting':
            rem_sec = 60
            if now_dt and duel['created_at']:
                try:
                    c_clean = duel['created_at'].replace('Z', '+00:00') if 'Z' in duel['created_at'] else duel['created_at']
                    c_dt = datetime.fromisoformat(c_clean)
                    if c_dt.tzinfo is None:
                        c_dt = c_dt.replace(tzinfo=timezone.utc)
                    rem_sec = max(0, 60 - int((now_dt - c_dt).total_seconds()))
                except Exception:
                    pass
            return (
                f"⚔️【仙道决斗 · 等待应战中】\n"
                f"挑战者：【{p1_name}】\n"
                f"受邀者：【{p2_name}】\n"
                f"💎 押注对决：{wager} 灵石\n"
                f"⏳ 剩余应答时间：{rem_sec} 秒\n\n"
                f"• 请【{p2_name}】发送【#接受决斗】或【#拒绝决斗】\n"
                f"• 发起者可发送【#取消决斗】撤销邀请"
            )
        elif duel['state'] == 'fighting':
            rem_sec = 60
            if now_dt and duel['round_deadline_at']:
                try:
                    d_clean = duel['round_deadline_at'].replace('Z', '+00:00') if 'Z' in duel['round_deadline_at'] else duel['round_deadline_at']
                    d_dt = datetime.fromisoformat(d_clean)
                    if d_dt.tzinfo is None:
                        d_dt = d_dt.replace(tzinfo=timezone.utc)
                    rem_sec = max(0, int((d_dt - now_dt).total_seconds()))
                except Exception:
                    pass
            round_num = duel['current_round'] or 1
            state_data = json.loads(duel['round_state_json']) if duel['round_state_json'] else {}
            p1_ok = '✅ 已确认' if state_data.get('p1_confirmed') else '⏳ 待确认'
            p2_ok = '✅ 已确认' if state_data.get('p2_confirmed') else '⏳ 待确认'
            return (
                f"⚔️【仙道决斗 · 第 {round_num} 轮交锋中】\n"
                f"参战双方：【{p1_name}】 vs 【{p2_name}】\n"
                f"💎 押注对决：{wager} 灵石\n"
                f"• 【{p1_name}】：{p1_ok}\n"
                f"• 【{p2_name}】：{p2_ok}\n"
                f"⏳ 剩余确认时间：{rem_sec} 秒\n\n"
                f"• 双方请在倒计时结束前发送【#继续决斗】推进\n"
                f"• 劣势方可发送【#投降】止损（扣除 80% 押注）"
            )
        return "⚔️ 当前没有进行中的仙道决斗。"
