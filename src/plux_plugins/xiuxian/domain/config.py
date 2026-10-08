"""Stage-one game rules loaded from an optional TOML file."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib


REALMS = ("qi", "foundation", "core", "nascent")
RARITIES = ("artifact", "spirit", "ancient", "treasure")


@dataclass(frozen=True)
class RealmDropWeights:
    artifact: int
    spirit: int
    ancient: int
    treasure: int

    def __post_init__(self) -> None:
        values = (self.artifact, self.spirit, self.ancient, self.treasure)
        if any(type(value) is not int or value < 0 for value in values) or sum(values) != 100:
            raise ValueError("each realm drop weight must be a non-negative integer total of 100")


@dataclass(frozen=True)
class GameConfig:
    rules_version: int = 2
    duel_enabled: bool = False
    visual_cards_enabled: bool = False
    initial_spirit_stones: int = 100
    cultivation_reward: int = 50
    cultivation_stones_reward: int = 100
    self_cultivation_interval_seconds: int = 3600
    self_cultivation_success_percent: int = 50
    self_cultivation_reward: int = 5
    self_cultivation_loss: int = 2
    duel_loss_cultivation: int = 20
    breakthrough_cultivation: tuple[int, int, int] = (100, 250, 500)
    breakthrough_stones: tuple[int, int, int] = (100, 250, 500)
    exploration_cost: int = 60
    inventory_limit: int = 6
    duel_inventory_limit: int = 32
    daily_duel_limit: int = 100
    pair_daily_duel_limit: int = 100
    daily_invitation_limit: int = 100
    invitation_cooldown_seconds: int = 60
    invitation_timeout_seconds: int = 60
    support_timeout_seconds: int = 30
    turn_timeout_seconds: int = 30
    prompt_timeout_seconds: int = 15
    poll_stall_seconds: int = 5
    dungeon_idle_timeout_seconds: int = 600
    dungeon_round_timeout_seconds: int = 90
    support_minimum: int = 10
    support_maximum: int = 100
    return_values: tuple[int, int, int, int] = (20, 30, 40, 50)
    rare_pool_returns: tuple[int, int] = (40, 50)
    devil_contract_enabled: bool = True
    devil_contract_max_tier: int = 5
    devil_rewards: tuple[int, ...] = (300, 600, 1200, 2400, 4800)
    devil_daily_losses: tuple[int, ...] = (10, 35, 50, 75, 120)
    drop_weights: tuple[RealmDropWeights, RealmDropWeights, RealmDropWeights, RealmDropWeights] = (
        RealmDropWeights(50, 30, 15, 5),
        RealmDropWeights(45, 32, 17, 6),
        RealmDropWeights(38, 35, 20, 7),
        RealmDropWeights(30, 37, 25, 8),
    )

    def __post_init__(self) -> None:
        if type(self.duel_enabled) is not bool:
            raise ValueError("duel_enabled must be a bool")
        if type(self.visual_cards_enabled) is not bool:
            raise ValueError("visual_cards_enabled must be a bool")
        if type(self.self_cultivation_success_percent) is not int or not 0 <= self.self_cultivation_success_percent <= 100:
            raise ValueError("self_cultivation_success_percent must be an integer between 0 and 100")
        scalar_fields = ("rules_version", "initial_spirit_stones", "cultivation_reward",
                         "cultivation_stones_reward", "self_cultivation_interval_seconds",
                         "self_cultivation_reward", "self_cultivation_loss", "duel_loss_cultivation",
                         "exploration_cost", "inventory_limit",
                         "duel_inventory_limit", "daily_duel_limit", "pair_daily_duel_limit",
                         "daily_invitation_limit", "invitation_cooldown_seconds",
                         "invitation_timeout_seconds", "support_timeout_seconds", "turn_timeout_seconds",
                         "prompt_timeout_seconds", "poll_stall_seconds", "support_minimum", "support_maximum",
                         "dungeon_idle_timeout_seconds", "dungeon_round_timeout_seconds")
        if any(type(getattr(self, field)) is not int or getattr(self, field) <= 0 for field in scalar_fields):
            raise ValueError("game numeric limits must be positive integers")
        # if self.duel_inventory_limit > self.inventory_limit or self.pair_daily_duel_limit > self.daily_duel_limit:
        if self.pair_daily_duel_limit > self.daily_duel_limit:
            raise ValueError("duel limits must not exceed their enclosing limits")
        if self.support_minimum > self.support_maximum:
            raise ValueError("support minimum cannot exceed support maximum")
        if self.prompt_timeout_seconds > 600:
            raise ValueError("prompt_timeout_seconds cannot exceed 600")
        for field in ("breakthrough_cultivation", "breakthrough_stones", "return_values", "rare_pool_returns"):
            values = getattr(self, field)
            expected = 3 if field.startswith("breakthrough") else 4 if field == "return_values" else 2
            if not isinstance(values, (tuple, list)) or len(values) != expected or any(
                    type(value) is not int or value <= 0 for value in values):
                raise ValueError(f"{field} must contain {expected} positive integers")
            object.__setattr__(self, field, tuple(values))
        if type(self.devil_contract_enabled) is not bool:
            raise ValueError("devil_contract_enabled must be a bool")
        if type(self.devil_contract_max_tier) is not int or self.devil_contract_max_tier <= 0:
            raise ValueError("devil_contract_max_tier must be a positive integer")
        for field in ("devil_rewards", "devil_daily_losses"):
            values = getattr(self, field)
            if not isinstance(values, (tuple, list)) or len(values) != self.devil_contract_max_tier or any(
                    type(value) is not int or value <= 0 for value in values):
                raise ValueError(f"{field} must contain {self.devil_contract_max_tier} positive integers")
            object.__setattr__(self, field, tuple(values))
        if not isinstance(self.drop_weights, (tuple, list)) or len(self.drop_weights) != len(REALMS) or not all(
                isinstance(item, RealmDropWeights) for item in self.drop_weights):
            raise ValueError("drop_weights must define each realm exactly once")
        object.__setattr__(self, "drop_weights", tuple(self.drop_weights))


DEFAULT_GAME_CONFIG = GameConfig()


def _positive_int(data: dict[str, object], key: str, default: int) -> int:
    value = data.get(key, default)
    if type(value) is not int or value <= 0:
        raise ValueError(f"game.{key} must be a positive integer")
    return value


def _int_tuple(data: dict[str, object], key: str, default: tuple[int, ...]) -> tuple[int, ...]:
    value = data.get(key, list(default))
    if not isinstance(value, list) or len(value) != len(default) or any(type(item) is not int or item <= 0 for item in value):
        raise ValueError(f"game.{key} must be an array of {len(default)} positive integers")
    return tuple(value)


def load_game_config(path: Path | None = None) -> GameConfig:
    """Return immutable defaults, overlaid by a strict ``[game]`` TOML table."""
    if path is None:
        return DEFAULT_GAME_CONFIG
    with Path(path).open("rb") as stream:
        document = tomllib.load(stream)
    if not isinstance(document, dict) or set(document) - {"game"} or not isinstance(document.get("game"), dict):
        raise ValueError("game configuration must contain only a [game] table")
    data = document["game"]
    allowed = {field for field in GameConfig.__dataclass_fields__} | {"drop_weights"}
    unknown = set(data) - allowed
    if unknown:
        raise ValueError(f"unknown game configuration fields: {', '.join(sorted(unknown))}")
    kwargs = {key: _positive_int(data, key, getattr(DEFAULT_GAME_CONFIG, key)) for key in (
        "rules_version", "initial_spirit_stones", "cultivation_reward", "cultivation_stones_reward",
        "self_cultivation_interval_seconds", "self_cultivation_reward", "self_cultivation_loss", "duel_loss_cultivation",
        "exploration_cost", "inventory_limit", "duel_inventory_limit", "daily_duel_limit",
        "pair_daily_duel_limit", "daily_invitation_limit", "invitation_cooldown_seconds",
        "invitation_timeout_seconds", "support_timeout_seconds", "turn_timeout_seconds",
        "prompt_timeout_seconds", "poll_stall_seconds", "support_minimum", "support_maximum",
        "dungeon_idle_timeout_seconds", "dungeon_round_timeout_seconds")}
    kwargs["duel_enabled"] = data.get("duel_enabled", DEFAULT_GAME_CONFIG.duel_enabled)
    kwargs["visual_cards_enabled"] = data.get("visual_cards_enabled", DEFAULT_GAME_CONFIG.visual_cards_enabled)
    kwargs["self_cultivation_success_percent"] = data.get(
        "self_cultivation_success_percent", DEFAULT_GAME_CONFIG.self_cultivation_success_percent)
    kwargs["breakthrough_cultivation"] = _int_tuple(data, "breakthrough_cultivation", DEFAULT_GAME_CONFIG.breakthrough_cultivation)
    kwargs["breakthrough_stones"] = _int_tuple(data, "breakthrough_stones", DEFAULT_GAME_CONFIG.breakthrough_stones)
    kwargs["return_values"] = _int_tuple(data, "return_values", DEFAULT_GAME_CONFIG.return_values)
    kwargs["rare_pool_returns"] = _int_tuple(data, "rare_pool_returns", DEFAULT_GAME_CONFIG.rare_pool_returns)
    kwargs["devil_contract_enabled"] = data.get("devil_contract_enabled", DEFAULT_GAME_CONFIG.devil_contract_enabled)
    kwargs["devil_contract_max_tier"] = data.get("devil_contract_max_tier", DEFAULT_GAME_CONFIG.devil_contract_max_tier)
    kwargs["devil_rewards"] = _int_tuple(data, "devil_rewards", DEFAULT_GAME_CONFIG.devil_rewards)
    kwargs["devil_daily_losses"] = _int_tuple(data, "devil_daily_losses", DEFAULT_GAME_CONFIG.devil_daily_losses)
    raw_weights = data.get("drop_weights")
    if raw_weights is None:
        kwargs["drop_weights"] = DEFAULT_GAME_CONFIG.drop_weights
    else:
        if not isinstance(raw_weights, dict) or set(raw_weights) != set(REALMS):
            raise ValueError("game.drop_weights must define qi, foundation, core, and nascent")
        weights = []
        for realm in REALMS:
            row = raw_weights[realm]
            if not isinstance(row, dict) or set(row) != set(RARITIES):
                raise ValueError(f"game.drop_weights.{realm} must define all rarities")
            try:
                weights.append(RealmDropWeights(**row))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid game.drop_weights.{realm}") from exc
        kwargs["drop_weights"] = tuple(weights)
    return GameConfig(**kwargs)
