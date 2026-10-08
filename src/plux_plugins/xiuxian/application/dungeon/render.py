"""Compact group-friendly dungeon text."""

import re

from . import combat
from .content import CLASSES, EXPLORATION_NODES, ROOTS, UPGRADES, EQUIPMENT, STARTER_ROOTS, RULES


def title(state):
    return f"🏯 妖祸夜行｜队伍 {state['code']}"


def party(state):
    members = state['members']
    if state['phase'] == 'gathering':
        rows = []
        for i, member in enumerate(members, 1):
            ready = '✅ 已准备' if member['player_id'] in state.get('ready', []) else '⏳ 未准备'
            leader = ' · 队长' if member['player_id'] == state['leader'] else ''
            rows.append(f"{i}. {member['name']} · {CLASSES[member['class_id']]['name']}{leader}\n{ready}")
        return (f"{title(state)}\n🚩 集结中 · {len(members)}/3 人\n\n"
                + '\n\n'.join(rows))
    phase = '选择路线' if state['phase'] == 'explore' else '领取宝物'
    names = '、'.join(m['name'] for m in members)
    fighters = state.get('fighters', [])
    level = fighters[0]['level'] if fighters else 1
    experience = state.get('experience', 0)
    progress = ('已满级' if level >= RULES['max_level'] else
                f"下级需 {RULES['experience_thresholds'][level]} 经验")
    return (f"{title(state)}\n☀️ 第 {state['day']} 个白天 · 探索 {state['node']}/{RULES['nodes_per_day']} · {phase}"
            f"\n👥 {names}\n📈 Lv.{level} · 经验 {experience} · {progress}")


def choices(state):
    entries = state.get('choices', [])
    if state['phase'] == 'explore':
        lines = [f"{i}. {EXPLORATION_NODES[x]['name']}\n{EXPLORATION_NODES[x]['description']}"
                 f"\n📈 {'胜利后' if EXPLORATION_NODES[x]['kind'] in {'camp', 'elite'} else '完成后'}"
                 f" +{RULES['node_experience'][EXPLORATION_NODES[x]['kind']]} 经验"
                 for i, x in enumerate(entries, 1)]
        return (party(state) + '\n\n🧭 可选路线\n\n' + '\n\n'.join(lines)
                + f"\n\n👉 队长选择（编号 1～{len(entries)}）\n#副本选择 {state['token']} 编号"
                + f"\n白天也可即时自用灵药（不占节点）：#副本行动 {state['token']} 灵药")
    if state['phase'] == 'loot':
        lines = []
        for member in state['members']:
            if member['player_id'] in state.get('loot_taken', []):
                continue
            options = state['loot_choices'][member['player_id']]
            names = []
            for i, item_id in enumerate(options, 1):
                if item_id == 'E00':
                    label = '强化当前装备\n提高已有装备属性'
                elif item_id == 'S_HEAL':
                    heal = (RULES['spring_heal'] if state.get('loot_kind') == 'spring'
                            else RULES['potion_heal'])
                    label = f'恢复最大生命 {heal:.0%}'
                elif item_id == 'S_POTION':
                    label = '补充 1 瓶灵药（最多 3）'
                elif item_id == 'SKIP':
                    label = '放弃（不失去生命）'
                else:
                    item = (UPGRADES if item_id.startswith('F') else EQUIPMENT)[item_id]
                    label = item['name'] + '\n' + item['description']
                names.append(f'{i}. {label}')
            lines.append(f"🎁 {member['name']} · 请选择一项\n\n" + '\n\n'.join(names))
        warning = ('\n\n⚠️ 奇遇代价：接受强化扣除 10% 最大生命；可选择放弃。'
                   if state.get('loot_kind') == 'event' else '')
        return (party(state) + warning + '\n\n' + '\n\n'.join(lines)
                + f"\n\n👉 每人选择自己的宝物\n#副本选宝 {state['token']} 编号"
                + f"\n白天也可即时自用灵药（不占节点）：#副本行动 {state['token']} 灵药")
    return party(state)


