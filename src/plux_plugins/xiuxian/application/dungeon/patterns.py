"""Fixed enemy action patterns for the stance-based dungeon combat design.

Multipliers here are final coefficients.  Do not apply the legacy range/status
discount or the legacy boss phase multiplier a second time.
"""

from __future__ import annotations

from typing import Any


def _hit(target: str, multiplier: float, weight: str,
         effects: tuple[str, ...] = (), **extra: Any) -> dict[str, Any]:
    return {"target": target, "multiplier": multiplier, "weight": weight,
            "effects": list(effects), **extra}


def _round(name: str, cue: str, *, front: dict[str, Any] | None = None,
           back: dict[str, Any] | None = None, recovery: bool = False,
           part: bool = False, summon: bool = False, mark_setup: bool = False,
           lock_thunder: bool = False, expose: float = 0.0,
           charge: bool = False, strong: bool = False) -> dict[str, Any]:
    return {"name": name, "cue": cue, "front": front, "back": back,
            "recovery": recovery, "part": part, "summon": summon,
            "mark_setup": mark_setup, "lock_thunder": lock_thunder,
            "expose": expose, "charge": charge, "strong": strong}


def _mob(l_name: str, l_cue: str, l_target: str, l_mult: float,
         q_cue: str, s_name: str, s_cue: str, s_target: str, s_mult: float,
         r_cue: str, *, l_segment: str = "front", s_segment: str = "back",
         l_effects: tuple[str, ...] = (), s_effects: tuple[str, ...] = (),
         part: bool = False, expose: float = 0.0,
         s_extra: dict[str, Any] | None = None) -> dict[str, Any]:
    light = _hit(l_target, l_mult, "light", l_effects)
    heavy = _hit(s_target, s_mult, "heavy", s_effects, **(s_extra or {}))
    return {"phase1": [
        _round(l_name, l_cue, **{l_segment: light}),
        _round(f"蓄势{s_name}", q_cue, part=part, charge=True),
        _round(s_name, s_cue, **{s_segment: heavy}, strong=True),
        _round("收势", r_cue, recovery=True, expose=expose),
    ]}


