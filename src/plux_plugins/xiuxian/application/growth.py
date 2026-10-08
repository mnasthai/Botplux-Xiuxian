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

class GrowthUseCases:
    def _self_cultivation_remaining(self, inventory=None, debuffs=None):
        return self_cultivation_remaining(
            self.rules.self_cultivation_interval_seconds,
            self.repo.inventory() if inventory is None else inventory,
            self.repo.debuffs(today=self.today) if debuffs is None else debuffs,
            self.repo.latest_action('self_cultivate'), self.context.now,
        )

    def _apply_devil_on_cultivate(self, gain_cult, gain_stones):
        """Apply devil contract loss on daily cultivation, return (is_collapsed, collapse_reply, devil_note, final_cult, devil_loss)."""
        tier = (_val(self.player, 'devil_contract_tier', 0) or 0)
        if tier <= 0 or not self.rules.devil_contract_enabled:
            return False, None, "", self.player['cultivation'] + gain_cult, 0

        devil_loss = self.rules.devil_daily_losses[tier - 1]
        projected = self.player['cultivation'] + gain_cult - devil_loss
        current_realm = self.player['realm']

        if projected <= 0:
            realm_idx = REALMS.index(current_realm)
            if realm_idx > 0:
                new_realm_idx = realm_idx - 1
                new_realm = REALMS[new_realm_idx]
                target_cult = self.rules.breakthrough_cultivation[new_realm_idx]
                half_cult = target_cult // 2
                stones = self.player['spirit_stones'] + gain_stones
                self.repo.update_player(
                    realm=new_realm,
                    cultivation=half_cult,
                    spirit_stones=stones,
                    last_cultivated_on=self.today,
                    devil_last_settled_on=self.today
                )
                self.change = {'cultivation': half_cult - self.player['cultivation'], 'spirit_stones': gain_stones, 'day': self.today}
                self.player = self.repo.player()
                reply = (
                    f"💥【道基崩塌 · 心魔反噬】\n"
                    f"闭关修炼之际，体内魔契第 {tier} 层心魔骤然反噬（抽吸 {devil_loss} 点修为），道基瓦解破碎！\n"
                    f"⚠️ 境界跌落至【{REALM_NAMES[new_realm]}】，修为保留为跌落境界的一半（{half_cult}/{target_cult}）！\n"
                    f"💎 勉力运功，获得灵石 +{gain_stones}。\n"
                    f"👹 心魔未除！每次【#修炼】仍将抽吸 {devil_loss} 修为，速速修炼重新突破至【{REALM_NAMES[current_realm]}】解除魔契！"
                )
                return True, reply, "", half_cult, devil_loss
            else:
                self.repo.strip_all_items()
                self.repo.update_player(
                    cultivation=0,
                    spirit_stones=0,
                    devil_contract_tier=0,
                    devil_max_contract_tier=_devil_highest_tier(self.player),
                    devil_last_settled_on=None,
                    devil_signed_on=None,
                    last_cultivated_on=self.today
                )
                self.change = {'cultivation': -self.player['cultivation'], 'spirit_stones': -self.player['spirit_stones'], 'day': self.today}
                self.player = self.repo.player()
                reply = (
                    f"💥💥💥【道心彻底崩溃】\n"
                    f"心魔反噬强行抽吸 {devil_loss} 修为！你在【炼气】境界中道基彻底瓦解崩溃！\n"
                    f"💸 修为散尽归零，灵石全失归零，所有法宝尽数离体破散！\n"
                    f"魔鬼饱餐神魂后狂笑着遁入虚空，魔契已彻底解除。"
                )
                return True, reply, "", 0, devil_loss
        else:
            devil_note = f"\n👹【心魔反噬】魔契第 {tier} 层发难，抽吸 -{devil_loss} 修为！"
            return False, None, devil_note, projected, devil_loss

    def cultivate(self):
        if self.player['last_cultivated_on'] == self.today:
            return '⏳ 今日已经修炼过了，明日零点后再来。'
        inv = self.repo.inventory()
        base_cult = self.rules.cultivation_reward
        base_stones = self.rules.cultivation_stones_reward
        gain_cult, gain_stones, bonuses = daily_cultivation_rewards(base_cult, base_stones, inv)

        debuffs = self.repo.debuffs(today=self.today)
        qieling = next((d for d in debuffs if d['debuff_kind'] == 'qieling_gu'), None)
        curse_note = ""
        if qieling:
            stolen_cult = round(gain_cult * 0.30)
            stolen_stones = round(gain_stones * 0.30)
            gain_cult -= stolen_cult
            gain_stones -= stolen_stones
            caster = self.repo.store.execute(
                "SELECT dao_name FROM game_players WHERE account_id=? AND group_id=? AND player_id=?",
                (*self.repo.scope, qieling['caster_player_id'])
            ).fetchone()
            caster_name = caster['dao_name'] if caster else "施蛊修士"
            self.repo.store.execute(
                "UPDATE game_players SET cultivation=cultivation+?, spirit_stones=spirit_stones+? WHERE account_id=? AND group_id=? AND player_id=?",
                (stolen_cult, stolen_stones, *self.repo.scope, qieling['caster_player_id'])
            )
            self.repo.consume_debuff(qieling['debuff_id'])
            curse_note = f"\n🩸【窃灵蛊】破茧吸食！损失 30% 收益（-{stolen_cult}修为, -{stolen_stones}灵石），被盗运反哺给【{caster_name}】！"

        is_collapsed, collapse_reply, devil_note, final_cult, devil_loss = self._apply_devil_on_cultivate(gain_cult, gain_stones)
        if is_collapsed:
            return collapse_reply

        stones = self.player['spirit_stones'] + gain_stones
        if max(final_cult, stones) > _SQLITE_MAX:
            return '⚠️ 修为或灵石已达存储上限，本次未领取。'
        self.repo.update_player(cultivation=final_cult, spirit_stones=stones, last_cultivated_on=self.today, devil_last_settled_on=self.today)
        self.change = {'cultivation': final_cult - self.player['cultivation'],
                       'spirit_stones': gain_stones, 'day': self.today}
        extra_note = f"\n💡 神通加成：{'、'.join(bonuses)}" if bonuses else ""

        tier = (_val(self.player, 'devil_contract_tier', 0) or 0)
        if tier > 0 and self.rules.devil_contract_enabled:
            net_cult = gain_cult - devil_loss
            net_str = f"+{net_cult}" if net_cult > 0 else f"{net_cult}"
            return (f"🧘【静坐修炼】\n{self.player['dao_name']}闭目吐纳，丹田灵气渐丰。\n\n"
                    f'✨ 基础修为 +{gain_cult}\n💎 灵石 +{gain_stones}{extra_note}{curse_note}{devil_note}\n'
                    f'────────────────\n'
                    f'📊 净增修为：{net_str}\n'
                    f'当前修为：{final_cult}\n当前灵石：{stones}')
        else:
            return (f"🧘【静坐修炼】\n{self.player['dao_name']}闭目吐纳，丹田灵气渐丰。\n\n"
                    f'✨ 修为 +{gain_cult}\n💎 灵石 +{gain_stones}{extra_note}{curse_note}\n'
                    f'当前修为：{final_cult}\n当前灵石：{stones}')

    def _handle_self_cultivate_failure(self, inv, header):
        loss = self.rules.self_cultivation_loss
        has_bichen = any(item['template_id'] == 'bichen_zhu' for item in inv)
        if has_bichen:
            loss = 1
        tier = (_val(self.player, 'devil_contract_tier', 0) or 0)
        devil_note = ""
        if tier > 0 and self.rules.devil_contract_enabled and self.rng.randrange(100) < 50:
            mult = 1 + tier
            loss = loss * mult
            devil_note = f"👹【心魔引动】魔契第 {tier} 层（{tier}成）借机发难，反噬加剧为 {mult} 倍！\n"

        projected = self.player['cultivation'] - loss
        current_realm = self.player['realm']

        if projected <= 0 and tier > 0 and self.rules.devil_contract_enabled:
            realm_idx = REALMS.index(current_realm)
            if realm_idx > 0:
                new_realm_idx = realm_idx - 1
                new_realm = REALMS[new_realm_idx]
                target_cult = self.rules.breakthrough_cultivation[new_realm_idx]
                half_cult = target_cult // 2
                self.repo.update_player(
                    realm=new_realm,
                    cultivation=half_cult,
                )
                self.change = {'cultivation': half_cult - self.player['cultivation'], 'self_cultivate': 'failed'}
                self.player = self.repo.player()
                bichen_note = '（避尘珠【辟邪】庇护心神，损失减半）\n' if has_bichen else ''
                reply = (
                    f"{header}"
                    f"{bichen_note}{devil_note}"
                    f"💥【道基崩塌 · 心魔反噬】\n"
                    f"走火入魔之际，体内魔契第 {tier} 层心魔乘隙反噬，修为散尽，道基瓦解破碎！\n"
                    f"⚠️ 境界跌落至【{REALM_NAMES[new_realm]}】，修为保留为跌落境界的一半（{half_cult}/{target_cult}）！\n"
                    f"👹 心魔未除！速速修炼重新突破至【{REALM_NAMES[current_realm]}】解除魔契！"
                )
                return reply
            else:
                self.repo.strip_all_items()
                self.repo.update_player(
                    cultivation=0,
                    spirit_stones=0,
                    devil_contract_tier=0,
                    devil_max_contract_tier=_devil_highest_tier(self.player),
                    devil_last_settled_on=None,
                    devil_signed_on=None,
                )
                self.change = {'cultivation': -self.player['cultivation'], 'spirit_stones': -self.player['spirit_stones'], 'self_cultivate': 'failed'}
                self.player = self.repo.player()
                bichen_note = '（避尘珠【辟邪】庇护心神，损失减半）\n' if has_bichen else ''
                reply = (
                    f"{header}"
                    f"{bichen_note}{devil_note}"
                    f"💥💥💥【道心彻底崩溃】\n"
                    f"走火入魔导致修为散尽，体内魔契第 {tier} 层心魔彻底吞噬道心！你在【炼气】境界中道基彻底瓦解崩溃！\n"
                    f"💸 修为散尽归零，灵石全失归零，所有法宝尽数离体破散！\n"
                    f"魔鬼饱餐神魂后狂笑着遁入虚空，魔契已彻底解除。"
                )
                return reply
        else:
            cultivation = max(0, projected)
            self.repo.update_player(cultivation=cultivation)
            self.change = {'cultivation': cultivation - self.player['cultivation'], 'self_cultivate': 'failed'}
            actual_loss = self.player['cultivation'] - cultivation
            bichen_note = '（避尘珠【辟邪】庇护心神，损失减半）\n' if has_bichen and actual_loss > 0 else ''
            return (f"{header}"
                    f"{bichen_note}{devil_note}"
                    f"📉 修为 -{actual_loss}\n当前修为：{cultivation}")

    def self_cultivate(self):
        inv = self.repo.inventory()
        debuffs = self.repo.debuffs(today=self.today)
        duanmai = next((d for d in debuffs if d['debuff_kind'] == 'duanmai_san'), None)
        remaining = self._self_cultivation_remaining(inv, debuffs)
        if remaining > 0:
            duanmai_tip = "（受【断脉散】阻滞，冷却大幅增加）" if duanmai else ""
            return f'⏳ 自主修炼尚需 {remaining} 秒后才能再次尝试。{duanmai_tip}'

        if duanmai:
            self.repo.consume_debuff(duanmai['debuff_id'])

        raoxin = next((d for d in debuffs if d['debuff_kind'] == 'raoxin_fu'), None)
        if raoxin:
            self.repo.consume_debuff(raoxin['debuff_id'])
            header = ('🌀【自主修炼 · 走火入魔】\n'
                      '🩸 附着的【扰心符】骤然爆发！煞气乱窜，本次必定走火入魔！\n\n')
            return self._handle_self_cultivate_failure(inv, header)

        rate = self_cultivation_success_percent(self.rules.self_cultivation_success_percent, inv)
        ids = template_ids(inv)
        has_xuantie = 'xuantie_yin' in ids
        has_huixin = 'huixin_yu' in ids

        if self.rng.randrange(100) < rate:
            reward = self_cultivation_reward(self.rules.self_cultivation_reward, inv)
            cultivation = self.player['cultivation'] + reward
            if cultivation > _SQLITE_MAX:
                return '⚠️ 修为已达存储上限，本次未进行自主修炼。'
            self.repo.update_player(cultivation=cultivation)
            self.change = {'cultivation': reward, 'self_cultivate': 'success'}
            xuantie_note = '（玄铁印【沉心】稳固道基）\n' if has_xuantie else ''
            huixin_note = '（慧心玉【明心】提高成功率）\n' if has_huixin else ''
            hongmeng_note = '（鸿蒙珠【悟真】额外 +4 修为）\n' if 'hongmeng_zhu' in ids else ''
            return (f'🌀【自主修炼】\n{self.player["dao_name"]}有所领悟，灵机乍现。\n\n'
                    f'{xuantie_note}{huixin_note}{hongmeng_note}'
                    f'✨ 修为 +{reward}\n当前修为：{cultivation}')

        header = f'🌀【自主修炼】\n{self.player["dao_name"]}走火入魔，只得收束心神。\n\n'
        return self._handle_self_cultivate_failure(inv, header)

    def mine(self):
        mine_cooldown = 3600
        recent = self.repo.latest_action('mine')
        if recent:
            try:
                previous = datetime.fromisoformat(recent['created_at'].replace('Z', '+00:00')).timestamp()
            except (TypeError, ValueError, OverflowError, OSError):
                previous = self.context.now
            remaining = mine_cooldown - (self.context.now - previous)
            if remaining > 0:
                return f'⏳ 灵矿灵气枯竭，尚需 {math.ceil(remaining)} 秒后灵气方能再次凝聚，请稍候再来采矿。'

        realm = self.player['realm']
        ranges = {
            'qi': (10, 15),
            'foundation': (15, 20),
            'core': (20, 25),
            'nascent': (25, 30),
        }
        low, high = ranges.get(realm, (10, 15))
        base_stones = low + self.rng.randrange(high - low + 1)

        is_crit = self.rng.randrange(100) < 10
        crit_bonus = 15 if is_crit else 0
        lingquan_bonus = 5 if 'lingquan_ping' in template_ids(self.repo.inventory()) else 0
        total_stones = base_stones + crit_bonus + lingquan_bonus
        lingquan_note = f'（灵泉瓶【采露】+{lingquan_bonus}）' if lingquan_bonus else ''

        new_stones = self.player['spirit_stones'] + total_stones
        if new_stones > _SQLITE_MAX:
            return '⚠️ 灵石已达存储上限，本次未进行开采。'

        self.repo.update_player(spirit_stones=new_stones)
        self.change = {'spirit_stones': total_stones, 'mine': 'crit' if is_crit else 'normal'}

        if is_crit:
            return (f"⛏️【灵矿采矿 · 灵髓乍现】\n"
                    f"{self.player['dao_name']}一镐挥下，矿壁金光流溢，竟挖出一块【极品灵髓】！\n\n"
                    f"💎 获得灵石：+{total_stones}（含灵髓奖励 +{crit_bonus}）{lingquan_note}\n"
                    f"当前灵石：{new_stones}\n"
                    f"⏳ 灵矿进入休眠，1 小时后可再次采矿。")
        else:
            return (f"⛏️【灵矿采矿】\n"
                    f"{self.player['dao_name']}深入后山灵矿，运转灵力凿采灵石！\n\n"
                    f"💎 获得灵石：+{total_stones}{lingquan_note}\n"
                    f"当前灵石：{new_stones}\n"
                    f"⏳ 灵矿进入休眠，1 小时后可再次采矿。")

    def breakthrough(self):
        index = REALMS.index(self.player['realm'])
        if index == len(REALMS) - 1:
            return '🎆 你已达到当前最高境界：元婴。\n👉 仍可修炼和探索秘境。'
        inv = self.repo.inventory()
        cultivation, stones = breakthrough_costs(
            self.rules.breakthrough_cultivation[index], self.rules.breakthrough_stones[index], inv)
        ids = template_ids(inv)
        has_fuhai = 'fuhai_yin' in ids
        has_xuanyuan = 'xuanyuan_jing' in ids

        devil_tier = (_val(self.player, 'devil_contract_tier', 0) or 0)
        signed_on = _val(self.player, 'devil_signed_on')
        if devil_tier > 0 and signed_on == self.today:
            return "👹 心魔正在狂暴肆虐，周天阻塞，道心未稳，今日无法突破境界"

        debuffs = self.repo.debuffs(today=self.today)
        sanling = next((d for d in debuffs if d['debuff_kind'] == 'sanling_chen'), None)
        if sanling:
            return "⚠️ 周身被【散灵尘】死死封锁，神识混乱周天逆行，今日无法突破境界！"

        if self.player['cultivation'] < cultivation or self.player['spirit_stones'] < stones:
            discount_text = ('（' + '、'.join(x for x in (
                '玄元镜修为减免 10%' if has_xuanyuan else '',
                '覆海印灵石减免 10%' if has_fuhai else '') if x) + '）') if has_fuhai or has_xuanyuan else ''
            return (f'⚠️【突破受阻】\n需要 ✨ 修为 {cultivation}、💎 灵石 {stones}{discount_text}。\n\n'
                    f"当前修为：{self.player['cultivation']}\n当前灵石：{self.player['spirit_stones']}\n本次未扣除。")
        realm = REALMS[index + 1]
        new_cultivation = self.player['cultivation'] - cultivation
        new_stones = self.player['spirit_stones'] - stones
        devil_cleared = devil_tier > 0
        update_kwargs = {
            'realm': realm,
            'cultivation': new_cultivation,
            'spirit_stones': new_stones,
        }
        if devil_cleared:
            update_kwargs['devil_contract_tier'] = 0
            update_kwargs['devil_max_contract_tier'] = _devil_highest_tier(self.player)
            update_kwargs['devil_last_settled_on'] = None
            update_kwargs['devil_signed_on'] = None
        self.repo.update_player(**update_kwargs)
        self.change = {'realm': realm, 'cultivation': -cultivation, 'spirit_stones': -stones}
        if devil_cleared:
            self.change['devil_contract_cleared'] = True
        fuhai_note = '（覆海印【涌现】减免 10% 灵石）' if has_fuhai else ''
        xuanyuan_note = '（玄元镜【省元】减免 10% 修为）' if has_xuanyuan else ''
        devil_note = '\n\n⚡【天雷涤魂】破境雷劫九天降临，涤尽心魔！魔契已彻底破除！' if devil_cleared else ''
        return (f"🎆【境界突破】\n{self.player['dao_name']}破境成功，踏入【{REALM_NAMES[realm]}】！\n\n"
                f'📉 修为 -{cultivation}{xuanyuan_note}\n💎 灵石 -{stones}{fuhai_note}\n'
                f'🌀 高品阶法宝的寻获机会提升。{devil_note}')

    def explore(self):
        if self.player['last_explored_on'] == self.today:
            return '⏳ 今日已经探索过秘境，明日零点后再来。'
        inventory = self.repo.inventory()
        limit = self._inventory_limit(inventory)
        if len(inventory) >= limit:
            return '🎒 储物袋已满，请先用 #献宝 编号 整理法宝。\n本次未扣灵石和探索次数。'
        cost = exploration_cost(self.rules.exploration_cost, inventory)
        if self.player['spirit_stones'] < cost:
            return (f'💎 灵石不足，暂时无法进入秘境。\n\n需要：{cost}\n'
                    f"持有：{self.player['spirit_stones']}\n\n本次未扣灵石，未消耗探索次数。")
        has_zhaoyao = any(item['template_id'] == 'zhaoyao_jing' for item in inventory)
        weights = self.rules.drop_weights[REALMS.index(self.player['realm'])]
        if has_zhaoyao:
            delta = min(15, weights.artifact)
            art_w = weights.artifact - delta
            spi_w = weights.spirit + int(delta * 8 / 15)
            anc_w = weights.ancient + int(delta * 5 / 15)
            tre_w = 100 - art_w - spi_w - anc_w
            adjusted_weights = {'artifact': art_w, 'spirit': spi_w, 'ancient': anc_w, 'treasure': tre_w}
        else:
            adjusted_weights = {'artifact': weights.artifact, 'spirit': weights.spirit, 'ancient': weights.ancient, 'treasure': weights.treasure}

        roll = self.rng.randrange(100)
        rarity = RARITIES[-1]
        for candidate in RARITIES:
            roll -= adjusted_weights[candidate]
            if roll < 0:
                rarity = candidate
                break
        pool = [template for template in CATALOG.values() if template.rarity == rarity and template.id != 'tishen_caoren']
        if rarity in ('ancient', 'treasure'):
            existing = self.repo.rare_items()
            pool = [template for template in pool if template.id not in existing
                    or existing[template.id]['state'] == 'pool']
        refund = 0
        if not pool:
            if not inventory:
                pool = [template for template in CATALOG.values() if template.rarity == 'artifact' and template.id != 'tishen_caoren']
            else:
                refund = self.rules.rare_pool_returns[('ancient', 'treasure').index(rarity)]
        extra_refund = 0
        item_id = None
        if pool:
            template = self.rng.choice(pool)
            item_id = self.repo.grant_item(template, self.now)
            result = f'⚔️ 发现【{template.name}】（{RARITY_NAMES[template.rarity]}）！\n编号：{item_id}'
            biyu_gain = 0
            if template.rarity == 'artifact' and any(item['template_id'] == 'biyu_hulu' for item in inventory):
                biyu_gain = 30
                if has_zhaoyao:
                    biyu_gain = int(biyu_gain * 1.25)
                extra_refund += biyu_gain
                result += f'\n💧【回泉】碧玉葫芦泛起甘霖，返还 {biyu_gain} 灵石！'
            if 'wujie_tu' in template_ids(inventory):
                extra_refund += 20
                result += '\n🗺️【归元】无界图引路，返还 20 灵石！'
            if has_zhaoyao:
                zhaoyao_reward = 25
                extra_refund += zhaoyao_reward
                result += f'\n🪞【破妄】照妖镜辨析宝光，提升稀有法宝概率，额外获得 {zhaoyao_reward} 灵石！'
        else:
            if has_zhaoyao and refund > 0:
                refund = int(refund * 1.25)
            result = f'⚔️ 感应到{RARITY_NAMES[rarity]}气息，但此类法宝均已有主人。\n↩️ 退回 {refund} 灵石'
            if has_zhaoyao:
                result += '\n🪞【破妄】照妖镜破除迷障，退回灵石额外提升 25%！'
        stones = self.player['spirit_stones'] - cost + refund + extra_refund
        if stones > _SQLITE_MAX:
            return '⚠️ 灵石结算超出可存储范围，本次未探索。'
        self.repo.update_player(spirit_stones=stones, last_explored_on=self.today)
        self.change = {'spirit_stones': refund + extra_refund - cost, 'day': self.today, 'item_id': item_id}
        return (f"🌀【探索秘境】\n{self.player['dao_name']}踏入秘境。\n\n"
                f'💎 灵石 -{cost}\n{result}\n当前灵石：{stones}')
