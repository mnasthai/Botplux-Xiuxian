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

class InventoryUseCases:
    def inventory(self, item_id):
        if item_id:
            item = self.repo.item(item_id)
            if item is None or item['state'] == 'retired':
                return '⚠️ 本群没有这件可查询的法宝。\n👉 请发送 #法宝 或 #宝录 查看编号。'
            template = CATALOG[item['template_id']]
            owner = item['owner_name'] if item['state'] == 'held' else '尚在秘境'
            ability_text = f'\n✨ 神通【{template.ability_name}】：{template.ability_desc}' if template.ability_name else ''
            return (f'⚔️【{template.name}】{item_id} · {RARITY_NAMES[template.rarity]}\n'
                    f'当前归属：{owner}\n📖 {template.description}{ability_text}')
        items = self.repo.inventory()
        limit = self._inventory_limit(items)
        if not items:
            return '🎒 储物袋空空如也。\n👉 修炼积攒灵石，再用 #秘境 寻找法宝。'
        lines = [f"🎒【{self.player['dao_name']}的法宝】{len(items)}/{limit}"]
        for item in items[:30]:
            template = CATALOG[item['template_id']]
            lines.append(f"⚔️ {item['item_id']} · {template.name}（{RARITY_NAMES[template.rarity]}）")
        if len(items) > limit:
            lines.append('⚠️ 已超过储物袋上限，请先献宝整理。')
        if len(items) > 30:
            lines.append('⚠️ 仅展示前 30 件，整理后可查看其余法宝。')
        lines.append('👉 详情：#法宝 F编号\n👉 神通：#法宝帮助\n👉 交换灵石：#献宝 F编号')
        return '\n'.join(lines)

    def offer(self, item_id):
        item = self.repo.item(item_id)
        if item is None or item['state'] != 'held' or item['owner_player_id'] != self.context.user_id:
            return '⚠️ 未找到你持有的这件法宝。\n👉 请发送 #法宝 查看编号。'
        reward = self.rules.return_values[RARITIES.index(item['rarity'])]
        stones = self.player['spirit_stones'] + reward
        if stones > _SQLITE_MAX:
            return '⚠️ 灵石已达存储上限，本次未献出法宝。'
        self.repo.return_item(item)
        self.repo.update_player(spirit_stones=stones)
        self.change = {'spirit_stones': reward, 'item_id': item_id, 'offered': True}
        name = CATALOG[item['template_id']].name
        detail = '\n🌀 此宝已回归秘境，等待有缘人再次发现。' if item['rarity'] in ('ancient', 'treasure') else ''
        return f'↩️【献宝】\n献出【{name}】{item_id}。\n\n💎 灵石 +{reward}\n当前灵石：{stones}{detail}'

    def props(self):
        props = self.repo.props()
        count = len(props)
        lines = [
            f"🎒【{self.player['dao_name']} · 百宝囊】（{count}/3 格）\n",
            f"🎒 道具占用独立道具格，发送【#道具】查看，【#使用 道具名 @群友】施展\n",
            "────────────────",
        ]
        if not props:
            lines.append("百宝囊空空如也，暂无符箓道具。")
            lines.append("👉 发送【#商店】选购扰心符、断脉散等秘传道具！")
        else:
            for idx, p in enumerate(props, 1):
                tmpl = PROP_TEMPLATES.get(p['template_id'])
                name = tmpl.name if tmpl else p['template_id']
                desc = tmpl.description if tmpl else ""
                lines.append(f"{idx}. 📜【{name}】（编号 {p['prop_id']}）")
                if desc:
                    lines.append(f"   └ 功效：{desc}")
            lines.extend([
                "────────────────",
                "👉 发送【#使用 道具名 @群友】对目标施展暗算",
                "👉 发送【#使用 清心净衣符】驱散自身异常状态",
            ])
        debuffs = self.repo.debuffs(today=self.today)
        if debuffs:
            lines.append("")
            lines.append("⚠️【当前异常状态】")
            for d in debuffs:
                tmpl = PROP_TEMPLATES.get(d['debuff_kind'])
                name = tmpl.name if tmpl else d['debuff_kind']
                caster = self.repo.store.execute(
                    "SELECT dao_name FROM game_players WHERE account_id=? AND group_id=? AND player_id=?",
                    (*self.repo.scope, d['caster_player_id'])
                ).fetchone()
                caster_name = caster['dao_name'] if caster else "神秘修士"
                lines.append(f"• 受【{name}】缠身（施术者：{caster_name}）")
        return "\n".join(lines)

    def use_prop(self, prop_query, target_id):
        prop = self.repo.find_player_prop(prop_query)
        if not prop:
            for p in self.repo.props():
                tmpl = PROP_TEMPLATES.get(p['template_id'])
                if tmpl and (prop_query == tmpl.name or prop_query in tmpl.aliases):
                    prop = p
                    break
        if not prop:
            return f"⚠️ 你的百宝囊中并未找到【{prop_query}】道具。\n👉 发送【#道具】查看当前所持符箓。"

        template = PROP_TEMPLATES.get(prop['template_id'])
        if not template:
            return "⚠️ 未知道具，无法使用。"

        if not template.target_required:
            self.repo.consume_prop(prop['prop_id'])
            cleared = self.repo.clear_debuffs(self.player['player_id'])
            self.change = {'consumed_prop': prop['prop_id'], 'cleared_debuffs': cleared}
            if cleared > 0:
                return (f"✨【百宝囊 · 使用成功】\n"
                        f"{self.player['dao_name']}祭出【{template.name}】，浩然正气涤荡身心！\n"
                        f"已成功驱除体内附着的 {cleared} 种负面诅咒，周天气机恢复澄澈！")
            else:
                return (f"✨【百宝囊 · 使用成功】\n"
                        f"{self.player['dao_name']}使用了【{template.name}】。\n"
                        f"体内本无杂念诅咒，神清气爽！")

        if not target_id:
            return f"👉 用法：#使用 {template.name} @群友"
        if target_id == self.player['player_id']:
            return "⚠️ 此符箓为暗算诅咒之物，切莫对自己使用！"

        target_row = self.repo.store.execute(
            "SELECT * FROM game_players WHERE account_id=? AND group_id=? AND player_id=?",
            (*self.repo.scope, target_id)
        ).fetchone()
        if not target_row:
            return "⚠️ 目标尚未踏入仙途，无法对其施展道家符箓。"

        target_inv = self.repo.store.execute(
            "SELECT template_id FROM game_items WHERE account_id=? AND group_id=? AND owner_player_id=? AND state='held'",
            (*self.repo.scope, target_id)
        ).fetchall()
        if any(item['template_id'] == 'wuxing_qi' for item in target_inv):
            self.repo.consume_prop(prop['prop_id'])
            self.change = {'consumed_prop': prop['prop_id'], 'target_id': target_id,
                           'blocked_by': 'wuxing_qi'}
            return (f"🛡️【暗算落空 · 五行辟易】\n"
                    f"{self.player['dao_name']}暗中向【{target_row['dao_name']}】祭出【{template.name}】！\n"
                    f"不料对方怀揣至宝【五行旗】，五色霞光冲天而起，直接将符咒焚为飞灰！暗算完全失效！")

        target_qingxin = self.repo.store.execute(
            "SELECT prop_id FROM game_player_props WHERE account_id=? AND group_id=? AND player_id=? AND template_id='qingxin_fu' ORDER BY created_at LIMIT 1",
            (*self.repo.scope, target_id)
        ).fetchone()
        if target_qingxin:
            self.repo.consume_prop(prop['prop_id'])
            self.repo.consume_prop(target_qingxin['prop_id'], player_id=target_id)
            self.change = {'consumed_prop': prop['prop_id'], 'target_id': target_id,
                           'blocked_by': 'qingxin_fu', 'consumed_target_prop': target_qingxin['prop_id']}
            return (f"🛡️【浩然正气 · 替身化劫】\n"
                    f"{self.player['dao_name']}暗中对【{target_row['dao_name']}】祭出【{template.name}】！\n\n"
                    f"⚡ 刹那间，【{target_row['dao_name']}】百宝囊中的【清心净衣符】无风自燃，化作浩然正气金光护体，瞬间抵消了本次暗算！\n"
                    f"👉 目标的【清心净衣符】已替主化劫碎裂消散。")

        existing_debuffs = self.repo.debuffs(target_id, today=self.today)
        if any(d['debuff_kind'] == template.id for d in existing_debuffs):
            return f"⚠️ 【{target_row['dao_name']}】体内已有【{template.name}】附着生效，无法重复施加同种负面效果！"

        self.repo.consume_prop(prop['prop_id'])
        debuff_id = self.repo.add_debuff(target_id, self.player['player_id'], template.id, self.now)
        self.change = {'consumed_prop': prop['prop_id'], 'target_id': target_id,
                       'debuff_id': debuff_id, 'debuff_kind': template.id}
        if template.id == 'duanmai_san':
            return (f"🩸【暗算得手 · 气脉断绝】\n"
                    f"{self.player['dao_name']}捏碎【{template.name}】，无色无味的断脉阴毒悄然渗入【{target_row['dao_name']}】的气海！\n"
                    f"⚠️ 对方气机逆乱，当前【#自主修炼】冷却被迫大幅延长 60 分钟！\n"
                    f"👉 发动暗算者：{self.player['dao_name']}")

        effect_tips = {
            'raoxin_fu': '对方下一次【#自主修炼】必将走火入魔，道行受损！',
            'qieling_gu': '对方下次【#修炼】时，其 30% 收益将被窃灵蛊偷取并反哺给你！',
            'sanling_chen': '对方今日周天阻塞，无法突破境界！',
        }
        tip = effect_tips.get(template.id, '诅咒已潜伏于其道躯。')
        return (f"🩸【暗算得手 · 阴阳咒缚】\n"
                f"{self.player['dao_name']}暗中催动【{template.name}】，一道血煞黑气悄无声息没入【{target_row['dao_name']}】眉心！\n"
                f"⚠️ {tip}\n"
                f"👉 发动暗算者：{self.player['dao_name']}")