PATTERNS: dict[str, dict[str, Any]] = {
    # Daytime small enemies, M13–M20.
    "M13": _mob("啃咬", "灰耳贴地窜近一人，门牙先露出。", "main", .80,
                 "它缩进墙根，后腿绷紧，目光钉住一人。", "窜扑",
                 "灰影跃离墙根，却在半空收爪，身形短暂停滞。", "main", 1.20,
                 "灰耳落地后忙着调整脚步，暂未再扑。"),
    "M14": _mob("黏舌", "鼓起的喉囊一缩，黏舌直伸向一人。", "main", .80,
                 "它伏低湿滑的腹部，后脚压进水洼。", "扑水",
                 "水洼向外裂开，它却仍收着后腿，腹部迟迟未离水面。", "main", 1.20,
                 "青苔蛙落回浅水，水纹逐渐平静。", l_effects=("attack_down",)),
    "M15": _mob("甩砂", "尾尖已铲起砂粒，随即甩向被盯住的人影。", "two", .80,
                 "它侧身拖长砂痕，粗尾卷到身后。", "砂尾扫",
                 "砂痕外沿扬起，尾根却仍向身后拧紧，尚未松开。", "all", .96,
                 "尾巴扫空地面，它停下重新聚拢砂粒。"),
    "M16": _mob("蜂刺", "一枚亮刺从蜂腹前方指向一人。", "main", .80,
                 "蜂群拢成一点，嗡鸣压低，围住一人。", "蜂群",
                 "密集蜂影已拢成一团，却在目标身边短暂悬停。", "main", 1.08,
                 "蜂群四散回旋，暂时没有再压近。",
                 l_effects=("poison",), s_effects=("poison",)),
    "M17": _mob("夹击", "两只钳尖合拢，先探向一人。", "main", .80,
                 "赤壳蟹举钳蓄力，蟹壳边缘露出一道缝。", "横钳",
                 "钳身已横转，张开的钳口仍停着，钳根继续收紧。", "main", 1.20,
                 "双钳卡在地面片刻，赤壳蟹缓慢拔出。", part=True),
    "M18": _mob("啄影", "夜纹雀盯住一名伤者，头颈骤然伸出，尖喙已迫近。",
                 "low_two", .80, "它盘旋到选定落点上方，双翼收成窄线。", "掠翼",
                 "窄翼已经收紧，鸟身却在高处稍停，还未压下。", "two", 1.08,
                 "它飞回枝头抖落羽屑，暂时不再俯冲。"),
    "M19": _mob("迷雾", "白雾裹着狸爪，先探向一人肩侧。", "main", .80,
                 "雾绒狸蹲伏在雾里，尾端停在一人身前。", "扑影",
                 "雾里短影微微前倾，后腿却仍收在腹下，尾端一顿。", "main", 1.20,
                 "雾影退回原处，狸爪重新贴地。", l_effects=("attack_down",)),
    "M20": _mob("火屑", "炭尾一抖，几粒火屑先飞向一人。", "main", .80,
                 "它拱起背脊，尾上红炭悬在全队上方。", "炭雨",
                 "红炭仍悬在空中，它的尾端又收紧了一寸。", "all", .864,
                 "尾火暗下去，炭尾鼬缩起身子歇息。",
                 l_effects=("burn",), s_effects=("burn",)),

    # Daytime elites, M05–M08.
    "M05": _mob("藤鞭", "一根枯藤先从脚边抽向一人。", "main", 1.05,
                 "数条藤蔓收向木魈掌心，藤端指住一人。", "缠枝",
                 "藤端已围住人影，粗枝却仍向掌心收缩，尚未合拢。", "main", 2.00,
                 "木魈慢慢拉回藤蔓，枝条尚未再次伸出。",
                 l_effects=("attack_down",), s_effects=("attack_down",)),
    "M06": _mob("火羽", "一枚赤羽从喙边飘向一人。", "main", 1.05,
                 "双翼张开，火色沿羽脉铺满半空。", "炎翼",
                 "炎羽已经铺满半空，翼根仍在向上提起，尚未下压。", "all", 1.44,
                 "炎羽鸦收翼落枝，羽火微暗。",
                 l_effects=("burn",), s_effects=("burn",)),
    "M07": _mob("溅水", "鱼尾拨水，水珠先向众人溅开。", "all", 1.05,
                 "鱼身贴近水面，鱼鳍翻出亮白一面。", "潮线",
                 "水线已隆成薄墙，鱼鳍却仍压住水面，迟迟未松。", "all", 1.60,
                 "水线退去，鱼身停在浅滩换气。", part=True),
    "M08": _mob("摇铃", "腕上小铃朝一人轻晃，猿爪随之探出。", "main", 1.05,
                 "它将金铃举到耳侧，另一只手指住一人。", "金铃敲击",
                 "金铃高悬着，猿臂却又缩回半寸，腕上的小铃忽然停响。", "main", 2.00,
                 "灵猿抱铃后撤，短暂伏在石上。",
                 l_effects=("vulnerable",), s_effects=("vulnerable",)),

    # First night, M01/M03/M09/M10.
    "M01": _mob("追猎", "血牙沿着伤者脚印低跑，獠牙先擦向一人。", "low_two", .75,
                 "它定住前爪，紧盯一人的肩臂动作，后腿正向后压。", "血牙扑击",
                 "后腿已经绷紧，狼背却仍向下压，獠牙悬而未动。", "main", 6.30,
                 "妖狼落地磨爪，暂时不再追扑。",
                 s_extra={"offensive_multiplier": 7.20}),
    "M03": _mob("毒牙", "一对细牙从雾里探向一人。", "main", .75,
                 "蛛腹鼓起，雾丝在众人脚边织成圈。", "雾网",
                 "雾丝已围到众人脚边，蛛腹却仍在鼓起，网面尚未合拢。", "all", 4.536,
                 "妖蛛伏在空网中央，口器暂时合拢。",
                 l_effects=("poison",), s_effects=("poison",)),
    "M09": _mob("碎石", "石面裂开，松动的碎石仍卡在缝中，石脸继续抬高。", "two", .75,
                 "它将整张石脸昂起，裂缝正对一人。", "石面重砸",
                 "石脸骤然向下翻倒，整个身躯已越过支撑的石足。", "main", 6.30,
                 "沉重石脸卡进地面，石面鬼正费力拔出。",
                 l_segment="back", s_segment="front"),
    "M10": _mob("霜爪", "霜爪在地上划白痕，先伸向一人。", "main", .75,
                 "它张口吸入冷雾，霜白气流盖过众人。", "霜息",
                 "冷雾已凝在齿间，胸腹却仍鼓起，最后一口气尚未吐出。", "all", 5.04,
                 "冷雾散去，霜息狼压低头颈喘息。",
                 l_effects=("attack_down",), s_effects=("attack_down",)),

    # Second night, M02/M04/M11/M12/M21–M24.
    "M02": _mob("甲尾扫", "厚尾从地面抬起，先拂向众人脚边。", "all", .80,
                 "它拱起背甲，甲片间露出一条明缝。", "甲刺横扫",
                 "甲刺已立起，厚尾却仍反向收紧，背甲迟迟未转。", "all", 7.20,
                 "甲刺缓缓伏平，玄甲巨兽停步换气。", part=True),
    "M04": _mob("雷踏", "前蹄已悬在地面上方，雷纹仍向蹄下聚拢，尚未踏实。", "all", .80,
                 "犀角蓄着白光，角根露在低垂的头侧。", "雷角冲撞",
                 "后蹄猛然蹬地，整个犀身已经越过低垂的雷角。", "all", 7.20,
                 "雷角犀冲过后回身刹住蹄步。", l_segment="back",
                 s_segment="front", part=True),
    "M11": _mob("鳞粉", "月白鳞粉先落向一人肩头。", "main", .80,
                 "双翅并拢，细鳞在众人上方积成薄云。", "月翅",
                 "鳞云已经压低，双翅却仍拢在身侧，翅尖停住一瞬。", "all", 6.48,
                 "蚀月蛾伏在暗处，翅粉缓慢沉落。",
                 l_effects=("bleed",), s_effects=("bleed",)),
    "M12": _mob("骨锤", "骨锤先垂下，敲向一人身前。", "main", .80,
                 "胸口骨扣撑开甲片，数根骨刺悬在众人上方。", "骨雨",
                 "骨刺仍悬在空中，骨扣继续向外撑开，骨锤尚未落下。", "all", 7.20,
                 "骨刺落尽，铸骨傀正拾起松开的骨甲。", part=True),
    "M21": _mob("毒钩", "毒钩已抬到肩高，尾节仍逐节收拢，刺尖尚未弹出。", "main", .80,
                 "背脊裂缝张开，尾刺直指一人胸前。", "裂脊穿刺",
                 "尾节猛然绷直，刺尖已越过它的前钳。", "main", 8.10,
                 "蝎尾扎入土中，妖蝎正慢慢抽出。", l_segment="back",
                 s_segment="front", l_effects=("poison",), s_effects=("poison",)),
    "M22": _mob("潮刃", "黑水先在众人脚边划出几道水刃。", "all", .80,
                 "巨鳐潜到水下，黑潮在众人周围隆起。", "黑潮",
                 "黑潮已高过背鳍，巨鳐却仍压着水底，浪头尚未翻下。", "all", 7.20,
                 "黑潮退落，巨鳐翻身露出浅色腹面。", expose=.25),
    "M23": _mob("骨火", "骨灯吐出一粒青火，先追向一人。", "main", .80,
                 "骨灯升到选定落点上方，灯焰指向被照住的人影。", "灯阵",
                 "骨灯已高悬，灯焰却向内缩紧，灯身短暂停住。", "two", 7.29,
                 "骨灯暗下，灯魅低头重新挑起灯芯。",
                 l_effects=("burn",), s_effects=("burn",)),
    "M24": _mob("落岩", "肩上碎岩已经松动，妖猿却仍向上耸肩，石块尚未坠下。", "all", .80,
                 "岩臂高举，臂甲裂口朝向众人。", "裂岩",
                 "岩臂骤然下沉，妖猿的重心已越过撑地的另一只手。", "all", 7.20,
                 "岩臂陷入地面，妖猿正用另一手拔出。", l_segment="back",
                 s_segment="front", part=True),
}


