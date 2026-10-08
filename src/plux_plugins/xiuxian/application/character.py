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

_BEIJING = timezone(timedelta(hours=8))
_SQLITE_MAX = 2**63 - 1
_RANDOM = random.SystemRandom()
_CREATE_HINT = '👉 你还没有创建修士，请发送「#修仙 <仙名>」。例如：#修仙 青玄。'


def format_devil_help(rules) -> str:
    """Return guide for the devil's bargain system."""
    return ("📖【大爱仙途 · 与魔鬼的契约】\n"
            "与魔鬼立约，各取所需，花费修为，获得大量财富！\n\n"
            "📜【契约规则】\n"
            "• 花费修为与魔鬼签订契约可以获得灵石，如果修为不足则无法签订契约。\n"
            f"• 契约共可深化 {rules.devil_contract_max_tier} 层，每次深化均可获得更加丰厚的灵石奖励。\n"
            "• 永久记录历史最高签订层数；破除契约后再次签订，须从历史最高层的下一层开始。\n"
            "• 重签按新层数扣除修为、发放该层奖励，不重复领取低层收益；历史达到最高层后不能再签。\n"
            "• 最高境界，不可签订魔契。\n"
            # "• 每次执行【#修炼】结算一次心魔抽吸（层级越高抽吸越重，若修为扣尽将触发道基崩塌跌落境界！）。\n"
            # "• 每次【#自主修炼】若走火入魔失败，有 50% 概率引动心魔反噬加剧，损失放大为（原损失 × (1 + 魔契层数)）！\n\n"
            "每次签约扣除修为：当前境界突破所需修为 × 10% × 将签订的层数。\n"
            "如果临近突破，也无法签订契约\n\n"
            "👉 查看魔契状态：#魔契\n"
            "👉 签订/深化魔契：#签订魔契\n"
            # "👉 努力修炼突破境界即可彻底破除魔契！"
            )


def _message_id(message):
    value = message.message_id_candidate
    if (isinstance(value, str) and len(value) <= 20 and value.isascii()
            and value.isdecimal() and 0 < int(value) < 2**64):
        return str(int(value))
    return None


def _name(argument):
    value = unicodedata.normalize('NFKC', argument).strip()
    if not 2 <= len(value) <= 12 or not all(c.isalnum() or c == '_' for c in value):
        return None
    return value, value.casefold()


def _val(row, key, default=None):
    if row is None:
        return default
    try:
        val = row[key]
        return default if val is None else val
    except (IndexError, KeyError):
        return default


def _devil_highest_tier(player):
    return max(_val(player, 'devil_contract_tier', 0) or 0,
               _val(player, 'devil_max_contract_tier', 0) or 0)



