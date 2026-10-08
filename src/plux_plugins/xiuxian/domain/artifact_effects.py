"""Bounded, inventory-derived artifact bonuses shared by commands and cards."""

import math


def template_ids(inventory):
    """Duplicate copies of one template never multiply a passive."""
    return {value for item in inventory if (value := dict(item).get('template_id'))}


def breakthrough_costs(base_cultivation, base_stones, inventory):
    ids = template_ids(inventory)
    cultivation = math.floor(base_cultivation * 0.9) if 'xuanyuan_jing' in ids else base_cultivation
    stones = math.floor(base_stones * 0.9) if 'fuhai_yin' in ids else base_stones
    return cultivation, stones


def exploration_cost(base_cost, inventory):
    return max(1, base_cost - (10 if 'xunling_pan' in template_ids(inventory) else 0))


def daily_cultivation_rewards(base_cultivation, base_stones, inventory):
    """Return rewards and applied labels; percent effects use the configured base."""
    ids = template_ids(inventory)
    cultivation, stones, notes = base_cultivation, base_stones, []
    if 'shanhe_tu' in ids:
        cultivation += round(base_cultivation * 0.20)
        stones += round(base_stones * 0.20)
        notes.append('山河图【洞天福地】+20%修为/灵石')
    if 'tongtian_bei' in ids:
        cultivation += round(base_cultivation * 0.20)
        notes.append('通天碑【悟道】+20%修为')
    for key, gain_cult, gain_stones, label in (
        ('juqi_hulu', 2, 10, '聚气葫芦【纳灵】+2修为/+10灵石'),
        ('zijin_bo', 5, 20, '紫金钵【聚灵】+5修为/+20灵石'),
        ('huoyun_pei', 3, 0, '火云佩【温养】+3修为'),
        ('qinglian_deng', 8, 0, '青莲灯【养元】+8修为'),
    ):
        if key in ids:
            cultivation += gain_cult
            stones += gain_stones
            notes.append(label)
    return cultivation, stones, notes


def self_cultivation_success_percent(base_percent, inventory):
    ids = template_ids(inventory)
    return max(base_percent, min(95, base_percent + (15 if 'xuantie_yin' in ids else 0)
                                + (8 if 'huixin_yu' in ids else 0)))


def self_cultivation_reward(base_reward, inventory):
    return base_reward + (4 if 'hongmeng_zhu' in template_ids(inventory) else 0)
