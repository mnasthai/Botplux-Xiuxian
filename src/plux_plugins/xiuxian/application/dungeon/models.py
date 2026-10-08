"""JSON-compatible current-run state for the independent dungeon engine.

The service controls the run's lifetime. State contains no Python instances,
timers, or random-number generators and makes no restart-recovery promise.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict


ActionType = Literal["attack", "defend", "dodge", "recover", "skill", "ultimate", "potion", "rescue", "part"]
Outcome = Literal["ongoing", "victory", "defeat"]


class Action(TypedDict, total=False):
    type: ActionType
    kind: ActionType
    target: str
    enemy_target: str
    segment: Literal["front", "back"]


class Member(TypedDict, total=False):
    player_id: str
    name: str
    class_id: str
    roots: list[str]
    fortunes: list[str]
    level: int
    max_hp: float
    hp: float
    atk: float
    defense: float
    potions: int
    down_count: int
    solo_revive_used: bool
    equipment_stats: dict[str, float]
    buffs: list[dict[str, Any]]
    debuffs: list[dict[str, Any]]
    shields: list[dict[str, Any]]
    dots: list[dict[str, Any]]
    counters: dict[str, int | float]
    resource: int
    stance: int
    dodge_paid_round: int
    dodge_segment: Literal["front", "back"] | None
    skill_ready_round: int
    skill_cd_remaining: int
    ultimate_used: bool
    rescue_by: str | None
    rescue_progress: int


class Battle(TypedDict, total=False):
    members: list[Member]
    enemy: dict[str, Any]
    round: int
    night: int
    outcome: Outcome
    intent: dict[str, Any]
    previous_offensive_ratio: float
    beast_cursor: int
    pattern: dict[str, Any]


ACTION_ALIASES = {
    "攻击": "attack", "普攻": "attack", "防御": "defend",
    "技能": "skill", "绝技": "ultimate", "灵药": "potion",
    "用药": "potion", "救援": "rescue", "部位": "part",
    "闪避": "dodge", "回气": "recover",
}


def action_type(action: dict[str, Any] | str) -> str:
    value = action if isinstance(action, str) else action.get("type", action.get("kind", "attack"))
    return ACTION_ALIASES.get(str(value), str(value))
