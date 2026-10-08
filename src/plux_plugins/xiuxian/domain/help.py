"""Two-level gameplay navigation and read-only module guides."""


MODULE_HELP_KINDS = frozenset({
    'beginner_help', 'cultivation_help', 'exploration_help',
    'props_help', 'lightning_help',
})


def with_help_navigation(text):
    return text.rstrip() + '\n\n↩️ 玩法总览：#修仙帮助'


def format_help(rules):
    lightning = '轮流引雷，围观支持，争夺法宝。' if rules.duel_enabled else '轮流引雷与围观支持，当前未开启。'
    return f'''📖【大爱仙途-妖祸夜行 beta v2.3】
发送对应帮助指令查看玩法与操作

🌱 入门与仙榜
创建修士，查看人物与排名。
👉 #新手帮助

🧘 修炼与采矿
积累修为、开采灵石、突破境界。
👉 #修炼帮助

🗺️ 秘境与法宝
探索寻宝，收集法宝与神通。
👉 #秘境帮助 · #法宝帮助

🏯 妖祸夜行
副本玩法 1～3 人组队，白天成长、夜晚击败妖兽。
👉 #副本帮助

🛒 商店与道具
购买补给，使用道具辅助修行或互动。
👉 #商店帮助 · #道具帮助

📜 魔鬼交易
消耗修为换取灵石，签订与深化魔契。
👉 #魔鬼交易帮助

⚔️ 仙道决斗
凭修为、境界和法宝交锋，押注灵石争胜。
👉 #决斗帮助

⚡ 引雷斗法
{lightning}
👉 #斗法帮助'''


def format_module_help(kind, rules):
    if kind == 'beginner_help':
        return '''🌱【新手指引】
先创建修士，再积累修为与灵石，逐步体验秘境、法宝和副本。

① 创建修士
#修仙 仙名
例如：#修仙 青玄。仙名为 2～12 位文字、数字或下划线；已有角色时，此指令用于改名。

② 查看自己与同门
#修仙：人物面板、资产与当前状态。
#仙榜：本群修士排名。

③ 开始修行
#修炼：领取每日修炼收益。
#采矿：开采灵石。
更多成长方式：#修炼帮助

④ 选择玩法
寻宝收藏：#秘境帮助
组队镇妖：#副本帮助

💡 指令在群聊内发送。仙名、编号、金额等占位内容须替换；@群友时从群成员列表选择。'''
    if kind == 'cultivation_help':
        seconds = rules.self_cultivation_interval_seconds
        interval = f'{seconds // 60} 分钟' if seconds % 60 == 0 else f'{seconds} 秒'
        return f'''🧘【修炼与采矿指引】
修为用于成长与突破，灵石用于寻宝、购物等玩法。

☀️ 每日修炼：#修炼
每日基础次数一次，北京时间零点刷新。
基础收益为 {rules.cultivation_reward} 修为、{rules.cultivation_stones_reward} 灵石；法宝与状态可能影响实际收益。

🌙 自主修炼：#自主修炼
基础间隔 {interval}，可能获得修为，也可能失败损失修为；实际冷却与结果以当前状态为准。

⛏️ 灵矿采矿：#采矿
每小时可开采一次，获得灵石，也有机会遇到灵髓。

✨ 突破境界：#突破
达到当前境界所需修为与灵石后，可消耗资源突破；条件不足时会提示所需数值。

查看资产与状态：#修仙'''
    if kind == 'exploration_help':
        return f'''🗺️【秘境与法宝指引】
探索秘境收集法宝，积累收藏并获得各类神通效果。

🔎 探索秘境：#秘境
每次消耗 {rules.exploration_cost} 灵石；每日基础次数一次，北京时间零点刷新。
法宝品质与境界、神通等因素有关，结果以探索提示为准。

🎒 我的法宝：#法宝
查看已持有法宝及各自的 F 编号。
单件详情：#法宝 F编号
神通效果：#法宝帮助

📚 稀有宝录：#宝录
查看本群稀有法宝的收藏情况。

💎 献宝换灵石：#献宝 F编号
交出指定法宝换取灵石，该法宝将不再由你持有。

另有组队探索与夜战玩法：#副本帮助'''
    if kind == 'props_help':
        return '''🎒【道具使用指引】
道具可用于补给、保护自身或影响群友，具体作用与限制见道具说明。

📦 我的道具：#道具
查看百宝囊中的道具、数量和效果。

🧪 自用道具：#使用 道具名
例如：#使用 清心净衣符

🎯 对群友使用：#使用 道具名 @群友
仅适用于可指定他人的道具；请从群成员列表选择目标。

🛒 获取道具：#商店
购买方式与库存说明：#商店帮助

凝气丹、洗髓丹、寻宝令在购买时直接生效，无需再发送使用指令。

💡 副本内的灵药属于本局补给，使用方式见 #副本帮助。'''
    if kind == 'lightning_help':
        status = '' if rules.duel_enabled else '\n⏸️ 当前未开启，以下为玩法说明。\n'
        return f'''⚡【引雷斗法指引】{status}
双方轮流引雷，触雷者落败；胜负可能带来法宝转移与修为损失。

① 发起与应战
#斗法 @群友
对方在 {rules.invitation_timeout_seconds} 秒内发送 #接受斗法 或 #拒绝斗法。
应战前，发起者可发送 #取消斗法。

② 围观支持
应战后开放 {rules.support_timeout_seconds} 秒支持窗口。
#支持 @参战者 金额
每位围观者每场限支持一次，金额为 {rules.support_minimum}～{rules.support_maximum} 灵石；参战者不能支持，收益按结算结果计算。

③ 轮流引雷
轮到自己时发送 #引雷，须在 {rules.turn_timeout_seconds} 秒内行动；超时判负。

④ 查询进度与结果
#斗法状态：当前阶段与行动提示。
#战绩：近期斗法记录。
#战绩 D编号：指定场次结果。

按人物战力逐轮交锋的玩法见：#决斗帮助'''
    raise ValueError(f'Unknown help module: {kind}')
