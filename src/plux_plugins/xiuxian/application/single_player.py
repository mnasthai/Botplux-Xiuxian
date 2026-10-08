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
from .character import CharacterUseCases, _BEIJING, _SQLITE_MAX, _RANDOM, _CREATE_HINT, _message_id, _name, _val, _devil_highest_tier
from .growth import GrowthUseCases
from .inventory import InventoryUseCases
from .economy import EconomyUseCases
from .pvp import PvpUseCases
from . import duels

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



class Game(CharacterUseCases, GrowthUseCases, InventoryUseCases, EconomyUseCases, PvpUseCases):
    """Composable single player, economy, and PvP use cases."""


def handle_command(command, context, *, rng=None):
    """Apply one command in the caller's transaction; returning never sends directly."""
    group = context.conversation_id
    if (not group or not group.endswith('@chatroom') or group not in context.allowed_targets
            or not context.user_id or not context.event_key or context.message is None):
        return None
    if command.kind == 'pvp_cheat' and not context.is_admin:
        return '⚠️ 仅管理员可以使用祈愿。'
    if command.kind == 'usage':
        return f'👉 用法：{command.argument}'
    if command.kind == 'help':
        return _help_text(context.game_config)
    if command.kind in MODULE_HELP_KINDS:
        return with_help_navigation(format_module_help(command.kind, context.game_config))
    if command.kind == 'artifact_help':
        return with_help_navigation(format_artifact_help())
    if command.kind == 'pvp_help':
        return with_help_navigation(format_pvp_help())
    if command.kind == 'shop_help':
        return with_help_navigation(format_shop_help())
    if command.kind == 'devil_help':
        return with_help_navigation(format_devil_help(context.game_config))
    if command.kind.startswith('dungeon_'):
        from .dungeon.service import handle_command as handle_dungeon
        reply = handle_dungeon(command, context, rng=rng)
        return with_help_navigation(reply) if command.kind == 'dungeon_help' and reply else reply
    if command.kind in duels.KINDS:
        return duels.handle_command(command, context, rng=rng)
    game = Game(context, rng if rng is not None else _RANDOM)
    kind, argument = command.kind, command.argument
    if kind == 'book':
        return game.book()
    if kind == 'ranking':
        return game.ranking()
    if kind in ('devil_status', 'devil_sign', 'props', 'use_prop', 'pvp_duel', 'accept_pvp_duel', 'reject_pvp_duel', 'continue_pvp_duel', 'surrender_pvp_duel', 'pvp_stats', 'cancel_pvp_duel', 'pvp_duel_status', 'pvp_cheat'):
        if game.player is None:
            return _CREATE_HINT
    if kind == 'pvp_stats':
        return game.pvp_stats()
    if kind == 'pvp_duel_status':
        return game.pvp_duel_status()
    if kind == 'props':
        return game.props()
    if kind == 'devil_status':
        reply = game.devil_status()
        if game.notice and isinstance(reply, str):
            reply = game.notice + reply
        return reply
    if kind == 'shop':
        if game.player is None:
            return _CREATE_HINT
        return game.shop()
    if kind == 'profile' and not argument:
        reply = game.profile('')
        if game.notice:
            if isinstance(reply, str):
                reply = game.notice + reply
            elif isinstance(reply, tuple):
                reply = (game.notice,) + reply
            elif isinstance(reply, list):
                reply = [game.notice] + reply
            else:
                reply = (game.notice.strip(), reply)
        return reply
    if game.player is None and kind != 'profile':
        return _CREATE_HINT
    if kind == 'inventory':
        return game.inventory(argument)
    if kind not in ('profile', 'cultivate', 'self_cultivate', 'mine', 'breakthrough', 'explore', 'offer', 'buy', 'devil_sign', 'use_prop', 'pvp_duel', 'accept_pvp_duel', 'reject_pvp_duel', 'continue_pvp_duel', 'surrender_pvp_duel', 'cancel_pvp_duel', 'pvp_cheat'):
        return None

    message_id = _message_id(context.message)
    fingerprint = hashlib.sha256((context.message.content or '').encode('utf-8')).hexdigest()
    previous = game.repo.action(context.event_key, message_id)
    if previous:
        data = json.loads(previous['resource_json'])
        if (previous['group_id'] == group and previous['player_id'] == context.user_id
                and data.get('message_fingerprint') == fingerprint):
            return None
        logging.warning('Game command identity conflict for %s', context.event_key)
        return '⚠️ 消息标识发生冲突，本次未执行，请重新发送指令。'
    if kind not in ('cultivate', 'self_cultivate', 'mine', 'pvp_duel', 'accept_pvp_duel', 'reject_pvp_duel', 'continue_pvp_duel', 'surrender_pvp_duel', 'cancel_pvp_duel', 'pvp_cheat') and game.repo.duel():
        return '⚠️ 你正在斗法中，请等待本场结束后再改名、突破、探索、献宝或购买。'

    if kind == 'profile':
        reply = game.profile(argument)
    elif kind == 'offer':
        reply = game.offer(argument)
    elif kind == 'buy':
        reply = game.buy(argument)
    elif kind == 'devil_sign':
        reply = game.devil_sign()
    elif kind == 'use_prop':
        reply = game.use_prop(argument, command.target_id)
    elif kind == 'pvp_duel':
        reply = game.pvp_duel(command.target_id, command.amount)
    elif kind == 'pvp_cheat':
        reply = game.pvp_cheat(command.argument, command.amount)
    elif kind == 'accept_pvp_duel':
        reply = game.accept_pvp_duel()
    elif kind == 'reject_pvp_duel':
        reply = game.reject_pvp_duel()
    elif kind == 'continue_pvp_duel':
        reply = game.continue_pvp_duel()
    elif kind == 'surrender_pvp_duel':
        reply = game.surrender_pvp_duel()
    elif kind == 'cancel_pvp_duel':
        reply = game.cancel_pvp_duel()
    else:
        reply = getattr(game, kind)()

    if game.notice:
        if isinstance(reply, str):
            reply = game.notice + reply
        elif isinstance(reply, tuple):
            reply = (game.notice,) + reply
        elif isinstance(reply, list):
            reply = [game.notice] + reply
        else:
            reply = (game.notice.strip(), reply)

    if game.change is not None:
        summary = {'message_fingerprint': fingerprint, 'changes': game.change}
        game.repo.record_action(context.event_key, kind, message_id,
            json.dumps(summary, ensure_ascii=True, separators=(',', ':')), game.now)
    return reply
