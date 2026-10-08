"""Structured content catalog for 妖祸夜行.

Content IDs retain their original identity. Current balance uses two daytime
exploration stretches and three nights, experience-driven curved growth, and
the segment/stance encounters defined in patterns.py. Historical move metadata
below remains available for catalog descriptions, not runtime random selection.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


_RAW = json.loads(Path(__file__).with_name("content_catalog.json").read_text(encoding="utf-8"))

# Level-one HP/ATK are reduced; class-specific quadratic growth rewards XP.
# Defense (base and growth) is exactly 85% of the previous class curve.
_CLASS_STATS = {
    "C01": (800, 140, 85, 1.0, 2),
    "C02": (1000, 119, 93.5, 1.0, 2),
    "C03": (630, 180, 76.5, 0.75, 2),
    "C04": (840, 75, 76.5, 0.8, 3),
    "C05": (760, 128, 80.75, 1.0, 2),
    "C06": (630, 184, 72.25, 1.0, None),
    "C07": (880, 144, 89.25, 1.0, 2),
    "C08": (840, 176, 72.25, 1.0, 2),
}

# hp, atk, defense, normal, light, single-target strong. An area or status
# attack reduces the direct-hit coefficient below the single-target marker.
_TIERS = {
    "mob": (300, 100, 40, 0.65, 0.8, 1.2),
    "elite": (600, 120, 65, 0.9, 1.05, 2.0),
    "night1": (1800, 120, 65, 0.45, 0.75, 6.3),
    "night2": (2800, 150, 80, 0.45, 0.8, 9.0),
    "boss": (3360, 180, 90, 0.45, 0.65, 10.2),
}

# Name, light target, strong target, light ailment, strong ailment, part.
# Targets: random, two, lowest_two, all. A strong move always previews its
# exact targets for a complete round; the target selector is used at lock time.
_ENEMY_MOVES = {
    "M01": ("追猎", "血牙扑击", "lowest_two", "random", "", "", ""),
    "M02": ("甲尾扫", "甲刺横扫", "all", "all", "", "", "背甲"),
    "M03": ("毒牙", "雾网", "random", "all", "poison", "poison", ""),
    "M04": ("雷踏", "雷角冲撞", "all", "all", "", "", "雷角"),
    "M05": ("藤鞭", "缠枝", "random", "random", "attack_down", "attack_down", ""),
    "M06": ("火羽", "炎翼", "random", "all", "burn", "burn", ""),
    "M07": ("溅水", "潮线", "all", "all", "", "", "鱼鳍"),
    "M08": ("摇铃", "金铃敲击", "random", "random", "vulnerable", "vulnerable", ""),
    "M09": ("碎石", "石面重砸", "two", "random", "", "", ""),
    "M10": ("霜爪", "霜息", "random", "all", "attack_down", "attack_down", ""),
    "M11": ("鳞粉", "月翅", "random", "all", "bleed", "bleed", ""),
    "M12": ("骨锤", "骨雨", "random", "all", "", "", "骨扣"),
    "M13": ("啃咬", "窜扑", "random", "random", "", "", ""),
    "M14": ("黏舌", "扑水", "random", "random", "attack_down", "", ""),
    "M15": ("甩砂", "砂尾扫", "two", "all", "", "", ""),
    "M16": ("蜂刺", "蜂群", "random", "random", "poison", "poison", ""),
    "M17": ("夹击", "横钳", "random", "random", "", "", "蟹壳"),
    "M18": ("啄影", "掠翼", "lowest_two", "two", "", "", ""),
    "M19": ("迷雾", "扑影", "random", "random", "attack_down", "", ""),
    "M20": ("火屑", "炭雨", "random", "all", "burn", "burn", ""),
    "M21": ("毒钩", "裂脊穿刺", "random", "random", "poison", "poison", ""),
    "M22": ("潮刃", "黑潮", "all", "all", "", "", ""),
    "M23": ("骨火", "灯阵", "random", "two", "burn", "burn", ""),
    "M24": ("落岩", "裂岩", "all", "all", "", "", "岩臂"),
}

_BOSS_MOVES = {
    "B01": ("烬爪", "焚庭", "random", "all", "burn", "burn", ""),
    "B02": ("潮刃", "涨潮", "all", "all", "", "", ""),
    "B03": ("金雨", "裂刃", "all", "random", "", "bleed", "甲扣"),
    "B04": ("毒藤", "孢雾", "random", "all", "poison", "poison", ""),
    "B05": ("落石", "裂地", "two", "all", "", "", ""),
    "B06": ("电网", "霆刑", "two", "random", "thunder_mark", "", ""),
}

# Tags retain exact source identity and let combat dispatch by C/R/F ID.
# A trigger string is metadata, not an executable rule; the source text and
# bounds remain available to the combat implementation.
_ROOT_TRIGGERS = {
    "R01": "after_skill_or_ultimate", "R02": "after_guard", "R03": "attack_low_hp",
    "R04": "first_basic", "R05": "skill_direct_hit", "R06": "basic_on_bleeding",
    "R07": "after_potion", "R08": "own_poison_tick", "R09": "heal_overflow",
    "R10": "single_target_heal", "R11": "first_low_hp_survival", "R12": "companion_hit",
    "R13": "guard_and_hit", "R14": "effective_heal_received", "R15": "guard_and_hit",
    "R16": "undamaged_previous_round", "R17": "after_potion", "R18": "after_ultimate",
    "R19": "skill_direct_hit", "R20": "skill_below_half_hp", "R21": "own_burn_expires",
    "R22": "direct_hit_burning", "R23": "self_paid_hp", "R24": "kill_burning_target",
    "R25": "battle_start", "R26": "guard_and_hit", "R27": "own_shield_expires",
    "R28": "low_hp_guard", "R29": "skill_grants_shield", "R30": "rescue_completed",
}

_UPGRADE_TRIGGERS = {
    "F01": "skill_direct_hit", "F02": "second_damage_basic", "F03": "basic_hit_debuffed",
    "F04": "damage_basic_hit", "F05": "own_heal", "F06": "effective_heal_received",
    "F07": "on_acquire", "F08": "heal_overflow", "F09": "on_acquire",
    "F10": "skill_direct_hit", "F11": "guard", "F12": "undamaged_previous_round",
    "F13": "own_burn_tick", "F14": "apply_burn", "F15": "skill_hit_burning",
    "F16": "own_burn_tick", "F17": "guard", "F18": "counterattack",
    "F19": "counterattack_hit", "F20": "basic_with_shield", "F21": "companion_hit",
    "F22": "apply_poison", "F23": "poison_reaches_three", "F24": "companion_hit",
    "F25": "effective_main_heal", "F26": "overflow_damage", "F27": "consume_shadow",
    "F28": "gain_shadow_from_guard_or_potion",
}

_DAMAGE_BASIC_CLASSES = ("C01", "C02", "C03", "C05", "C06", "C07", "C08")
_DIRECT_SKILL_CLASSES = ("C01", "C02", "C03", "C05", "C07", "C08")
_UPGRADE_CLASSES: dict[str, tuple[str, ...]] = {
    "F01": _DIRECT_SKILL_CLASSES,
    "F02": ("C01", "C03", "C05", "C07", "C08"),
    "F03": _DAMAGE_BASIC_CLASSES, "F04": _DAMAGE_BASIC_CLASSES,
    "F06": _DAMAGE_BASIC_CLASSES, "F08": ("C04",),
    "F09": ("C01", "C02", "C03", "C04", "C05", "C07", "C08"),
    "F10": ("C03", "C07"), "F12": _DAMAGE_BASIC_CLASSES,
    "F15": _DIRECT_SKILL_CLASSES, "F16": _DAMAGE_BASIC_CLASSES,
    "F20": _DAMAGE_BASIC_CLASSES,
    "F21": ("C05",), "F24": ("C05",), "F25": ("C04",),
    "F26": ("C04",), "F27": ("C06",), "F28": ("C06",),
}
_UPGRADE_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "F01": ("own_skill_direct",),
    "F02": ("damage_basic", "own_skill_direct"),
    "F03": ("damage_basic", "team_defense_down"),
    "F04": ("damage_basic",),
    "F06": ("damage_basic",),
    "F09": ("own_skill",), "F10": ("own_skill_direct",),
    "F12": ("damage_basic",),
    "F13": ("own_burn",), "F14": ("own_burn",),
    "F15": ("own_skill_direct", "team_burn"),
    "F16": ("own_burn",),
    "F18": ("own_counter",), "F19": ("own_counter",),
    "F20": ("damage_basic", "team_shield"),
    "F21": ("own_companion",), "F22": ("own_poison",),
    "F23": ("own_poison",), "F24": ("own_companion",),
    "F25": ("own_main_heal",), "F26": ("own_main_heal",),
    "F27": ("own_shadow",), "F28": ("own_shadow",),
}


def _effects(item: dict[str, Any], trigger: str) -> list[dict[str, str]]:
    return [{"id": item["id"], "trigger": trigger, "description": item["effect"], "limit": item["limit"]}]


CLASSES: dict[str, dict[str, Any]] = {}
for _row in _RAW["classes"]:
    _id = _row["id"]
    _hp, _atk, _def, _basic, _cd = _CLASS_STATS[_id]
    CLASSES[_id] = {
        **_row,
        "hp": _hp, "attack": _atk, "defense": _def,
        "base_hp": _hp, "base_atk": _atk, "base_defense": _def,
        "growth_hp": 0.08 if _id in {"C03", "C06"} else 0.14,
        "growth_hp_quadratic": 0.002 if _id in {"C03", "C06"} else 0.004,
        "growth_atk": 0.06 if _id in {"C01", "C02"} else 0.08 if _id == "C04" else 0.12,
        "growth_atk_quadratic": 0.002 if _id in {"C01", "C02"} else 0.003 if _id == "C04" else 0.004,
        "growth_defense": 4.25,
        "basic_multiplier": _basic, "basic_kind": "heal" if _id == "C04" else "damage",
        "skill_cd": _cd, "effects": [f"{_id}:passive", f"{_id}:skill", f"{_id}:ultimate"],
        "has_skill": _cd is not None,
    }

CLASSES["C04"]["low_hp_bonus_atk_cap"] = 0.25
CLASSES["C04"]["passive"] = (
    "扶危：每回合首次治疗生命低于50%的友方，主治疗额外加其最大生命5%，"
    "这部分额外治疗最多为自身攻击的25%；主治疗溢出100%转为对一名敌人的伤害，"
    "每行动原始转换伤害上限200%自身ATK。"
)
CLASSES["C06"]["skill"] = (
    "无主动技能；可使用普攻、防御、闪避前段/后段、回气、灵药、绝技、救援。"
    "不可获得主动技能冷却或仅主动技能触发的奖励。"
)

ROOTS: dict[str, dict[str, Any]] = {}
for _row in _RAW["roots"]:
    _id = _row["id"]
    ROOTS[_id] = {**_row, "description": _row["effect"],
                  "trigger": _ROOT_TRIGGERS[_id], "effects": _effects(_row, _ROOT_TRIGGERS[_id])}

UPGRADES: dict[str, dict[str, Any]] = {}
for _row in _RAW["upgrades"]:
    _id = _row["id"]
    UPGRADES[_id] = {**_row, "description": _row["effect"],
                     "trigger": _UPGRADE_TRIGGERS[_id], "effects": _effects(_row, _UPGRADE_TRIGGERS[_id]),
                     "eligible_classes": _UPGRADE_CLASSES.get(_id, tuple(CLASSES)),
                     "requires": _UPGRADE_REQUIREMENTS.get(_id, ())}
FORTUNES = UPGRADES


def _capabilities(member: dict[str, Any]) -> set[str]:
    class_id = member["class_id"]
    roots = set(member.get("roots", ()))
    fortunes = set(member.get("fortunes", ()))
    caps: set[str] = set()
    if class_id in _DAMAGE_BASIC_CLASSES:
        caps.add("damage_basic")
    if class_id != "C06":
        caps.add("own_skill")
    if class_id in _DIRECT_SKILL_CLASSES:
        caps.add("own_skill_direct")
    if class_id == "C04":
        caps.add("own_main_heal")
    if class_id == "C05":
        caps.update({"own_companion", "own_poison"})
    if class_id == "C06":
        caps.add("own_shadow")
    if class_id == "C02" or "R26" in roots:
        caps.add("own_counter")
    if class_id == "C03" or ("R19" in roots and "own_skill_direct" in caps) or (
        "F19" in fortunes and "own_counter" in caps
    ):
        caps.add("own_burn")
    if class_id in {"C02", "C07"} or "R25" in roots or "R30" in roots or (
        "F16" in fortunes and "own_burn" in caps
    ):
        caps.add("own_shield")
    if class_id == "C01" or ("own_skill_direct" in caps and ("R05" in roots or "F01" in fortunes)) or (
        "F14" in fortunes and "own_burn" in caps
    ) or ("F23" in fortunes and "own_poison" in caps):
        caps.add("own_defense_down")
    return caps


def available_upgrades(member: dict[str, Any], party: list[dict[str, Any]] | None = None) -> list[str]:
    """Return unowned, immediately triggerable F IDs for one run member.

    Existing team status sources count for effects that target the enemy or
    receive shields. A prerequisite upgrade can be selected first, then its
    dependent effects become candidates on later nodes.
    """
    team = party or [member]
    own = _capabilities(member)
    everyone = [_capabilities(person) for person in team]
    caps = set(own)
    if any("own_burn" in other for other in everyone):
        caps.add("team_burn")
    if any("own_defense_down" in other for other in everyone):
        caps.add("team_defense_down")
    if "own_shield" in own or any(person["class_id"] in {"C02", "C07"} for person in team):
        caps.add("team_shield")
    owned = set(member.get("fortunes", ()))
    return [key for key, entry in UPGRADES.items()
            if key not in owned and member["class_id"] in entry["eligible_classes"]
            and set(entry["requires"]) <= caps]


def _stage_for(stage: str) -> tuple[str, int | None]:
    return {
        "白天小怪": ("mob", None), "白天精英": ("elite", None),
        "第一夜": ("night1", 1), "第二夜": ("night2", 2),
    }[stage]


def _make_enemy(row: dict[str, Any], tier: str, night: int | None,
                meta: tuple[str, str, str, str, str, str, str]) -> dict[str, Any]:
    hp, atk, defense, normal, light, strong = _TIERS[tier]
    light_name, strong_name, light_target, strong_target, light_status, strong_status, part_name = meta
    # v0.1 calls for area strong attacks at 70–85% of the single-target
    # baseline. A status attack spends an additional 10% of direct damage on
    # its two-round damage-over-time budget. These are initial values.
    strong *= 0.8 if strong_target == "all" else 0.9 if strong_target == "two" else 1.0
    if strong_status in {"burn", "poison", "bleed"}:
        strong *= 0.9
    strong = round(strong, 3)
    moves = {
        "normal": {"name": "普攻", "kind": "normal", "multiplier": normal,
                   "target": "random", "weight": {"mob": 60, "elite": 50, "night1": 40,
                                                  "night2": 40, "boss": 30}[tier], "effects": []},
        "light": {"name": light_name, "kind": "light", "multiplier": light,
                  "target": light_target, "weight": {"mob": 30, "elite": 30,
                                                          "night1": 35, "night2": 35,
                                                          "boss": 30}[tier],
                  "effects": [light_status] if light_status else []},
        "strong": {"name": strong_name, "kind": "strong", "multiplier": strong,
                   "target": strong_target, "weight": {"mob": 10, "elite": 20,
                                                            "night1": 25, "night2": 25,
                                                            "boss": 40}[tier],
                   "effects": [strong_status] if strong_status else [],
                   "telegraph_rounds": 1, "cooldown": 2},
    }
    part = ({"name": part_name, "damage_to_body_ratio": 0.6,
             "max_triggers_per_round": 1} if part_name else None)
    return {
        **row, "kind": tier, "tier": tier, "day": 1 if night is None else None,
        "night": night, "hp": hp, "atk": atk, "attack": atk, "defense": defense,
        "normal_multiplier": normal, "light_multiplier": light,
        "strong_multiplier": strong, "moves": moves, "part": part,
        "description": row.get("mechanic", row.get("description", "")),
    }


ENEMIES: dict[str, dict[str, Any]] = {}
for _row in _RAW["enemies"]:
    _tier, _night = _stage_for(_row["stage"])
    ENEMIES[_row["id"]] = _make_enemy(_row, _tier, _night, _ENEMY_MOVES[_row["id"]])

BOSSES: dict[str, dict[str, Any]] = {}
for _row in _RAW["bosses"]:
    _boss = _make_enemy(_row, "boss", 3, _BOSS_MOVES[_row["id"]])
    _boss["mechanic"] = _row["description"]
    _boss["phase_threshold"] = 0.5
    _boss["phase2_multipliers"] = {
        "normal": round(_boss["normal_multiplier"] * 1.08, 3),
        "light": round(_boss["light_multiplier"] * 1.1, 3),
        "strong": round(_boss["strong_multiplier"] * 1.12, 3),
    }
    if _row["id"] == "B05":
        _boss["moves"]["strong_alt"] = {
            **_boss["moves"]["strong"], "name": "沉石", "target": "random",
            "multiplier": round(_TIERS["boss"][5] * 0.95, 3),
        }
    if _row["id"] == "B06":
        _boss["moves"]["strong_alt"] = {
            **_boss["moves"]["strong"], "name": "霆刑·无满痕", "target": "random",
            "multiplier": round(_TIERS["boss"][5] * 0.875, 3),
        }
        _boss["moves"]["strong"]["requires_thunder_marks"] = 2
    if _row["id"] == "B04":
        _boss["special"] = {"kind": "poison_sac", "id": "B04:sac",
                            "hp_ratio": 0.10, "heal_per_round": 0.03,
                            "heal_total_cap": 0.15, "respawns": False}
    if _row["id"] == "B03":
        _boss["hp"] = 3000
    BOSSES[_row["id"]] = _boss

# Six temporary pieces use two slots. These initial values are deliberately
# modest relative to level growth; they are new content pending play tests.
_EQUIPMENT_ROWS = (
    ("E01", "青锋短剑", "weapon", 0, 20, 0, "稳定提高攻击，适合普攻与主动技能路线。"),
    ("E02", "养元木杖", "weapon", 80, 15, 0, "增加生命与攻击，兼顾治疗和持久战。"),
    ("E03", "破岳重刃", "weapon", 0, 35, 0, "偏向高风险输出的兵冢武器。"),
    ("E04", "织灵护衣", "armor", 150, 0, 0, "增加最大生命，给失误和持续伤害留余量。"),
    ("E05", "玄铁甲", "armor", 50, 0, 15, "增加防御与少量生命，适合承受直伤。"),
    ("E06", "山纹护心镜", "armor", 100, 0, 10, "均衡的生命和防御护具。"),
)
EQUIPMENT: dict[str, dict[str, Any]] = {
    row[0]: {"id": row[0], "name": row[1], "slot": row[2], "batch": "首发",
             "hp": row[3], "atk": row[4], "attack": row[4], "defense": row[5],
             "stats": {"hp": row[3], "atk": row[4], "defense": row[5]},
             "description": row[6], "max_level": 3, "upgrade_stat_ratio": 0.5}
    for row in _EQUIPMENT_ROWS
}

EXPLORATION_NODES: dict[str, dict[str, Any]] = {
    "N01": {"id": "N01", "name": "妖兽营地", "kind": "camp", "batch": "首发",
            "description": "自动战斗普通妖兽，获经验并从适用的局内强化中选择。",
            "experience": "combat", "rewards": ["experience", "upgrade"]},
    "N02": {"id": "N02", "name": "古修遗迹", "kind": "ruins", "batch": "首发",
            "description": "展示三枚可触发的局内功法强化，从中选一。",
            "experience": "support", "choice_count": 3, "rewards": ["experience", "upgrade"]},
    "N03": {"id": "N03", "name": "兵冢", "kind": "armory", "batch": "首发",
            "description": "展示三件装备，选一件装配或升级已有装备。",
            "experience": "support", "choice_count": 3, "rewards": ["experience", "equipment"]},
    "N04": {"id": "N04", "name": "灵泉", "kind": "spring", "batch": "首发",
            "description": "恢复生命或补充一瓶灵药，获得少量经验。",
            "experience": "support", "rewards": ["experience", "heal_or_potion"]},
    "N05": {"id": "N05", "name": "精英巢穴", "kind": "elite", "batch": "首发",
            "description": "挑战白天精英妖兽，以较高风险换取局内强化和经验。",
            "experience": "elite", "rewards": ["experience", "upgrade"]},
    "N06": {"id": "N06", "name": "诡异奇遇", "kind": "event", "batch": "首发",
            "description": "先展示生命或灵药代价，再由队伍决定是否换取强化。",
            "experience": "support", "rewards": ["experience", "cost_for_upgrade"]},
}

RULE_TEXT: dict[str, dict[str, str]] = {row["id"]: row for row in _RAW["rules"]}
RULE_TEXT["G01"] = {
    **RULE_TEXT["G01"], "text": "两昼三夜；每个白天6个探索节点，前两夜妖兽，第二夜胜利后直接挑战第三夜妖王。",
    "boundary": "营地胜利得100经验、精英胜利得250经验，其他节点完成得20经验；按累计经验升级，升级保持生命比例，没有第三个白天。",
}
RULE_TEXT["G12"] = {
    **RULE_TEXT["G12"],
    "text": "灵药入场1瓶，上限3瓶；只能自用，消耗1行动，恢复自身最大生命25%。第一夜胜利后补1瓶，第二夜胜利后补2瓶。",
    "boundary": "灵泉恢复生命40%；白天可用阶段编号行动灵药即时自用，不推进节点；终局剩余灵药不带出。",
}
RULE_TEXT["G08"] = {
    **RULE_TEXT["G08"],
    "text": "架势上限3；格挡轻击耗1、重击耗2，足额时直接减伤70%、上限80%；闪避指定前段或后段耗1，回气轮末恢复2。",
    "boundary": "架势不足的那一击无主动格挡减伤；护盾最多30%生命且默认持续2回合，先吸收直接与持续伤害；主动付血绕过护盾与减伤。",
}
RULE_TEXT["G18"] = {
    **RULE_TEXT["G18"],
    "text": "每天最多出发5次，第6次不能出发；取消练习模式。集结取消不扣次数，队伍出发时扣1次。",
    "boundary": "北京时间零点刷新，跨日局归出发日；副本无玩家存档，重启结束未完成局，出发次数与奖励结算仍须去重。",
}
RULE_TEXT["G19"] = {
    **RULE_TEXT["G19"],
    "text": "未过第一夜无奖励；过第一夜后失败得10修为、50灵石、0灵尘；过第二夜后失败得20修为、150灵石、1灵尘。",
    "boundary": "按最终进度给总额，不累计；通关奖励另按G20结算。",
}
RULE_TEXT["G20"] = {
    **RULE_TEXT["G20"],
    "text": "通关每人得30修为、250灵石、1灵尘；每名玩家独立以50%概率获得1枚已开放随机灵根，未命中则无灵根。",
    "boundary": "无首通额外自选灵根；首次开启副本的基础灵根自选仍保留，重复灵根分解得2灵尘。",
}
RULE_TEXT["G33"] = {
    **RULE_TEXT["G33"],
    "text": "每轮先展示姿态与锁定目标；前后段为回合内时序，现实消息速度不影响闪避。强招提前预告，战报只展示结果。",
    "boundary": "已预告招式和目标在当局状态中锁定，不能读当轮回复改招；妖兽每轮每人至多一击，妖王每段每人至多一击。",
}
RULE_TEXT["G24"] = {
    **RULE_TEXT["G24"], "text": "妖兽使用轻招、蓄势、强招、收势的固定循环；妖王按各自两阶段套路执行前后段。",
    "boundary": "山魁只根据上一轮进攻比例选择下一套；霆狱在两轮积痕之后凝雷锁定霆刑；半血不改已公开动作。",
}
RULE_TEXT["G25"] = {
    **RULE_TEXT["G25"],
    "boundary": "部位只在暴露轮可选，同轮效果不叠加；白天自动战处理防御、闪避、回气及低血时的可选部位，不自动消耗灵药绝技。",
}
RULE_TEXT["G32"] = {
    **RULE_TEXT["G32"], "name": "招式套路",
    "text": "动作按已锁定套路执行，替代旧普攻、轻招、强招随机权重及冷却；目标倒地不转移。",
    "boundary": "妖王半血待当前套路和收招结束后切换；二阶段按专属表执行，不再叠加旧全局阶段倍率。",
}
RULES: dict[str, Any] = {
    "days": 2, "nights": 3, "nodes_per_day": 6, "floors": 15,
    "min_party": 1, "max_party": 3, "root_slots": 3,
    "starter_roots": ("R01", "R07", "R13", "R19", "R25"),
    "rewarded_runs_per_day": 3, "practice_mode": False, # 修改了出击奖励次数
    "timezone": "Asia/Shanghai",
    "max_level": 9,
    "experience_thresholds": (0, 40, 100, 180, 280, 400, 540, 700, 880),
    "node_experience": {"camp": 100, "elite": 250, "ruins": 20,
                        "armory": 20, "spring": 20, "event": 20},
    "hp_growth_per_level": 0.14,
    "atk_growth_per_level": 0.12, "defense_growth_per_level": 4.25,
    "stance_max": 3, "dodge_cost": 1, "recover_stance": 2,
    "guard_reduction": 0.70, "damage_reduction_cap": 0.80,
    "shield_cap": 0.30, "shield_default_duration": 2,
    "penetration_cap": 0.50, "defense_down_cap": 0.50,
    "attack_down_cap": 0.30, "part_to_body_ratio": 0.60,
    "potion_start": 1, "potion_cap": 3, "potion_heal": 0.25,
    "potion_after_night": {1: 1, 2: 2}, "spring_heal": 0.40,
    "solo_rebirths": 1, "rebirth_heal": 0.30,
    "rescue_heal": 0.30, "rescue_round_cap": 3,
    "status_duration": 2, "status_stack_cap": 3,
    "burn_tick_atk": 0.10, "poison_tick_atk": 0.10,
    "bleed_tick_atk": 0.08,
    "strong_telegraph_rounds": 1, "strong_cooldown": 2,
    "enemy_hp_party_multipliers": (1.0, 2.35, 3.80),
    "enemy_atk_party_multipliers": (1.0, 1.30, 1.60),
    # Newly proposed test values: level-1 daytime tier baselines need to
    # follow the team's day-2/day-3 growth; night encounters use fixed tiers.
    "day_enemy_scaling": {
        1: {"hp": 1.0, "atk": 1.0, "defense": 0},
        2: {"hp": 1.5, "atk": 1.25, "defense": 15},
    },
    "first_clear_root_choice": False,
    "root_duplicate_dust": 2, "root_exchange_dust": 6,
    "failure_rewards": {0: (0, 0, 0), 1: (10, 30, 0), 2: (20, 60, 1)},
    "clear_rewards": {"cultivation": 30, "stones": 90, "dust": 1,
                      "root_chance": 0.5},
    #我在这里修改了一些奖励配置
    "reference_levels": {"night1": 5, "night2": 8, "boss": 9},
    "target_rounds": {"night1": (10, 14), "night2": (14, 18), "boss": (20, 26)},
    "enemy_weights": {tier: {"normal": weights[0], "light": weights[1], "strong": weights[2]}
                      for tier, weights in {"mob": (60, 30, 10), "elite": (50, 30, 20),
                                            "night1": (40, 35, 25), "night2": (40, 35, 25),
                                            "boss": (30, 30, 40)}.items()},
}
STARTER_ROOTS = RULES["starter_roots"]
OPEN_ROOTS = tuple(ROOTS)


def validate_catalog() -> None:
    """Raise ValueError if the runtime catalog is incomplete or inconsistent."""
    expected = {"C": (CLASSES, 8), "R": (ROOTS, 30), "M": (ENEMIES, 24),
                "B": (BOSSES, 6), "F": (UPGRADES, 28), "E": (EQUIPMENT, 6),
                "N": (EXPLORATION_NODES, 6), "G": (RULE_TEXT, 36)}
    for prefix, (catalog, count) in expected.items():
        ids = {f"{prefix}{number:02d}" for number in range(1, count + 1)}
        if set(catalog) != ids:
            raise ValueError(f"{prefix} IDs mismatch: {set(catalog) ^ ids}")
        for key, entry in catalog.items():
            if entry["id"] != key or not entry["name"]:
                raise ValueError(f"{key}: missing id or name")
    for catalog in (CLASSES, ROOTS, ENEMIES, BOSSES, UPGRADES):
        for key, entry in catalog.items():
            if entry["batch"] not in {"首发", "扩展"}:
                raise ValueError(f"{key}: invalid batch")
            if not entry.get("description"):
                raise ValueError(f"{key}: missing description")
    for key, entry in CLASSES.items():
        if min(entry["base_hp"], entry["base_atk"], entry["base_defense"]) <= 0:
            raise ValueError(f"{key}: invalid base stats")
        if entry["skill_cd"] is not None and entry["skill_cd"] < 1:
            raise ValueError(f"{key}: invalid skill CD")
        if entry["basic_multiplier"] <= 0 or any(entry[field] < 0 for field in (
            "growth_hp", "growth_atk", "growth_hp_quadratic", "growth_atk_quadratic"
        )):
            raise ValueError(f"{key}: invalid combat scaling")
        if not all(entry[field] for field in ("passive", "skill", "ultimate")):
            raise ValueError(f"{key}: incomplete class abilities")
    for catalog in (ROOTS, UPGRADES):
        for key, entry in catalog.items():
            if not entry["effect"] or not entry["limit"] or entry["effects"][0]["id"] != key:
                raise ValueError(f"{key}: invalid effect")
            if not entry["trigger"] or entry["effects"][0]["trigger"] != entry["trigger"]:
                raise ValueError(f"{key}: invalid trigger")
    if {entry["element"] for entry in ROOTS.values()} != {"金", "木", "水", "火", "土"}:
        raise ValueError("root element missing")
    for key, entry in UPGRADES.items():
        references = re.findall(r"R\d\d", entry["roots"])
        if any(ref not in ROOTS for ref in references):
            raise ValueError(f"{key}: unknown root reference")
        classes = re.findall(r"C\d\d", entry["classes"])
        if any(ref not in CLASSES for ref in classes):
            raise ValueError(f"{key}: unknown class reference")
        if not entry["eligible_classes"] or set(entry["eligible_classes"]) - set(CLASSES):
            raise ValueError(f"{key}: invalid eligible classes")
        if set(entry["requires"]) - {
            "damage_basic", "own_skill", "own_skill_direct", "own_main_heal",
            "own_companion", "own_poison", "own_shadow", "own_counter",
            "own_burn", "team_burn", "team_defense_down", "team_shield",
        }:
            raise ValueError(f"{key}: unknown requirement")
    all_references = {**CLASSES, **ROOTS, **ENEMIES, **BOSSES, **UPGRADES, **RULE_TEXT}
    for catalog in (CLASSES, ROOTS, ENEMIES, BOSSES, UPGRADES, RULE_TEXT):
        for key, entry in catalog.items():
            for field, value in entry.items():
                if field == "id" or not isinstance(value, str):
                    continue
                for reference in re.findall(r"[CRMBFG]\d\d", value):
                    if reference not in all_references:
                        raise ValueError(f"{key}.{field}: unknown reference {reference}")
    for catalog in (ENEMIES, BOSSES):
        for key, entry in catalog.items():
            if min(entry["hp"], entry["attack"], entry["defense"]) <= 0:
                raise ValueError(f"{key}: invalid enemy stats")
            if entry["tier"] not in _TIERS or not entry["mechanic"]:
                raise ValueError(f"{key}: invalid tier or mechanic")
            for kind in ("normal", "light", "strong"):
                move = entry["moves"].get(kind)
                if not move or move["multiplier"] <= 0 or move["weight"] <= 0:
                    raise ValueError(f"{key}: missing {kind} move")
                if not move["name"] or move["target"] not in {"random", "two", "lowest_two", "all"}:
                    raise ValueError(f"{key}: invalid {kind} move target")
                if any(effect not in {"burn", "poison", "bleed", "attack_down",
                                      "vulnerable", "thunder_mark"} for effect in move["effects"]):
                    raise ValueError(f"{key}: invalid {kind} move effect")
            if entry["moves"]["strong"]["telegraph_rounds"] != 1:
                raise ValueError(f"{key}: strong move without preview")
            if entry["part"] and entry["part"]["damage_to_body_ratio"] != RULES["part_to_body_ratio"]:
                raise ValueError(f"{key}: part damage mismatch")
            if sum(entry["moves"][kind]["weight"] for kind in ("normal", "light", "strong")) != 100:
                raise ValueError(f"{key}: move weights must total 100")
    for key, entry in BOSSES.items():
        if entry["phase_threshold"] != 0.5 or any(
            entry["phase2_multipliers"][kind] <= 0 for kind in ("normal", "light", "strong")
        ):
            raise ValueError(f"{key}: invalid second phase")
    for key, entry in EQUIPMENT.items():
        if entry["slot"] not in {"weapon", "armor"} or not entry["description"]:
            raise ValueError(f"{key}: invalid equipment")
        if any(entry[field] < 0 for field in ("hp", "atk", "defense")) or not any(
            entry[field] > 0 for field in ("hp", "atk", "defense")
        ):
            raise ValueError(f"{key}: invalid equipment stats")
        if entry["stats"] != {field: entry[field] for field in ("hp", "atk", "defense")}:
            raise ValueError(f"{key}: inconsistent equipment stats")
    for key, entry in EXPLORATION_NODES.items():
        if not entry["description"] or entry["experience"] not in {"combat", "elite", "support"} or not entry["rewards"]:
            raise ValueError(f"{key}: invalid exploration reward")
        if "choice_count" in entry and entry["choice_count"] != 3:
            raise ValueError(f"{key}: expected three choices")
    if set(STARTER_ROOTS) - set(ROOTS):
        raise ValueError("starter roots missing")
    if set(OPEN_ROOTS) != set(ROOTS):
        raise ValueError("not all roots are open")
    if RULES["guard_reduction"] != 0.70 or RULES["damage_reduction_cap"] != 0.80:
        raise ValueError("obsolete guard constants")
    if RULES["rewarded_runs_per_day"] != 3 or RULES["practice_mode"] is not False or RULES["failure_rewards"] != {
        0: (0, 0, 0), 1: (10, 30, 0), 2: (20, 60, 1)
    }:
        raise ValueError("obsolete departure or failure reward rules")
    if RULES["clear_rewards"] != {
        "cultivation": 30, "stones": 90, "dust": 1, "root_chance": 0.5
    } or RULES["root_duplicate_dust"] != 2 or RULES["root_exchange_dust"] != 6:
        raise ValueError("invalid clear or root exchange rules")
    if set(RULES["day_enemy_scaling"]) != {1, 2} or any(
        RULES["day_enemy_scaling"][day][field] <= 0 for day in (1, 2)
        for field in ("hp", "atk")
    ):
        raise ValueError("invalid daytime enemy scaling")
    thresholds = RULES["experience_thresholds"]
    if (len(thresholds) != RULES["max_level"] or thresholds[0] != 0
            or any(b <= a for a, b in zip(thresholds, thresholds[1:]))):
        raise ValueError("invalid experience curve")
    if not (RULES["node_experience"]["elite"] > RULES["node_experience"]["camp"]
            > max(RULES["node_experience"][kind] for kind in ("ruins", "armory", "spring", "event"))):
        raise ValueError("combat experience must reward risk")
    expected_tiers = {"mob": 8, "elite": 4, "night1": 4, "night2": 8}
    if {tier: sum(entry["tier"] == tier for entry in ENEMIES.values())
            for tier in expected_tiers} != expected_tiers:
        raise ValueError("incorrect enemy tier distribution")
    if {entry["kind"] for entry in EXPLORATION_NODES.values()} != {
        "camp", "elite", "ruins", "armory", "spring", "event"
    }:
        raise ValueError("missing exploration kind")


validate_catalog()