# Bosses preserve the distinct cycles in 妖王机制重设计.md.  Every recovery
# row is a genuine attack-free round.  B06 alone locks its execution target
# at 凝雷 after two independently targeted mark-building rounds.
PATTERNS.update({
    "B01": {
        "phase1": [
            _round("烬爪", "他以一爪划出细火线，另一爪仍压在胸前。",
                   front=_hit("main", .45, "light", ("burn",))),
            _round("焚庭", "火线沿地面聚到足下，他高举双爪，掌心明亮。",
                   back=_hit("all", 6.40, "heavy", ("burn",)), strong=True),
            _round("灰熄", "火焰向内塌落，他抖去爪上灰屑，短暂敛息。", recovery=True),
        ],
        "phase2": [
            _round("明火烬爪", "爪尖火色由暗转明，火舌只先贴向一人。",
                   front=_hit("main", .45, "light", ("burn",))),
            _round("焚庭", "地面先泛一圈赤光，双爪随后向上合拢。",
                   front=_hit("all", 2.00, "light"),
                   back=_hit("all", 5.20, "heavy", ("burn",)), strong=True),
            _round("余焰", "火墙已散，仍有一束余火沿他收回的手腕甩向原目标。",
                   back=_hit("main", 1.10, "light")),
            _round("灰熄", "他将双爪埋进灰中，火光暂时沉下。", recovery=True),
        ],
    },
    "B02": {
        "phase1": [
            _round("击水", "一束水线先点向原目标，池面尚未抬高。",
                   front=_hit("main", .45, "light")),
            _round("潮刃·蓄势", "浅水凝成短刃扫过众人脚边，高处浪头仍在蓄起。",
                   front=_hit("all", 1.00, "light")),
            _round("涨潮", "水位沿石壁攀高，双臂抬至肩上，浪头尚未落下。",
                   back=_hit("all", 7.00, "heavy"), strong=True),
            _round("退潮", "浪头退走，胸前水甲裂开，身形完全露出。",
                   recovery=True, expose=.25),
        ],
        "phase2": [
            _round("深潮击水", "黑水先点向原目标，池底随后浮出更深的潮线。",
                   front=_hit("main", .45, "light")),
            _round("潮刃·蓄势", "潮刃贴地掠向众人，两层高浪还停在他背后。",
                   front=_hit("all", 1.20, "light")),
            _round("双潮", "高浪已经折断扑下，贴地的回流水纹却仍在向内收拢。",
                   front=_hit("all", 4.80, "heavy"),
                   back=_hit("all", 2.20, "light"), strong=True),
            _round("深潮退潮", "两层浪都向外抽走，胸前水甲大面积敞开。",
                   recovery=True, expose=.35),
        ],
    },
    "B03": {
        "phase1": [
            _round("甲缝", "他转肩露出腰间甲扣，刀尖在地上划出短痕；甲扣暴露。",
                   front=_hit("main", .45, "light"), part=True),
            _round("裂刃", "裂刃被抬过头顶，腰侧甲片随之绷紧；甲扣隐没。",
                   back=_hit("main", 7.20, "heavy"), strong=True),
            _round("金雨", "刀刃碎光先洒向众人，他的手腕已经松开。",
                   front=_hit("all", 1.10, "light")),
            _round("整甲", "他压回腰侧甲片，重新扣合金甲。", recovery=True),
        ],
        "phase2": [
            _round("断扣", "裂纹沿腰甲浮出，他转肩时甲扣再次暴露。",
                   front=_hit("main", .45, "light"), part=True),
            _round("裂刃见血", "刀口赤线一闪，刃背已离肩，重心越过前脚。",
                   front=_hit("main", 6.80, "heavy", ("bleed",)), strong=True),
            _round("碎金", "刀上的金屑向外散开，他已开始卸力。",
                   front=_hit("all", 1.60, "light")),
            _round("整甲", "他以掌压住甲缝，金甲重新闭合。", recovery=True),
        ],
    },
    "B04": {
        "phase1": [
            _round("毒藤探路", "一根藤尖贴地探向原目标，花冠仍紧闭。",
                   front=_hit("main", .45, "light", ("poison",))),
            _round("孢雾", "粗藤先朝一人绷直，花冠随后向众人张开。",
                   front=_hit("main", 5.60, "heavy"),
                   back=_hit("all", 1.40, "light", ("poison",)), strong=True),
            _round("孢落", "孢粉落地，花冠闭合，藤身停在原处。", recovery=True),
        ],
        "phase2": [
            _round("孢纹", "孢纹先沿地面漫开，粗藤仍绕在根部。",
                   front=_hit("all", .45, "light")),
            _round("缠刺", "孢纹骤然卷紧众人脚边，粗藤后端只朝原目标绷直。",
                   front=_hit("all", 1.60, "light"),
                   back=_hit("main", 5.60, "heavy", ("poison",)), strong=True),
            _round("孢落", "花冠闭合，藤条停下，根部的孢粉逐渐沉落。", recovery=True),
        ],
        "summon_round": _round("召囊", "根部鼓起一枚完整毒囊，藤条暂时缩回；本轮只召唤。",
                               summon=True),
    },
    "B05": {
        "phase1": {
            "quake": [
                _round("落石·引震", "碎石先飞向已锁的落点，他的双掌随后贴向地面。",
                       front=_hit("two", .45, "light")),
                _round("裂地", "石纹聚成整圈，他的手臂仍压在地面。",
                       back=_hit("all", 6.60, "heavy"), strong=True),
                _round("止震", "地面合拢，他提起双掌，碎石停止跳动。", recovery=True),
            ],
            "stone": [
                _round("石拳·抱石", "他以石拳点向一人，另一块巨石仍抱在胸前。",
                       front=_hit("main", .45, "light")),
                _round("沉石", "巨石被举过头顶，影子完整压在原目标身上。",
                       back=_hit("main", 7.80, "heavy"), strong=True),
                _round("卸力", "巨石崩散，他垂臂缓慢站直。", recovery=True),
            ],
        },
        "phase2": {
            "quake": [
                _round("落石·山崩前兆", "碎石先飞向已锁的落点，地面随后裂出细密石纹。",
                       front=_hit("two", .45, "light")),
                _round("山崩", "低矮石刺先翻出，高处岩层仍向内倾斜。",
                       front=_hit("all", 2.00, "light"),
                       back=_hit("all", 4.80, "heavy"), strong=True),
                _round("止震", "岩层落尽，他从碎石中缓缓抽出手臂。", recovery=True),
            ],
            "stone": [
                _round("石拳·压肩", "石拳先撞向一人，他将更大的岩块扛到肩上。",
                       front=_hit("main", .45, "light")),
                _round("叠石", "整块岩心已脱肩直落，震松的碎层仍挂在岩后。",
                       front=_hit("main", 5.30, "heavy"),
                       back=_hit("main", 3.00, "light"), strong=True),
                _round("卸力", "岩心落尽，他双肩下沉，站定调息。", recovery=True),
            ],
        },
    },
    "B06": {
        "phase1": [
            _round("雷击", "指尖一道细雷点向已锁的一人，雷云尚未铺开。",
                   front=_hit("main", .45, "light", ("thunder",)), mark_setup=True),
            _round("电网", "电弧沿地面分开，停在本轮已锁的落点前。",
                   front=_hit("two", 1.20, "light", ("thunder",)), mark_setup=True),
            _round("凝雷", "电弧收回云心，一道竖线悬在已锁目标头顶；他只凝雷。",
                   lock_thunder=True),
            _round("霆刑", "竖线直指预告过的目标，他的掌心正向下压。",
                   back=_hit("main", 6.40, "heavy", marked_multiplier=7.40,
                             clear_marks=True), strong=True),
            _round("散雷", "竖线散成微光，他收掌凝息。", recovery=True),
        ],
        "phase2": [
            _round("双网", "两道电弧先平贴地面，云心仍悬在半空。",
                   front=_hit("two", 1.30, "light", ("thunder",)), mark_setup=True),
            _round("雷击", "云心射出一道细雷点向本轮已锁的一人，粗雷仍未落下。",
                   front=_hit("main", .45, "light", ("thunder",)), mark_setup=True),
            _round("凝雷", "云心沉向已锁目标头顶，细雷停下；他只凝雷。",
                   lock_thunder=True),
            _round("霆刑", "细雷先沿地面指向预告目标，云心的一道粗雷还悬在其上。",
                   front=_hit("main", 2.00, "light", ("thunder",)),
                   back=_hit("main", 5.00, "heavy", marked_multiplier=6.20,
                             clear_marks=True), strong=True),
            _round("散雷", "云心散开，他按下双掌，雷光暂歇。", recovery=True),
        ],
    },
})


# Armor/open duration is in rounds starting after a successful part hit.
# strong_multiplier overrides only the already locked strong hit.
PART_RULES: dict[str, dict[str, Any]] = {
    "M02": {"name": "背甲", "armor": .25, "open_rounds": 2},
    "M04": {"name": "雷角", "strong_multiplier": 5.04},
    "M07": {"name": "鱼鳍", "strong_multiplier": 1.12},
    "M12": {"name": "骨扣", "armor": .20, "open_rounds": 2},
    "M17": {"name": "蟹壳", "armor": .10, "open_rounds": 1,
            "strong_multiplier": .84},
    "M24": {"name": "岩臂", "armor": .20, "open_rounds": 1,
            "strong_multiplier": 5.35},
    "B03": {"name": "甲扣", "armor": .25, "open_rounds": 2},
}
