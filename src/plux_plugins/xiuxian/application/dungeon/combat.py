"""Pure current-run combat for 妖祸夜行.

Public operations copy their input. Random choices are made only when creating
the next intent; that intent (including strong-move targets and phase) is part
of the returned JSON state. Additional damage never enters the primary-action
trigger dispatcher. This is the boundary that prevents proc/heal/counter loops.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import random
from typing import Any

from .content import BOSSES, CLASSES, ENEMIES, ROOTS, RULES
from .models import action_type
from .patterns import PART_RULES, PATTERNS


_BASE_ACTIONS = {"attack", "defend", "dodge", "recover", "skill", "ultimate", "potion", "rescue", "part"}
_ARMOR = {key: row["armor"] for key, row in PART_RULES.items() if row.get("armor")}
_PARTS = {key: row["name"] for key, row in PART_RULES.items()}
_ATTACK_DOWN = {"M05": .20, "M10": .15, "M14": .10, "M19": .10}
_DOT_NAMES = {"burn": "灼烧", "poison": "中毒", "bleed": "流血"}


def _r(value: float) -> float:
    return round(max(0.0, float(value)), 4)


def _display(value: float) -> str:
    """Keep a living fraction of HP visibly different from death/zero damage."""
    return "<1" if 0 < value < 1 else f"{value:.0f}"


def _has(member: dict, effect: str) -> bool:
    return effect in member.get("roots", []) or effect in member.get("fortunes", [])


def _alive(unit: dict) -> bool:
    return unit.get("hp", 0) > 0


def _pid(unit: dict) -> str:
    return str(unit.get("player_id", unit.get("id", "enemy")))


def _member(battle: dict, player_id: str | None) -> dict | None:
    return next((m for m in battle["members"] if m["player_id"] == player_id), None)


def _once(member: dict, key: str, round_no: int) -> bool:
    stamps = member.setdefault("triggered", {})
    if stamps.get(key) == round_no:
        return False
    stamps[key] = round_no
    return True


def _limited(member: dict, key: str, limit: int) -> bool:
    counters = member.setdefault("counters", {})
    if counters.get(key, 0) >= limit:
        return False
    counters[key] = counters.get(key, 0) + 1
    return True


def _active(status: dict, round_no: int) -> bool:
    return status.get("starts", 0) <= round_no <= status.get("expires", round_no)


def _buff(member: dict, key: str, round_no: int, consume: bool = False) -> float:
    value = 0.0
    for item in list(member.get("buffs", [])):
        if item["id"] == key and _active(item, round_no):
            value = item["value"]
            if consume:
                member["buffs"].remove(item)
            break
    return value


def _put_buff(member: dict, key: str, value: float, round_no: int,
              duration: int = 2, *, starts: int | None = None) -> None:
    items = member.setdefault("buffs", [])
    items[:] = [s for s in items if s["id"] != key]
    start = round_no if starts is None else starts
    items.append({"id": key, "value": value, "starts": start, "expires": start + duration - 1})


def _debuff(unit: dict, source: str, key: str, kind: str, value: float,
            round_no: int, duration: int = 2, *, once: bool = False,
            starts: int | None = None) -> None:
    items = unit.setdefault("debuffs", [])
    items[:] = [s for s in items if (s["source"], s["id"]) != (source, key)]
    start = round_no if starts is None else starts
    items.append({"source": source, "id": key, "kind": kind, "value": value,
                  "starts": start, "expires": start + duration - 1, "once": once})


def _reduction(unit: dict, kind: str, round_no: int, *, consume: bool = False) -> float:
    totals: dict[str, float] = defaultdict(float)
    for item in list(unit.get("debuffs", [])):
        if item["kind"] == kind and _active(item, round_no):
            totals[item["source"]] += item["value"]
            if consume and item.get("once"):
                unit["debuffs"].remove(item)
    cap = RULES["defense_down_cap"] if kind == "defense_down" else RULES["attack_down_cap"] if kind == "attack_down" else 1.0
    return min(cap, max(totals.values(), default=0.0))


def _attack(unit: dict, round_no: int, *, consume: bool = False) -> float:
    return unit["atk"] * (1 - _reduction(unit, "attack_down", round_no, consume=consume))


def _shield_total(unit: dict, round_no: int) -> float:
    return sum(s["amount"] for s in unit.get("shields", []) if _active(s, round_no))


def _shield(unit: dict, amount: float, source: str, key: str, round_no: int,
            logs: list[str], duration: int = 2) -> float:
    amount = min(amount, max(0, unit["max_hp"] * RULES["shield_cap"] - _shield_total(unit, round_no)))
    if amount <= 0 or not _alive(unit):
        return 0.0
    unit.setdefault("shields", []).append({"amount": _r(amount), "source": source,
                                           "id": key, "starts": round_no,
                                           "expires": round_no + duration - 1})
    logs.append(f"{unit['name']}获得{amount:.0f}护盾。")
    return amount


def potion_heal_amount(member: dict) -> float:
    """Share potion potency between exploration and combat."""
    bonus = (.10 if _has(member, "F07") else 0) + (.10 if _has(member, "F05") else 0)
    return _r(member["max_hp"] * RULES["potion_heal"] * (1 + bonus))


def _heal(battle: dict, target: dict, amount: float, source: dict,
          logs: list[str], *, amplify: bool = True, cap: float | None = None) -> float:
    """A terminal heal: cannot copy, generate overflow, or trigger main-heal effects."""
    if not _alive(target):
        return 0.0
    if amplify and _has(source, "F05"):
        amount *= 1.1
    if cap is not None:
        amount = min(amount, cap)
    actual = _r(min(max(0, target["max_hp"] - target["hp"]), amount))
    target["hp"] = _r(target["hp"] + actual)
    if actual:
        logs.append(f"{target['name']}恢复{_display(actual)}生命。")
        round_no = battle["round"]
        for key, value in (("R14", .10), ("F06", .05)):
            if _has(target, key) and _once(target, key, round_no):
                _put_buff(target, key, value, round_no)
    return actual


def _regen(unit: dict, key: str, source: dict, fraction: float, round_no: int,
           duration: int = 2) -> None:
    # Snapshot the source's healing bonus once, not at every copied/ticked heal.
    _put_buff(unit, key, fraction * (1.1 if _has(source, "F05") else 1), round_no, duration)


def _cleanse(unit: dict) -> str | None:
    counts = Counter(s["kind"] for s in unit.get("dots", []) if s["kind"] in {"burn", "poison"})
    if not counts:
        return None
    kind = "burn" if counts["burn"] >= counts["poison"] else "poison"
    item = min((s for s in unit["dots"] if s["kind"] == kind), key=lambda s: s["remaining"])
    unit["dots"].remove(item)
    return kind


def _add_dot(battle: dict, target: dict, source: dict, kind: str,
             logs: list[str], *, attack: float | None = None) -> None:
    if not _alive(target):
        return
    round_no = battle["round"]
    dots = target.setdefault("dots", [])
    same = [s for s in dots if s["kind"] == kind]
    if len(same) >= RULES["status_stack_cap"]:
        dots.remove(min(same, key=lambda s: s["remaining"]))
    dots.append({"kind": kind, "source": _pid(source),
                 "attack": _r(_attack(source, round_no) if attack is None else attack),
                 "remaining": RULES["status_duration"] + (1 if kind == "poison" and _has(source, "F22") else 0)})
    logs.append(f"{target['name']}附加1层{_DOT_NAMES[kind]}。")
    if kind == "burn" and _has(source, "F14"):
        _debuff(target, _pid(source), "F14", "defense_down", .05, round_no)
    if kind == "poison" and _has(source, "F23") and sum(s["kind"] == kind for s in dots) == RULES["status_stack_cap"]:
        if _once(source, "F23", round_no):
            _debuff(target, _pid(source), "F23", "defense_down", .05, round_no)


def create_member(player_id: str, name: str, class_id: str,
                  roots: list[str] | tuple[str, ...] = ()) -> dict:
    """Create an independent level-one run member; no overworld stats are used."""
    if class_id not in CLASSES:
        raise ValueError("未知副本职业。")
    if len(roots) > 3 or len(set(roots)) != len(roots):
        raise ValueError("最多装配3枚不同灵根。")
    if any(root not in ROOTS for root in roots):
        raise ValueError("未知灵根。")
    stats = CLASSES[class_id]
    hp, atk, defense = stats["base_hp"], stats["base_atk"], stats["base_defense"]
    return {"player_id": str(player_id), "name": name, "class_id": class_id,
            "roots": list(roots), "fortunes": [], "level": 1,
            "max_hp": float(hp), "hp": float(hp), "atk": float(atk), "defense": float(defense),
            "potions": RULES["potion_start"], "down_count": 0, "solo_revive_used": False,
            "equipment_stats": {"hp": 0, "atk": 0, "defense": 0}}


def scale_member(member: dict, level: int) -> dict:
    """Recompute level/equipment stats while preserving the current HP fraction."""
    result = deepcopy(member)
    level = max(1, int(level))
    stats = CLASSES[result["class_id"]]
    hp, atk, defense = stats["base_hp"], stats["base_atk"], stats["base_defense"]
    gear = result.get("equipment_stats", {})
    fraction = result["hp"] / result["max_hp"] if result["max_hp"] else 0
    n = level - 1
    result["max_hp"] = _r(hp * (1 + stats["growth_hp"] * n + stats.get("growth_hp_quadratic", 0) * n * n) + gear.get("hp", 0))
    result["hp"] = _r(result["max_hp"] * fraction)
    result["atk"] = _r(atk * (1 + stats["growth_atk"] * n + stats.get("growth_atk_quadratic", 0) * n * n) + gear.get("atk", 0))
    result["defense"] = _r(defense + stats["growth_defense"] * n + gear.get("defense", 0))
    result["level"] = level
    return result


def _reset_member(member: dict) -> None:
    member.update({"buffs": [], "debuffs": [], "shields": [], "dots": [], "counters": {},
                   "triggered": {}, "resource": 0, "skill_ready_round": 1,
                   "skill_cd_remaining": 0, "ultimate_used": False, "defending": False,
                   "stance": RULES.get("stance_max", 3), "dodge_paid_round": 0,
                   "dodge_segment": None, "dodge_used_round": 0,
                   "rescue_by": None, "rescue_progress": 0, "rescue_round": 0, "thunder_marks": 0,
                   "previous_lost_hp": None, "round_lost_hp": 0.0})
    member.setdefault("fortunes", [])
    member.setdefault("down_count", 0)
    member.setdefault("solo_revive_used", False)
    member.setdefault("potions", 1)


def start_battle(members: list[dict], enemy_id: str, night: int = 1,
                 rng: random.Random | None = None, *, day: int = 1) -> dict:
    if not 1 <= len(members) <= 3:
        raise ValueError("副本队伍必须为1至3人。")
    if len({_pid(m) for m in members}) != len(members):
        raise ValueError("队伍中有重复修士。")
    catalog = BOSSES if enemy_id.startswith("B") else ENEMIES
    if enemy_id not in catalog:
        raise ValueError("未知副本敌人。")
    entry = catalog[enemy_id]
    party = deepcopy(members)
    for member in party:
        _reset_member(member)
    daytime_scale = RULES["day_enemy_scaling"][min(RULES["days"], max(1, int(day)))] if entry["tier"] in {"mob", "elite"} else {"hp": 1, "atk": 1, "defense": 0}
    hp = float(entry["hp"]) * RULES["enemy_hp_party_multipliers"][len(party) - 1] * daytime_scale["hp"]
    enemy = {"id": enemy_id, "name": entry["name"], "max_hp": _r(hp), "hp": _r(hp),
             "atk": _r(float(entry["atk"]) * RULES["enemy_atk_party_multipliers"][len(party) - 1] * daytime_scale["atk"]),
             "defense": float(entry["defense"]) + daytime_scale["defense"], "phase": 1,
             "buffs": [], "debuffs": [], "dots": [], "shields": [],
             "phase_pending": False, "armor_open_start": 0,
             "armor_open_until": 0, "summon_used": False, "sac": None,
             "healing_spent": 0.0,
             "tier": entry["tier"]}
    battle = {"members": party, "enemy": enemy, "round": 1, "night": int(night),
              "outcome": "ongoing", "intent": {}, "previous_offensive_ratio": 0.0,
              "beast_cursor": 0, "day": day, "opening_logs": []}
    for member in party:
        if _has(member, "R25"):
            _shield(member, member["max_hp"] * .08, _pid(member), "R25", 1, battle["opening_logs"])
    if not any(_alive(m) for m in party):
        battle["outcome"] = "defeat"
    else:
        battle["intent"] = _choose_intent(battle, rng or random.Random())
    return battle


def _targets(battle: dict, mode: str, rng: random.Random) -> list[str]:
    alive = [m for m in battle["members"] if _alive(m)]
    if not alive:
        return []
    if mode == "all":
        return [_pid(m) for m in alive]
    if mode == "two":
        return [_pid(m) for m in rng.sample(alive, min(2, len(alive)))]
    if mode == "low_two":
        alive = sorted(alive, key=lambda m: m["hp"] / m["max_hp"])[:2]
    if mode == "marked":
        marked = [m for m in alive if m.get("thunder_marks", 0) >= 2]
        if marked:
            alive = marked
    return [_pid(rng.choice(alive))]


def _hit_targets(move: dict) -> list[str]:
    return list(dict.fromkeys(player_id for segment in ("front", "back")
                              for player_id in (move.get(segment) or {}).get("targets", [])))


def _lock_round(battle: dict, move: dict, rng: random.Random,
                targets: dict[str, list[str]] | None = None) -> None:
    """Bind one data row without consulting actions submitted for this round."""
    shared = targets if targets is not None else {}
    for segment in ("front", "back"):
        hit = move.get(segment)
        if not hit:
            continue
        mode = hit["target"]
        if mode not in shared:
            shared[mode] = _targets(battle, mode, rng)
        hit["targets"] = list(shared[mode])


def _new_pattern(battle: dict, rng: random.Random) -> dict:
    enemy = battle["enemy"]
    data = PATTERNS[enemy["id"]]
    variant = "default"
    if enemy["id"] == "B04" and enemy["phase"] == 2 and not enemy["summon_used"]:
        rows = [deepcopy(data["summon_round"])]
        variant = "summon"
    else:
        rows = data[f"phase{enemy['phase']}"]
        if isinstance(rows, dict):
            aggressive = battle.get("previous_offensive_ratio", 0) > .5
            variant = rng.choices(["quake", "stone"], weights=[2, 1] if aggressive else [1, 2], k=1)[0]
            rows = rows[variant]
        rows = deepcopy(rows)
    if enemy["id"] != "B06":
        shared = {} if enemy["id"].startswith("B") else None
        for move in rows:
            _lock_round(battle, move, rng, shared)
    return {"phase": enemy["phase"], "variant": variant, "index": 0, "rounds": rows}


def _choose_intent(battle: dict, rng: random.Random) -> dict:
    """Expose the next immutable row from a committed routine instance."""
    enemy, round_no = battle["enemy"], battle["round"]
    pattern = battle.get("pattern")
    if pattern is None or pattern["index"] >= len(pattern["rounds"]):
        pattern = battle["pattern"] = _new_pattern(battle, rng)
    index, rows = pattern["index"], pattern["rounds"]
    move = rows[index]
    if enemy["id"] == "B06":
        if move.get("mark_setup"):
            _lock_round(battle, move, rng)
        elif move.get("lock_thunder"):
            main = _targets(battle, "marked", rng)
            marked = bool(main and (_member(battle, main[0]) or {}).get("thunder_marks", 0) >= 2)
            pattern["marked_snapshot"] = marked
            for future in rows[index:]:
                _lock_round(battle, future, rng, {"main": main})
                for segment in ("front", "back"):
                    hit = future.get(segment)
                    if hit and marked and "marked_multiplier" in hit:
                        hit["multiplier"] = hit["marked_multiplier"]
            move["targets"] = main
    intent = deepcopy(move)
    intent.update(phase=pattern["phase"], round=round_no,
                  kind="summon" if move.get("summon") else "charge" if move.get("charge") or move.get("lock_thunder")
                  else "strong" if move.get("strong") else "recovery" if move.get("recovery") else "light")
    intent["targets"] = _hit_targets(move) or list(move.get("targets", []))
    if move.get("charge"):
        announced = next((row for row in rows[index + 1:] if row.get("strong")), None)
        if announced:
            intent["targets"] = _hit_targets(announced)
    if index + 1 < len(rows) and rows[index + 1].get("strong"):
        intent["next_targets"] = _hit_targets(rows[index + 1])
    if move.get("expose", 0):
        _put_buff(enemy, "exposed", move["expose"], round_no, duration=1)
    return intent


def _advance_pattern(battle: dict, rng: random.Random, logs: list[str]) -> None:
    pattern, enemy = battle["pattern"], battle["enemy"]
    pattern["index"] += 1
    if pattern["index"] >= len(pattern["rounds"]) and enemy.get("phase_pending"):
        enemy["phase"], enemy["phase_pending"] = 2, False
        logs.append(f"{enemy['name']}进入第二阶段。")
    battle["intent"] = _choose_intent(battle, rng)


def commit_action(battle: dict, actor_id: str, action: dict | str) -> None:
    """Reserve a valid dodge once inside the service's submission transaction.

    Round resolution also accepts unreserved actions for pure-engine callers.
    The paid marker belongs to the battle, never to untrusted command data.
    """
    error = validate_action(battle, actor_id, action)
    if error:
        raise ValueError(error)
    if action_type(action) != "dodge":
        return
    actor = _member(battle, actor_id)
    if actor.get("dodge_paid_round") != battle["round"]:
        actor["stance"] -= RULES.get("dodge_cost", 1)
        actor["dodge_paid_round"] = battle["round"]
        actor["dodge_segment"] = action["segment"]


def validate_action(battle: dict, actor_id: str, action: dict | str) -> str | None:
    if battle.get("outcome") != "ongoing":
        return "本场战斗已结束。"
    actor = _member(battle, actor_id)
    if actor is None:
        return "你不在这支副本队伍中。"
    if not _alive(actor):
        return "倒地期间无法行动，等待队友救援。"
    kind = action_type(action)
    if kind not in _BASE_ACTIONS:
        return "未知行动，可用普攻、防御、闪避、回气、技能、绝技、灵药、救援、部位。"
    data = action if isinstance(action, dict) else {}
    target = data.get("target")
    enemy_target = data.get("enemy_target")
    reserved = actor.get("dodge_paid_round") == battle["round"]
    if reserved and (kind != "dodge" or data.get("segment") != actor.get("dodge_segment")):
        return "本回合闪避已经提交。"
    if kind == "dodge":
        if data.get("segment") not in {"front", "back"}:
            return "请选择闪避前段或闪避后段。"
        if not reserved and actor.get("stance", 0) < RULES.get("dodge_cost", 1):
            return "架势不足，闪避需要1点架势，请重新选择行动。"
    elif "segment" in data:
        return "只有闪避需要指定前段或后段。"
    if enemy_target is not None:
        if actor["class_id"] != "C04" or kind not in {"attack", "skill", "ultimate"}:
            return "只有丹修主治疗行动可以额外指定过量伤害目标。"
        if enemy_target not in {"enemy", battle["enemy"]["id"], "sac", "毒囊"}:
            return "过量伤害目标应为本体或毒囊。"
    if kind == "skill":
        if actor["class_id"] == "C06":
            return "影修没有主动技能，可用普攻、防御、闪避、回气、灵药、绝技或救援。"
        if battle["round"] < actor.get("skill_ready_round", 1):
            return f"主动技能还需冷却{actor['skill_ready_round'] - battle['round']}个完整回合。"
    if kind == "ultimate" and (battle["night"] <= 0 or actor.get("ultimate_used")):
        return "绝技仅夜战可用，每场夜战一次。"
    if kind == "potion":
        if actor["potions"] <= 0:
            return "没有灵药了。"
        if target not in {None, "", actor_id, "self", "自己"}:
            return "灵药只能自己使用。"
    if kind in {"defend", "dodge", "recover"} and target not in {None, "", actor_id, "self", "自己"}:
        return "防御、闪避和回气只作用于自己，请勿指定其他目标。"
    if kind == "rescue":
        friend = _member(battle, target)
        if friend is None or friend is actor or _alive(friend):
            return "请选择一名倒地队友救援。"
    if kind == "part" and battle["enemy"]["id"] not in _PARTS:
        return "这个敌人没有可攻击的部位。"
    if kind == "part" and not battle["intent"].get("part"):
        return "当前部位未暴露，请重新选择行动。"
    if kind == "part" and target not in {None, "", _PARTS.get(battle["enemy"]["id"])}:
        return f"当前可攻击部位为{_PARTS[battle['enemy']['id']]}。"
    is_heal = actor["class_id"] == "C04" and kind in {"attack", "skill"}
    is_shield = actor["class_id"] in {"C02", "C07"} and kind == "skill"
    if (is_heal or is_shield) and target not in {None, "", "self", "自己"}:
        friend = _member(battle, target)
        if friend is None or not _alive(friend):
            return "治疗与护盾只能选择自己或存活队友，倒地队友需要救援。"
    if actor["class_id"] == "C04" and kind == "part":
        return "丹修的普攻是治疗，不能攻击部位。"
    if not is_heal and not is_shield and kind in {"attack", "skill", "ultimate"}:
        allowed = {None, "", "enemy", battle["enemy"]["id"], "sac", "毒囊"}
        if target not in allowed:
            return "请选择敌人或毒囊作为攻击目标。"
        if target in {"sac", "毒囊"} and not _alive(battle["enemy"].get("sac") or {}):
            return "当前没有存活的毒囊。"
    if data.get("enemy_target") in {"sac", "毒囊"} and not _alive(battle["enemy"].get("sac") or {}):
        return "当前没有存活的毒囊。"
    return None


def _enemy_target(battle: dict, action: dict, *, default_lowest: bool = False) -> dict:
    enemy, target = battle["enemy"], action.get("enemy_target", action.get("target"))
    sac = enemy.get("sac")
    if target in {"sac", "毒囊"} and sac and _alive(sac):
        return sac
    if target in {"enemy", enemy["id"]}:
        return enemy
    if default_lowest and sac and _alive(sac):
        return min((enemy, sac), key=lambda e: e["hp"] / e["max_hp"])
    return enemy


def _friend_target(battle: dict, actor: dict, action: dict) -> dict:
    return _member(battle, action.get("target")) or actor


def _damage_amount(battle: dict, target: dict, raw: float, *, penetration: float = 0,
                   direct: bool = True, part: bool = False) -> float:
    round_no = battle["round"]
    if not direct:
        return _r(raw)
    defense = max(0, target["defense"] * (1 - _reduction(target, "defense_down", round_no)))
    defense *= 1 - min(RULES["penetration_cap"], max(0, penetration))
    reduction = 0.0
    if target.get("defending"):
        reduction = RULES["guard_reduction"] + (.05 if _has(target, "F17") else 0)
        reduction += target.get("low_hp_defense", 0)
    if target.get("id") in _ARMOR:
        if not target.get("armor_open_start", 0) <= round_no <= target.get("armor_open_until", 0):
            reduction += _ARMOR[target["id"]]
    amount = raw * 100 / (100 + defense) * (1 - min(RULES["damage_reduction_cap"], reduction))
    amount *= 1 + _buff(target, "exposed", round_no)
    amount *= 1 + _reduction(target, "vulnerable", round_no, consume=True)
    return _r(amount * (RULES["part_to_body_ratio"] if part else 1))


def _on_rescue(battle: dict, actor: dict, target: dict, logs: list[str]) -> None:
    if _has(actor, "R30") and _limited(actor, "R30", 2):
        _shield(actor, actor["max_hp"] * .10, _pid(actor), "R30", battle["round"], logs)
        if target is not actor:
            _shield(target, target["max_hp"] * .10, _pid(actor), "R30", battle["round"], logs)


def _down(battle: dict, member: dict, logs: list[str]) -> None:
    member["hp"] = 0.0
    member["down_count"] += 1
    member["rescue_by"], member["rescue_progress"] = None, 0
    member["shields"] = []
    # A rescuer falling breaks the binding; their old partial progress cannot
    # be inherited by a different rescuer later in the run.
    for friend in battle["members"]:
        if friend.get("rescue_by") == _pid(member):
            friend["rescue_by"], friend["rescue_progress"] = None, 0
    logs.append(f"{member['name']}倒地（本局第{member['down_count']}次）。")
    if len(battle["members"]) == 1 and not member["solo_revive_used"]:
        member["solo_revive_used"] = True
        member["hp"] = _r(member["max_hp"] * RULES["rebirth_heal"])
        member["dots"] = []
        member["debuffs"] = []
        logs.append(f"{member['name']}消耗本局唯一返魂，恢复30%生命。")
        _on_rescue(battle, member, member, logs)


def _apply_damage(battle: dict, target: dict, amount: float, source: dict,
                  logs: list[str], *, label: str, direct: bool = True) -> float:
    if not _alive(target) or amount <= 0:
        return 0.0
    round_no, remaining = battle["round"], amount
    for shield in sorted(target.get("shields", []), key=lambda s: s["expires"]):
        if not _active(shield, round_no):
            continue
        used = min(shield["amount"], remaining)
        shield["amount"] = _r(shield["amount"] - used)
        remaining = _r(remaining - used)
        if remaining <= 0:
            break
    target["shields"] = [s for s in target.get("shields", []) if s["amount"] > 0]
    actual = _r(min(target["hp"], remaining))
    target["hp"] = _r(target["hp"] - actual)
    if target is battle["enemy"] and target["id"].startswith("B") and target["phase"] == 1 and target["hp"] <= target["max_hp"] * .5:
        target["phase_pending"] = True
    if "player_id" in target:
        target["round_lost_hp"] = _r(target.get("round_lost_hp", 0) + actual)
    absorbed = amount - remaining
    suffix = f"，护盾吸收{absorbed:.0f}" if absorbed else ""
    logs.append(f"{source['name']}的{label}对{target['name']}造成{_display(actual)}伤害{suffix}。")
    if not _alive(target):
        if "player_id" in target:
            _down(battle, target, logs)
        else:
            _on_kill(battle, target, source, logs)
    elif "player_id" in target and actual and target["hp"] < target["max_hp"] * .30:
        if _has(target, "R11") and _limited(target, "R11", 1):
            _regen(target, "regen_R11", target, .05, round_no)
    return actual


def _on_kill(battle: dict, target: dict, source: dict, logs: list[str]) -> None:
    if "player_id" not in source or not _has(source, "R24"):
        return
    if not any(s["kind"] == "burn" and s["source"] == _pid(source) for s in target.get("dots", [])):
        return
    if not _limited(source, "R24", 1):
        return
    enemy = battle["enemy"]
    others = [u for u in (enemy, enemy.get("sac")) if u and u is not target and _alive(u)]
    if others:
        _add_dot(battle, min(others, key=lambda u: u["hp"] / u["max_hp"]), source, "burn", logs)
    else:
        _heal(battle, source, source["max_hp"] * .05, source, logs)


def _extra_damage(battle: dict, actor: dict, target: dict, multiplier: float,
                  logs: list[str], label: str, *, counter: bool = False) -> float:
    if not _alive(actor) or not _alive(target):
        return 0.0
    raw = _attack(actor, battle["round"]) * multiplier
    if counter and _has(actor, "F18"):
        raw *= 1.4
    actual = _apply_damage(battle, target, _damage_amount(battle, target, raw), actor, logs, label=label)
    # F19 is the single explicit post-counter exception; it never dispatches
    # a new counter and is capped across both the class and root counter.
    if counter and actual and _has(actor, "F19") and _once(actor, "F19", battle["round"]):
        _add_dot(battle, target, actor, "burn", logs)
    return actual


def _primary_damage(battle: dict, actor: dict, target: dict, multiplier: float,
                    kind: str, logs: list[str], *, part: bool = False,
                    action_bonus: float = 0, penetration_bonus: float = 0) -> float:
    if not _alive(target):
        return 0.0
    round_no = battle["round"]
    burning = any(s["kind"] == "burn" for s in target.get("dots", []))
    bonus = action_bonus
    if _has(actor, "R03") and target["hp"] < target["max_hp"] * .4:
        bonus += .06
    if kind == "skill":
        if _has(actor, "R16") and actor.get("previous_lost_hp") == 0:
            bonus += .10
        if burning and _has(actor, "F15"):
            bonus += .10
    if kind == "attack":
        if _has(actor, "F12") and actor.get("previous_lost_hp") == 0:
            bonus += .15
        if _has(actor, "F20") and _shield_total(actor, round_no) > 0:
            bonus += .10
    penetration = penetration_bonus + (.05 if burning and _has(actor, "R22") else 0)
    raw = _attack(actor, round_no) * multiplier * (1 + bonus)
    amount = _damage_amount(battle, target, raw, penetration=penetration, part=part)
    actual = _apply_damage(battle, target, amount, actor, logs,
                           label={"attack": "普攻", "skill": "主动技能", "ultimate": "绝技"}[kind])
    # Direct hit effects are once-per-source, not recursively called by extras.
    if actual and actor["class_id"] == "C08" and _once(actor, "C08_lifesteal", round_no):
        _heal(battle, actor, actual * .10, actor, logs, cap=actor["max_hp"] * .08)
    if actual and actor["class_id"] == "C03" and burning and _once(actor, "C03_burn", round_no):
        _extra_damage(battle, actor, target, .30, logs, "积符成术")
    if actual and kind == "skill":
        for key in ("R05", "F01"):
            if _has(actor, key):
                _debuff(target, _pid(actor), key, "defense_down", .05, round_no)
        if _has(actor, "R19") and _once(actor, "R19", round_no):
            _add_dot(battle, target, actor, "burn", logs)
        if _has(actor, "F10") and _once(actor, "F10", round_no):
            _extra_damage(battle, actor, target, .25, logs, "回响")
    return actual


def _pay_life(battle: dict, actor: dict, fraction: float, logs: list[str]) -> None:
    loss = min(actor["hp"] * fraction, max(0, actor["hp"] - 1))
    actor["hp"] = _r(actor["hp"] - loss)
    actor["round_lost_hp"] = _r(actor.get("round_lost_hp", 0) + loss)
    logs.append(f"{actor['name']}主动支付{loss:.0f}生命。")
    if loss and _has(actor, "R23"):
        _put_buff(actor, "R23", .12, battle["round"])


def _shadow(actor: dict) -> None:
    old = actor["resource"]
    gain = 1
    if old == 0 and _has(actor, "F28") and _limited(actor, "F28", 2):
        gain += 1
    actor["resource"] = min(2, old + gain)


def _main_heal(battle: dict, actor: dict, action: dict, logs: list[str]) -> float:
    kind, round_no = action_type(action), battle["round"]
    targets = [m for m in battle["members"] if _alive(m)] if kind == "ultimate" else [_friend_target(battle, actor, action)]
    effective: list[tuple[dict, float]] = []
    overflow: list[tuple[dict, float]] = []
    bonus = .10 if _has(actor, "F05") else 0
    if kind == "attack":
        bonus += _buff(actor, "F25", round_no, consume=True)
    low_bonus = _buff(actor, "F26", round_no)
    used_low_bonus = False
    for target in targets:
        low = target["hp"] < target["max_hp"] * .5
        attack = _attack(actor, round_no)
        base = attack * CLASSES["C04"]["basic_multiplier"] if kind == "attack" else target["max_hp"] * (.35 if kind == "ultimate" else .20)
        if low and _once(actor, "C04_help", round_no):
            base += min(target["max_hp"] * .05, attack * CLASSES["C04"].get("low_hp_bonus_atk_cap", .25))
        extra = low_bonus if low and not used_low_bonus else 0
        if extra:
            used_low_bonus = True
        amount = base * (1 + bonus + extra)
        actual = _heal(battle, target, amount, actor, logs, amplify=False)
        effective.append((target, actual))
        overflow.append((target, _r(max(0, amount - actual))))
    if used_low_bonus:
        _buff(actor, "F26", round_no, consume=True)
    if len(targets) == 1 and _has(actor, "R10") and _once(actor, "R10", round_no):
        others = [m for m in battle["members"] if _alive(m) and m is not targets[0]]
        copied = effective[0][1] * (.20 if others else .10)
        recipient = min(others, key=lambda m: m["hp"] / m["max_hp"]) if others else actor
        _heal(battle, recipient, copied, actor, logs, amplify=False)
    if _has(actor, "F25") and any(amount > 0 for _, amount in effective) and _once(actor, "F25", round_no):
        _put_buff(actor, "F25", .15, round_no, duration=1, starts=round_no + 1)
    over = sum(amount for _, amount in overflow)
    if over and _has(actor, "R09") and _once(actor, "R09", round_no):
        recipient, _ = max(overflow, key=lambda pair: pair[1])
        _regen(recipient, "regen_R09", actor, .02, round_no)
    raw = min(over * (1.15 if _has(actor, "F08") else 1), _attack(actor, round_no) * 2)
    if raw:
        logs.append(f"{actor['name']}主治疗产生{over:.0f}过量，锁定{raw:.0f}原始转伤。")
    return _r(raw)


def _rescue(battle: dict, actor: dict, target: dict, logs: list[str]) -> None:
    if target.get("rescue_round") == battle["round"]:
        return
    if target.get("rescue_by") != _pid(actor):
        target["rescue_by"], target["rescue_progress"] = _pid(actor), 0
    target["rescue_round"] = battle["round"]
    target["rescue_progress"] += 1
    required = min(RULES["rescue_round_cap"], max(1, target["down_count"]))
    logs.append(f"{actor['name']}救援{target['name']}：{target['rescue_progress']}/{required}。")
    if target["rescue_progress"] >= required:
        target["hp"] = _r(target["max_hp"] * RULES["rescue_heal"])
        target["rescue_by"], target["rescue_progress"] = None, 0
        target["dots"], target["debuffs"] = [], []
        logs.append(f"{target['name']}被救起，恢复30%生命。")
        _on_rescue(battle, actor, target, logs)


def _prepare_action(battle: dict, actor: dict, action: dict, logs: list[str]) -> dict:
    kind, round_no = action_type(action), battle["round"]
    prepared = {"action": action, "kind": kind, "resource": actor["resource"], "overflow": 0.0}
    if kind == "defend":
        actor["defending"] = True
        actor["low_hp_defense"] = .10 if _has(actor, "R28") and actor["hp"] < actor["max_hp"] * .35 else 0
        if _has(actor, "R02"):
            _put_buff(actor, "R02", .20, round_no)
        if _has(actor, "F11"):
            _cleanse(actor)
        if actor["class_id"] == "C06":
            _shadow(actor)
        if battle["enemy"]["id"] == "B06":
            actor["thunder_marks"] = max(0, actor.get("thunder_marks", 0) - 1)
        logs.append(f"{actor['name']}摆出防御姿态。")
    elif kind == "dodge":
        commit_action(battle, _pid(actor), action)
        logs.append(f"{actor['name']}消耗{RULES.get('dodge_cost', 1)}点架势，准备闪避。")
    elif kind == "recover":
        logs.append(f"{actor['name']}收势回气。")
    elif kind == "potion":
        actor["potions"] -= 1
        _heal(battle, actor, potion_heal_amount(actor), actor, logs, amplify=False)
        if _has(actor, "R07"):
            _regen(actor, "regen_R07", actor, .02, round_no)
        if _has(actor, "R17"):
            _cleanse(actor)
        if actor["class_id"] == "C06":
            _shadow(actor)
    elif kind == "rescue":
        target = _member(battle, action.get("target"))
        if target and not _alive(target):
            _rescue(battle, actor, target, logs)
    elif kind in {"skill", "ultimate", "attack", "part"}:
        if kind == "skill":
            cd = max(1, CLASSES[actor["class_id"]]["skill_cd"] - (1 if _has(actor, "F09") else 0))
            actor["skill_ready_round"] = round_no + cd + 1
        if kind == "ultimate":
            actor["ultimate_used"] = True
        if actor["class_id"] == "C04":
            prepared["overflow"] = _main_heal(battle, actor, action, logs)
        elif kind in {"skill", "ultimate"}:
            class_id = actor["class_id"]
            if class_id in {"C02", "C07"}:
                if kind == "skill":
                    targets = [_friend_target(battle, actor, action)]
                    fraction = .10 + (prepared["resource"] * .05 if class_id == "C02" else 0)
                else:
                    targets = [actor] if class_id == "C02" else [m for m in battle["members"] if _alive(m)]
                    fraction = .20 if class_id == "C02" else .15
                added = sum(_shield(t, t["max_hp"] * fraction, _pid(actor), class_id, round_no, logs) for t in targets)
                if added and kind == "skill" and _has(actor, "R29"):
                    _put_buff(actor, "R29", .10, round_no)
    return prepared


def _beast_assist(battle: dict, target: dict, logs: list[str]) -> None:
    if battle.get("assist_round") == battle["round"] or not _alive(target):
        return
    owners = [m for m in battle["members"] if m["class_id"] == "C05"]
    if not owners:
        return
    start = battle.get("beast_cursor", 0) % len(owners)
    for step in range(len(owners)):
        index = (start + step) % len(owners)
        owner = owners[index]
        if _alive(owner):
            break
    else:
        return
    battle["assist_round"] = battle["round"]
    actual = _extra_damage(battle, owner, target, .45 * (1.2 if _has(owner, "F21") else 1), logs, "灵兽协击")
    if actual:
        for key, fraction in (("R12", .01), ("F24", .02)):
            if _has(owner, key) and _once(owner, key, battle["round"]):
                _heal(battle, owner, owner["max_hp"] * fraction, owner, logs)


def _part_effect(battle: dict, logs: list[str]) -> None:
    enemy, round_no = battle["enemy"], battle["round"]
    if not _alive(enemy) or enemy.get("part_round") == round_no:
        return
    enemy["part_round"] = round_no
    enemy_id = enemy["id"]
    if enemy_id in _ARMOR:
        duration = PART_RULES[enemy_id]["open_rounds"]
        already_open = enemy["armor_open_start"] <= round_no <= enemy["armor_open_until"]
        if not already_open:
            enemy["armor_open_start"] = round_no + 1
        enemy["armor_open_until"] = max(enemy["armor_open_until"], round_no + duration)
        logs.append(f"击破{_PARTS[enemy_id]}，从下回合起破甲{duration}回合。")
    multiplier = PART_RULES[enemy_id].get("strong_multiplier")
    if multiplier is not None:
        pattern = battle["pattern"]
        strong = next((row for row in pattern["rounds"][pattern["index"] + 1:] if row.get("strong")), None)
        if strong:
            for segment in ("front", "back"):
                if strong.get(segment):
                    strong[segment]["multiplier"] = multiplier
            strong["weakened"] = True
            enemy["weakened_until"] = round_no + 1
            logs.append(f"击中{_PARTS[enemy_id]}，{enemy['name']}的蓄势受到削弱。")


def _attack_action(battle: dict, actor: dict, prepared: dict, logs: list[str]) -> None:
    kind, action, round_no = prepared["kind"], prepared["action"], battle["round"]
    if kind not in {"attack", "part", "skill", "ultimate"}:
        return
    target = _enemy_target(battle, action, default_lowest=actor["class_id"] == "C04")
    if actor["class_id"] == "C04":
        if prepared["overflow"] and _alive(target):
            amount = _damage_amount(battle, target, prepared["overflow"])
            dealt = _apply_damage(battle, target, amount, actor, logs, label="过量转伤")
            if dealt and _has(actor, "F26"):
                _put_buff(actor, "F26", .10, round_no, starts=round_no + 1)
        if kind == "attack":
            _beast_assist(battle, target, logs)
        _after_cast(battle, actor, kind, logs)
        return
    class_id = actor["class_id"]
    if kind in {"attack", "part"}:
        multiplier = CLASSES[class_id]["basic_multiplier"]
        penetration = _buff(actor, "R01", round_no, consume=True)
        bonus = sum(_buff(actor, key, round_no, consume=True) for key in ("R02", "R23", "R14", "F06"))
        extras = [("厚载", _buff(actor, "R29", round_no, consume=True)),
                  ("隐刃", _buff(actor, "F27", round_no, consume=True))]
        shadow = class_id == "C06" and actor["resource"] > 0
        if shadow:
            actor["resource"] -= 1
            extras.append(("藏影", .25))
        if _has(actor, "R04") and _limited(actor, "R04", 1):
            extras.append(("惊鸿", .30))
        was_reduced = _reduction(target, "defense_down", round_no) > 0
        actual = _primary_damage(battle, actor, target, multiplier, "attack", logs,
                                 part=kind == "part", action_bonus=bonus, penetration_bonus=penetration)
        if actual:
            if kind == "part":
                _part_effect(battle, logs)
            if class_id in {"C01", "C03"}:
                actor["resource"] = min(3, actor["resource"] + 1)
            if class_id == "C07":
                _shield(actor, actor["max_hp"] * .05, _pid(actor), "C07_basic", round_no, logs)
            if _has(actor, "F02"):
                actor["counters"]["F02_basic"] = actor["counters"].get("F02_basic", 0) + 1
                if actor["counters"]["F02_basic"] >= 2:
                    actor["counters"]["F02_basic"] = 0
                    _put_buff(actor, "F02", .20, round_no, duration=10000)
            if was_reduced and _has(actor, "F03"):
                _heal(battle, actor, actor["max_hp"] * .02, actor, logs)
            if _has(actor, "F04"):
                _add_dot(battle, target, actor, "bleed", logs)
            if _has(actor, "R06"):
                actor["triggered"]["R06"] = round_no
            for label, extra in extras:
                if extra:
                    _extra_damage(battle, actor, target, extra * (RULES["part_to_body_ratio"] if kind == "part" else 1), logs, label)
            if shadow and _has(actor, "F27"):
                _put_buff(actor, "F27", .15, round_no)
            _beast_assist(battle, target, logs)
        return
    resource = prepared["resource"]
    low_at_cast = actor["hp"] < actor["max_hp"] * .5
    if class_id == "C08":
        _pay_life(battle, actor, .10 if kind == "skill" else .20, logs)
    targets = [target]
    if kind == "ultimate" and class_id in {"C03", "C05", "C07", "C08"} or kind == "skill" and class_id == "C07":
        targets = [battle["enemy"]]
        if _alive(battle["enemy"].get("sac") or {}):
            targets.append(battle["enemy"]["sac"])
    multiplier = {
        "C01": 1.4 + min(resource, 2) * .15 if kind == "skill" else 2 + resource * .25,
        "C02": 1.2 if kind == "skill" else 1.8 + resource * .40,
        "C03": 1.2 + min(resource, 2) * .15 if kind == "skill" else 1.6 + resource * .20,
        "C05": 1.5 if kind == "skill" else 1.7,
        "C06": 2.2 + resource * .40,
        "C07": .9 if kind == "skill" else 1.5,
        "C08": 1.8 if kind == "skill" else 2.0,
    }[class_id]
    bonus = sum(_buff(actor, key, round_no, consume=True) for key in ("R02", "R23"))
    if kind == "skill":
        bonus += _buff(actor, "F02", round_no, consume=True)
        if low_at_cast and _has(actor, "R20"):
            bonus += .10
    if class_id == "C06" and target["hp"] < target["max_hp"] * .30:
        multiplier *= 1.20
    if class_id in {"C01", "C03"}:
        actor["resource"] = max(0, resource - 2) if kind == "skill" else 0
    if class_id in {"C02", "C06"}:
        actor["resource"] = 0
    for victim in targets:
        actual = _primary_damage(battle, actor, victim, multiplier, kind, logs, action_bonus=bonus)
        if not actual:
            continue
        if class_id == "C01":
            if kind == "ultimate" or resource >= 2:
                _debuff(victim, _pid(actor), "C01_" + kind, "defense_down", .15 if kind == "ultimate" else .10, round_no)
        if class_id in {"C03", "C05", "C08"}:
            _add_dot(battle, victim, actor, {"C03": "burn", "C05": "poison", "C08": "bleed"}[class_id], logs)
    _after_cast(battle, actor, kind, logs)


def _after_cast(battle: dict, actor: dict, kind: str, logs: list[str]) -> None:
    if kind not in {"skill", "ultimate"}:
        return
    if _has(actor, "R01"):
        _put_buff(actor, "R01", .10, battle["round"])
    if kind == "ultimate" and _has(actor, "R18"):
        _heal(battle, actor, actor["max_hp"] * .05, actor, logs)
        _cleanse(actor)


def _enemy_action(battle: dict, actions: dict[str, dict], logs: list[str]) -> None:
    enemy, intent, round_no = battle["enemy"], battle["intent"], battle["round"]
    if not _alive(enemy):
        return
    if intent.get("summon"):
        hp = _r(enemy["max_hp"] * .10)
        enemy["summon_used"] = True
        enemy["sac"] = {"id": "sac", "name": "毒囊", "hp": hp, "max_hp": hp,
                        "atk": 0.0, "defense": enemy["defense"], "buffs": [], "debuffs": [], "dots": [], "shields": []}
        logs.append(f"{enemy['name']}召出唯一毒囊（{hp:.0f}生命），本回合不攻击。")
        return
    if not any(intent.get(segment) for segment in ("front", "back")):
        logs.append(f"{enemy['name']}保持{intent['name']}的姿态。")
        return
    attack = _attack(enemy, round_no, consume=True)
    # All segments use one attack snapshot; a defensive response in the first
    # segment weakens the next enemy action, never later allies in this action.
    for segment in ("front", "back"):
        hit = intent.get(segment)
        if not hit:
            continue
        for player_id in hit["targets"]:
            if not _alive(enemy):
                return
            target = _member(battle, player_id)
            if target is None or not _alive(target):
                logs.append(f"{intent['name']}对已倒地目标落空。")
                continue
            if (target.get("dodge_paid_round") == round_no and target.get("dodge_segment") == segment
                    and target.get("dodge_used_round") != round_no):
                target["dodge_used_round"] = round_no
                logs.append(f"{target['name']}避开了{enemy['name']}的{intent['name']}。")
                continue
            multiplier = hit["multiplier"]
            if player_id in actions and _offensive(target, actions[player_id]):
                multiplier = hit.get("offensive_multiplier", multiplier)
            guarding, blocked = target.get("defending", False), False
            if guarding:
                cost = 2 if hit["weight"] == "heavy" else 1
                before = target["stance"]
                blocked = before >= cost
                target["stance"] = max(0, before - cost)
                logs.append(f"{target['name']}架势{before}→{target['stance']}。")
            target["defending"] = blocked
            amount = _damage_amount(battle, target, attack * multiplier)
            target["defending"] = guarding
            _apply_damage(battle, target, amount, enemy, logs, label=intent["name"])
            if hit.get("clear_marks"):
                target["thunder_marks"] = 0
            if blocked and intent.get("strong") and enemy["id"] == "M09":
                _put_buff(enemy, "exposed", .15, round_no, duration=1, starts=round_no + 1)
            if not _alive(target):
                continue
            for effect in hit.get("effects", []):
                if effect in _DOT_NAMES:
                    _add_dot(battle, target, enemy, effect, logs, attack=attack)
                elif effect == "attack_down":
                    _debuff(target, enemy["id"], "enemy_attack_down", "attack_down", _ATTACK_DOWN[enemy["id"]],
                            round_no, starts=round_no + 1)
                elif effect == "vulnerable":
                    _debuff(target, enemy["id"], "enemy_vulnerable", "vulnerable", .15, round_no,
                            once=True, starts=round_no + 1)
                elif effect == "thunder":
                    target["thunder_marks"] = min(2, target.get("thunder_marks", 0) + 1)
            if blocked:
                if target["class_id"] == "C02" and _once(target, "C02_counter", round_no):
                    target["resource"] = min(2, target["resource"] + 1)
                    _extra_damage(battle, target, enemy, .50, logs, "不屈战意反击", counter=True)
                if _has(target, "R26") and _once(target, "R26", round_no):
                    _extra_damage(battle, target, enemy, .15, logs, "震岳反击", counter=True)
                if (_has(target, "R13") and target["class_id"] != "C06" and _once(target, "R13", round_no)
                        and _limited(target, "R13", 2)):
                    target["skill_ready_round"] = max(round_no + 1, target["skill_ready_round"] - 1)
                if _has(target, "R15") and _once(target, "R15", round_no):
                    _debuff(enemy, player_id, "R15", "attack_down", .10, round_no,
                            once=True, starts=round_no + 1)


def _offensive(actor: dict, action: dict) -> bool:
    return actor["class_id"] != "C04" and action_type(action) in {"attack", "part", "skill", "ultimate"}


def _source(battle: dict, source_id: str) -> dict:
    return _member(battle, source_id) or battle["enemy"]


def _end_effects(battle: dict, logs: list[str]) -> None:
    round_no, enemy = battle["round"], battle["enemy"]
    units = [enemy] + ([enemy["sac"]] if enemy.get("sac") else []) + battle["members"]
    for unit in units:
        if not _alive(unit):
            continue
        for dot in list(unit.get("dots", [])):
            if dot not in unit["dots"] or not _alive(unit):
                continue
            source = _source(battle, dot["source"])
            multiplier = RULES[dot["kind"] + "_tick_atk"]
            if dot["kind"] == "burn" and _has(source, "F13"):
                multiplier *= 1.2
            if dot["kind"] == "bleed" and source.get("triggered", {}).get("R06") == round_no:
                multiplier *= 1.3
            actual = _apply_damage(battle, unit, _r(dot["attack"] * multiplier), source, logs,
                                   label=_DOT_NAMES[dot["kind"]], direct=False)
            if actual and _alive(source):
                if dot["kind"] == "poison" and _has(source, "R08") and _once(source, "R08", round_no):
                    _heal(battle, source, source["max_hp"] * .02, source, logs)
                if dot["kind"] == "burn" and _has(source, "F16") and _once(source, "F16", round_no):
                    used = source.setdefault("counters", {}).get("F16_shield", 0.0)
                    allowance = source["max_hp"] * .08 - used
                    if allowance > 0:
                        added = _shield(source, min(source["max_hp"] * .02, allowance), _pid(source), "F16", round_no, logs)
                        if added:
                            source["counters"]["F16_shield"] = _r(used + added)
            dot["remaining"] -= 1
            if dot["remaining"] <= 0 and dot in unit["dots"]:
                unit["dots"].remove(dot)
                if dot["kind"] == "burn" and _has(source, "R21") and _once(source, "R21", round_no):
                    _extra_damage(battle, source, unit, .15, logs, "余烬")
    for unit in units:
        if _alive(unit):
            for buff in list(unit.get("buffs", [])):
                if buff["id"].startswith("regen_") and _active(buff, round_no):
                    _heal(battle, unit, unit["max_hp"] * buff["value"], unit, logs, amplify=False)
            expired_shields = [s for s in unit.get("shields", []) if s["expires"] <= round_no]
            leftover = sum(s["amount"] for s in expired_shields)
            if leftover and _has(unit, "R27") and _once(unit, "R27", round_no):
                _heal(battle, unit, leftover * .25, unit, logs, cap=unit["max_hp"] * .05)
        unit["shields"] = [s for s in unit.get("shields", []) if s["expires"] > round_no]
        unit["buffs"] = [s for s in unit.get("buffs", []) if s["expires"] > round_no]
        unit["debuffs"] = [s for s in unit.get("debuffs", []) if s["expires"] > round_no]
    sac = enemy.get("sac")
    if _alive(enemy) and sac and _alive(sac):
        allowance = max(0, enemy["max_hp"] * .15 - enemy["healing_spent"])
        amount = min(enemy["max_hp"] * .03, allowance, enemy["max_hp"] - enemy["hp"])
        enemy["hp"] = _r(enemy["hp"] + amount)
        enemy["healing_spent"] = _r(enemy["healing_spent"] + amount)
        if amount:
            logs.append(f"毒囊为{enemy['name']}恢复{amount:.0f}生命，剩余额度{enemy['max_hp'] * .15 - enemy['healing_spent']:.0f}。")


def _finish(battle: dict, logs: list[str]) -> bool:
    if not _alive(battle["enemy"]):
        battle["outcome"] = "victory"
        for member in battle["members"]:
            if not _alive(member):
                member["hp"] = _r(member["max_hp"] * RULES["rebirth_heal"])
                logs.append(f"战斗胜利，{member['name']}以30%生命复活。")
            member["rescue_by"], member["rescue_progress"] = None, 0
            member["rescue_round"], member["thunder_marks"] = 0, 0
            member["dots"], member["debuffs"] = [], []
            member["buffs"], member["shields"] = [], []
            member["resource"], member["skill_cd_remaining"] = 0, 0
            member["skill_ready_round"] = 1
            if battle["night"] > 0:
                member["potions"] = min(RULES["potion_cap"], member["potions"]
                                        + RULES["potion_after_night"].get(battle["night"], 0))
        logs.append(f"击败{battle['enemy']['name']}！存活修士不自动回血。")
        return True
    if not any(_alive(m) for m in battle["members"]):
        battle["outcome"] = "defeat"
        logs.append("全队倒地，本次副本结束。")
        return True
    return False


def resolve_round(battle: dict, actions: dict[str, dict | str],
                  rng: random.Random | None = None) -> tuple[dict, list[str]]:
    """Resolve exactly one global round, rejecting invalid commands atomically.

    Every living member must submit one action. A revived member waits until
    the next round. Two rescuers cannot both progress the same target.
    """
    if battle.get("outcome") != "ongoing":
        raise ValueError("本场战斗已结束。")
    normalized = {str(key): ({"type": value} if isinstance(value, str) else dict(value)) for key, value in actions.items()}
    living_ids = {_pid(m) for m in battle["members"] if _alive(m)}
    if living_ids - normalized.keys():
        raise ValueError("仍有存活修士未提交行动。")
    if normalized.keys() - living_ids:
        raise ValueError("行动包含非存活队员。")
    rescued: set[str] = set()
    for player_id, action in normalized.items():
        error = validate_action(battle, player_id, action)
        if error:
            raise ValueError(error)
        if action_type(action) == "rescue":
            target = action["target"]
            if target in rescued:
                raise ValueError("同一回合一名倒地修士只能由一名队友救援。")
            rescued.add(target)
    result, logs = deepcopy(battle), []
    round_no, enemy = result["round"], result["enemy"]
    if enemy["id"].startswith("B") and enemy["phase"] == 1 and enemy["hp"] <= enemy["max_hp"] * .5:
        enemy["phase_pending"] = True
    logs.append(f"第{round_no}回合")
    for actor in result["members"]:
        actor["defending"], actor["low_hp_defense"], actor["round_lost_hp"] = False, 0.0, 0.0
    # Categories have fixed priority; entry order only breaks ties inside a
    # category. In particular, an ally rescued this round is not an earlier
    # main-healing target, irrespective of where the rescuer entered.
    prepared: dict[str, dict] = {}
    def priority(actor: dict) -> int:
        kind = action_type(normalized[_pid(actor)])
        if kind == "defend" or actor["class_id"] in {"C02", "C07"} and kind in {"skill", "ultimate"}:
            return 0
        if kind == "potion" or actor["class_id"] == "C04" and kind in {"attack", "skill", "ultimate"}:
            return 1
        return 2 if kind == "rescue" else 3
    support_order = sorted((m for m in result["members"] if _pid(m) in living_ids), key=priority)
    for actor in support_order:
        prepared[_pid(actor)] = _prepare_action(result, actor, normalized[_pid(actor)], logs)
    for actor in result["members"]:
        if _pid(actor) in prepared and _alive(actor):
            _attack_action(result, actor, prepared[_pid(actor)], logs)
            if not _alive(enemy):
                break
    if _finish(result, logs):
        return result, logs
    _enemy_action(result, normalized, logs)
    if _finish(result, logs):
        return result, logs
    _end_effects(result, logs)
    for actor in result["members"]:
        if _alive(actor) and _pid(actor) in normalized and action_type(normalized[_pid(actor)]) == "recover":
            before = actor["stance"]
            actor["stance"] = min(RULES.get("stance_max", 3), before + RULES.get("recover_stance", 2))
            logs.append(f"{actor['name']}架势{before}→{actor['stance']}。")
    if _finish(result, logs):
        return result, logs
    result["previous_offensive_ratio"] = sum(_offensive(m, normalized[_pid(m)]) for m in result["members"] if _pid(m) in living_ids) / len(living_ids)
    for member in result["members"]:
        member["previous_lost_hp"] = member["round_lost_hp"]
        member["skill_cd_remaining"] = max(0, member["skill_ready_round"] - (round_no + 1))
    result["round"] = round_no + 1
    owners = sum(m["class_id"] == "C05" for m in result["members"])
    if owners:
        result["beast_cursor"] = (result["beast_cursor"] + 1) % owners
    _advance_pattern(result, rng or random.Random(), logs)
    return result, logs


def battle_summary(battle: dict) -> str:
    enemy, intent = battle["enemy"], battle.get("intent", {})
    night = battle["night"]
    rows = [f"🌙 第 {night} 夜 · 第 {battle['round']} 回合" if night > 0
            else f"☀️ 白昼 · 第 {battle['round']} 回合",
            "", f"👹 敌方｜{enemy['name']}",
            f"生命 {_display(enemy['hp'])}/{enemy['max_hp']:.0f} HP · 阶段{enemy['phase']}"]
    enemy_effects = [f"{_DOT_NAMES[kind]}{count}层" for kind, count in Counter(s["kind"] for s in enemy.get("dots", [])).items()]
    if _reduction(enemy, "attack_down", battle["round"]):
        enemy_effects.append("攻击受抑")
    if _reduction(enemy, "defense_down", battle["round"]):
        enemy_effects.append("防御削弱")
    if enemy_effects:
        rows.append("状态：" + " · ".join(enemy_effects))
    if enemy["id"] in _PARTS:
        rows.append(f"{_PARTS[enemy['id']]}：{'暴露' if intent.get('part') else '隐没'}")
        if enemy.get("armor_open_start", 0) <= battle["round"] <= enemy.get("armor_open_until", 0):
            rows.append("护甲：破损")
        if enemy.get("weakened_until", 0) >= battle["round"]:
            rows.append("蓄势：已削弱")
    if _buff(enemy, "exposed", battle["round"]):
        rows.append("状态：破绽显露")
    sac = enemy.get("sac")
    if sac and _alive(sac):
        rows.append(f"毒囊：{_display(sac['hp'])}/{sac['max_hp']:.0f} HP")
        rows.append(f"剩余恢复额度：{enemy['max_hp'] * .15 - enemy['healing_spent']:.0f}")

    if battle["outcome"] == "ongoing":
        names = [(_member(battle, p) or {}).get("name", p) for p in intent.get("targets", [])]
        rows.extend(["", intent.get("cue", intent.get("name", "")), f"目标：{'、'.join(names) or '无'}"])
        if intent.get("next_targets"):
            upcoming = [(_member(battle, p) or {}).get("name", p) for p in intent["next_targets"]]
            rows.append(f"蓄势目标：{'、'.join(upcoming)}")

    rows.extend(["", "👥 队员状态"])
    for index, member in enumerate(battle["members"]):
        if index:
            rows.append("")
        rows.append(f"{index + 1}. {member['name']} · {CLASSES[member['class_id']]['name']}")
        if _alive(member):
            rows.append(f"❤️ {_display(member['hp'])}/{member['max_hp']:.0f} HP · 架势{member.get('stance', 0)}/{RULES.get('stance_max', 3)} · 灵药{member['potions']}")
        else:
            required = min(RULES["rescue_round_cap"], max(1, member["down_count"]))
            progress = member.get("rescue_progress", 0)
            rescue = f"救援{progress}/{required}" if progress else f"待救援（需{required}回合）"
            rows.append(f"💀 倒地 · {rescue} · 架势{member.get('stance', 0)}/{RULES.get('stance_max', 3)} · 灵药{member['potions']}")
        shield = _shield_total(member, battle["round"])
        if night <= 0:
            ultimate = "绝技仅夜间"
        elif member.get("ultimate_used"):
            ultimate = "绝技已用"
        else:
            ultimate = "绝技可用" if _alive(member) else "绝技未用"
        cooldown = max(0, member.get('skill_ready_round', 1) - battle['round'])
        if CLASSES[member['class_id']]['skill_cd'] is None:
            skill = "无主动技能"
        else:
            skill = f"技能冷却{cooldown}回合" if cooldown else "技能可用" if _alive(member) else "技能就绪"
        rows.append(f"{skill} · {ultimate}")
        effects = ([f"🛡️ 护盾{shield:.0f}"] if shield else [])
        effects.extend(f"{_DOT_NAMES[kind]}{count}层" for kind, count in Counter(s["kind"] for s in member.get("dots", [])).items())
        if _reduction(member, "attack_down", battle["round"]):
            effects.append("攻击受抑")
        if _reduction(member, "vulnerable", battle["round"]):
            effects.append("易伤")
        if enemy['id'] == 'B06':
            effects.append(f"⚡ 雷痕{member.get('thunder_marks', 0)}")
        if effects:
            rows.append(' · '.join(effects))
    if battle["outcome"] != "ongoing":
        rows.extend(["", "战斗胜利" if battle["outcome"] == "victory" else "战斗失败"])
    return "\n".join(rows)


def _auto_actions(battle: dict) -> dict[str, dict]:
    """Choose using this round's published intent and visible party state."""
    intent, enemy = battle["intent"], battle["enemy"]
    living = [member for member in battle["members"] if _alive(member)]
    actions: dict[str, dict] = {}
    for member in living:
        hits = [(segment, intent[segment]) for segment in ("front", "back")
                if intent.get(segment) and _pid(member) in intent[segment]["targets"]]
        stance = member["stance"]
        if not hits and stance <= 1:
            action = {"type": "recover"}
        elif hits and intent.get("strong"):
            action = {"type": "defend"} if stance >= 2 else (
                {"type": "dodge", "segment": hits[0][0]} if stance else {"type": "recover"})
        else:
            action = {"type": "attack"}
        actions[_pid(member)] = action
    if intent.get("part") and enemy["id"] in {"M07", "M17"}:
        threatened = [m for m in living if _pid(m) in intent["targets"]]
        fighters = [m for m in living if m["class_id"] != "C04"]
        base = {_pid(m): _damage_amount(battle, deepcopy(enemy),
                     _attack(m, battle["round"]) * CLASSES[m["class_id"]]["basic_multiplier"]) for m in fighters}
        candidates = [m for m in fighters if m["stance"] >= 2]
        if sum(base.values()) >= enemy["hp"]:
            for member in fighters:
                actions[_pid(member)] = {"type": "attack"}
        elif candidates and any(m["hp"] < m["max_hp"] * .5 for m in threatened):
            chosen = min(candidates, key=lambda m: base[_pid(m)])
            actions[_pid(chosen)] = {"type": "part", "target": _PARTS[enemy["id"]]}
    return actions


def auto_battle(members: list[dict], enemy_id: str, night: int = 0,
                rng: random.Random | None = None, *, day: int = 1) -> tuple[dict, list[str]]:
    """Daytime policy with finite stance, recovery and optional exposed parts.

    A finite safety bound detects non-progressing custom input; it never awards
    a win on timeout or inserts a hidden damage/enrage mechanic.
    """
    generator = rng or random.Random()
    battle = start_battle(members, enemy_id, night, generator, day=day)
    logs = list(battle.get("opening_logs", []))
    while battle["outcome"] == "ongoing" and battle["round"] <= 200:
        actions = _auto_actions(battle)
        battle, current = resolve_round(battle, actions, generator)
        logs.extend(current)
    if battle["outcome"] == "ongoing":
        raise ValueError("自动战超过200回合仍未结束，请改用手动行动处理当前构筑。")
    return battle, logs
