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

class EconomyUseCases:
    def shop(self):
        purchases = self.repo.shop_group_purchases_today(self.today)
        return format_shop(self.player, purchases)

    def buy(self, argument):
        item = find_shop_item(argument)
        if item is None:
            return ('⚠️ 仙家商店暂无此物品。\n'
                    '👉 发送【#商店】查看货架，或发送【#购买 物品名/编号】（例如：#购买 1 或 #购买 替身草人）。')
        cost = item.price
        if self.player['spirit_stones'] < cost:
            return (f'💎 灵石不足，暂时无法购买【{item.name}】。\n\n'
                    f'售价：{cost} 灵石\n持有：{self.player["spirit_stones"]} 灵石\n\n'
                    '本次未扣除灵石。')
        if item.daily_stock is not None:
            sold = self.repo.shop_group_purchase_count_today(self.today, item.id)
            if sold >= item.daily_stock:
                return (f'⏳ 今日【{item.name}】库存已售罄（每日限量 {item.daily_stock} 件）。\n'
                        '明日零点重新补货，请明日再来。')

        stones = self.player['spirit_stones'] - cost
        if stones < 0:
            return '💎 灵石不足，暂时无法购买。'

        if item.id == 'tishen_caoren':
            inv = self.repo.inventory()
            limit = self._inventory_limit(inv)
            if len(inv) >= limit:
                return ('🎒 储物袋已满，无法放入新的法宝。\n'
                        '👉 请先使用 #献宝 编号 整理法宝后再来购买。\n'
                        '本次未扣除灵石。')
            template = CATALOG['tishen_caoren']
            item_id = self.repo.grant_item(template, self.now)
            self.repo.update_player(spirit_stones=stones)
            self.repo.record_shop_purchase(self.today, item.id, count=1, now_iso=self.now)
            self.change = {'spirit_stones': -cost, 'item_id': item_id, 'bought': item.id}
            return (f'🛒【商店 · 购买成功】\n'
                    f'购得护身法器【{item.name}】{item_id}，已收入储物袋！\n\n'
                    f'💎 灵石 -{cost}\n'
                    f'当前灵石：{stones}\n'
                    f'🎒 物品已放入储物袋。斗法落败时将优先替主遭劫碎裂消散，保全其它法宝。')

        if item.id == 'ningqi_dan':
            cultivation = self.player['cultivation'] + 20
            if cultivation > _SQLITE_MAX:
                return '⚠️ 修为已达存储上限，本次未购买。'
            self.repo.update_player(cultivation=cultivation, spirit_stones=stones)
            self.repo.record_shop_purchase(self.today, item.id, count=1, now_iso=self.now)
            self.change = {'spirit_stones': -cost, 'cultivation': 20, 'bought': item.id}
            return (f'🛒【商店 · 购买成功】\n'
                    f'服用【{item.name}】，灵气入体，经络舒畅！\n\n'
                    f'💎 灵石 -{cost}\n'
                    '✨ 修为 +20\n'
                    f'当前修为：{cultivation}\n'
                    f'当前灵石：{stones}')

        if item.id == 'xisui_dan':
            if self._self_cultivation_remaining() == 0:
                return ('💡 当前自主修炼并未处于冷却中，无需使用洗髓丹。\n'
                        # '本次未扣除灵石与购买配额。'
                        )
            self.repo.clear_self_cultivate_cooldown()
            self.repo.update_player(spirit_stones=stones)
            self.repo.record_shop_purchase(self.today, item.id, count=1, now_iso=self.now)
            self.change = {'spirit_stones': -cost, 'reset_self_cultivate': True, 'bought': item.id}
            return (f'🛒【商店 · 购买成功】\n'
                    f'服用【{item.name}】，洗筋伐髓，气息平复！\n\n'
                    f'💎 灵石 -{cost}\n'
                    '⏳ 自主修炼冷却已重置，可立即发送【#自主修炼】。\n'
                    f'当前灵石：{stones}')

        if item.id == 'xunbao_ling':
            if self.player['last_explored_on'] != self.today:
                return ('💡 今日尚未探索过秘境，可直接发送【#秘境】前往探索，无需使用寻宝令。\n'
                        # '本次未扣除灵石与购买配额。'
                        )
            self.repo.update_player(spirit_stones=stones, last_explored_on=None)
            self.repo.record_shop_purchase(self.today, item.id, count=1, now_iso=self.now)
            self.change = {'spirit_stones': -cost, 'reset_explore': True, 'bought': item.id}
            return (f'🛒【商店 · 购买成功】\n'
                    f'祭出【{item.name}】，秘境云雾再次散开！\n\n'
                    f'💎 灵石 -{cost}\n'
                    '🌀 今日秘境探索资格已刷新，可再次发送【#秘境】探宝。\n'
                    f'当前灵石：{stones}')

        if item.kind == 'prop':
            if self.repo.prop_count() >= 3:
                return ('🎒 百宝囊已满（道具格上限 3 格）。\n'
                        '👉 请先发送【#道具】查看并使用后再来选购。\n'
                        '本次未扣除灵石。')

            if item.id == 'qingxin_fu':
                roll = self.rng.randrange(100)
                if roll >= 50:
                    self.repo.update_player(spirit_stones=stones)
                    self.repo.record_shop_purchase(self.today, item.id, count=1, now_iso=self.now)
                    self.change = {'spirit_stones': -cost, 'bought': item.id, 'success': False}
                    return (f'🛒【商店 · 求购结果】\n'
                            f'尝试求购护体灵符【{item.name}】……\n\n'
                            f'💎 灵石 -{cost}\n'
                            f'当前灵石：{stones}\n\n'
                            f'💨 符纸灵光微散，化为飞灰！本次未能成功。\n'
                            f'💡 今日该物品剩余配额减少 1 次。')

            prop_id = self.repo.grant_prop(item.id, self.now)
            self.repo.update_player(spirit_stones=stones)
            self.repo.record_shop_purchase(self.today, item.id, count=1, now_iso=self.now)
            self.change = {'spirit_stones': -cost, 'prop_id': prop_id, 'bought': item.id}
            target_tip = "发送【#使用 " + item.name + " @群友】施展暗算" if item.id != 'qingxin_fu' else "发送【#使用 " + item.name + "】主动驱散负面诅咒；或随身携带自动抵消一次他人暗算"
            success_title = "🛒【商店 · 购买成功】" if item.id != 'qingxin_fu' else "🛒【商店 · 求符大吉】"
            prefix = "" if item.id != 'qingxin_fu' else "灵光大炽，吉星高照！"
            return (f'{success_title}\n'
                    f'{prefix}购得秘传符箓【{item.name}】（编号 {prop_id}），已放入百宝囊！\n\n'
                    f'💎 灵石 -{cost}\n'
                    f'当前灵石：{stones}\n'
                    f'🎒 占用 1 格道具栏（当前 {self.repo.prop_count()}/3 格）。\n'
                    f'👉 {target_tip}。')

        return '⚠️ 暂不支持购买此物品。'

    def devil_status(self):
        tier = (_val(self.player, 'devil_contract_tier', 0) or 0)
        highest = _devil_highest_tier(self.player)
        max_tier = self.rules.devil_contract_max_tier
        next_tier = highest + 1
        lines = [
            "📜【魔鬼的交易】",
            "每次立约只能进入尚未签订的更高层。",
            "────────────────",
            (f"👹 当前状态：魔契第 {tier} 层 / 共 {max_tier} 层" if tier else
             "🧘 当前状态：道心清明（未签魔契）"),
            (f"📌 历史最高签订：第 {highest} 层" if highest else
             "📌 历史最高签订：尚未签订"),
            f"✨ 当前剩余修为：{self.player['cultivation']} 点",
        ]
        if highest >= max_tier:
            lines.extend([
                "",
                f"⚠️ 历史已达极渊第 {highest} 层，不可再次签订或深化。",
            ])
        elif self.player['realm'] == 'nascent':
            lines.extend(["", "⚠️ 已达最高境界，不可签订魔契。"])
        else:
            reward = self.rules.devil_rewards[next_tier - 1]
            action = "深化契约" if tier else "再次签约" if highest else "首次签约"
            lines.extend([
                "",
                f"👉 {action}（第 {next_tier} 层）：花费自身修为，立即获得 💎 +{reward} 灵石",
                "💡 破除契约不会清除历史，重签按新层数扣除修为并发放奖励。",
                "────────────────",
                ("👉 发送【#深化魔契】继续向魔鬼交易" if tier else
                 "👉 发送【#签订魔契】与魔鬼立契"),
            ])
        lines.append("👉 发送【#魔契帮助】查看契约全览")
        return "\n".join(lines)

    def devil_sign(self):
        if self.player['realm'] == 'nascent':
            return ('⚠️ 元婴大能道心圆满，心魔不侵，不可与魔鬼签订契约。\n'
                    '👉 已达最高境界，唯有潜心问道。')
        tier = (_val(self.player, 'devil_contract_tier', 0) or 0)
        highest = _devil_highest_tier(self.player)
        max_tier = self.rules.devil_contract_max_tier
        if highest >= max_tier:
            return (f'⚠️ 魔契已深达极渊第 {highest} 层，历史签订层数已达上限！\n'
                    '即使契约已破除，也无法再次签订或深化契约。')

        # 方案二：临近突破拒贷拦截（当前修为 >= 突破门槛的 80%）
        realm_idx = REALMS.index(self.player['realm'])
        target_cult = self.rules.breakthrough_cultivation[realm_idx]
        threshold = math.ceil(target_cult * 0.8)
        if self.player['cultivation'] >= threshold:
            action_desc = "深化" if tier > 0 else "签订"
            return (
                f"魔鬼冷笑一声隐入虚空，本次拒绝{action_desc}魔契。\n"
                f"⚠️ 已达突破门槛的 80%（≥{threshold}），魔鬼察觉你意图后，拒绝交易！\n"
                f"👉 建议潜心修炼，准备【#突破】境界！"
            )

        new_tier = highest + 1

        # 方案一：瞬间扣除当前境界突破所需修为的 10% * 契约层数
        instant_loss = int(round(target_cult * 0.10 * new_tier))
        if self.player['cultivation'] < instant_loss:
            return "修为过于孱弱，魔鬼不屑地离开了，本次未能立下契约"
        new_cult = self.player['cultivation'] - instant_loss

        reward = self.rules.devil_rewards[new_tier - 1]
        daily_loss = self.rules.devil_daily_losses[new_tier - 1]
        stones = self.player['spirit_stones'] + reward
        if stones > _SQLITE_MAX:
            return '⚠️ 灵石已达存储上限，本次无法签订魔契。'
        borrowed = (_val(self.player, 'devil_total_borrowed', 0) or 0) + reward

        self.repo.update_player(
            spirit_stones=stones,
            cultivation=new_cult,
            devil_contract_tier=new_tier,
            devil_max_contract_tier=new_tier,
            devil_total_borrowed=borrowed,
            devil_last_settled_on=self.today,
            devil_signed_on=self.today,
        )
        self.change = {
            'spirit_stones': reward,
            'cultivation': -instant_loss,
            'devil_contract_tier': new_tier,
            'devil_max_contract_tier': new_tier,
            'borrowed': reward,
        }

        if tier == 0:
            return (f"🩸【契约签订】\n"
                    f"{self.player['dao_name']}划破指尖，与魔灵立下契约！\n\n"
                    f"💎 灵石 +{reward}\n"
                    f"👹 魔契等级：第 {new_tier} 层\n"
                    f"签订契约需要扣除修为，修为 -{instant_loss}（剩余：{new_cult}）\n"
                    f"魔鬼不求回报，心满意足的离开了\n\n"
                    f"吗？\n"
                    f"「拿起刀的，终死于刀下。」\n\n"
                    f"当前灵石：{stones}")
        else:
            return (f"🩸【契约深化】\n"
                    f"越是跌紧越是吃进，{self.player['dao_name']}再次与魔灵交易！\n\n"
                    f"💎 灵石 +{reward}\n"
                    f"👹 魔契等级：第 {new_tier} 层 / 共 {max_tier} 层\n"
                    f"签订契约需要扣除修为，修为 -{instant_loss}（剩余：{new_cult}）\n"
                    f"魔鬼不求回报，心满意足的离开了\n\n"
                    f"吗？\n"
                    f"「拿起刀的，终死于刀下。」\n\n"
                    f"当前灵石：{stones}")
