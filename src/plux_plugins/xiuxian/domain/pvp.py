"""PVP Duel combat system with 3D stats (HP=Cultivation, DEF=Realm, ATK=Artifacts, heavenly dice roll)."""
from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Mapping, Sequence

from .catalog import REALM_NAMES
from .artifact_effects import template_ids

REALM_ORDER: Mapping[str, int] = {
    'qi': 1,
    'foundation': 2,
    'core': 3,
    'nascent': 4,
}

REALM_DEFENSE: Mapping[str, int] = {
    'qi': 20,
    'foundation': 45,
    'core': 60,
    'nascent': 80,
}

# Breakthrough consumes cultivation. Each new realm therefore starts above the
# previous realm's HP at its default breakthrough threshold (100/250/500).
REALM_BASE_HP: Mapping[str, int] = {
    'qi': 100,
    'foundation': 200,
    'core': 400,
    'nascent': 730,
}

RARITY_ATTACK: Mapping[str, int] = {
    'artifact': 15,
    'spirit': 25,
    'ancient': 40,
    'treasure': 80,
}

REALM_POWER_RATIO: Mapping[str, Mapping[str, float]] = {
    'treasure': {
        'qi': 0.50,
        'foundation': 0.70,
        'core': 0.85,
        'nascent': 1.00,
    },
    'ancient': {
        'qi': 0.75,
        'foundation': 0.90,
        'core': 1.00,
        'nascent': 1.00,
    },
    'spirit': {
        'qi': 0.85,
        'foundation': 1.00,
        'core': 1.00,
        'nascent': 1.00,
    },
    'artifact': {
        'qi': 1.00,
        'foundation': 1.00,
        'core': 1.00,
        'nascent': 1.00,
    },
}


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        clean = ts.replace('Z', '+00:00') if 'Z' in ts else ts
        dt = datetime.fromisoformat(clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def is_treasure_dormant(consecutive_refuse_count: int = 0, **kwargs) -> bool:
    """Return True if a player has continuously refused or not participated 3 or more times when invited."""
    return int(consecutive_refuse_count or 0) >= 3


def calculate_fighter_stats(
    player: Mapping[str, object],
    inventory: Sequence[Mapping[str, object]],
    consecutive_refuse_count: int | None = None,
    last_duel_at: str | None = None,
    now_iso: str | None = None,
) -> dict:
    """Calculate 3D stats for a fighter: HP, Defense, Attack and artifact passives."""
    p_dict = dict(player) if player is not None else {}
    cultivation = max(0, int(p_dict.get('cultivation', 0) or 0))
    realm = str(p_dict.get('realm', 'qi'))
    hp = REALM_BASE_HP.get(realm, REALM_BASE_HP['qi']) + int(math.pow(cultivation, 0.75) * 3)
    defense = REALM_DEFENSE.get(realm, 15)

    refuse_count = int(
        consecutive_refuse_count if consecutive_refuse_count is not None
        else p_dict.get('consecutive_refuse_duel_count', 0) or 0
    )

    has_dormant_treasure = is_treasure_dormant(refuse_count)
    attack = 0
    if inventory:
        for item in inventory:
            it_dict = dict(item)
            if it_dict.get('template_id') == 'qiankun_ding':
                continue
            rarity = str(it_dict.get('rarity', 'artifact'))
            base_atk = RARITY_ATTACK.get(rarity, 18)
            ratio = REALM_POWER_RATIO.get(rarity, {}).get(realm, 1.0)
            item_atk = int(base_atk * ratio)
            attack += item_atk
    if attack == 0:
        attack = 15

    if has_dormant_treasure:
        attack = max(1, int(attack * 0.40))

    item_templates = template_ids(inventory)
    defense += (3 if 'xuanjia_fu' in item_templates else 0) + (8 if 'xuanbing_jia' in item_templates else 0)
    extra_true_damage = (8 if 'qingshuang_jian' in item_templates else 0) + (10 if 'tianlei_gu' in item_templates else 0)
    pierce_chance = 0.20 if 'taixu_shenzhen' in item_templates else 0.0

    return {
        'name': str(p_dict.get('dao_name', '修士')),
        'realm': realm,
        'realm_name': REALM_NAMES.get(realm, realm),
        'realm_order': REALM_ORDER.get(realm, 1),
        'cultivation': cultivation,
        'hp': hp,
        'max_hp': hp,
        'defense': defense,
        'attack': attack,
        'extra_true_damage': extra_true_damage,
        'pierce_chance': pierce_chance,
        'artifacts_count': len(inventory),
        'has_dormant_treasure': has_dormant_treasure,
        'consecutive_refuse_count': refuse_count,
    }


def apply_cheat_mode(
    fighter_stats: dict,
    mode: str = 'god',
    custom_dice: int | None = 20,
) -> dict:
    """Build an administrator's temporary duel fighter with a GM combat mode."""
    if mode not in ('god', 'one_hit', 'immortal', 'lucky'):
        raise ValueError(f'unknown cheat mode: {mode}')
    if custom_dice is None:
        custom_dice = 20
    if mode == 'lucky' and (type(custom_dice) is not int or not 1 <= custom_dice <= 20):
        raise ValueError('custom_dice must be an integer from 1 to 20')

    fighter = dict(fighter_stats)
    fighter['is_cheat'] = True
    fighter['cheat_mode'] = mode

    if mode == 'god':
        fighter['name'] = f"【天道化身】{fighter['name']}"
        fighter['hp'] = 999999
        fighter['max_hp'] = 999999
        fighter['defense'] = 9999
        fighter['attack'] = 99999
        fighter['extra_true_damage'] = 99999
        fighter['pierce_chance'] = 1.0
        fighter['gold_shield'] = True
        fighter['fixed_dice'] = 20
        fighter['cheat_title'] = '不可战胜'
    elif mode == 'one_hit':
        fighter['attack'] = 999999
        fighter['extra_true_damage'] = 999999
        fighter['pierce_chance'] = 1.0
        fighter['fixed_dice'] = 20
        fighter['cheat_title'] = '一念诛仙·大道寂灭'
    elif mode == 'immortal':
        fighter['hp'] = max(fighter.get('hp', 100), 888888)
        fighter['max_hp'] = max(fighter.get('max_hp', 100), 888888)
        fighter['defense'] = 99999
        fighter['gold_shield'] = True
        fighter['cheat_title'] = '万劫不磨·不灭真身'
    else:
        fighter['fixed_dice'] = custom_dice
        fighter['pierce_chance'] = 1.0
        fighter['cheat_title'] = '天命所归'

    return fighter


def roll_attack(attacker_stats: dict, defender_stats: dict, rng) -> tuple[int, int, str]:
    """Calculate single attack damage with D20 heavenly dice and underdog balancing."""
    dice = attacker_stats.get('fixed_dice') or rng.randrange(1, 21)
    base_atk = attacker_stats['attack']
    is_underdog = attacker_stats.get('realm_order', 1) < defender_stats.get('realm_order', 1)
    is_superior = attacker_stats.get('realm_order', 1) > defender_stats.get('realm_order', 1)

    is_crit = False
    if is_underdog:
        # Underdog surge: Crit threshold relaxed to 16..20 (25% crit chance)
        if dice >= 16:
            is_crit = True
            factor = 1.50
            desc = f"天道垂青·越阶暴击【{dice}】"
        elif dice == 1:
            factor = 0.70
            desc = "灵力逆流【1】"
        else:
            factor = 0.85 + (dice * 0.02)
            desc = f"天机骰【{dice}】"
    elif is_superior:
        # Overconfidence of the strong: dice 1..3 triggers misplay (-35% damage)
        if dice <= 3:
            factor = 0.65
            desc = f"自视甚高·轻敌失手【{dice}】"
        elif dice >= 19:
            is_crit = True
            factor = 1.50
            desc = f"天道共鸣·暴击【{dice}】"
        else:
            factor = 0.85 + (dice * 0.02)
            desc = f"天机骰【{dice}】"
    else:
        # Even match
        if dice == 1:
            factor = 0.70
            desc = "灵力逆流【1】"
        elif dice >= 19:
            is_crit = True
            factor = 1.50
            desc = f"天道共鸣·暴击【{dice}】"
        else:
            factor = 0.85 + (dice * 0.02)
            desc = f"天机骰【{dice}】"

    raw_damage = int(base_atk * factor)
    def_val = defender_stats['defense']

    if attacker_stats.get('pierce_chance', 0) > 0 and (rng.randrange(100) < int(attacker_stats['pierce_chance'] * 100)):
        def_val = def_val - (def_val // 4)
        desc += "·太虚破气"

    # Asymptotic damage reduction: Mitigation = DEF / (DEF + 80)
    mitigation = def_val / (def_val + 80.0)
    effective_damage = int(raw_damage * (1.0 - mitigation))
    damage = max(5, effective_damage) + attacker_stats.get('extra_true_damage', 0)

    # Golden Shield BUFF: if defender has gold_shield (>=2 realms higher than attacker)
    has_gold_shield = defender_stats.get('gold_shield', False)
    if has_gold_shield:
        if dice in (19, 20):
            # Great success breaks / bypasses gold shield
            desc += "·【大成功·贯穿金盾】"
        else:
            # Fixed 15% damage reduction
            damage = max(5, int(damage * 0.85))
            desc += "·【金盾化解15%】"

    # Underdog critical hits deal bonus true damage (6% of defender's max HP)
    if is_underdog and is_crit:
        bonus_true = max(5, int(defender_stats['max_hp'] * 0.06))
        damage += bonus_true
        desc += f"(破体真伤+{bonus_true})"

    return damage, dice, desc


def init_fighters_for_duel(fighter1: dict, fighter2: dict) -> None:
    """Set up gold shield and initial status for two fighters."""
    diff = abs(fighter1.get('realm_order', 1) - fighter2.get('realm_order', 1))
    if diff >= 2:
        if fighter1.get('realm_order', 1) > fighter2.get('realm_order', 1):
            fighter1['gold_shield'] = True
            fighter2['gold_shield'] = fighter2.get('cheat_mode') in ('god', 'immortal')
        else:
            fighter2['gold_shield'] = True
            fighter1['gold_shield'] = fighter1.get('cheat_mode') in ('god', 'immortal')
    else:
        fighter1['gold_shield'] = fighter1.get('cheat_mode') in ('god', 'immortal')
        fighter2['gold_shield'] = fighter2.get('cheat_mode') in ('god', 'immortal')


def simulate_duel_round(fighter1: dict, fighter2: dict, round_num: int, rng) -> dict:
    """Simulate a single round of battle between two fighters."""
    init_fighters_for_duel(fighter1, fighter2)
    logs = []
    hp1 = fighter1['hp']
    hp2 = fighter2['hp']

    # Fighter 1 attacks Fighter 2
    dmg1, dice1, note1 = roll_attack(fighter1, fighter2, rng)
    hp2 = max(1 if fighter2.get('cheat_mode') in ('god', 'immortal') else 0, hp2 - dmg1)
    fighter2['hp'] = hp2
    logs.append(f"🗡️ 第 {round_num} 轮：【{fighter1['name']}】{note1}，轰出 {dmg1} 点伤害！（【{fighter2['name']}】气血余 {hp2}）")

    # Fighter 2 attacks Fighter 1 (即使在当轮受创，仍进行交锋反击)
    dmg2, dice2, note2 = roll_attack(fighter2, fighter1, rng)
    hp1 = max(1 if fighter1.get('cheat_mode') in ('god', 'immortal') else 0, hp1 - dmg2)
    fighter1['hp'] = hp1
    logs.append(f"⚡ 反击：【{fighter2['name']}】{note2}，反击造成 {dmg2} 点伤害！（【{fighter1['name']}】气血余 {hp1}）")

    # 双方结算血量，若均 <= 0，判定为同归于尽 平局
    if hp1 <= 0 and hp2 <= 0:
        return {
            'round_num': round_num,
            'logs': logs,
            'hp1': hp1,
            'hp2': hp2,
            'is_over': True,
            'winner': 0,
            'is_decision': False,
        }

    # Fighter 2 气血耗尽，Fighter 1 获胜
    if hp2 <= 0:
        return {
            'round_num': round_num,
            'logs': logs,
            'hp1': hp1,
            'hp2': hp2,
            'is_over': True,
            'winner': 1,
            'is_decision': False,
        }

    # Fighter 1 气血耗尽，Fighter 2 获胜
    if hp1 <= 0:
        return {
            'round_num': round_num,
            'logs': logs,
            'hp1': hp1,
            'hp2': hp2,
            'is_over': True,
            'winner': 2,
            'is_decision': False,
        }

    # 若战斗轮次大于八轮，则剩余 HP 多者获胜
    if round_num >= 8:
        pct1 = hp1 / max(1, fighter1['max_hp'])
        pct2 = hp2 / max(1, fighter2['max_hp'])
        if pct1 == pct2:
            winner = 0
        else:
            winner = 1 if pct1 > pct2 else 2
        return {
            'round_num': round_num,
            'logs': logs,
            'hp1': hp1,
            'hp2': hp2,
            'is_over': True,
            'winner': winner,
            'is_decision': True,
        }

    return {
        'round_num': round_num,
        'logs': logs,
        'hp1': hp1,
        'hp2': hp2,
        'is_over': False,
        'winner': None,
        'is_decision': False,
    }


def simulate_duel(fighter1: dict, fighter2: dict, rng) -> dict:
    """Simulate a multi-round duel until someone's temporary HP hits 0 or 8 rounds expire."""
    all_logs = []
    round_res = None
    for r in range(1, 9):
        round_res = simulate_duel_round(fighter1, fighter2, r, rng)
        all_logs.extend(round_res['logs'])
        if round_res['is_over']:
            break

    return {
        'winner': round_res['winner'] if round_res else 0,
        'rounds': round_res['round_num'] if round_res else 1,
        'logs': all_logs,
        'final_hp1': fighter1['hp'],
        'final_hp2': fighter2['hp'],
    }


def format_round_report(
    fighter1: dict,
    fighter2: dict,
    round_result: dict,
    wager: int,
    p1_confirmed: bool = False,
    p2_confirmed: bool = False,
    remaining_seconds: int = 60,
) -> str:
    """Format the report for a single round of battle with confirmation prompt."""
    r_num = round_result['round_num']
    hp1, hp2 = round_result['hp1'], round_result['hp2']
    max1, max2 = fighter1['max_hp'], fighter2['max_hp']
    pct1 = max(0, int(hp1 / max(1, max1) * 100))
    pct2 = max(0, int(hp2 / max(1, max2) * 100))

    lines = [
        f"⚔️【擂台 · 第 {r_num} 轮交锋】",
        f"{fighter1['name']}（{fighter1['realm_name']}） VS {fighter2['name']}（{fighter2['realm_name']}）",
        f"💎 押注对决：{wager} 灵石",
        "───── 战况 ─────",
    ]
    lines.extend(round_result['logs'])
    lines.extend([
        "",
        f"⏸️【第 {r_num} 轮 · 气血余量】",
        f"• 【{fighter1['name']}】：❤️ {hp1} / {max1} ({pct1}%)",
        f"• 【{fighter2['name']}】：❤️ {hp2} / {max2} ({pct2}%)",
        "────────────────",
    ])

    m1 = fighter1.get('mention') or f"@{fighter1['name']}\u2005"
    m2 = fighter2.get('mention') or f"@{fighter2['name']}\u2005"

    if p1_confirmed and not p2_confirmed:
        lines.append(f"⏳ 【{fighter1['name']}】已确认继续，等待【{fighter2['name']}】确认（倒计时 {remaining_seconds} 秒）！")
        lines.append(f"👉 请 {m2} 发送【#继续决斗】确认下一轮，或发送【#投降】认输（扣除 80% 押注）。")
    elif p2_confirmed and not p1_confirmed:
        lines.append(f"⏳ 【{fighter2['name']}】已确认继续，等待【{fighter1['name']}】确认（倒计时 {remaining_seconds} 秒）！")
        lines.append(f"👉 请 {m1} 发送【#继续决斗】确认下一轮，或发送【#投降】认输（扣除 80% 押注）。")
    else:
        lines.append(f"⏳ 请双方在 {remaining_seconds} 秒内发送【#继续决斗】确认下一轮，或发送【#投降】认输（扣除 80% 押注）。")
        lines.append(f"👉 请参战道友 {m1} {m2} 回复确认！")

    return "\n".join(lines)


def format_surrender_report(
    surrendered_fighter: dict,
    winner_fighter: dict,
    wager: int,
    penalty: int,
    is_timeout: bool = False,
) -> str:
    """Format report when a player surrenders or fails to confirm within deadline."""
    preserved = max(0, wager - penalty)
    if is_timeout:
        title = "⏳【仙道决斗 · 超时弃权】"
        reason_text = f"【{surrendered_fighter['name']}】超过 60 秒未确认继续决斗，视为畏战投降！"
        summary_win = f"• 【{winner_fighter['name']}】斩获对手 80% 押注，获得 +{penalty} 灵石！"
        summary_lose = f"• 【{surrendered_fighter['name']}】超时弃权，扣除 -{penalty} 灵石（保全 {preserved} 灵石）！"
    else:
        title = "🏳️【仙道决斗 · 认输退场】"
        reason_text = f"【{surrendered_fighter['name']}】审时度势，自知不敌，主动认输投降！"
        summary_win = f"• 【{winner_fighter['name']}】击退强敌，斩获 +{penalty} 灵石！"
        summary_lose = f"• 【{surrendered_fighter['name']}】认输止损，扣除 -{penalty} 灵石（保全 {preserved} 灵石）！"

    lines = [
        title,
        reason_text,
        f"🏆 最终胜者：【{winner_fighter['name']}】",
        "",
        "💎 灵石清算（投降扣除 80% 押注）：",
        summary_win,
        summary_lose,
    ]
    return "\n".join(lines)


def format_duel_report(fighter1: dict, fighter2: dict, result: dict, wager: int) -> str:
    """Format the full battle card text for WeChat (normal victory or tie)."""
    win_idx = result['winner']
    winner_name = fighter1['name'] if win_idx == 1 else (fighter2['name'] if win_idx == 2 else "")
    loser_name = fighter2['name'] if win_idx == 1 else (fighter1['name'] if win_idx == 2 else "")
    is_upset = (win_idx == 1 and fighter1['realm_order'] < fighter2['realm_order']) or (
        win_idx == 2 and fighter2['realm_order'] < fighter1['realm_order']
    )
    upset_tag = "（以弱胜强，越阶伐仙！）" if is_upset else ""

    def_mit1 = int(fighter1['defense'] / (fighter1['defense'] + 80.0) * 100)
    def_mit2 = int(fighter2['defense'] / (fighter2['defense'] + 80.0) * 100)
    shield1 = " 🛡️【金盾护体·免伤15%】" if fighter1.get('gold_shield') else ""
    shield2 = " 🛡️【金盾护体·免伤15%】" if fighter2.get('gold_shield') else ""

    dormant1 = " 💤【至宝沉眠】" if fighter1.get('has_dormant_treasure') else ""
    dormant2 = " 💤【至宝沉眠】" if fighter2.get('has_dormant_treasure') else ""

    diff = abs(fighter1.get('realm_order', 1) - fighter2.get('realm_order', 1))

    lines = [
        "⚔️【生死擂台 · 仙道决斗】",
        f"{fighter1['name']}（{fighter1['realm_name']}） VS {fighter2['name']}（{fighter2['realm_name']}）",
        f"💎 押注对决：{wager} 灵石",
    ]
    if diff >= 2:
        lines.append("⚠️【境界悬殊】双方相差 2 阶及以上，强者获【金盾】固定免伤 15%（唯有 19/20 大成功可贯穿）！")
    lines.extend([
        "",
        "📊【三维属性对比】",
        f"• {fighter1['name']}：❤️ 气血 {fighter1['max_hp']} ｜ 🛡️ 防御 {fighter1['defense']} (减伤{def_mit1}%){shield1}{dormant1} ｜ ⚔️ 总攻 {fighter1['attack']}（持{fighter1['artifacts_count']}宝）",
        f"• {fighter2['name']}：❤️ 气血 {fighter2['max_hp']} ｜ 🛡️ 防御 {fighter2['defense']} (减伤{def_mit2}%){shield2}{dormant2} ｜ ⚔️ 总攻 {fighter2['attack']}（持{fighter2['artifacts_count']}宝）",
        "",
        "──────── 战斗推演 ────────",
    ])
    lines.extend(result['logs'])
    lines.extend([
        "",
        "──────── 决斗结算 ────────",
    ])

    if win_idx == 0:
        lines.extend([
            "🏆 最终胜者：【势均力敌 · 战成平局】（双方同归于尽）",
            "💎 灵石清算：",
            f"• 双方旗鼓相当，押注各自全额退回（{wager} 灵石）！",
            "💡 仙道切磋，双方道基真实修为未损，储物袋法宝完好无缺！",
        ])
    else:
        lines.extend([
            f"🏆 最终胜者：【{winner_name}】{upset_tag}",
            "💎 灵石清算：",
            f"• 【{winner_name}】战胜对手筹码，获得 +{wager} 灵石！",
            f"• 【{loser_name}】决斗落败，损失 -{wager} 灵石！",
            "💡 仙道切磋，双方道基真实修为未损，储物袋法宝完好无缺！",
        ])
    return "\n".join(lines)


def format_pvp_stats(
    player: Mapping[str, object],
    inventory: Sequence[Mapping[str, object]],
    consecutive_refuse_count: int | None = None,
    last_duel_at: str | None = None,
    now_iso: str | None = None,
) -> str:
    """Format individual player's 3D combat stats."""
    stats = calculate_fighter_stats(
        player,
        inventory,
        consecutive_refuse_count=consecutive_refuse_count,
        last_duel_at=last_duel_at,
        now_iso=now_iso,
    )
    passives = []
    if 'qingshuang_jian' in template_ids(inventory):
        passives.append("青霜剑（+8 冰霜穿透真伤）")
    if 'tianlei_gu' in template_ids(inventory):
        passives.append("天雷鼓（每次攻击+10 真伤）")
    if 'xuanjia_fu' in template_ids(inventory):
        passives.append("玄甲符（防御+3）")
    if 'xuanbing_jia' in template_ids(inventory):
        passives.append("玄冰甲（防御+8）")
    if stats['pierce_chance'] > 0:
        passives.append("太虚神针（20% 概率破气穿甲 25%）")
    passive_str = "、".join(passives) if passives else "无额外神通加持"

    mitigation = int(stats['defense'] / (stats['defense'] + 80.0) * 100)

    dormant_notice = ""
    refuse_cnt = stats.get('consecutive_refuse_count', 0)
    if stats.get('has_dormant_treasure'):
        dormant_notice = (
            f"⚠️【至宝自晦】检测到你已连续 {refuse_cnt} 次拒绝或未响应斗法/决斗，战意溃散自晦，至宝威能沉眠，整体攻击力减少 60%！唯有参与一次【#斗法】方可唤醒满额神威（参与决斗无法移除沉眠）！\n"
            "────────────────\n"
        )
    elif refuse_cnt > 0:
        dormant_notice = (
            f"⚠️【受邀提醒】当前已连续 {refuse_cnt}/3 次拒绝或未响应斗法/决斗，达 3 次将触发至宝沉眠（整体攻击力减少 60%）！\n"
            "────────────────\n"
        )

    return (
        f"📊【{stats['name']} · 决斗三维属性】\n"
        f"────────────────\n"
        f"❤️ 气血（HP）：{stats['hp']}（境界基础气血 + 当前修为转化）\n"
        f"🛡️ 防御（DEF）：{stats['defense']}（{stats['realm_name']}护体罡气，免伤 {mitigation}%）\n"
        f"⚔️ 攻击（ATK）：{stats['attack']}（储物袋 {stats['artifacts_count']} 件法宝，乾坤鼎不提供攻击；受境界承载与战意调谐）\n"
        f"✨ 神通特效：{passive_str}\n"
        f"────────────────\n"
        f"{dormant_notice}"
        f"👉 发起决斗：#决斗 @群友 金额\n"
    )


def format_pvp_help() -> str:
    """Format the help guidelines for #决斗."""
    return (
        "⚔️【仙道决斗指引】\n"
        "决斗是基于修士战力的擂台切磋\n\n"
        "📊【三维数值计算】\n"
        "• ❤️ 气血（HP）: 炼气/筑基/金丹/元婴基础气血分别为 100/200/400/730，当前修为额外增加气血\n"
        "• 🛡️ 防御（DEF）: 基于当前境界\n"
        "• ⚔️ 攻击（ATK）: 基于法宝数量与品质；乾坤鼎仅扩容、不提供攻击。仅持鼎时按徒手 15 攻击计算（受境界承载限制：炼气驾驭至宝50%、筑基70%、金丹85%、元婴100%）\n"
        "• 🎲 天机骰：每次出招掷 D20 骰波动伤害\n"
        "• 🛡️ 金盾机制：当对战双方大境界相差 ≥ 2 阶时，高境界一方获得【金盾】护体（固定免伤 15%），唯有低境界者攻击掷出 19、20 点大成功时方可贯穿破除！\n"
        "• 💤 至宝沉眠：当有斗法或决斗邀请时，若连续 3 次拒绝或未响应，战意自晦沉眠，整体攻击力减少 60%！唯有参与一次【#斗法】方可解除沉眠（参与决斗无法移除）！\n"
        "• ⚔️ 逐轮交锋：决斗一轮一轮进行，每轮交锋后双方均需发送【#继续决斗】（或【#继续】）方可推进！\n"
        "• 💎 接受决斗时双方押注会先从可消费余额中扣出托管；正常胜者收回本金并赢得对方押注，若同归于尽战平或双方超时则全额退回，投降败者取回 20%。\n"
        "• 🏳️ 投降止损：劣势方可发送【#投降】（或【#认输】），扣除 80% 押注交给胜者，保全剩余 20% 灵石！仅一方确认继续而另一方超时未确认时，未确认者视为投降。\n\n"
        "📜【决斗规则】\n"
        "• 发起决斗：发送【#决斗 @群友 金额】（金额为 1～1000 的正整数，1 灵石即可发起）\n"
        "• 应战决斗：受邀者 60 秒内发送【#接受决斗】打响第 1 轮，或发送【#拒绝决斗】（超时未应答自动撤销并计入避战）\n"
        "• 撤销邀请：发起者在对方应战前可发送【#取消决斗】主动撤销\n"
        "• 推进轮次：双方在 60 秒内均发送【#继续决斗】进入下一轮\n"
        "• 认输弃权：发送【#投降】主动认输，扣除 80% 押注止损\n"
        "• 查看战况：发送【#决斗状态】查看当前决斗等待或交锋倒计时\n"
        "• 查看三维：发送【#决斗属性】查看本人当前的决斗战力。"
    )
