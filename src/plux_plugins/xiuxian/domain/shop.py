"""Shop items, inventory and purchase logic for the xiuxian cultivation game."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True, slots=True)
class ShopItem:
    id: str
    name: str
    kind: str  # 'artifact' or 'consumable'
    price: int
    daily_stock: int | None
    description: str
    aliases: tuple[str, ...] = ()

    @property
    def daily_limit(self) -> int | None:
        return self.daily_stock


@dataclass(frozen=True, slots=True)
class PropTemplate:
    id: str
    name: str
    description: str
    target_required: bool = True
    aliases: tuple[str, ...] = ()


PROP_TEMPLATES: dict[str, PropTemplate] = {
    "raoxin_fu": PropTemplate("raoxin_fu", "扰心符", "破坏打坐：对目标下咒，目标下一次【#自主修炼】必定走火入魔（损失修为）。", target_required=True, aliases=("扰心符", "扰心")),
    "duanmai_san": PropTemplate("duanmai_san", "断脉散", "阻滞气脉：洒向目标，目标当前的【#自主修炼】冷却时间立即增加 60 分钟。", target_required=True, aliases=("断脉散", "断脉")),
    "qieling_gu": PropTemplate("qieling_gu", "窃灵蛊", "分润气运：潜伏于目标气海，目标下次【#修炼】时，其收益的 30% 修为与灵石被偷取转移给施术者！", target_required=True, aliases=("窃灵蛊", "窃灵")),
    "sanling_chen": PropTemplate("sanling_chen", "散灵尘", "污染气场：目标今日无法执行【#突破】（心绪不宁，强行突破必遭反噬）。", target_required=True, aliases=("散灵尘", "散灵")),
    "qingxin_fu": PropTemplate("qingxin_fu", "清心净衣符", "防御解咒：给自己使用立即清除身上所有负面妨碍；或随身携带自动抵消一次他人暗算。", target_required=False, aliases=("清心净衣符", "清心符", "净衣符")),
}


SHOP_ITEMS: tuple[ShopItem, ...] = (
    ShopItem(
        id="tishen_caoren",
        name="替身草人",
        kind="artifact",
        price=88,
        daily_stock=None,
        description="护身法器。入背包占1格。斗法落败时自动触发，保全真实法宝",
        aliases=("替身草人", "草人", "替身", "1", "tishen_caoren"),
    ),
    ShopItem(
        id="ningqi_dan",
        name="凝气丹",
        kind="consumable",
        price=150,
        daily_stock=5,
        description="纯阳丹药。服用直接增加 20 点修为。",
        aliases=("凝气丹", "凝气", "2", "ningqi_dan"),
    ),
    ShopItem(
        id="xisui_dan",
        name="洗髓丹",
        kind="consumable",
        price=10,
        daily_stock=50,
        description="伐毛洗髓。服用重置自主修炼冷却时间，可立即再次自主修炼。",
        aliases=("洗髓丹", "洗髓", "3", "xisui_dan"),
    ),
    ShopItem(
        id="xunbao_ling",
        name="寻宝令",
        kind="consumable",
        price=100,
        daily_stock=5,
        description="秘境符印。使用重置今日秘境探索资格，今日可再次探索秘境一次。",
        aliases=("寻宝令", "寻宝", "4", "xunbao_ling"),
    ),
    ShopItem(
        id="raoxin_fu",
        name="扰心符",
        kind="prop",
        price=15,
        daily_stock=10,
        description="破坏打坐。入百宝囊占1格。对目标下咒，目标下次【#自主修炼】必走火入魔（损失修为）",
        aliases=("扰心符", "扰心", "5", "raoxin_fu"),
    ),
    ShopItem(
        id="duanmai_san",
        name="断脉散",
        kind="prop",
        price=30,
        daily_stock=5,
        description="阻滞气脉。入百宝囊占1格。洒向目标，目标当前【#自主修炼】冷却立即增加 60 分钟",
        aliases=("断脉散", "断脉", "6", "duanmai_san"),
    ),
    ShopItem(
        id="qieling_gu",
        name="窃灵蛊",
        kind="prop",
        price=100,
        daily_stock=3,
        description="分润气运。入百宝囊占1格。潜伏目标气海，目标下次【#修炼】30%修为与灵石被偷取转移给你",
        aliases=("窃灵蛊", "窃灵", "7", "qieling_gu"),
    ),
    ShopItem(
        id="sanling_chen",
        name="散灵尘",
        kind="prop",
        price=50,
        daily_stock=3,
        description="污染气场。入百宝囊占1格。目标今日无法执行【#突破】（强行突破必遭反噬）",
        aliases=("散灵尘", "散灵", "8", "sanling_chen"),
    ),
    ShopItem(
        id="qingxin_fu",
        name="清心净衣符",
        kind="prop",
        price=60,
        daily_stock=2,
        description="护体灵符。购买后概率获得，入百宝囊占1格。防御解咒：清除自身负面妨碍；或随身自动抵消他人暗算",
        aliases=("清心净衣符", "清心符", "净衣符", "9", "qingxin_fu"),
    ),
)


def find_shop_item(query: str) -> ShopItem | None:
    """Find a shop item by id, name, index (1-based), or alias."""
    cleaned = query.strip()
    if not cleaned:
        return None
    for item in SHOP_ITEMS:
        if cleaned.lower() == item.id.lower() or cleaned == item.name or cleaned in item.aliases:
            return item
    return None


def format_shop(player: Mapping[str, object], purchases: Mapping[str, int]) -> str:
    """Render the text display of the shop shelves for a player."""
    dao_name = player["dao_name"] if "dao_name" in player.keys() else "修士"
    stones = player["spirit_stones"] if "spirit_stones" in player.keys() else 0

    lines = [
        "╭─── 🛒 仙家商店 ───╮",
        f" {dao_name} · 💎 灵石：{stones}",
        "",
    ]
    for idx, item in enumerate(SHOP_ITEMS, 1):
        if item.daily_stock is not None:
            sold = purchases.get(item.id, 0)
            remaining = max(0, item.daily_stock - sold)
            stock_text = f"今日库存：{remaining}/{item.daily_stock}"
        else:
            stock_text = "今日库存：充裕"

        if item.kind == "artifact":
            type_desc = "护身法器（占用法宝格）"
        elif item.kind == "prop":
            type_desc = "秘传符箓（占用道具格）"
        elif item.id == "xunbao_ling":
            type_desc = "秘境神符（即时生效）"
        else:
            type_desc = "灵丹妙药（即时生效）"

        lines.extend([
            f" {idx}. 📦 {item.name} · {item.price} 灵石",
            f"    ├ 类别：{type_desc}",
            f"    ├ 说明：{item.description}",
            f"    └ 库存：{stock_text}",
            "",
        ])

    lines.extend([
        " 👉 选购指令：#购买 物品名 或 #购买 编号",
        " 示例：#购买 扰心符 或 #购买 5",
        " 💡 每日库存，售完即止",
        "╰─────────────╯",
    ])
    return "\n".join(lines)


def format_shop_help() -> str:
    """Return instructions on how to use the shop."""
    return (
        "🛒【商店指引】\n"
        "• 查看货架：发送【#商店】查看今日商品及剩余库存\n"
        "• 选购商品：发送【#购买 物品名】或【#购买 编号】\n"
        "  例如：#购买 替身草人 或 #购买 1\n"
        "• 库存机制：每日零点补货\n"
        "• 灵石获取：日常【#修炼】、【#采矿】或【#献宝】闲置法宝。\n\n"
        "🎒 符箓道具存入【#道具】；凝气丹、洗髓丹、寻宝令购买时直接生效。\n"
        "道具使用方法见【#道具帮助】。"
    )
