"""Strict parsing for cultivation, collection and lightning duel commands."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
import re


class Message(Protocol):
    content: str
    conversation_id: str
    message_kind: str
    mention_state: str
    mentioned_ids: tuple[str, ...]



_OUTER_WHITESPACE = " \t\r\n"
_LEADING_MENTION = re.compile(r"^@[^@\r\n\u2005\u200a]+[\u2005\u200a]+")
_TRAILING_MENTION = re.compile(r"(?:[ \t\r\n\u2005\u200a]+)@[^@\r\n\u2005\u200a]+(?:[\u2005\u200a])?$")
_ITEM_ID = re.compile(r"F[0-9A-Z]{2,31}\Z", re.IGNORECASE)
_DUEL_ID = re.compile(r"D[0-9A-F]{8}\Z", re.IGNORECASE)
_SUPPORT_MENTION = re.compile(r"^#支持\s+@[^@\r\n\u2005\u200a]+[\u2005\u200a]+\s*([0-9]+)\s*$")
_PVP_DUEL_MENTION = re.compile(r"^#决斗\s+@[^@\r\n\u2005\u200a]+[\u2005\u200a]+\s*([0-9]+)\s*$")


@dataclass(frozen=True, slots=True)
class Command:
    kind: str
    argument: str = ""
    target_id: str | None = None
    page: int = 1
    amount: int | None = None


def _command_text(message: Message) -> str | None:
    if not message is not None or message.message_kind != "text":
        return None
    if not isinstance(message.conversation_id, str) or not message.conversation_id.endswith("@chatroom"):
        return None
    if not isinstance(message.content, str):
        return None

    text = message.content.strip(_OUTER_WHITESPACE)
    # A verified atuserlist is the authorization evidence.  Display names are
    # deliberately never used to decide whether a mention is ours.
    if message.mention_state == "explicit_self":
        while match := _LEADING_MENTION.match(text):
            text = text[match.end():].strip(_OUTER_WHITESPACE)
        while match := _TRAILING_MENTION.search(text):
            text = text[:match.start()].strip(_OUTER_WHITESPACE)
    return text


def _with_no_arguments(text: str, literal: str, kind: str, usage: str) -> Command | None:
    if text == literal:
        return Command(kind)
    if text.startswith(literal) and len(text) > len(literal) and text[len(literal)].isspace():
        return Command("usage", usage)
    return None


def _item_command(text: str, literal: str, kind: str, usage: str, *, required: bool = False) -> Command | None:
    if text == literal:
        return Command("usage", usage) if required else Command(kind)
    if not text.startswith(literal) or len(text) == len(literal) or not text[len(literal)].isspace():
        return None
    argument = text[len(literal):].strip()
    if _ITEM_ID.fullmatch(argument) is None:
        return Command("usage", usage)
    return Command(kind, argument.upper())


def _is_command_prefix(text: str, literal: str) -> bool:
    return text == literal or (text.startswith(literal) and len(text) > len(literal) and text[len(literal)].isspace())


def _without_display_mentions(text: str) -> str:
    while match := _LEADING_MENTION.match(text):
        text = text[match.end():].strip(_OUTER_WHITESPACE)
    while match := _TRAILING_MENTION.search(text):
        text = text[:match.start()].strip(_OUTER_WHITESPACE)
    return text


def _challenge_command(message: Message) -> Command | None:
    """Parse a challenge only when one normalized, non-self mention is present."""
    if not message is not None or message.message_kind != "text":
        return None
    if not isinstance(message.conversation_id, str) or not message.conversation_id.endswith("@chatroom"):
        return None
    if not isinstance(message.content, str):
        return None

    raw = message.content.strip(_OUTER_WHITESPACE)
    if message.mention_state != "explicit_other" or len(message.mentioned_ids) != 1:
        candidate = _without_display_mentions(raw)
        return Command("usage", "#斗法 @群友") if _is_command_prefix(candidate, "#斗法") else None
    target_id = message.mentioned_ids[0]
    if not isinstance(target_id, str) or not target_id:
        return Command("usage", "#斗法 @群友") if _is_command_prefix(raw, "#斗法") else None

    # The protocol evidence establishes that precisely one person was really
    # mentioned.  Require exactly one matching display segment as well, so an
    # extra visible @ fragment cannot be silently ignored.
    leading = _LEADING_MENTION.match(raw)
    if leading:
        remainder = raw[leading.end():].strip(_OUTER_WHITESPACE)
    else:
        trailing = _TRAILING_MENTION.search(raw)
        remainder = raw[:trailing.start()].strip(_OUTER_WHITESPACE) if trailing else None
    if remainder == "#斗法":
        return Command("challenge", target_id=target_id)
    if remainder is not None and _is_command_prefix(remainder, "#斗法"):
        return Command("usage", "#斗法 @群友")
    return Command("usage", "#斗法 @群友") if _is_command_prefix(raw, "#斗法") else None


def _history_command(text: str) -> Command | None:
    if text == "#战绩":
        return Command("history")
    if not _is_command_prefix(text, "#战绩"):
        return None
    parts = text.split()
    if len(parts) not in (2, 3) or _DUEL_ID.fullmatch(parts[1]) is None:
        return Command("usage", "#战绩 [D编号 [页码]]")
    page = 1
    if len(parts) == 3:
        raw_page = parts[2]
        if (len(raw_page) > 6 or not raw_page.isascii() or not raw_page.isdecimal()
                or not 1 <= int(raw_page) <= 100_000):
            return Command("usage", "#战绩 [D编号 [页码]]")
        page = int(raw_page)
    return Command("history", parts[1].upper(), page=page)


def _support_command(message: Message) -> Command | None:
    raw = message.content.strip(_OUTER_WHITESPACE)
    candidate = _without_display_mentions(raw)
    if not _is_command_prefix(raw, "#支持") and not _is_command_prefix(candidate, "#支持"):
        return None
    usage = Command("usage", "#支持 @参战者 金额（从群成员列表选择一人，金额为正整数）")
    if message.mention_state != 'explicit_other' or len(message.mentioned_ids) != 1:
        return usage
    # Accept the customary middle mention and the same leading/trailing
    # placements supported by other commands, always using native ID evidence.
    middle = _SUPPORT_MENTION.fullmatch(raw)
    if middle:
        amount_text = middle[1]
    else:
        leading = _LEADING_MENTION.match(raw)
        trailing = _TRAILING_MENTION.search(raw)
        plain = raw[leading.end():].strip() if leading else raw[:trailing.start()].strip() if trailing else ''
        parts = plain.split()
        if len(parts) != 2 or parts[0] != '#支持':
            return usage
        amount_text = parts[1]
    if (len(amount_text) > 19 or not amount_text.isascii() or not amount_text.isdecimal()
            or not 0 < int(amount_text) <= 2**63 - 1):
        return usage
    return Command('support', target_id=message.mentioned_ids[0], amount=int(amount_text))


def _use_prop_command(message: Message) -> Command | None:
    if not message is not None or message.message_kind != "text":
        return None
    if not isinstance(message.conversation_id, str) or not message.conversation_id.endswith("@chatroom"):
        return None
    if not isinstance(message.content, str):
        return None

    raw = message.content.strip(_OUTER_WHITESPACE)
    candidate = _without_display_mentions(raw)
    if not _is_command_prefix(raw, "#使用") and not _is_command_prefix(candidate, "#使用"):
        return None

    usage = Command("usage", "#使用 道具名 [@群友]")
    target_id = None
    if message.mention_state == "explicit_other" and len(message.mentioned_ids) == 1:
        target_id = message.mentioned_ids[0]

    clean_text = candidate.strip()
    if clean_text == "#使用":
        return usage
    parts = clean_text.split()
    if len(parts) < 2 or parts[0] != "#使用":
        return usage
    prop_query = parts[1].strip()
    if not prop_query:
        return usage
    return Command("use_prop", argument=prop_query, target_id=target_id)


def _pvp_duel_command(message: Message) -> Command | None:
    if not message is not None or message.message_kind != "text":
        return None
    if not isinstance(message.conversation_id, str) or not message.conversation_id.endswith("@chatroom"):
        return None
    if not isinstance(message.content, str):
        return None

    raw = message.content.strip(_OUTER_WHITESPACE)
    candidate = _without_display_mentions(raw)
    if _is_command_prefix(raw, "#决斗属性") or _is_command_prefix(candidate, "#决斗属性"):
        return None
    if _is_command_prefix(raw, "#决斗帮助") or _is_command_prefix(candidate, "#决斗帮助"):
        return None
    if not _is_command_prefix(raw, "#决斗") and not _is_command_prefix(candidate, "#决斗"):
        return None

    usage = Command("usage", "#决斗 @群友 金额（1～1000 的正整数，例如：#决斗 @百里 1）")
    if message.mention_state != 'explicit_other' or len(message.mentioned_ids) != 1:
        return usage
    target_id = message.mentioned_ids[0]

    middle = _PVP_DUEL_MENTION.fullmatch(raw)
    if middle:
        amount_text = middle[1]
    else:
        leading = _LEADING_MENTION.match(raw)
        trailing = _TRAILING_MENTION.search(raw)
        plain = raw[leading.end():].strip() if leading else raw[:trailing.start()].strip() if trailing else ''
        parts = plain.split()
        if len(parts) != 2 or parts[0] != '#决斗':
            return usage
        amount_text = parts[1]
    if (len(amount_text) > 10 or not amount_text.isascii() or not amount_text.isdecimal()
            or not 1 <= int(amount_text) <= 1000):
        return usage
    return Command('pvp_duel', target_id=target_id, amount=int(amount_text))



def _dungeon_command(text: str) -> Command | None:
    """Keep a visible phase token on decisions so late messages cannot advance a new round."""
    plain = {
        '#副本': 'help', '#副本帮助': 'help', '#副本状态': 'status',
        '#副本准备': 'ready', '#副本出发': 'start', '#副本退出': 'leave',
        '#副本取消': 'leave',
    }
    for literal, kind in plain.items():
        parsed = _with_no_arguments(text, literal, 'dungeon_' + kind, literal)
        if parsed is not None:
            return parsed
    if _is_command_prefix(text, '#副本创建'):
        arg = text[len('#副本创建'):].strip()
        return (Command('dungeon_create') if not arg
                else Command('usage', '#副本创建'))
    for literal, kind, usage, count in (
        ('#副本加入', 'join', '#副本加入 队伍编号', (1,)),
        ('#职业', 'class', '#职业 [职业名或C编号]；#职业 详情 职业名或C编号', (0, 1, 2)),
        ('#灵根', 'roots', '#灵根 [灵根名或R编号]', (0, 1)),
        ('#灵根领取', 'root_claim', '#灵根领取 基础灵根名或R编号', (1,)),
        ('#灵根装配', 'root_equip', '#灵根装配 槽位 灵根名或R编号', (2,)),
        ('#灵根卸下', 'root_remove', '#灵根卸下 槽位', (1,)),
        ('#灵根兑换', 'root_exchange', '#灵根兑换 灵根名或R编号', (1,)),
        ('#副本选择', 'choose', '#副本选择 阶段编号 1～3', (2,)),
        ('#副本选宝', 'loot', '#副本选宝 阶段编号 1～3', (2,)),
        ('#副本行动', 'act', '#副本行动 阶段编号 进攻/技能/绝技/防御/闪避 前段或后段/回气/灵药/救援/部位 [目标] [丹修过量目标]', (2, 3, 4)),
    ):
        if not _is_command_prefix(text, literal):
            continue
        arg = text[len(literal):].strip()
        parts = arg.split()
        if len(parts) not in count or len(arg) > 120:
            return Command('usage', usage)
        if kind == 'class' and parts:
            if (parts[0] == '详情' and len(parts) != 2) or (len(parts) == 2 and parts[0] != '详情'):
                return Command('usage', usage)
        if kind in ('choose', 'loot', 'act'):
            if re.fullmatch(r'[A-Za-z0-9.:-]{1,32}', parts[0]) is None:
                return Command('usage', usage)
            if kind != 'act' and parts[1] not in ('1', '2', '3'):
                return Command('usage', usage)
        if kind in ('root_equip', 'root_remove') and parts[0] not in ('1', '2', '3'):
            return Command('usage', usage)
        return Command('dungeon_' + kind, ' '.join(parts))
    return None


def parse_command(message: Message) -> Command | None:
    """Return only a complete, supported group command.

    Validation of a dao name belongs to the service so registration and rename
    requests use the same normalization and uniqueness rules.
    """
    text = _command_text(message)
    if text is None:
        return None

    dungeon = _dungeon_command(text)
    if dungeon is not None:
        return dungeon

    if _is_command_prefix(text, '#祈愿') or _is_command_prefix(text, '#决斗开挂'):
        usage = Command('usage', '#祈愿 god|lucky [1～20]（仅管理员可许愿）')
        parts = text.split()
        if len(parts) not in (2, 3) or parts[1] not in ('god', 'one_hit', 'immortal', 'lucky'):
            return usage
        if len(parts) == 3 and (parts[1] != 'lucky' or len(parts[2]) > 2 or not parts[2].isascii()
                                or not parts[2].isdecimal() or not 1 <= int(parts[2]) <= 20):
            return usage
        return Command('pvp_cheat', parts[1], amount=int(parts[2]) if len(parts) == 3 else None)

    pvp_duel = _pvp_duel_command(message)
    if pvp_duel is not None:
        return pvp_duel

    support = _support_command(message)
    if support is not None:
        return support

    challenge = _challenge_command(message)
    if challenge is not None:
        return challenge

    use_prop = _use_prop_command(message)
    if use_prop is not None:
        return use_prop

    for literal, kind in (
        ("#新手帮助", "beginner_help"),
        ("#修炼帮助", "cultivation_help"),
        ("#秘境帮助", "exploration_help"),
        ("#道具帮助", "props_help"),
        ("#斗法帮助", "lightning_help"),
    ):
        parsed = _with_no_arguments(text, literal, kind, literal)
        if parsed is not None:
            return parsed

    if text in ("#道具", "#百宝囊", "#我的道具"):
        return Command("props")
    if (text.startswith("#道具") and len(text) > len("#道具") and text[len("#道具")].isspace()) or \
       (text.startswith("#百宝囊") and len(text) > len("#百宝囊") and text[len("#百宝囊")].isspace()):
        return Command("props")

    if text == "#修仙帮助":
        return Command("help")
    if text.startswith("#修仙帮助") and len(text) > len("#修仙帮助") and text[len("#修仙帮助")].isspace():
        return Command("usage", "#修仙帮助")

    if text == "#修仙":
        return Command("profile")
    if text.startswith("#修仙") and len(text) > len("#修仙") and text[len("#修仙")].isspace():
        argument = text[len("#修仙"):].strip()
        return Command("profile", argument) if argument else Command("profile")

    for literal, kind, usage in (
        ("#修炼", "cultivate", "#修炼"),
        ("#自主修炼", "self_cultivate", "#自主修炼"),
        ("#采矿", "mine", "#采矿"),
        ("#开采", "mine", "#采矿"),
        ("#采灵", "mine", "#采矿"),
        ("#突破", "breakthrough", "#突破"),
        ("#秘境", "explore", "#秘境"),
        ("#宝录", "book", "#宝录"),
        ("#仙榜", "ranking", "#仙榜"),
    ):
        parsed = _with_no_arguments(text, literal, kind, usage)
        if parsed is not None:
            return parsed

    for prefix in ("#法宝帮助", "#法宝效果", "#法宝图鉴", "#法宝神通", "#法宝help"):
        if text == prefix:
            return Command("artifact_help")
        if text.startswith(prefix) and len(text) > len(prefix) and text[len(prefix)].isspace():
            return Command("usage", "#法宝帮助")

    if text.startswith("#法宝") and len(text) > len("#法宝") and text[len("#法宝")].isspace():
        sub = text[len("#法宝"):].strip()
        if sub in ("帮助", "help", "效果", "图鉴", "神通"):
            return Command("artifact_help")

    parsed = _item_command(text, "#法宝", "inventory", "#法宝 [F编号]")
    if parsed is not None:
        return parsed
    parsed = _item_command(text, "#献宝", "offer", "#献宝 F编号", required=True)
    if parsed is not None:
        return parsed

    if text in ("#商店", "#修仙商店"):
        return Command("shop")
    if (text.startswith("#商店") and len(text) > len("#商店") and text[len("#商店")].isspace()) or \
       (text.startswith("#修仙商店") and len(text) > len("#修仙商店") and text[len("#修仙商店")].isspace()):
        arg = text.split(maxsplit=1)[1].strip() if len(text.split(maxsplit=1)) > 1 else ""
        if arg in ("帮助", "help"):
            return Command("shop_help")
        return Command("usage", "#商店 或 #购买 物品名")

    if text == "#商店帮助":
        return Command("shop_help")
    if text.startswith("#商店帮助") and len(text) > len("#商店帮助") and text[len("#商店帮助")].isspace():
        return Command("usage", "#商店帮助")

    if text == "#购买":
        return Command("usage", "#购买 物品名（例如：#购买 替身草人 或 #购买 1）")
    if text.startswith("#购买") and len(text) > len("#购买") and text[len("#购买")].isspace():
        argument = text[len("#购买"):].strip()
        if not argument:
            return Command("usage", "#购买 物品名（例如：#购买 替身草人 或 #购买 1）")
        return Command("buy", argument)

    if text in ("#魔契", "#魔鬼交易"):
        return Command("devil_status")
    if (text.startswith("#魔契") and len(text) > len("#魔契") and text[len("#魔契")].isspace()) or \
       (text.startswith("#魔鬼交易") and len(text) > len("#魔鬼交易") and text[len("#魔鬼交易")].isspace()):
        arg = text.split(maxsplit=1)[1].strip() if len(text.split(maxsplit=1)) > 1 else ""
        if arg in ("帮助", "help"):
            return Command("devil_help")
        if arg in ("签订", "签约", "深化"):
            return Command("devil_sign")
        return Command("usage", "#魔契 或 #签订魔契")

    for prefix in ("#魔契帮助", "#魔鬼交易帮助"):
        if text == prefix:
            return Command("devil_help")
        if text.startswith(prefix) and len(text) > len(prefix) and text[len(prefix)].isspace():
            return Command("usage", "#魔契帮助")

    for prefix in ("#签订魔契", "#签订契约", "#深化魔契"):
        if text == prefix:
            return Command("devil_sign")
        if text.startswith(prefix) and len(text) > len(prefix) and text[len(prefix)].isspace():
            return Command("usage", "#签订魔契")

    for literal, kind, usage in (
        ("#接受斗法", "accept", "#接受斗法"),
        ("#拒绝斗法", "reject", "#拒绝斗法"),
        ("#取消斗法", "cancel", "#取消斗法"),
        ("#引雷", "lightning", "#引雷"),
        ("#斗法状态", "duel_status", "#斗法状态"),
        ("#接受决斗", "accept_pvp_duel", "#接受决斗"),
        ("#拒绝决斗", "reject_pvp_duel", "#拒绝决斗"),
        ("#取消决斗", "cancel_pvp_duel", "#取消决斗"),
        ("#撤销决斗", "cancel_pvp_duel", "#取消决斗"),
        ("#决斗状态", "pvp_duel_status", "#决斗状态"),
        ("#继续决斗", "continue_pvp_duel", "#继续决斗"),
        ("#继续", "continue_pvp_duel", "#继续"),
        ("#确认决斗", "continue_pvp_duel", "#确认决斗"),
        ("#投降", "surrender_pvp_duel", "#投降"),
        ("#认输", "surrender_pvp_duel", "#认输"),
        ("#决斗投降", "surrender_pvp_duel", "#决斗投降"),
        ("#决斗属性", "pvp_stats", "#决斗属性"),
        ("#决斗帮助", "pvp_help", "#决斗帮助"),
    ):
        parsed = _with_no_arguments(text, literal, kind, usage)
        if parsed is not None:
            return parsed

    if text == "#决斗" or (text.startswith("#决斗") and len(text) > len("#决斗") and text[len("#决斗")].isspace()):
        return Command("usage", "#决斗 @群友 金额（1～1000 的正整数，例如：#决斗 @百里 1）")
    parsed = _history_command(text)
    if parsed is not None:
        return parsed
    return None
