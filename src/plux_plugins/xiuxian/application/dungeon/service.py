"""Dungeon command orchestration inside the Plux unit of work."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import random
import string

from ...presentation.models import Reply
from ...persistence.game import GameRepository
from ...persistence import dungeon as repo
from . import combat, render
from .content import (BOSSES, CLASSES, ENEMIES, EQUIPMENT, EXPLORATION_NODES,
                      ROOTS, UPGRADES, RULES, STARTER_ROOTS, available_upgrades)


KINDS = frozenset({
    'dungeon_help', 'dungeon_status', 'dungeon_create', 'dungeon_join',
    'dungeon_ready', 'dungeon_start', 'dungeon_leave', 'dungeon_choose',
    'dungeon_loot', 'dungeon_act', 'dungeon_class', 'dungeon_roots',
    'dungeon_root_claim', 'dungeon_root_equip', 'dungeon_root_remove',
    'dungeon_root_exchange',
})
_BEIJING = timezone(timedelta(hours=8))
_ACTION_NAMES = {'普攻': 'attack', '进攻': 'attack', '攻击': 'attack', '技能': 'skill',
                 '绝技': 'ultimate', '防御': 'defend', '灵药': 'potion',
                 '救援': 'rescue', '部位': 'part', '闪避': 'dodge', '回气': 'recover'}


def _today(now):
    return datetime.fromtimestamp(now, _BEIJING).date().isoformat()


def _idle_label(ctx):
    seconds = getattr(ctx.game_config, 'dungeon_idle_timeout_seconds', 600)
    return f'{seconds // 60} 分钟' if seconds % 60 == 0 else f'{seconds} 秒'


def _message_id(message):
    value = getattr(message, 'message_id_candidate', None)
    if (isinstance(value, str) and value.isascii() and value.isdecimal()
            and len(value) <= 20 and 0 < int(value) < 2**64):
        return str(int(value))
    event_key = getattr(message, 'event_key', None)
    return event_key if isinstance(event_key, str) and 0 < len(event_key) <= 512 else None


def _find(catalog, value):
    value = (value or '').strip()
    if value.upper() in catalog:
        return value.upper()
    return next((key for key, item in catalog.items() if item['name'] == value), None)


def _member(state, player):
    return next((m for m in state['members'] if m['player_id'] == player), None)


def _token(state):
    state['version'] += 1
    state['token'] = f"{state['code']}.{state['version']}"


def _sample(rng, values, count):
    values = list(values)
    if len(values) <= count:
        rng.shuffle(values)
        return values
    return rng.sample(values, count)


def _save(ctx, run, state, last_progress=None, deadline=None):
    old = repo.load_state(ctx.store, run['run_id'])
    repo.save_state(ctx.store, run['run_id'], state,
                    last_progress if last_progress is not None else (old[1] if old else ctx.now),
                    deadline)


def _notice(ctx, run, text, event):
    return Reply(text, target_id=run['group_id'],
                 request_key=f"dungeon:{run['run_id']}:{event}")


def _player_name(ctx, group, player):
    row = GameRepository(ctx.store, ctx.account_id, group, player).player()
    return row['dao_name'] if row else '修士'


def _has_pvp(ctx, group, player):
    game = GameRepository(ctx.store, ctx.account_id, group, player)
    if game.duel(include_invitation=True):
        return True
    return game.get_active_pvp_duel_for_player(player) is not None


def _collect_members(ctx, run):
    return ctx.store.execute('''SELECT * FROM dungeon_run_members
        WHERE run_id=? ORDER BY rowid''', (run['run_id'],)).fetchall()


def _finish(ctx, run, state, reason, rng, *, victory=False):
    """Single settlement gate for failure, victory, timeout and restart."""
    changed = ctx.store.execute('''UPDATE dungeon_runs SET state='finished',reason=?,
        highest_night=?,settled_at=? WHERE run_id=? AND state IN ('gathering','active')''',
        (reason, state.get('highest_night', 0), ctx.now, run['run_id']))
    if changed.rowcount != 1:
        return None
    highest = 3 if victory else state.get('highest_night', 0)
    if highest == 3:
        clear = RULES['clear_rewards']
        cult, stones, dust = (clear['cultivation'], clear['stones'], clear['dust'])
    else:
        cult, stones, dust = RULES['failure_rewards'][highest]
    progress = f'最高通过第 {highest} 夜' if highest else '尚未通过第一夜'
    lines = [f"🏯 妖祸夜行｜队伍 {run['code']}\n"
             f"{'🏆' if victory else '📜'} 副本结束 · {reason}\n{progress}\n\n🎁 本局收获"]
    for member in _collect_members(ctx, run):
        player = member['player_id']
        eligible = bool(member['reward_eligible'])
        reward_root = None
        extra_dust = 0
        if eligible:
            gp = GameRepository(ctx.store, ctx.account_id, run['group_id'], player)
            old = gp.player()
            if old is None:
                raise RuntimeError('dungeon member lost game player')
            gp.update_player(cultivation=old['cultivation'] + cult,
                             spirit_stones=old['spirit_stones'] + stones)
            if highest == 3 and rng.random() < RULES['clear_rewards']['root_chance']:
                reward_root = rng.choice(list(ROOTS))
                owned = {r['root_id'] for r in repo.roots(ctx.store, ctx.account_id,
                                                         run['group_id'], player)}
                if reward_root in owned:
                    extra_dust = 2
                else:
                    ctx.store.execute('''INSERT INTO dungeon_roots
                        (account_id,group_id,player_id,root_id) VALUES(?,?,?,?)''',
                        (ctx.account_id, run['group_id'], player, reward_root))
            ctx.store.execute('''UPDATE dungeon_profiles SET dust=dust+?,
                highest_night=MAX(highest_night,?)
                WHERE account_id=? AND group_id=? AND player_id=?''',
                (dust + extra_dust, highest, ctx.account_id, run['group_id'], player))
        ctx.store.execute('''UPDATE dungeon_run_members SET settled=1,
            cultivation_awarded=?,stones_awarded=?,dust_awarded=?,root_awarded=?
            WHERE run_id=? AND player_id=? AND settled=0''',
            (cult if eligible else 0, stones if eligible else 0,
             (dust + extra_dust) if eligible else 0,
             reward_root if eligible and not extra_dust else None,
             run['run_id'], player))
        name = _player_name(ctx, run['group_id'], player)
        reward_text = (("尚未出发，无奖励且不计次数" if run['departed_at'] is None
                        else '本局不发奖励') if not eligible else
                       f'{cult} 修为、{stones} 灵石、{dust + extra_dust} 灵尘')
        if eligible and highest == 3:
            if reward_root is None:
                reward_text += '\n🌱 本次未掉落灵根'
            elif extra_dust:
                reward_text += '\n🌱 重复' + ROOTS[reward_root]['name'] + '已转2灵尘'
            else:
                reward_text += '\n🌱 获得' + ROOTS[reward_root]['name']
        lines.append(f'\n{name}\n{reward_text}')
    repo.remove_state(ctx.store, run['run_id'])
    return '\n'.join(lines)


def _catching_up(ctx):
    return (getattr(ctx, 'runtime_issue', None) or '').startswith('receiver_catching_up')


def _expire(ctx, run, rng):
    loaded = repo.load_state(ctx.store, run['run_id'])
    if loaded is None:
        state = {'highest_night': run['highest_night']}
        return _finish(ctx, run, state, '程序重启', rng)
    state, last_progress, _ = loaded
    if _catching_up(ctx):
        return None
    idle = getattr(ctx.game_config, 'dungeon_idle_timeout_seconds', 600)
    observed_ms = getattr(getattr(ctx, 'message', None), 'observed_at_ms', None)
    if (type(observed_ms) is int and 0 <= observed_ms / 1000 < last_progress + idle
            and observed_ms / 1000 <= ctx.now):
        # A committed on-time command may be the last item in the backlog.
        return None
    if ctx.now - last_progress >= idle:
        return _finish(ctx, run, state,
                       (_idle_label(ctx) + ('集结未完成' if run['state'] == 'gathering'
                                            else '无有效推进')), rng)
    return None


def _gather(ctx, command, rng):
    group, player = ctx.conversation_id, ctx.user_id
    if command.kind == 'dungeon_create':
        if repo.active_run(ctx.store, ctx.account_id, group, player):
            return '⚠️ 你已在一支副本队伍中。'
        if _has_pvp(ctx, group, player):
            return '⚠️ 斗法或决斗尚未结束。'
        if command.argument.strip():
            return '⚠️ 副本不再提供练习模式，请发送 #副本创建。'
        mode = '正式'
        for _ in range(20):
            code = ''.join(rng.choices(string.ascii_uppercase + string.digits, k=4))
            exists = ctx.store.execute('''SELECT 1 FROM dungeon_runs
                WHERE account_id=? AND group_id=? AND code=?''',
                (ctx.account_id, group, code)).fetchone()
            if not exists:
                break
        else:
            raise RuntimeError('could not allocate dungeon code')
        profile = repo.profile(ctx.store, ctx.account_id, group, player)
        member = {'player_id': player, 'name': _player_name(ctx, group, player),
                  'class_id': profile['class_id'], 'roots': [r['root_id'] for r in repo.roots(
                      ctx.store, ctx.account_id, group, player) if r['equipped_slot']]}
        state = {'code': code, 'mode': mode, 'phase': 'gathering', 'version': 1,
                 'token': f'{code}.1', 'leader': player, 'members': [member],
                 'ready': [], 'highest_night': 0}
        repo.new_run(ctx.store, ctx.account_id, group, player, code, mode, ctx.now, state)
        return (render.party(state) + '\n\n👉 成员准备\n#副本准备'
                '\n\n全员准备后，队长发送\n#副本出发')
    if command.kind == 'dungeon_join':
        code = command.argument.strip().upper()
        run = repo.by_code(ctx.store, ctx.account_id, group, code)
        if run is None:
            return '⚠️ 没有这个待出发队伍。'
        if repo.active_run(ctx.store, ctx.account_id, group, player):
            return '⚠️ 你已在一支副本队伍中。'
        if _has_pvp(ctx, group, player):
            return '⚠️ 斗法或决斗尚未结束。'
        state, progress, deadline = repo.load_state(ctx.store, run['run_id'])
        if len(state['members']) >= 3:
            return '⚠️ 队伍已满。'
        profile = repo.profile(ctx.store, ctx.account_id, group, player)
        state['members'].append({'player_id': player,
            'name': _player_name(ctx, group, player), 'class_id': profile['class_id'],
            'roots': [r['root_id'] for r in repo.roots(ctx.store, ctx.account_id,
                group, player) if r['equipped_slot']]})
        ctx.store.execute('''INSERT INTO dungeon_run_members
            (run_id,account_id,group_id,player_id) VALUES(?,?,?,?)''',
            (run['run_id'], ctx.account_id, group, player))
        ctx.store.execute('UPDATE dungeon_runs SET member_ids_json=? WHERE run_id=?',
                          (json.dumps([m['player_id'] for m in state['members']]), run['run_id']))
        _save(ctx, run, state, ctx.now)
        return render.party(state) + '\n\n👉 新成员请准备\n#副本准备'
    return None


def _explore_options(state, rng):
    state['phase'] = 'explore'
    pool = list(EXPLORATION_NODES)
    combat_nodes = [key for key in pool if EXPLORATION_NODES[key]['kind'] in ('camp', 'elite')]
    required = rng.choice(combat_nodes)
    state['choices'] = [required] + _sample(rng, [key for key in pool if key != required], 2)
    rng.shuffle(state['choices'])
    _token(state)


def _award_experience(state, kind):
    state['experience'] += RULES['node_experience'][kind]
    thresholds = RULES['experience_thresholds']
    level = min(RULES['max_level'], sum(state['experience'] >= value for value in thresholds))
    state['fighters'] = [combat.scale_member(m, level) for m in state['fighters']]
    for fighter in state['fighters']:
        fighter['experience'] = state['experience']


def _loot_options(state, kind, rng):
    state['phase'] = 'loot'
    state['loot_kind'] = kind
    state['loot_choices'] = {}
    for member in state['fighters']:
        if kind == 'spring':
            choices = ['S_HEAL', 'S_POTION', 'SKIP']
        elif kind == 'armory':
            choices = _sample(rng, EQUIPMENT, 3)
            if member.get('equipment'):
                choices[-1] = 'E00'
        else:
            pool = available_upgrades(member, state['fighters'])
            choices = _sample(rng, pool, 3)
            if kind == 'event':
                choices = choices[:2]
            for supply in ('S_HEAL', 'S_POTION', 'SKIP'):
                if len(choices) >= 3:
                    break
                choices.append(supply)
            if kind == 'event' and 'SKIP' not in choices:
                choices[-1] = 'SKIP'
        state['loot_choices'][member['player_id']] = choices
    state['loot_taken'] = []
    _token(state)


def _battle_start(state, enemy_id, night, rng):
    state['phase'] = 'battle'
    state['battle'] = combat.start_battle(state['fighters'], enemy_id, night=night, rng=rng)
    state['actions'] = {}
    _token(state)
    return render.battle_prompt(state)


def _after_node(ctx, run, state, rng):
    state['completed_nodes'] += 1
    if state['node'] < RULES['nodes_per_day']:
        state['node'] += 1
        _explore_options(state, rng)
        _save(ctx, run, state, ctx.now)
        return render.choices(state)
    night = state['night']
    pool = [key for key, enemy in ENEMIES.items() if enemy.get('night') == night]
    enemy_id = rng.choice(pool)
    text = _battle_start(state, enemy_id, night, rng)
    _save(ctx, run, state, ctx.now, ctx.now + getattr(ctx.game_config,
        'dungeon_round_timeout_seconds', 90))
    return text


def _after_victory(ctx, run, state, rng):
    state['fighters'] = state['battle']['members']
    state['highest_night'] = state['battle']['night']
    ctx.store.execute('UPDATE dungeon_runs SET highest_night=? WHERE run_id=?',
                      (state['highest_night'], run['run_id']))
    if state['highest_night'] == RULES['nights']:
        return _finish(ctx, run, state, '通关', rng, victory=True)
    state['night'] += 1
    if state['highest_night'] < RULES['days']:
        state['day'] += 1
        state['node'] = 1
        _explore_options(state, rng)
        text = render.choices(state)
        deadline = None
    else:
        text = _battle_start(state, state['boss_id'], state['night'], rng)
        deadline = ctx.now + getattr(ctx.game_config, 'dungeon_round_timeout_seconds', 90)
    _save(ctx, run, state, ctx.now, deadline)
    potions = RULES['potion_after_night'].get(state['highest_night'], 0)
    return (f"✅ 第 {state['highest_night']} 夜已通过！\n"
            f"🧪 灵药补充 {potions} 瓶（最多 {RULES['potion_cap']} 瓶）。\n\n" + text)


def _advance_battle(ctx, run, state, rng, *, automated=False):
    battle = state['battle']
    actions = dict(state['actions'])
    if automated:
        for member in battle['members']:
            if member.get('hp', 0) > 0 and member['player_id'] not in actions:
                actions[member['player_id']] = {'type': 'defend'}
    new_battle, logs = combat.resolve_round(battle, actions, rng=rng)
    state['battle'] = new_battle
    state['actions'] = {}
    report = render.battle_report(logs)
    if new_battle['outcome'] == 'victory':
        return report + '\n\n──────────\n\n' + _after_victory(ctx, run, state, rng)
    if new_battle['outcome'] == 'defeat':
        return report + '\n\n──────────\n\n' + _finish(ctx, run, state, '全队倒地', rng)
    _token(state)
    # A timeout-driven round with real submitted actions is genuine team progress.
    _save(ctx, run, state, ctx.now, ctx.now + getattr(ctx.game_config,
        'dungeon_round_timeout_seconds', 90))
    return report + '\n\n──────────\n\n' + render.battle_prompt(state)


def _select_node(ctx, run, state, index, rng):
    node = EXPLORATION_NODES[state['choices'][index]]
    kind = node['kind']
    if kind == 'spring':
        _loot_options(state, 'spring', rng)
        _save(ctx, run, state, ctx.now)
        return '⛲ 抵达灵泉\n每人可恢复生命、补充灵药或放弃。\n\n' + render.choices(state)
    if kind in ('camp', 'elite'):
        tier = 'mob' if kind == 'camp' else 'elite'
        pool = [key for key, enemy in ENEMIES.items()
                if str(enemy.get('tier', '')).lower() == tier]
        enemy_id = rng.choice(pool or list(ENEMIES))
        battle, logs = combat.auto_battle(state['fighters'], enemy_id, night=0,
                                          rng=rng, day=state['day'])
        state['fighters'] = battle['members']
        report = render.battle_report(logs[:5] +
            ([f'……自动结算其余 {len(logs) - 10} 条战报……'] if len(logs) > 10 else [])
            + (logs[-5:] if len(logs) > 10 else logs[5:]), heading=f"⚔️ {node['name']} · 自动战斗")
        if battle['outcome'] != 'victory':
            return report + '\n\n──────────\n\n' + _finish(ctx, run, state, '白天战斗失败', rng)
        _award_experience(state, kind)
        _loot_options(state, kind, rng)
        _save(ctx, run, state, ctx.now)
        return report + '\n\n──────────\n\n' + render.choices(state)
    _loot_options(state, kind, rng)
    _save(ctx, run, state, ctx.now)
    return (f"📍 抵达{node['name']}\n\n"
            + render.choices(state))


def _apply_loot(member, item_id, *, node_kind=None):
    if item_id == 'S_HEAL':
        if member['hp'] >= member['max_hp']:
            return False
        fraction = RULES['spring_heal'] if node_kind == 'spring' else .25
        member['hp'] = min(member['max_hp'], member['hp'] + int(member['max_hp'] * fraction))
        return True
    if item_id == 'S_POTION':
        if member.get('potions', 0) >= 3:
            return False
        member['potions'] = min(3, member.get('potions', 0) + 1)
        return True
    if item_id == 'SKIP':
        return True
    if item_id.startswith('F'):
        fortunes = member.setdefault('fortunes', [])
        if item_id in fortunes:
            return False
        fortunes.append(item_id)
        if item_id == 'F07':
            member['potions'] = min(3, member.get('potions', 0) + 1)
        return True
    equipment = member.setdefault('equipment', {})
    if item_id == 'E00':
        if not equipment:
            return False
        slot = next(iter(equipment))
        if equipment[slot].get('level', 1) >= EQUIPMENT[equipment[slot]['id']]['max_level']:
            return False
        equipment[slot]['level'] = equipment[slot].get('level', 1) + 1
    else:
        item = EQUIPMENT[item_id]
        equipment[item['slot']] = {'id': item_id, 'level': 1}
    stats = {'hp': 0, 'atk': 0, 'defense': 0}
    for equipped in equipment.values():
        item = EQUIPMENT[equipped['id']]
        ratio = 1 + (equipped['level'] - 1) * item['upgrade_stat_ratio']
        for field in stats:
            stats[field] += int(item[field] * ratio)
    member['equipment_stats'] = stats
    return True


def _profile_command(ctx, command):
    group, player, store = ctx.conversation_id, ctx.user_id, ctx.store
    row = repo.profile(store, ctx.account_id, group, player)
    owned = repo.roots(store, ctx.account_id, group, player)
    kind, argument = command.kind, command.argument.strip()
    if kind == 'dungeon_roots':
        if argument:
            root_id = _find(ROOTS, argument)
            return render.root_detail(root_id) if root_id else '⚠️ 未开放的灵根。'
        return render.roots_list(row, owned) + f"\n今日剩余出击次数：{max(0, RULES['rewarded_runs_per_day'] - repo.daily_departures(store, ctx.account_id, group, player, _today(ctx.now)))}"
    if kind == 'dungeon_class':
        if not argument:
            return render.class_list()
        parts = argument.split()
        if parts[0] == '详情':
            if len(parts) != 2:
                return '👉 用法：#职业 详情 职业名或C编号'
            class_id = _find(CLASSES, parts[1])
            return (render.class_detail(class_id) if class_id else
                    '⚠️ 未开放的职业。发送 #职业 查看职业速览。')
    if repo.active_run(store, ctx.account_id, group, player):
        return '⚠️ 队伍集结或副本进行期间不能更换职业与灵根。'
    if kind == 'dungeon_class':
        class_id = _find(CLASSES, argument)
        if class_id is None:
            return '⚠️ 未开放的职业。发送 #职业 查看职业速览。'
        store.execute('''UPDATE dungeon_profiles SET class_id=?
            WHERE account_id=? AND group_id=? AND player_id=?''',
            (class_id, ctx.account_id, group, player))
        return f"✅ 已选择 {CLASSES[class_id]['name']}，下次出发起效。"
    if kind == 'dungeon_root_claim':
        root_id = _find(ROOTS, argument)
        if root_id not in STARTER_ROOTS:
            return '⚠️ 首次只能从五枚基础灵根中选择。'
        if row['starter_claimed']:
            return '⚠️ 基础灵根已领取。'
        if root_id in {r['root_id'] for r in owned}:
            return '⚠️ 已拥有这枚灵根，请选择另一枚基础灵根。'
        used_slots = {r['equipped_slot'] for r in owned if r['equipped_slot']}
        slot = next((number for number in (1, 2, 3) if number not in used_slots), None)
        store.execute('''INSERT INTO dungeon_roots
            (account_id,group_id,player_id,root_id,equipped_slot) VALUES(?,?,?,?,?)''',
            (ctx.account_id, group, player, root_id, slot))
        store.execute('''UPDATE dungeon_profiles SET starter_claimed=1
            WHERE account_id=? AND group_id=? AND player_id=? AND starter_claimed=0''',
            (ctx.account_id, group, player))
        return f"✅ 获得并装配 {ROOTS[root_id]['name']}。"
    if kind == 'dungeon_root_exchange':
        root_id = _find(ROOTS, argument)
        if root_id is None:
            return '⚠️ 未开放的灵根。'
        if row['dust'] < 6:
            return '⚠️ 兑换需要 6 灵尘。'
        if root_id in {r['root_id'] for r in owned}:
            return '⚠️ 已拥有这枚灵根。'
        store.execute('''UPDATE dungeon_profiles SET dust=dust-6
            WHERE account_id=? AND group_id=? AND player_id=?''',
            (ctx.account_id, group, player))
        store.execute('''INSERT INTO dungeon_roots
            (account_id,group_id,player_id,root_id) VALUES(?,?,?,?)''',
            (ctx.account_id, group, player, root_id))
        return f"✅ 花费 6 灵尘兑换 {ROOTS[root_id]['name']}。"
    if kind == 'dungeon_root_remove':
        if argument not in ('1', '2', '3'):
            return '👉 用法：#灵根卸下 槽位(1～3)'
        changed = store.execute('''UPDATE dungeon_roots SET equipped_slot=NULL
            WHERE account_id=? AND group_id=? AND player_id=? AND equipped_slot=?''',
            (ctx.account_id, group, player, int(argument)))
        return '✅ 已卸下。' if changed.rowcount else '⚠️ 这个槽位没有灵根。'
    if kind == 'dungeon_root_equip':
        pieces = argument.split(maxsplit=1)
        if len(pieces) != 2 or pieces[0] not in ('1', '2', '3'):
            return '👉 用法：#灵根装配 槽位(1～3) 灵根'
        slot, root_id = int(pieces[0]), _find(ROOTS, pieces[1])
        if root_id is None or root_id not in {r['root_id'] for r in owned}:
            return '⚠️ 尚未拥有这枚灵根。'
        store.execute('''UPDATE dungeon_roots SET equipped_slot=NULL
            WHERE account_id=? AND group_id=? AND player_id=? AND equipped_slot=?''',
            (ctx.account_id, group, player, slot))
        store.execute('''UPDATE dungeon_roots SET equipped_slot=?
            WHERE account_id=? AND group_id=? AND player_id=? AND root_id=?''',
            (slot, ctx.account_id, group, player, root_id))
        return f"✅ 第 {slot} 槽已装配 {ROOTS[root_id]['name']}。\n{ROOTS[root_id]['effect']}"
    return None


def _run_command(ctx, command, run, rng):
    loaded = repo.load_state(ctx.store, run['run_id'])
    if loaded is None:
        return _finish(ctx, run, {'highest_night': run['highest_night']}, '程序重启', rng)
    state, progress, deadline = loaded
    player = ctx.user_id
    kind, arg = command.kind, command.argument.strip()
    if kind == 'dungeon_status':
        if state['phase'] in ('explore', 'loot'):
            return render.choices(state) + render.build_summary(state)
        if state['phase'] == 'battle':
            return render.battle_prompt(state, details=True)
        return render.party(state)
    if kind == 'dungeon_ready':
        if state['phase'] != 'gathering':
            return '⚠️ 副本已出发。'
        if player in state['ready']:
            return '✅ 你已经准备。'
        state['ready'].append(player)
        _save(ctx, run, state, ctx.now)
        return render.party(state)
    if kind == 'dungeon_leave':
        if state['phase'] != 'gathering':
            if player != run['leader_id']:
                return '⚠️ 请队长发送 #副本退出 结束整队，或等待空闲超时。'
            return _finish(ctx, run, state, '队长主动退出', rng)
        if player == run['leader_id']:
            return _finish(ctx, run, state, '队长取消集结', rng)
        state['members'] = [m for m in state['members'] if m['player_id'] != player]
        state['ready'] = [p for p in state['ready'] if p != player]
        ctx.store.execute('DELETE FROM dungeon_run_members WHERE run_id=? AND player_id=?',
                          (run['run_id'], player))
        ctx.store.execute('UPDATE dungeon_runs SET member_ids_json=? WHERE run_id=?',
                          (json.dumps([m['player_id'] for m in state['members']]), run['run_id']))
        _save(ctx, run, state, ctx.now)
        return render.party(state)
    if kind == 'dungeon_start':
        if state['phase'] != 'gathering':
            return '⚠️ 副本已出发。'
        if player != run['leader_id']:
            return '⚠️ 只有队长可以出发。'
        if len(state['ready']) != len(state['members']):
            return '⚠️ 请等全员准备。'
        if any(_has_pvp(ctx, run['group_id'], m['player_id']) for m in state['members']):
            return '⚠️ 有成员正在斗法或决斗。'
        day = _today(ctx.now)
        if run['mode'] != '正式':
            return '⚠️ 旧练习队伍不能出发，请取消后重新创建。'
        for member in state['members']:
            if repo.daily_departures(ctx.store, ctx.account_id, run['group_id'],
                                     member['player_id'], day) >= RULES['rewarded_runs_per_day']:
                return f"⚠️ {member['name']} 今日出击次数已用完。"
        state['fighters'] = [combat.create_member(m['player_id'], m['name'],
            m['class_id'], m['roots']) for m in state['members']]
        state['day'], state['night'], state['node'] = 1, 1, 1
        state['completed_nodes'], state['experience'] = 0, 0
        for fighter in state['fighters']:
            fighter['experience'] = 0
        state['boss_id'] = rng.choice(list(BOSSES))
        _explore_options(state, rng)
        ctx.store.execute('''UPDATE dungeon_runs SET state='active',departure_day=?,departed_at=?
            WHERE run_id=? AND state='gathering' ''', (day, ctx.now, run['run_id']))
        ctx.store.execute('''UPDATE dungeon_run_members SET reward_eligible=1 WHERE run_id=?''',
                          (run['run_id'],))
        _save(ctx, run, state, ctx.now)
        boss = BOSSES[state['boss_id']]
        return ('🚩 已出发\n👹 第三夜妖王：' + boss['name'] + '\n'
                # + boss['mechanic'][:180] +
                '\n\n──────────\n\n' + render.choices(state))
    if state['phase'] == 'gathering':
        return '⚠️ 请先准备并由队长出发。'
    pieces = arg.split(maxsplit=3)
    if len(pieces) < 2 or pieces[0] != state['token']:
        return f"⚠️ 阶段已变化，请使用当前 TOKEN {state['token']}。"
    if kind == 'dungeon_choose':
        if state['phase'] != 'explore' or player != run['leader_id']:
            return '⚠️ 当前不是队长探索选择阶段。'
        if len(pieces) != 2 or pieces[1] not in ('1', '2', '3'):
            return '⚠️ 请选择 1～3。'
        return _select_node(ctx, run, state, int(pieces[1]) - 1, rng)
    if kind == 'dungeon_loot':
        if state['phase'] != 'loot':
            return '⚠️ 当前没有待领取的宝物。'
        if player in state['loot_taken']:
            return '✅ 本层已经选过宝物。'
        if (len(pieces) != 2 or pieces[1] not in ('1', '2', '3')
                or int(pieces[1]) > len(state['loot_choices'][player])):
            return '⚠️ 请选择 1～3。'
        item_id = state['loot_choices'][player][int(pieces[1]) - 1]
        fighter = next(m for m in state['fighters'] if m['player_id'] == player)
        if not _apply_loot(fighter, item_id, node_kind=state['loot_kind']):
            return '⚠️ 此强化已拥有或没有可升级装备，请选择其他宝物。'
        if state['loot_kind'] == 'event' and item_id != 'SKIP':
            fighter['hp'] = max(1, fighter['hp'] - int(fighter['max_hp'] * .1))
        state['loot_taken'].append(player)
        state['fighters'] = [combat.scale_member(m, m['level']) if m['player_id'] == player
                             else m for m in state['fighters']]
        item_name = ('强化当前装备' if item_id == 'E00' else
                     '放弃奇遇' if item_id == 'SKIP' else
                     '灵泉恢复' if item_id == 'S_HEAL' else
                     '补充灵药' if item_id == 'S_POTION' else
                     (UPGRADES if item_id.startswith('F') else EQUIPMENT)[item_id]['name'])
        if len(state['loot_taken']) == len(state['members']):
            if state['loot_kind'] not in ('camp', 'elite'):
                _award_experience(state, state['loot_kind'])
            return f"✅ 全员领取完成\n最后选择：{item_name}\n\n" + _after_node(ctx, run, state, rng)
        _save(ctx, run, state)
        return f"✅ 已选择 {item_name}，等待其余成员。"
    if kind == 'dungeon_act':
        if state['phase'] in ('explore', 'loot'):
            if pieces[1] != '灵药':
                return '⚠️ 白天探索阶段仅可使用灵药。'
            if len(pieces) != 2:
                return '⚠️ 灵药只能自己使用，无需指定目标。'
            fighter = next(m for m in state['fighters'] if m['player_id'] == player)
            if fighter['hp'] <= 0:
                return '⚠️ 倒地时无法使用灵药。'
            if fighter['hp'] >= fighter['max_hp']:
                return '⚠️ 当前生命已满，未消耗灵药。'
            if fighter['potions'] <= 0:
                return '⚠️ 没有灵药了。'
            before = fighter['hp']
            fighter['hp'] = min(fighter['max_hp'],
                                round(before + combat.potion_heal_amount(fighter), 6))
            fighter['potions'] -= 1
            _save(ctx, run, state)
            return (f"🧪 {fighter['name']} 使用灵药，恢复 {fighter['hp'] - before:.0f} 生命。\n"
                    f"❤️ {fighter['hp']:.0f}/{fighter['max_hp']:.0f} · 灵药 {fighter['potions']} 瓶\n"
                    '可继续当前探索，不消耗节点。')
        if state['phase'] != 'battle':
            return '⚠️ 当前不是夜战阶段。'
        observed_ms = getattr(ctx.message, 'observed_at_ms', None)
        if (deadline is not None and
                (type(observed_ms) is not int or observed_ms < 0
                 or observed_ms / 1000 >= deadline or observed_ms / 1000 > ctx.now)):
            return '⏳ 本回合行动已过时限，等待夜战结算。'
        if player in state['actions']:
            return '✅ 本回合已提交行动。'
        action_name = _ACTION_NAMES.get(pieces[1])
        if action_name is None:
            return '⚠️ 行动应为普攻/技能/绝技/防御/闪避/回气/灵药/救援/部位。'
        action = {'type': action_name}
        if action_name == 'dodge':
            if len(pieces) != 3 or pieces[2] not in ('前段', '后段'):
                return '⚠️ 闪避须指定前段或后段，不能附加目标。'
            action['segment'] = {'前段': 'front', '后段': 'back'}[pieces[2]]
        elif action_name == 'recover':
            if len(pieces) != 2:
                return '⚠️ 回气不能指定目标。'
        elif len(pieces) >= 3:
            target = pieces[2].strip()
            if target.isdecimal() and 1 <= int(target) <= len(state['members']):
                target = state['members'][int(target) - 1]['player_id']
            action['target'] = target
        if len(pieces) == 4:
            fighter = next(m for m in state['battle']['members'] if m['player_id'] == player)
            if fighter['class_id'] != 'C04' or action_name not in ('attack', 'skill', 'ultimate'):
                return '⚠️ 仅丹修治疗行动可同时指定过量伤害目标。'
            enemy_target = {'本体': 'enemy', '敌人': 'enemy', '毒囊': 'sac',
                            'enemy': 'enemy', 'sac': 'sac'}.get(pieces[3])
            if enemy_target is None:
                return '⚠️ 过量伤害目标只能选择本体或毒囊。'
            action['enemy_target'] = enemy_target
        if action_name == 'rescue' and any(
                prior['type'] == 'rescue' and prior.get('target') == action.get('target')
                for prior in state['actions'].values()):
            return '⚠️ 已有队友在救援该目标，本回合请选择其他行动。'
        error = combat.validate_action(state['battle'], player, action)
        if error:
            return '⚠️ ' + error
        combat.commit_action(state['battle'], player, action)
        state['actions'][player] = action
        living = {m['player_id'] for m in state['battle']['members'] if m.get('hp', 0) > 0}
        if living <= state['actions'].keys():
            return _advance_battle(ctx, run, state, rng)
        _save(ctx, run, state, deadline=deadline)
        name = next(m['name'] for m in state['members'] if m['player_id'] == player)
        return f"✅ {name} 已提交：{pieces[1]}\n\n" + render.waiting_actions(state)
    return '⚠️ 当前指令不适用。'


def handle_command(command, context, *, rng=None):
    if command.kind not in KINDS:
        return None
    group, player, message = context.conversation_id, context.user_id, context.message
    if (not group or not group.endswith('@chatroom') or group not in context.allowed_targets
            or not player or message is None or not context.event_key):
        return None
    if command.kind == 'dungeon_help':
        return render.help_text()
    if GameRepository(context.store, context.account_id, group, player).player() is None:
        return '👉 请先发送 #修仙 道号 创建修士。'
    identity = _message_id(message)
    if identity is None:
        return '⚠️ 未能确认消息唯一标识，请重发指令。'
    fingerprint = hashlib.sha256((message.content or '').encode('utf-8')).hexdigest()
    action_state = repo.check_action(context.store, context.account_id, context.event_key,
                                     identity, group, player, fingerprint)
    if action_state == 'repeat':
        return None
    if action_state == 'conflict':
        return '⚠️ 消息标识冲突，本次未执行。'
    random_source = rng if rng is not None else random.SystemRandom()
    run = repo.active_run(context.store, context.account_id, group, player)
    if run:
        expired = _expire(context, run, random_source)
        if expired:
            result = expired
        else:
            result = None
    else:
        result = None
    if result is None:
        if command.kind.startswith('dungeon_root') or command.kind == 'dungeon_class':
            result = _profile_command(context, command)
        elif command.kind == 'dungeon_roots':
            result = _profile_command(context, command)
        elif command.kind in ('dungeon_create', 'dungeon_join'):
            result = _gather(context, command, random_source)
        elif run:
            result = _run_command(context, command, run, random_source)
        elif command.kind == 'dungeon_status':
            row = repo.profile(context.store, context.account_id, group, player)
            result = '当前没有队伍。\n' + render.profile(row,
                repo.roots(context.store, context.account_id, group, player))
        else:
            result = '⚠️ 你尚未加入副本队伍。发送 #副本创建 开始集结。'
    repo.record_action(context.store, context.account_id, context.event_key,
                       identity, group, player, fingerprint, command.kind)
    return result


def _persist_notice(ctx, run, text, event):
    if text:
        notice_id = f"{run['run_id']}:{event}"
        ctx.store.execute('''INSERT OR IGNORE INTO dungeon_notices
            (notice_id,account_id,group_id,run_id,text) VALUES(?,?,?,?,?)''',
            (notice_id, ctx.account_id, run['group_id'], run['run_id'], text))


def _pending(ctx, limit):
    if not ctx.connection_id or limit <= 0:
        return []
    rows = ctx.store.execute('''SELECT * FROM dungeon_notices
        WHERE account_id=? AND queued=0 ORDER BY rowid LIMIT 100''',
        (ctx.account_id,)).fetchall()
    replies = []
    for row in rows:
        if row['group_id'] not in ctx.allowed_targets:
            continue
        key = 'dungeon:' + row['notice_id']
        if ctx.receipt(key) is not None:
            ctx.store.execute('UPDATE dungeon_notices SET queued=1 WHERE notice_id=?',
                              (row['notice_id'],))
            continue
        if len(replies) < limit:
            replies.append(Reply(row['text'], target_id=row['group_id'], request_key=key))
    return replies


def on_start(context, *, limit=3):
    """Resume persisted dungeon state; notices retain their logical keys."""
    return _pending(context, limit)


def on_before_messages(context, *, limit=3):
    return _pending(context, limit)


def on_poll(context, *, limit=3):
    runs = context.store.execute('''SELECT * FROM dungeon_runs
        WHERE account_id=? AND state IN ('gathering','active') ORDER BY created_at''',
        (context.account_id,)).fetchall()
    rng = random.SystemRandom()
    for run in runs:
        loaded = repo.load_state(context.store, run['run_id'])
        if loaded is None:
            text = _finish(context, run, {'highest_night': run['highest_night']},
                           '程序重启', rng)
            _persist_notice(context, run, text, 'restart')
            continue
        state, last_progress, deadline = loaded
        if _catching_up(context):
            continue
        if context.now - last_progress >= getattr(context.game_config,
                                                  'dungeon_idle_timeout_seconds', 600):
            reason = _idle_label(context) + ('集结未完成' if run['state'] == 'gathering'
                                             else '无有效推进')
            text = _finish(context, run, state, reason, rng)
            _persist_notice(context, run, text, 'idle')
            continue
        if (state['phase'] == 'battle' and state['actions'] and deadline is not None
                and context.now >= deadline):
            token = state['token']
            text = _advance_battle(context, run, state, rng, automated=True)
            _persist_notice(context, run, text, 'round:' + token)
    return _pending(context, limit)
