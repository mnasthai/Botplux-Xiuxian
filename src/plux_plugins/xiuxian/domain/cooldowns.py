"""Shared cultivation cooldown rules for commands, shop checks and cards."""
from datetime import datetime
import math
from .artifact_effects import template_ids


def self_cultivation_interval(base_seconds, inventory, debuffs):
    interval = base_seconds
    ids = template_ids(inventory)
    interval = max(0, interval - (1800 if 'liuguang_suo' in ids else 0)
                   - (300 if 'yufeng_shan' in ids else 0))
    if any(debuff['debuff_kind'] == 'duanmai_san' for debuff in debuffs if 'debuff_kind' in debuff.keys()):
        interval += 3600
    return interval


def self_cultivation_remaining(base_seconds, inventory, debuffs, recent, now):
    interval = self_cultivation_interval(base_seconds, inventory, debuffs)
    if recent is None:
        return 0
    try:
        previous = datetime.fromisoformat(recent['created_at'].replace('Z', '+00:00')).timestamp()
        return max(0, math.ceil(interval - (now - previous)))
    except (TypeError, ValueError, OverflowError, OSError):
        # A malformed timestamp must not make the shop and command disagree.
        return interval