class CharacterUseCases:
    def __init__(self, context, rng):
        self.context = context
        self.rules = context.game_config
        self.repo = GameRepository(context.store, context.account_id, context.conversation_id, context.user_id)
        self.player = self.repo.player()
        self.now = datetime.fromtimestamp(context.now, timezone.utc).isoformat(timespec='microseconds')
        self.today = datetime.fromtimestamp(context.now, _BEIJING).date().isoformat()
        self.rng = rng
        self.change = None
        self.notice = ""

    def mention(self, player_id: str) -> str:
        """Return a visible group mention without exposing the member ID."""
        nickname = self.context.member_name(self.context.conversation_id, player_id)
        nickname = nickname.strip() if isinstance(nickname, str) else ''
        if not nickname:
            p_row = self.repo.store.execute(
                "SELECT dao_name FROM game_players WHERE account_id=? AND group_id=? AND player_id=?",
                (*self.repo.scope, player_id)
            ).fetchone()
            nickname = p_row['dao_name'] if p_row else '道友'
        return '@' + nickname + '\u2005'

    def _inventory_limit(self, inv=None):
        items = inv if inv is not None else self.repo.inventory()
        base = self.rules.inventory_limit
        if any(item['template_id'] == 'qiankun_ding' for item in items):
            return base + 2
        return base

    def profile(self, argument=""):
        if argument:
            return self.rename_or_create(argument)
        if self.player is None:
            return _CREATE_HINT
        player = self.player
        realm_index = REALMS.index(player['realm'])
        cultivation = str(player['cultivation'])
        inventory = self.repo.inventory()
        if realm_index < len(REALMS) - 1:
            required_cultivation, _ = breakthrough_costs(
                self.rules.breakthrough_cultivation[realm_index],
                self.rules.breakthrough_stones[realm_index], inventory)
            cultivation += f" / {required_cultivation}"
        else:
            cultivation += '（已达当前最高境界）'
        count = len(inventory)
        limit = self._inventory_limit(inventory)
        debuffs = self.repo.debuffs(today=self.today)
        remaining = self._self_cultivation_remaining(inventory, debuffs)
        mine_recent = self.repo.latest_action('mine')
        mine_remaining = 0
        mine_cooldown = 3600
        if mine_recent:
            try:
                mine_prev = datetime.fromisoformat(mine_recent['created_at'].replace('Z', '+00:00')).timestamp()
                mine_remaining = max(0, math.ceil(mine_cooldown - (self.context.now - mine_prev)))
            except (TypeError, ValueError, OverflowError, OSError):
                mine_remaining = mine_cooldown

        lines = [f"🧘【{player['dao_name']}】", f"🎆 境界：{REALM_NAMES[player['realm']]}",
                 f'✨ 修为：{cultivation}', f"💎 灵石：{player['spirit_stones']}",
                 f'⚔️ 法宝：{count}/{limit}',
                 f'🎒 道具：{self.repo.prop_count()}/3',
                 '🧘 今日修炼：' + ('已领取' if player['last_cultivated_on'] == self.today else '可领取'),
                 '🌀 今日秘境：' + ('已探索' if player['last_explored_on'] == self.today
                                    else f'可探索（需 {exploration_cost(self.rules.exploration_cost, inventory)} 灵石）'),
                 ('🌀 自主修炼：可进行' if remaining == 0 else f'⏳ 自主修炼：{remaining} 秒后可进行'),
                 ('⛏️ 灵矿采矿：可开采' if mine_remaining == 0 else f'⏳ 灵矿采矿：{mine_remaining} 秒后可开采')]
        if debuffs:
            d_names = [PROP_TEMPLATES[d['debuff_kind']].name for d in debuffs if d['debuff_kind'] in PROP_TEMPLATES]
            if d_names:
                lines.append(f"⚠️ 异常状态：受【{'、'.join(d_names)}】缠身中！")
        tier = (_val(player, 'devil_contract_tier', 0) or 0)
        if tier > 0:
            daily_loss = self.rules.devil_daily_losses[tier - 1]
            lines.append(f'👹 魔契状态：第 {tier} 层（每日 -{daily_loss} 修为）')
        if count > limit:
            lines.append('⚠️ 🎒 储物袋已超限，请先献宝整理，再探索秘境或参加斗法。')
        duel = self.repo.duel(include_invitation=True)
        if duel:
            lines.append('⚔️ 当前斗法：' + duel['duel_id'] + ('（等待应战）' if duel['state'] == 'inviting' else '（进行中）'))
        text = '\n'.join(lines)
        if not self.rules.visual_cards_enabled:
            return text
        daily_info = {
            'cultivated': player['last_cultivated_on'] == self.today,
            'explored': player['last_explored_on'] == self.today,
            'self_cultivate_remaining': remaining,
            'mine_remaining': mine_remaining,
            'duel_status': (f"{duel['duel_id']} · "
                            f"{'等待应战' if duel['state'] == 'inviting' else '进行中'}") if duel else '',
            'props': [dict(p) for p in self.repo.props()],
            'debuffs': [dict(d) for d in debuffs],
        }
        return self.context.card('profile', {
            'player': dict(player),
            'inventory': [dict(item) for item in inventory],
            'daily_info': daily_info,
            'rules': asdict(self.rules),
        })

    def rename_or_create(self, argument):
        name = _name(argument)
        if name is None:
            return '⚠️ 道号须为 2～12 个汉字、字母、数字或下划线。示例：#修仙 青玄'
        value, key = name
        owner = self.repo.name_owner(key)
        if owner and owner['player_id'] != self.context.user_id:
            return '⚠️ 这个道号已被本群其他修士使用，请换一个。'
        if self.player:
            if self.player['dao_name'] == value:
                return f'🧘 你已是修士【{value}】。\n👉 发送 #修仙 查看面板。'
            self.repo.update_player(dao_name=value, dao_name_key=key)
            self.change = {'dao_name': value}
            return f'🧘【道号更改】\n新道号：{value}'
        stones = self.rules.initial_spirit_stones
        if stones > _SQLITE_MAX:
            return '⚠️ 初始灵石配置超出可存储范围，请联系机器人维护者。'
        self.repo.create_player(value, key, stones, self.now)
        item_id = self.repo.grant_item(CATALOG['qingfeng_jian'], self.now)
        self.change = {'created': True, 'spirit_stones': stones, 'item_id': item_id}
        return (f'🧘【踏入仙途】\n{value}已拜入仙门，当前境界：炼气。\n\n'
                f'💎 灵石 +{stones}\n⚔️ 法宝：【青锋剑】{item_id}\n\n'
                '👉 发送 #修炼 领取今日修为，发送 #修仙帮助 查看玩法。')

    def book(self):
        existing = self.repo.rare_items()
        lines = ['📖【本群稀有宝录】']
        for template in CATALOG.values():
            if template.rarity not in ('ancient', 'treasure'):
                continue
            item = existing.get(template.id)
            owner = item['owner_name'] if item and item['state'] == 'held' else '尚在秘境'
            identifier = ' ' + item['item_id'] if item else ''
            lines.append(f'⚔️【{template.name}】{RARITY_NAMES[template.rarity]}{identifier} · {owner}')
        return '\n'.join(lines)

    def ranking(self):
        rows = self.repo.ranking()
        text = ('🏆 本群仙榜尚无人上榜。\n👉 发送 #修仙 道号 成为第一位修士。' if not rows else '\n'.join(['🏆【本群仙榜】'] + [
            f"{index}. 🧘 {row['dao_name']} · {REALM_NAMES[row['realm']]} · ✨ 修为 {row['cultivation']}"
            for index, row in enumerate(rows, 1)]))
        if not self.rules.visual_cards_enabled:
            return text
        return self.context.card('ranking', {
            'players': [dict(row) for row in rows],
            'total_count': self.repo.ranking_count(),
        })