def battle_prompt(state, *, details=False):
    text = title(state) + '\n' + combat.battle_summary(state['battle'])
    if details:
        text += build_summary({**state, 'fighters': state['battle']['members']})
    if state.get('actions'):
        text += '\n\n' + waiting_actions(state)
    text += (f"\n\n🎮 本回合行动\n#副本行动 {state['token']} 行动"
             '\n可选：普攻 / 技能 / 绝技'
             '\n防御 / 灵药 / 救援'
             '\n闪避 前段 / 闪避 后段 / 回气')
    battle = state['battle']
    if battle.get('intent', {}).get('part'):
        from .patterns import PART_RULES
        part = PART_RULES.get(battle['enemy']['id'])
        if part:
            text += '\n部位 ' + part['name']
    sac = battle['enemy'].get('sac')
    if sac and sac.get('hp', 0) > 0:
        text += '\n攻击目标：本体 / 毒囊'
    text += '\n治疗、护盾或救援：行动后加队友编号。'
    return text


def waiting_actions(state):
    living = [m for m in state['battle']['members'] if m.get('hp', 0) > 0]
    pending = [m['name'] for m in living if m['player_id'] not in state['actions']]
    return (f"⏳ 已提交 {len(living) - len(pending)}/{len(living)}"
            + ('\n等待：' + '、'.join(pending) if pending else ''))


def battle_report(logs, *, heading=None):
    """Keep the full event order while separating round headings and outcomes."""
    rows = [heading] if heading else []
    for line in logs:
        round_match = re.fullmatch(r'第(\d+)回合', line)
        if round_match:
            if rows:
                rows.append('')
            rows.append(f'📜 第 {round_match[1]} 回合战报')
        elif line.startswith(('击败', '全队倒地')):
            rows.extend(['', ('🏆 ' if line.startswith('击败') else '💀 ') + line])
        else:
            rows.append('· ' + line)
    return '\n'.join(rows)


def profile(row, owned):
    chosen = [f"{ROOTS[r['root_id']]['name']}({r['equipped_slot'] or '未装'})" for r in owned]
    return (f"职业：{CLASSES[row['class_id']]['name']}；灵尘：{row['dust']}；"
            f"最高过夜：{row['highest_night']}\n灵根：{'、'.join(chosen) or '尚未选择'}")


def class_list():
    traits = {
        'C01': '⚔️ 剑势破甲，单体爆发',
        'C02': '🛡️ 防御反击，护盾守护',
        'C03': '🔥 积符施法，灼烧输出',
        'C04': '💚 治疗队友，过量转伤',
        'C05': '🐾 灵兽协击，毒伤输出',
        'C06': '🌑 守势蓄力，无主动技能',
        'C07': '🔷 护盾支援，群体攻击',
        'C08': '🩸 耗血爆发，吸血续战',
    }
    lines = [f"{key} {entry['name']}｜{traits[key]}" for key, entry in CLASSES.items()]
    return ('⚔️ 副本职业速览\n\n' + '\n'.join(lines)
            + '\n\n📖 查看详情：#职业 详情 剑修'
            + '\n✅ 选择职业：#职业 剑修'
            + '\n职业名也可替换为 C01～C08。')


def class_detail(class_id):
    entry = CLASSES[class_id]
    return (f"⚔️ {class_id} {entry['name']}\n{entry['description']}"
            f"\n\n📊 初始面板\n生命 {entry['base_hp']:g} · 攻击 {entry['base_atk']:g} · 防御 {entry['base_defense']:g}"
            '\n战斗经验提升等级，生命与攻击随等级加速成长。'
            f"\n\n🔹 被动\n{entry['passive']}"
            f"\n\n✨ 主动技能\n{entry['skill']}"
            f"\n\n🌟 绝技\n{entry['ultimate']}"
            f"\n\n选择职业：#职业 {entry['name']}\n返回速览：#职业")


def roots_list(row, owned):
    all_roots = '、'.join(f"{key} {entry['name']}" for key, entry in ROOTS.items())
    starters = '、'.join(f"{key} {ROOTS[key]['name']}" for key in STARTER_ROOTS)
    return (profile(row, owned) + '\n📜【已开放灵根】\n' + all_roots
            + '\n首次基础自选：' + starters
            + '\n发送 #灵根领取 基础灵根名；发送 #灵根 R编号 查看效果，6灵尘可兑换任意已开放灵根。')


def root_detail(root_id):
    item = ROOTS[root_id]
    return (f"🌱 {root_id} {item['name']}（{item['element']}，{item['batch']}）\n"
            f"效果：{item['effect']}\n限制：{item['limit']}\n获取：{item['acquisition']}")


