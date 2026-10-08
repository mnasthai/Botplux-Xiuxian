"""Validate plugin configuration and materialize immutable game rules."""
from __future__ import annotations
from dataclasses import asdict
from typing import Mapping
import re
from plux.api import ConfigurationError
from .domain.config import GameConfig, RealmDropWeights, REALMS, RARITIES
from .presentation.models import plain

def build_rules(raw: Mapping | None = None) -> GameConfig:
    raw = plain(raw or {})
    unknown = set(raw) - set(GameConfig.__dataclass_fields__)
    if unknown:
        raise ConfigurationError("unknown xiuxian rule fields: " + ", ".join(sorted(unknown)))
    values = asdict(GameConfig())
    values.update(raw)
    weights = values["drop_weights"]
    if isinstance(weights, dict):
        if set(weights) != set(REALMS):
            raise ConfigurationError("drop_weights must contain every realm")
        weights = [weights[realm] for realm in REALMS]
    values["drop_weights"] = tuple(RealmDropWeights(**row) for row in weights)
    try:
        return GameConfig(**values)
    except (TypeError, ValueError) as error:
        raise ConfigurationError(str(error)) from error

def validate_config(raw: Mapping) -> dict:
    unknown = set(raw) - {"account", "groups", "admin_ids", "rules"}
    if unknown:
        raise ConfigurationError("unknown xiuxian configuration fields: " + ", ".join(sorted(unknown)))
    account = raw.get("account", "")
    if not isinstance(account, str) or (account and not re.fullmatch(r"[A-Za-z0-9_.@-]{1,256}", account)):
        raise ConfigurationError("xiuxian account must be an internal account ID")
    def ids(field, groups=False):
        values = raw.get(field, ())
        if not isinstance(values, (tuple, list)) or any(
            not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.@-]{1,256}", value)
            or (groups and not value.endswith("@chatroom")) for value in values):
            raise ConfigurationError(field + " must contain internal IDs")
        if len(values) != len(set(values)):
            raise ConfigurationError(field + " contains duplicate IDs")
        return tuple(values)
    groups = ids("groups", True)
    if groups and not account:
        raise ConfigurationError("xiuxian groups require an explicit account")
    rules = raw.get("rules", {})
    if not isinstance(rules, Mapping):
        raise ConfigurationError("xiuxian rules must be a table")
    return {"account": account, "groups": groups, "admin_ids": ids("admin_ids"),
            "rules": asdict(build_rules(rules))}
