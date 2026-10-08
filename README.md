<div align="center">

# 🧙 Plux 修仙插件

### 群聊里的修仙游戏：从引气到渡劫，从单人到多人副本

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

游戏状态全部落在插件自己的数据库表与快照里：角色、修炼、经济、法宝、道具、魔契、引雷斗法、围观竞猜、PVP、多人副本。框架只负责收发消息与事务，插件不依赖框架的任何实现模块，也不依赖旧 `wechat_receiver` 代码。

> [!IMPORTANT]
> 这是**进程内可信代码**，没有沙箱。所有数值、冷却与结算规则都在插件内实现，改动前先读 §边界与限制。

---

## 🌟 它能做什么

- 🧘 **角色与成长** —— 创建道号、境界与修为、突破、自主修炼、采矿、仙榜排名；
- 💎 **经济与商店** —— 灵石收支、商店购买、道具使用、储物袋上限；
- 🗡️ **法宝与探索** —— 秘境探索、宝录、法宝装配与献宝、稀有度掉落权重；
- 👹 **魔契** —— 可分层深化的契约，带每日代价与历史记录；
- ⚡ **引雷斗法** —— 邀请、应答、回合制对抗，围观者可押注并结算退款；
- 🤝 **PVP 决斗** —— 双方押金托管，多回合推进、超时与投降分支；
- 🏰 **多人副本** —— 组队、准备、出发、阶段决策、选宝与结算，重启后可继续；
- 🖼️ **图卡（可选）** —— 角色与仙榜回复可渲染成 PNG，由持久后台任务生成。

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

`check` 校验配置、manifest 与注册声明，**不会创建数据库、不会连接原生管道**。首次运行时框架先执行插件声明的 schema 迁移、加载规则与内容，再恢复长流程；相对路径以配置文件所在目录为基准，本配置的运行目录是 `<本仓库>\runtime`。

### 图卡可选能力

`visual_cards_enabled = true` 时，角色与仙榜回复改为持久后台任务渲染的 PNG，模板与组件随包分发（构建产物含 20 张 PNG 与副本内容 JSON）。启动时会检查图卡依赖与背景资源，缺失时直接报配置错误，不会静默降级。默认 `false`，走纯文本回复。

---

## ⚙️ 配置

仓库自带 [config/plux.xiuxian.toml](config/plux.xiuxian.toml)，接入前把示例内部 ID 换成真实值：

```toml
[policy]
account = "wxid_example"                    # 必须与插件 config 相同，且精确等于登录 wxid
allowed_targets = ["example@chatroom"]      # 只有列在这里的会话会被回复
group_requires_mention = false              # 游戏命令由群成员直接输入
allow_unknown_history = true                # schema 2 无法证明消息实时性

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
> 平台侧还要满足三件事，否则表现为「命令有响应但发不出去」或「命令完全不触发」：
> `[policy].allowed_targets` 必须包含游戏群；`observer.enabled` / `send_enabled` 必须打开；涉及玩家的 `@` 必须有原生 `msg_source` 里的真实提醒证据，可见昵称文字不会授权目标玩家。

规则在启动时校验并发布为**带版本的不可变快照**，业务与图卡共用同一份。未覆盖字段取默认值；未知字段、重复身份和非法取值会被拒绝。重启时若仍有进行中的斗法、PVP 或副本，插件整体继续使用上一次绑定的规则；活动结束后再重启才应用新规则。活动期间更换已安装的法宝、道具或副本内容会拒绝启动——避免用新内容解释旧活动。

---

## 🎮 游戏命令

游戏内 `#修仙帮助`、分类帮助与 `#副本帮助` 会给出完整规则和当轮编号。严格解析见 [domain/commands.py](src/plux_plugins/xiuxian/domain/commands.py)。

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

玩家、资产、冷却、押注、战绩与活动都按「账号 + 群身份」归属。副本决策必须带提示中的阶段编号，迟到的旧阶段操作不会推进下一阶段。

---

## 🏗️ 代码结构

```
src/plux_plugins/xiuxian/
├── plugin.py          装配服务、注册命令/计划/任务、处理生命周期
├── settings.py        配置校验与规则快照构建
├── domain/            规则、内容、命令解析（纯逻辑，无 IO）
├── application/       用例：角色、成长、经济、背包、斗法、PVP、副本
├── persistence/       仓储与 schema 迁移（绑定当前事务）
├── presentation/      文本渲染、图卡计划与渲染器
└── resources/         图卡模板与组件、副本内容 JSON
```

分层是单向的：`plugin` 只装配，`domain` 不碰数据库，`persistence` 只把仓储绑定到当前工作单元，`presentation` 只产出文本与不可变的图卡计划。插件不导入 Plux 实现模块，也不导入其他插件的内部代码。

---

## 🔒 边界与限制

> [!NOTE]
> 下面这些是**有意的设计边界**，不是待修的缺陷。

- **事务边界**：业务变更、输入消费、回复与新任务在同一个事务提交。回复入队失败会回滚角色或经济操作；命令处理器不建表、不渲染图片、不发布文件。
- **幂等**：稳定的事件键、回复键与任务键保证重启或重复输入不会重复发奖励；副本结算与奖励有幂等边界。
- **托管资金**：PVP 双方押金由插件托管，只在确定结算或取消时处理；斗法的支持退款与持久通知受同一事务约束。
- **`accepted` 不等于送达**：斗法提示以原生 `accepted` 的尝试时间开启应答阶段，而 `accepted` 只表示本地提交成功。会话变化或发送结果不确定时走技术取消与一次性补偿；已尝试发送的提示不会被当作未发送而撤销或自动重放。
- **积压保护**：轮询发现还有持久输入或源日志未读完时，暂缓会受积压影响的超时结算；期限内捕获而延迟处理的 PVP 应答按**捕获时间**判断，避免处理延迟造成错误超时。
- **图卡与状态解耦**：后台任务在事务外渲染并发布不可变资产，结果快照持有资源引用，提交时转交回复队列；PNG 文件路径不会成为业务命令的状态。
- **未做真机验证**：测试用临时数据库与普通 IPC 宿主，没有启动微信、没有真实发送、没有转换生产数据。真实群内交互、Hook 行为与长期运行仍需部署验证。

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

实测：**59 项全部通过**（含 1 项需要 Pillow 的图卡用例，缺少 Pillow 时该项会以明确的配置错误失败而不是静默跳过）。框架自身的 54 项测试见 Botplux 仓库。

---

## 🔗 相关项目

| 项目 | 关系 |
| :--- | :--- |
| [Botplux](https://github.com/mnasthai/Botplux) | 本插件运行的 Python 运行时：消息、事务、任务与插件契约 |
| IRIS | 注入微信进程内的 C++ 后端，提供真正的收发通道（私有仓库） |
