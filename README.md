<div align="center">

# 🧙 Plux 修仙插件

### 群聊里的修仙游戏：从引气到渡劫，到多人副本

[![Distribution](https://img.shields.io/badge/Distribution-plux--xiuxian-blue.svg?style=flat-square)](#)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB.svg?style=flat-square&logo=python)](https://www.python.org/)
[![Framework](https://img.shields.io/badge/Framework-Botplux-3776AB.svg?style=flat-square)](https://github.com/mnasthai/Botplux)
[![Plugin API](https://img.shields.io/badge/Plugin%20API-0.1-blue.svg?style=flat-square)](https://github.com/mnasthai/Botplux/blob/main/docs/specs/plugin-api-spec.md)
[![Visual](https://img.shields.io/badge/Visual%20cards-Pillow%20可选-orange.svg?style=flat-square)](#图卡可选能力)

<p align="center">
  独立插件包，只通过 <code>plux.api</code> 接入 <a href="https://github.com/mnasthai/Botplux">Botplux</a> 运行时。
</p>

</div>

---

## 🎯 它是什么

一个跑在微信群里的文字修仙游戏，作为 **Botplux 的独立插件**分发（分发名 `plux-xiuxian`，入口 `plux_plugins.xiuxian:XiuxianPlugin`）。

游戏状态由游戏本身的数据库表与快照里维护：角色、修炼、经济、法宝、道具、魔契、引雷斗法、围观竞猜、PVP、多人副本。

---

## 🌟 游戏内容

- 🧘 **角色与成长** —— 创建角色、境界与修为、突破、自主修炼、采矿、仙榜排名；
- 💎 **经济与商店** —— 灵石收支、商店购买、道具使用；
- 🗡️ **法宝与探索** —— 秘境探索、宝录、法宝装配与献宝；
- 👹 **魔契** —— 魔鬼的契约；
- ⚡ **引雷斗法** —— 回合制对抗；
- 🤝 **PVP 决斗** —— PVP；
- 🏰 **多人副本** —— 多人共斗；
- 🖼️ **图卡（可选）** —— 角色与仙榜回复可渲染成 PNG。

---

## 📦 安装

先装框架，再装插件；插件声明的 `plux-framework` 依赖由本地已安装的框架满足。

```powershell
# 1) 框架（Botplux 仓库）
python -m pip install -e <Botplux 仓库>\python

# 2) 本插件
python -m pip install -e .

# 3) 需要图卡时（Pillow）
python -m pip install -e ".[visual]"
```

源码目录同样可以只靠 `PYTHONPATH` 运行：

```powershell
$env:PYTHONPATH = (Resolve-Path <Botplux 仓库>\python\src), (Resolve-Path .\src) -join ';'
python -m plux --config config\plux.xiuxian.toml check
python -m plux --config config\plux.xiuxian.toml run --once
```

### 图卡可选能力

`visual_cards_enabled = true` 时，角色与仙榜回复改为持久后台任务渲染的 PNG。启动时会检查图卡依赖与背景资源，缺失时直接报配置错误，默认 `false`，走纯文本回复。

---

## ⚙️ 配置

仓库自带 [config/plux.xiuxian.toml](config/plux.xiuxian.toml)，接入前把示例内部 ID 换成真实值：

```toml
[policy]
account = "wxid_example"                    # 必须与插件 config 相同，且精确等于登录 wxid
allowed_targets = ["example@chatroom"]      # 只有列在这里的会话会被回复
group_requires_mention = false              # 游戏命令由群成员直接输入
allow_unknown_history = true

[[plugins]]
entrypoint = "plux_plugins.xiuxian:XiuxianPlugin"

[plugins.config]
account = "wxid_example"
groups = ["example@chatroom"]
admin_ids = []

[plugins.config.rules]
duel_enabled = true
visual_cards_enabled = false
```

| 字段 | 作用 |
| :--- | :--- |
| `account` | 服务哪个账号；与平台 `[policy].account` 必须一致 |
| `groups` | 开放游戏的群；空列表可用于离线装配与检查，但不开放游戏操作 |
| `admin_ids` | 管理员内部成员 ID；祈愿等管理命令按它授权 |
| `rules` | 游戏规则，字段见 [domain/config.py](src/plux_plugins/xiuxian/domain/config.py) |

> [!WARNING]
> 平台侧还要满 否则表现为「命令有响应但发不出去」或「命令完全不触发」
> `[policy].allowed_targets` 必须包含游戏群；`observer.enabled` / `send_enabled` 必须打开

---

## 🎮 游戏命令

游戏内 `#修仙帮助`、分类帮助与 `#副本帮助` 严格解析见 [domain/commands.py](src/plux_plugins/xiuxian/domain/commands.py)。

| 玩法 | 常用命令 |
| :--- | :--- |
| 角色与成长 | `#修仙 道号`、`#修仙`、`#修炼`、`#自主修炼`、`#采矿`、`#突破`、`#仙榜` |
| 法宝与探索 | `#秘境`、`#宝录`、`#法宝 [F编号]`、`#法宝帮助`、`#献宝 F编号` |
| 商店与道具 | `#商店`、`#购买 物品名`、`#道具`、`#使用 道具名 [@群友]` |
| 魔契 | `#魔契`、`#魔契帮助`、`#签订魔契`、`#深化魔契` |
| 引雷斗法 | `#斗法 @群友`、`#接受斗法`、`#拒绝斗法`、`#取消斗法`、`#引雷`、`#斗法状态` |
| 围观与战绩 | `#支持 @参战者 金额`、`#战绩 [D编号 [页码]]` |
| PVP | `#决斗 @群友 金额`、`#接受决斗`、`#拒绝决斗`、`#继续决斗`、`#投降`、`#决斗状态`、`#决斗属性` |
| 副本组队 | `#副本创建`、`#副本加入 队伍编号`、`#副本准备`、`#副本出发`、`#副本状态`、`#副本退出` |
| 职业与灵根 | `#职业`、`#职业 详情 职业名或C编号`、`#灵根`、`#灵根领取`、`#灵根装配`、`#灵根卸下`、`#灵根兑换` |
| 副本决策 | `#副本选择 阶段编号 1～3`、`#副本选宝 阶段编号 1～3`、`#副本行动 阶段编号 动作 [目标]` |

玩家、资产、冷却、押注、战绩与活动都按「账号 + 群身份」归属。

---

## 🏗️ 代码结构

```
src/plux_plugins/xiuxian/
├── plugin.py          装配服务、注册命令/计划/任务、处理生命周期
├── settings.py        配置校验与规则快照构建
├── domain/            规则、内容、命令解析
├── application/       用例：角色、成长、经济、背包、斗法、PVP、副本
├── persistence/       仓储与 schema 迁移
├── presentation/      文本渲染、图卡计划与渲染器
└── resources/         图卡模板与组件、副本内容 JSON
```

---

## 🧪 测试

```powershell
$env:PYTHONPATH = (Resolve-Path <Botplux 仓库>\python\src), (Resolve-Path .\src) -join ';'
python -m unittest discover -s tests -t tests -p 'test_*.py'
```

| 测试文件 | 覆盖 |
| :--- | :--- |
| `test_core.py` | 规则、内容目录、命令解析 |
| `test_domain.py` | 领域规则与边界值 |
| `test_flows.py` | 成长、经济、法宝道具、斗法、PVP、副本流程 |
| `test_migration.py` | schema 迁移与旧数据兼容 |
| `test_plugin.py` | 装配、事务回滚、策略门禁、后台图卡 |

---

## 🔗 相关项目

| 项目 | 关系 |
| :--- | :--- |
| [Botplux](https://github.com/mnasthai/Botplux) | 本插件运行的 Python 运行时：消息、事务、任务与插件契约 |
| [IRIS](https://github.com/mnasthai/IRIS) | 注入微信进程内的 C++ 后端 |
