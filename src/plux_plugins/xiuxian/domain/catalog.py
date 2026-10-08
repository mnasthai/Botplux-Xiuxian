"""Immutable artifact templates used by the cultivation game."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Mapping


@dataclass(frozen=True, slots=True)
class ArtifactTemplate:
    """A named artifact type; player-owned artifacts are separate instances."""

    id: str
    name: str
    rarity: str
    description: str
    ability_name: str = ""
    ability_desc: str = ""


RARITY_NAMES: Final[Mapping[str, str]] = MappingProxyType({
    "artifact": "法器",
    "spirit": "灵器",
    "ancient": "古宝",
    "treasure": "至宝",
})

REALM_NAMES: Final[Mapping[str, str]] = MappingProxyType({
    "qi": "炼气",
    "foundation": "筑基",
    "core": "金丹",
    "nascent": "元婴",
})


_TEMPLATES: Final[tuple[ArtifactTemplate, ...]] = (
    ArtifactTemplate("qingfeng_jian", "青锋剑", "artifact", "剑锋尚利，随身之物。", "剑意", "剑气淬体，斗法围观支持胜利有25%概率获取两倍灵石。"),
    ArtifactTemplate("bichen_zhu", "避尘珠", "artifact", "袖中藏一珠，出门少三分狼狈。", "辟邪", "#自主修炼 失败走火入魔时，庇护心神，修为损失减半（仅扣 1 修为）。"),
    ArtifactTemplate("qingtong_ling", "青铜铃", "artifact", "铃声清脆，据说夜半不能随意摇动。"),
    ArtifactTemplate("juqi_hulu", "聚气葫芦", "artifact", "葫芦不大，倒也能收拢些许灵气。", "纳灵", "每日 #修炼 时，额外凝聚灵气，额外获得 +10 灵石 与 +2 修为。"),
    ArtifactTemplate("xuantie_yin", "玄铁印", "artifact", "其貌不扬，拿在手里颇有分量。", "沉心", "稳固道基，#自主修炼 成功率 提高 15%（从 50% 提升至 65%）。"),
    ArtifactTemplate("songwen_jian", "松纹剑", "artifact", "剑身刻着松纹，随身之物。", "剑意", "剑气淬体，斗法围观支持胜利有25%概率获取两倍灵石。"),
    ArtifactTemplate("qingshuang_jian", "青霜剑", "spirit", "出鞘时寒意扑面，剑脊凝着薄霜。", "霜刃", "#斗法 胜利时败者额外损失至多 10 修为；#决斗 每次攻击附加 8 点真伤。"),
    ArtifactTemplate("biyu_hulu", "碧玉葫芦", "spirit", "通体温润，内里偶尔传出流水声。", "回泉", "每日 #秘境 探索若只抽到法器，返还 30 灵石。"),
    ArtifactTemplate("zhenhun_ling", "镇魂铃", "spirit", "一声铃响，四下忽然安静下来。", "定魄", "斗法被紫霄神雷劈中时，有 30% 概率镇住神魂，免除败者的 20 修为扣除。"),
    ArtifactTemplate("liuguang_suo", "流光梭", "spirit", "银光一闪，便从指间滑向另一侧。", "疾行", "#自主修炼 冷却缩短 30 分钟；与御风扇合计最多缩短 35 分钟。"),
    ArtifactTemplate("zijin_bo", "紫金钵", "spirit", "佛门化缘降妖之宝，金光微漾。", "聚灵", "每日 #修炼 额外获得 5 修为、20 灵石。"),
    ArtifactTemplate("zhaoyao_jing", "照妖镜", "ancient", "镜面蒙尘，照来照去，先照见自己。", "破妄", "每日 #秘境 将至多 15% 的法器掉落权重移往高品质；抽中法宝额外获得 25 灵石，空池返还提高 25%。"),
    ArtifactTemplate("qiankun_ding", "乾坤鼎", "ancient", "小鼎腹中，仿佛另有一片天地。", "内藏乾坤", "持有时储物袋上限 +2（默认可持 8 件）；#决斗 中不提供攻击力。"),
    ArtifactTemplate("fuhai_yin", "覆海印", "ancient", "印底留有潮痕，近耳可闻海浪。", "涌现", "#突破 境界所需的灵石减少 10%。"),
    ArtifactTemplate("longwen_gu", "龙纹鼓", "ancient", "鼓皮久经岁月，轻叩仍有龙吟回声。", "龙吟", "每自然日首次 #斗法 战败时，龙魂护体，免除修为扣除。"),
    ArtifactTemplate("taixu_shenzhen", "太虚神针", "ancient", "细如牛毛，专破护身罡气。", "破气", "#斗法 夺中替身草人时有 10% 概率改夺真实法宝；#决斗 每次攻击有 20% 概率降低1/4对方防御。"),
    ArtifactTemplate("wanhun_fan", "万魂幡", "treasure", "幡动阴风起，百鬼夜行来。", "噬魂摄宝", "斗法获胜夺取败者法宝时，有 25% 概率额外从秘境卷出一件随机普通法器！"),
    ArtifactTemplate("shanhe_tu", "山河图", "treasure", "画中山河缓缓流转，藏着人间。", "洞天福地", "每日修炼收益提升 20%（获得 60 修为、120 灵石）。"),
    ArtifactTemplate("wuxing_qi", "五行旗", "treasure", "天地未分时的残旗，全服仅此一件。", "辟易", "绝对免疫所有暗算道具的负面妨碍效果。"),
    ArtifactTemplate("tishen_caoren", "替身草人", "artifact", "扎制精致的草人，内藏替身灵符，斗法败北时代主遭劫。", "替身", "斗法落败结算时优先替主遭劫碎裂消散，保全其它法宝。"),
    ArtifactTemplate("taomu_jian", "桃木剑", "artifact", "雷击桃木所削，凡间道士常用之物。"),
    ArtifactTemplate("baiyu_banzhi", "白玉扳指", "artifact", "质地温润，略含微弱灵气，佩于指间平添贵气。"),
    ArtifactTemplate("jingtie_yin", "精铁印", "artifact", "凡铁百炼压制而成，沉甸甸的压手。"),
    ArtifactTemplate("huoyun_pei", "火云佩", "artifact", "玉佩里封着一缕暖火。", "温养", "每日 #修炼 额外获得 3 修为。"),
    ArtifactTemplate("lingquan_ping", "灵泉瓶", "artifact", "瓶底总有一滴清泉。", "采露", "#采矿 额外获得 5 灵石。"),
    ArtifactTemplate("xuanjia_fu", "玄甲符", "artifact", "符纹结成一层薄甲。", "护甲", "#决斗 防御 +3。"),
    ArtifactTemplate("yufeng_shan", "御风扇", "artifact", "扇动时有轻风拂过经脉。", "御风", "#自主修炼 冷却缩短 5 分钟；与流光梭合计最多缩短 35 分钟。"),
    ArtifactTemplate("qinglian_deng", "青莲灯", "spirit", "灯焰如莲，静坐时长明。", "养元", "每日 #修炼 额外获得 8 修为。"),
    ArtifactTemplate("xuanbing_jia", "玄冰甲", "spirit", "薄甲流转寒光。", "凝甲", "#决斗 防御 +8；与玄甲符合计最多 +11。"),
    ArtifactTemplate("huixin_yu", "慧心玉", "spirit", "玉中有细微的心跳声。", "明心", "#自主修炼 成功率提高 8 个百分点；与玄铁印合计最多提高 23 个百分点。"),
    ArtifactTemplate("xunling_pan", "寻灵盘", "spirit", "盘针会指向秘境灵脉。", "寻径", "每日 #秘境 探索花费减少 10 灵石。"),
    ArtifactTemplate("tongtian_bei", "通天碑", "ancient", "碑面刻着残缺的吐纳法。", "悟道", "每日 #修炼 的基础修为额外增加 20%。"),
    ArtifactTemplate("xuanyuan_jing", "玄元镜", "ancient", "镜面映出突破关隘。", "省元", "#突破 所需修为减少 10%。"),
    ArtifactTemplate("tianlei_gu", "天雷鼓", "ancient", "鼓声里夹着细碎雷鸣。", "雷震", "#决斗 每次攻击附加 10 点真伤。"),
    ArtifactTemplate("hongmeng_zhu", "鸿蒙珠", "treasure", "珠内混沌缓缓旋转。", "悟真", "#自主修炼 成功时额外获得 4 修为。"),
    ArtifactTemplate("wujie_tu", "无界图", "treasure", "图上绘有无尽小径。", "归元", "每日 #秘境 探索获得法宝时返还 20 灵石。"),
)

CATALOG: Final[Mapping[str, ArtifactTemplate]] = MappingProxyType({template.id: template for template in _TEMPLATES})


def format_artifact_help() -> str:
    """Return a comprehensive guide of all artifact supernatural abilities."""
    lines = ["📖【大爱仙途 · 法宝神通全录】",
             "🎒 法宝存放于储物袋即可触发，【古宝与至宝在秘境中仅有一件】。相同模板持有多件只生效一次；不同模板效果可叠加，按说明中的上限结算。",
             "⚔️ #斗法 为引雷夺宝，#决斗 为灵石擂台；两种效果分别结算。"]
    for rarity, heading in (("treasure", "✨【至宝】"), ("ancient", "🔮【古宝】"),
                            ("spirit", "🎐【灵器】"), ("artifact", "⚔️【法器】")):
        lines.extend(("", heading))
        for template in _TEMPLATES:
            if template.rarity == rarity:
                ability = f"【{template.ability_name}】" if template.ability_name else ""
                lines.append(f"• {template.name}{ability}：{template.ability_desc or '无随身神通；仍计入 #决斗 法宝攻击，可收藏或献宝。'}")
    lines.extend(("", "👉 我的法宝：#法宝", "👉 法宝详情：#法宝 F编号"))
    return "\n".join(lines)