def build_summary(state):
    if 'fighters' not in state:
        return ''
    rows = []
    for member in state['fighters']:
        gear = member.get('equipment', {})
        names = [EQUIPMENT[item['id']]['name'] + f"+{item['level'] - 1}"
                 for item in gear.values()]
        fortunes = [UPGRADES[key]['name'] for key in member.get('fortunes', [])]
        roots = [ROOTS[key]['name'] for key in member.get('roots', [])]
        rows.append(f"{member['name']} Lv.{member['level']}"
                    f"\n生命 {member['hp']:.0f}/{member['max_hp']:.0f} · 灵药 {member['potions']}\n"
                    f"攻击 {member['atk']:.1f} · 防御 {member['defense']:.1f}\n"
                    f"装备：{'、'.join(names) or '无'}\n强化：{'、'.join(fortunes) or '无'}\n"
                    f"灵根：{'、'.join(roots) or '无'}")
    return '\n\n📦 当前构筑\n\n' + '\n\n'.join(rows)


def help_text():
    clear = RULES['clear_rewards']
    rewards = dict(RULES['failure_rewards'])
    rewards[3] = (clear['cultivation'], clear['stones'], clear['dust'])
    reward_lines = ['🎁【结算奖励】', '按最高通过进度结算一次，不逐夜累加。']
    for night, label in enumerate(('未通过第一夜', '第一夜', '第二夜', '第三夜')):
        cult, stones, dust = rewards[night]
        reward_lines.append(f'{label}：{cult} 修为、{stones} 灵石、{dust} 灵尘')
    reward_lines.extend([
        f"🌱 仅第三夜掉落灵根：每人独立以 {clear['root_chance']:.0%} 概率获得随机一枚。",
        f"重复灵根转为 {RULES['root_duplicate_dust']} 灵尘。",
    ])
    return (f"🏯【妖祸夜行】\n单人或组队挑战，两昼三夜。每天可出击 {RULES['rewarded_runs_per_day']} 次，用完后当天无法再出发。\n\n"
            "🗺️ 游戏流程\n"
            "白天探索 → 第一夜妖兽 → 再次白天探索 → 第二夜妖兽 → 第三夜妖王。\n"
            "白天选择路线、积累成长；夜晚具体操做行动迎战。\n 第二夜胜利后直接挑战第三夜妖王。\n\n"
            "🎒【出击前准备】\n"
            "拥有修士身份，再选择职业、装配灵根，最后集结出发。\n\n"
            "🌱 角色与构筑\n"
            "#修仙 仙名：创建修士\n"
            "#职业：查看各职业特点\n"
            "#职业 详情 名称：查看技能与被动\n"
            "#职业 名称：选择职业\n"
            "#灵根：查看收藏与装配\n"
            "#灵根领取 名称：领取初始灵根\n"
            "#灵根装配 槽位 名称\n"
            "#灵根卸下 槽位\n"
            "#灵根兑换 名称\n\n"
            "👥 集结出发\n"
            "#副本创建：创建队伍，也可单人出发\n"
            "#副本加入 短码：加入队伍\n"
            "#副本准备：确认准备\n"
            "#副本出发：全员准备后由队长出发\n"
            "职业和灵根在组队后锁定，需调整时先退出队伍。\n\n"
            "⚔️【副本中】\n"
            "☀️ 白天探索\n"
            "队长选择路线，每位成员领取自己的奖励。战斗带来更多经验成长，也可寻找装备、强化或补给。\n"
            "#副本选择 阶段编号 编号\n"
            "#副本选宝 阶段编号 编号\n\n"
            "🌙 夜晚战斗\n"
            "观察敌人的姿态与目标，选择进攻、防御或闪避；队友倒地时可救援，部位暴露时可选择攻击部位。\n"
            "#副本行动 阶段编号 行动 [目标]\n"
            "可选行动：进攻 / 技能 / 绝技 / 防御 / 灵药 / 救援 / 部位\n"
            "闪避写法：#副本行动 阶段编号 闪避 前段（或后段）\n"
            "回气写法：#副本行动 阶段编号 回气\n\n"
            "🛡️ 玩法机制\n"
            "• 防御和闪避消耗架势，回气用于恢复；需要在攻守之间安排节奏。\n"
            "• 技能有冷却，绝技有使用限制；多人挑战时敌方生命与攻击也会提高。\n"
            "• 局内等级、装备和强化仅本局有效，职业与灵根收藏保留。\n\n"
            "📍 查看与结束\n"
            "#副本状态：查看进度、构筑和当前操作\n"
            "#副本退出：集结时退出；出发后由队长结束整局\n"
            "操作时使用最新提示中的阶段编号；长时间无有效推进会自动结束并结算。\n\n"
            + '\n'.join(reward_lines))
