# 云顶之弈 屏幕元素提取模块

## 环境 / 运行方式

- **micromamba 环境**：`YunDing_MVP`（Python 3.11）
- 核心依赖：`opencv` 5.0、`numpy` 2.4、`python-mss` 10.2（截图，**注意是 python-mss 不是 mss**）、`pillow` 12、`requests`、`rapidocr-onnxruntime`（pip 段，决策卡 OCR）
- **坑（2026-07-04 实测踩过）**：`rapidocr` 在 environment.yml 的 pip 段——**老环境 `micromamba create` 建过就不会自动补**，需手动 `micromamba run -n YunDing_MVP pip install rapidocr-onnxruntime`。缺它时识别器**静默降级**（主链照跑），症状 = 决策窗口永远 READING；现 UI 会显示 `OCR UNAVAILABLE` + 原因
- 项目根目录：`/ShangGaoAIProjects/MVP`
- 环境定义：`environment.yml`（干净、跨平台，用于 Windows/Linux 重建）

```bash
# 重建环境（Windows / Linux 通用）
micromamba create -f environment.yml

# 激活
micromamba activate YunDing_MVP

# 或不激活直接跑单条命令
micromamba run -n YunDing_MVP python <script.py>

# micromamba 路径（若不在 PATH）
/home/sgao30/micromamba/bin/micromamba
```

> 注：该环境曾误装 conda-forge 的 `mss`（实为航空气象工具 mslib），残留了 matplotlib/flask/cartopy 等无关孤立依赖，功能无害，暂不清理。

## 这是什么

游戏助手的**感知层**。职责是把当前屏幕截图里**可观测的游戏元素**翻译成结构化 JSON，供后续策略推理模块使用。

**边界**：只做「感知」，不做「理解」。模块只回答「屏幕上看到了什么」（槽位里是谁、几费、棋盘哪个格子有谁），不回答「该买谁、该怎么打」——后者是策略层的事。

- **输入**：当前截图 + 屏幕分辨率 + UI 布局配置（layout profile）。
- **输出**：标准化的对局状态 JSON（见下方 Schema）——**不是逐帧独立快照，而是从开局持续维护、跨帧/跨阶段累积的一份对局状态**（running state / world model，见「持久化对局状态」一章）。当前帧看不见的事实（早先选的海克斯、战斗中看不清的备战席）仍保留在状态里。

## 终极目标：决策时刻的选择推荐

感知层服务的最终产品是**选择推荐助手**：检测场上阵容、备战席棋子、商店、装备、海克斯、神明祝福，结合**外部数据库**（海克斯胜率表 / 阵容强度数据，如 MetaTFT、tactics.tools，选型待定）在每个决策时刻给出建议。

由此倒推感知层的真正需求：**推荐引擎要的不是连续状态流，而是「决策窗口打开时的准确快照」**。决策窗口全是静止浮层或静止 planning 画面，多帧投票容易做稳；最难的棋盘 3D 识别反而是推荐最不急需的。阶段检测（phase-marker）本质就是**决策窗口触发器**。按「价值 / 识别成本 / 状态依赖」排列的决策窗口：

| 决策窗口 | 识别什么 | 依赖累积状态 | 外部数据 |
|---|---|---|---|
| 海克斯 3 选 1（`2-1/3-2/4-2`，实测确认） | 3 张卡是谁 | 否 | **已接**（MetaTFT 评级） |
| 神明祝福 2 选 1（`2-4/3-4/4-4`） | 2 张祝福卡 | 弱（1-1 介绍已锁定本局神明池） | 代理（神明恩赐评级；offering 无公开源） |
| 神之祝福 4 选 1（`4-7`） | 4 个装备/增益选项 | 弱 | 部分现成 |
| 商店买/D（每个 planning） | 5 槽 + 金币/等级 + **我方阵容** | **强**（board+bench） | 现成 |
| 装备合成 | 装备栏散件 | 弱 | 现成 |

**最短价值闭环 = 海克斯推荐**，而不是商店——该闭环已打通（识别 → 评级 → UI/track，见「决策卡识别与评级」章）。商店推荐需要「我有什么」，才是 board/bench 识别与 StateTracker 的真正客户——它们服务于商店那一刀，优先级据此排。

## 核心架构：阶段优先的感知流水线

整个模块围绕一条思路：**先把握时间轴，再判阶段，按阶段取 ROI，最后才识别**。三件事各司其职：

- **时间轴**（顶栏时钟）：每帧必读的心跳，钉死「我现在在一局的哪个时刻」，驱动阶段判定与状态追踪。
- **阶段**（SceneClassifier）：决定「当前哪些 ROI 激活、值可不可信」。
- **脏区检测**：决定「激活的 ROI 里哪些要重算」。

### 流水线骨架

```
ScreenCapture (高频轮询截图, 如每 0.5s; 可被鼠标点击触发额外抓拍)
  → TopBarClock      (顶栏时钟: 每帧 eager 读 stage-round + 倒计时, 单调校验)
  → SceneClassifier  (消费时钟 + 常驻 phase-marker + 迟滞 + 合法转移 → 判当前阶段)
  → LayoutMapper     (归一化标准坐标 → 实际截图像素坐标)
  → 按【当前阶段】取对应 ROI 集合, 对每个 ROI:
       ROICropper → 变化检测(差分) → 脏了才跑对应 Recognizer → 更新缓存(lazy)

     planning 阶段:
         ├─ ShopRecognizer    (商店五槽: 身份/空槽; 费用查 catalog)
         ├─ ItemRecognizer    (装备栏: 散件 vs 成装)
         ├─ TextRecognizer    (金币/等级/经验/血量)
         ├─ BenchRecognizer   (备战席: 身份 + 星级)
         └─ BoardRecognizer   (棋盘: 站位 + 星级 + 身份 + 携带装备)
     combat 阶段:
         └─ TextRecognizer    (金币/等级/血量; 商店/装备操作仍识别; 棋盘沿用上一帧快照)
     god_intro / god_boon / augment_select / blessing 等选择类阶段:
         └─ 对应「选项识别器」  (识别可选项 → decision 块 → 推荐)

  → 合并所有 ROI 最新结果
  → StateTracker (合并进持久化对局状态: 事件转移 + 备战席重对齐)
  → 输出当前对局状态 JSON
```

### 模块地图（当前实现状态）

| 文件 | 职责 | 状态 |
|---|---|---|
| `tft_mvp/capture/` | `FileCapture`（Linux 回放）/ `MSSCapture`（Windows 抓屏，BGRA→BGR） | ✅ |
| `tft_mvp/layout/` | LayoutMapper：归一化 ROI → 像素坐标（`profiles/16_9.json`） | ✅ |
| `tft_mvp/recognize/digits.py` | DigitReader 数字模板匹配（时钟用；金币/等级/血量将复用） | ✅ |
| `tft_mvp/scene/clock.py` | TopBarClock：顶栏时钟每帧 eager 读 | ✅ |
| `tft_mvp/scene/track.py` | ClockTrack：时间轴样本 + 事件（round_advance/countdown_*/**decision**） | ✅ |
| `tft_mvp/scene/rounds.py` | RoundTrack：回合 → 4 小阶段结构化 | ✅ |
| `tft_mvp/scene/markers.py` | PhaseMarker：浮层检测（augment_select/loading/god_intro/god_boon） | ✅ |
| `tft_mvp/recognize/cards.py` | CardRecognizer：决策卡 OCR 识别 + 评级（见专章） | ✅ |
| `tft_mvp/reco/metatft_tiers.py` | MetaTFT 评级表每日缓存 + `TAGS_ZH` 中文 tag | ✅ |
| `tft_mvp/ui/dashboard.py` | 仪表盘（DECISION 区、PIL 中文渲染、OCR 诊断行） | ✅ |
| `tft_mvp/pipeline.py` | 组装：时钟 → marker → cards → track → state | ✅ |
| `tools/live.py` | 实时运行 + 录制（`--record` / `--save-frames`） | ✅ |
| `tools/ocr_probe.py` | OCR 匹配离线实验 / 回归（打分配方在此标定） | ✅ |
| `scripts/download_assets.py` | 图标素材 + manifest + 中文语料下载 | ✅ |
| SceneClassifier（planning/combat 四层叠加）、Shop/Bench/Board 识别、StateTracker | 设计在案（见各章） | ⬜ |

## 时间轴骨架：顶栏时钟 track

屏幕最上方的回合记录 + 倒计时是**固定的时间轴**，常驻、廉价、与具体阶段无关。把它独立成一个 **每帧必读的「心跳」track**（不走差分闸门），由它驱动 SceneClassifier 与 StateTracker。它一次买到三样东西：

1. **stage-round = 单调锚点**：`3-2` 这类回合号**只增不减**。任何阶段切换都拿它校验；读到倒退 / 乱跳 → 立刻判定是坏帧，丢弃。这等于给整条时间轴上了一根锚，单帧误判没法把时间轴拽乱。
2. **倒计时 = 阶段内位置**：倒计时在走 = planning；归零 → 马上要切 combat（可**预判**转场，而非事后才发现）。倒计时还指示「干净读」重对齐的好时机（planning 早期单位刚落定）。
3. **回合类型图标行 = lookahead**：顶栏那排小图标预告后面是 战斗 / 神明 / PVE / 海克斯 回合，让合法转移表从静态规则升级为真前瞻。MVP 先只做 stage-round + 倒计时，图标行后补。

### eager vs lazy 分工（关键）

- **顶栏时钟**：每帧 **eager** 读（倒计时本来就每秒变，差分必触发，索性不 gate，直接读）。它是心跳。
- **其余所有 ROI**（商店 / 装备 / 数值 / 备战 / 棋盘）：**lazy**，差分闸门控制，脏了才识别。

这样「时钟」管时间轴、「内容」管「这一刻屏上是什么」，职责彻底分离。时钟的数字识别与金币 / 等级 / 血量是**同一套数字模板匹配**，故数字识别基建提前到骨架阶段。

### ClockTrack：track 落地（已实现）

`tft_mvp/scene/track.py` 的 **`ClockTrack`** 把每帧时钟读数 `(stage, round, countdown, sr_status)` 逐帧记成时间序列，并派生**事件**——这是 SceneClassifier 的原料（先落数据、观察规律，再定阈值），本身**不下阶段结论**：

- **样本**（samples）：全量逐帧记录，供录制导出 JSON。
- **事件**（events）：`round_advance`（回合号 +1）/ `countdown_reset`（倒计时回升≥4，新一轮 planning）/ `countdown_expired`（倒计时归零/消失，预判切 combat）。
- **小阶段（rounds）**：见下「回合 → 4 小阶段」。
- 挂在 `Pipeline`，输出进 state 的 **`track` 块**（最近样本 + 全部事件 + `cd_trend` + 最近 2 回合小阶段）。`tools/live.py --record track.json` 可把整局 track 落盘。

**实测坑（重要）**：海克斯 / 神明**浮层会把顶栏调暗**（`s_00012` 顶栏 `2-1`/`40` 是暗米色低对比），当前 `DigitReader` 阈值读不到 → `sr_status='miss'`、`countdown=None`。**但 miss 不更新单调锚点，故 track 不被污染**——只是这几帧「时钟盲读」。完整一局实测证实：**≥5 帧的连续 miss 只出现在 5 处——加载画面、三次海克斯浮层、结尾被淘汰**，miss 模式本身就是可靠的浮层信号（配合 phase-marker 交叉印证）。

**浮层遮蔽回合推进（未修）**：`4-2` 的海克斯浮层在回合切换瞬间弹出 → 顶栏调暗 ~17s 连续 miss → 单调锚点一直沿用 `4-1`，整段海克斯被 track 归到 `4-1`（帧图顶栏肉眼可见 `4-2/42`）。影响事件归属与 `at_augment_round` 软校验。修法方向：浮层结束读到新回合号时，把 miss 段回填给新回合。

**倒计时掉位坏读（未修）**：单帧把 `22` 读成 `2`（十位丢失；`5-5` 实测，`3-3` 同类）→ 触发 RoundTrack「回升≥2」切分出假小阶段。修法方向：倒计时 3 帧中值滤波，或小阶段切分需连续 2 帧确认。

**倒计时读数坑（已修）**：PVE 回合（`3-7`/`4-7`）倒计时 ROI 左侧会泄入别的暖色数字（`40`/`30`），无锚点的 `read_number` 从左拼到右 → 读出 `4019`/`3049` 垃圾值（一局 154 帧 + 4 个假事件）。已修：`read_number` ① 只取**最右侧数字簇**（倒计时紧邻时钟图标、总在最右）② 值 >99 判无效返回 None。对比 `read_stage_round` 一直没这问题——它有破折号锚点。

### 回合 → 4 小阶段（RoundTrack，已实现）

`tft_mvp/scene/rounds.py` 的 **`RoundTrack`** 把时钟读数结构化成「回合 → 4 小阶段」。**每个回合固定 4 个小阶段，各倒数一次（一条倒计时 ramp）**：

| 顺序 | label | 说明 | span |
|---|---|---|---|
| ① | `planning` | 策划 | 长（~30–50s） |
| ② | `trans_pc` | 策划→战斗 转场 | 短（~5s） |
| ③ | `combat` | 战斗 | 长（~30s） |
| ④ | `trans_cp` | 战斗→下一回合 转场 | 短（~2–5s） |

- **回合边界**：`round_advance`（stage-round 前进，实测 100% 可靠）。
- **小阶段边界**：倒计时「回升」≥2（回合内只降不升，一升即进入下一段；trans_cp 起始仅 ~2，故阈值取 2，比事件用的 4 灵敏）。
- **标签延后到输出时贴**：先丢掉空 ramp（`cd_max==0`，回合开头上一回合残留的 0 读数），再对真实 ramp 依次贴标签——否则空 ramp 会把标签顶偏。
- 每段附 `span=long/short`（按 `cd_max`），用于交叉校验「长短长短」预期节律。
- 全量回合留内存（很小），`snapshot(n=2)` 只吐最近 2 回合。

**实测验证（27 回合回放 + 34.7min 完整一局 37 回合，`1-1`→`6-5`）**：普通战斗回合 `长短长短` 四段模型完全成立（完整局约 24/31 个正常回合完美）。特殊结构（不是 bug）：
- **stage 2–3 前期回合**（2-1~2-3、2-5、3-1、3-2）出现第 5 段：combat 后多一个 ~14s ramp 再接 2s——疑似**战斗加时（overtime）**，战斗打满 29s 未分胜负才出现；后期战斗提前结束就没有。四段模型需允许这个可选段。
- **神明回合 `x-4`**（2-4/3-4/4-4）：单 ramp ~35s，无战斗。`5-4/6-4` 是普通回合（神明系列 4-4 选满结束），但回合号**中途才推进**（前半段顶栏还显示 x-3，导致 5-3 尾部多出小 ramp、5-4 只剩 14s）——事件归属不能只看回合号。
- **PVE `x-7`**（2-7/3-7）：三段 `planning(19)→trans(2)→combat(29)`，无尾转场；**`4-7` 是神之祝福选择回合**，单 ramp 49s，无战斗。
- 海克斯回合 planning 更长（36–42s vs 常规 29s）；`1-1` 仅 ~6s 一段（神明介绍占了大半）。

## 阶段清单 + 阶段状态机

云顶一局内画面形态差异极大，**不同阶段的 ROI 根本不一样**，不能用一套全局 ROI 硬切。每帧顺序：① 读时钟 → ② SceneClassifier 判阶段 → ③ 按阶段取对应 ROI 集合 → ④ 对这些 ROI 差分 → 跑识别器。

### 完整阶段表

| 阶段 | marker（常驻轻量 ROI） | 识别内容 |
|---|---|---|
| 开局 / `game_start` | （用于初始化状态） | — |
| 加载 / `loading` | 玩家名片画面、无顶栏（紫底，曾被误判成神明） | 无，识别出来排除即可 |
| 神明介绍 / `god_intro`（`1-1`） | 神明领域全屏深蓝紫 + 顶部介绍横幅 | 本局两位神明是谁（横幅纯文字）→ 存入状态，**无决策** |
| 策划 / `planning` | 商店栏 + 整备倒计时 | 商店 / 装备 / 数值 / 备战 / 棋盘 |
| 战斗 / `combat` | 战斗中指示 | 仅数值 + reroll 商店 / 装备操作 |
| 海克斯 / `augment_select`（`2-1/3-2/4-2` 实测确认） | 3 个等宽面板浮层 | 3 选 1 是哪三个 |
| 神明祝福 / `god_boon`（`2-4/3-4/4-4`） | 神明领域全屏深蓝紫（走动捡宝珠 + 2 选 1 浮层） | 2 张祝福卡是哪两个（卡上有中文名） |
| 神之祝福 / `blessing`（`4-7`） | 底部 4 选 1 面板 + 接受/重掷按钮 | 4 个装备/增益选项 |
| PVE 掉落 / `pve_loot`（`x-7` 战斗后） | 掉落浮层 | （后续）掉落物 |
| 结算 / `game_end` | （用于重置状态；**素材仍缺**，实录被淘汰后是观战视角） | — |

> **Set 17 没有选秀（carousel）**：完整一局实录（37 回合）无转盘环，传统 `x-4` 选秀被神明祝福回合取代。早期阶段表里的「选秀/转盘环」是按旧赛季知识写的，已废弃；此前 marker 分析中「防选秀误判」的说法实际防的是神明领域。若后续赛季选秀回归再补。

**标志 ROI（解决「鸡生蛋」）**：阶段判断本身也要读像素，靠一小撮**常驻、极轻量的「phase-marker ROI」**（神明面板在不在 / 海克斯 3 面板在不在 / 转盘环在不在 / 商店栏在不在）。结构两层：常驻标志 ROI 每帧查 → 判出阶段后才激活该阶段的完整 ROI 集。

**关键区分：「检测界面」≠「识别选项」**。判「当前在不在海克斯/神明界面」只要一个结构 marker，不需要内容资产；识别「是哪 3 个海克斯」才需要语料（已就位，走文字通道）。两者解耦，各自独立演进。

**PhaseMarker（海克斯 / 神明检测，已实现）** `tft_mvp/scene/markers.py`：**结构 + 颜色 + 固定回合**，抗补丁、不需资产。

> **为什么放弃纯颜色占比**：最初用「中带紫色占比 ≥0.08」判海克斯，单帧分离 50×，但**多帧真实对局翻车**——它把**神明领域回合**（`x-4`，紫色魔法 UI，当时误以为是选秀转盘）和偶发紫色特效一起误判（散在十几个回合）。教训：单帧标定的颜色阈值在全局数据上分离度会塌。

| 阶段 | 信号 | 判据（已按完整局数据重构 + 回放验证） |
|---|---|---|
| `augment_select` | 中带**面板结构**（`markers.augment_band` 列投影） | 恰 **3 个等宽面板**（宽 0.18–0.35、max/min<1.6）**且全屏蓝紫 < 0.40**（仲裁：神明领域偶发凑 3 等宽但 bp≥0.55，真海克斯 ≤0.30）+ **迟滞**（连续 3 帧激活 / 连续 3 帧退出，容忍浮层动画单帧坏读，实测坏帧最长连 2 帧） |
| `loading` / `god_intro` / `god_boon` | 全屏**深蓝紫**占比（`markers.overlay_full`）≥ 0.40 + 时钟上下文 | 无 stage → `loading`（加载画面 bp≈0.47）；stage≤1 → `god_intro`（1-1 介绍）；round==4 且 stage≥2 → `god_boon`。**god_boon 回合内锁存**：2 选 1 浮层会把画面压暗到 bp 0.26–0.34（与海克斯重叠，不能降阈值），故连续 2 帧高蓝紫即锁存整回合，回合号推进才解除。其余紫屏（如 4-7→5-1 转场 7 帧）拒判 |

- 挂在 `Pipeline`，写入 state 的 `scene`；`_marker`（`n_panels`/`panel_widths`/`bluepurple`/`at_augment_round`）存原始值供校准；track 样本逐帧记 `scene`/`n_panels`/`bluepurple`。
- **固定回合先验（Set 17 实测确认）**：海克斯 = 传统 `2-1/3-2/4-2`（`markers.py` 的 `_AUGMENT_ROUNDS` 正确；注意 4-2 浮层遮蔽回合推进，track 里显示 4-1，见 ClockTrack 坑）；神明祝福 = `2-4/3-4/4-4`；`1-1` 仅介绍无决策。`at_augment_round` 作软校验输出，不硬 gate（怕补丁改动）。
- **浮层不遮顶栏，但海克斯会把顶栏调暗**（见 ClockTrack 坑）；神明期间顶栏正常。
- **完整局回放验证（37 回合，track.json 3992 帧逐帧模拟 + 真实帧像素路径测试）**：海克斯 3/3 检出、2-4 误判消除；`god_boon` 锁存后覆盖三个神明回合 98–99%（含压暗的 2 选 1 浮层段）；加载 / 1-1 介绍 / 5-1 转场紫屏各归其位。神明回合形态（三次一致）：回合首帧旧画面 → ~4 帧领域高蓝紫（0.5–0.65）→ 浮层压暗（0.26–0.34，恰在回合开头 cd~32–24）→ 走动段（0.47–0.58）。
- **PhaseMarker 现在有状态**（迟滞计数 + 锁存跨帧），新一局须 `reset()`（`Pipeline.reset()` 已接）。
- 选项识别已实现（文字通道，见「决策卡识别与评级」章）。⬜ 未做：1-1 介绍横幅读出本局神明池 → `x-4` 匹配在该先验内剪枝（同「费用查 catalog」思路）。

`game_start` / `game_end` 用于**初始化 / 重置**持久化对局状态：一局内只增量更新，对局结束清空。

### planning vs combat 判定（防「时间轴混乱」，四层叠加）

最难判的不是花式阶段，而是 planning vs combat——**这两个阶段商店栏都在**（战斗中也能 reroll），光看「有没有商店」分不开。单帧判读还会因渐变转场 / 动画 / 坏帧而抖。四层叠起来基本不会乱：

1. **顶部「整备 / 战斗」字样**：对那一小块做**模板匹配**（字形固定，比 OCR 稳），但**不作唯一依据**——转场渐变时这块会糊。
2. **回合数单调时钟**（见上）：阶段切换必须与回合号对得上，combat 永远跟在同 stage-round 的 planning 后；回合号倒退/乱跳即判坏帧。
3. **时序迟滞（防抖）**：新阶段要**连续 N 帧**判到才真切换，单帧坏读直接被吞。
4. **合法转移 + unknown 缓冲**：维护「上一阶段 → 允许的下一阶段」表（如 combat 不能直接跳 augment_select）。转场渐变那几帧与其硬判，不如标 `phase: unknown`，**这期间不累积状态**——宁可置空也不输出低质量结果。

任何单一信号坏了，另外两三个把它拦下。

## 决策卡识别与评级（CardRecognizer，已实现）

`tft_mvp/recognize/cards.py`。PhaseMarker 判进 `augment_select` / `god_boon` 后，窗口内节流跑 OCR：帧 → 统一降采样 1280 宽（OCR 在 1280 已够，实验证明）→ RapidOCR → 卡片带内文本按 x 间隙聚列（3 卡 / 2 卡；多聚出列时按文本量取前 N 再按 x 排回，容忍悬停 tooltip）→ 每列封闭集匹配 → 查评级 → `decision` 块。

**匹配配方（`tools/ocr_probe.py` 离线标定：15 帧 12/12 全对，基线 4/12）**：
- **max(名字, 描述) 双通道**：两通道失效面不重叠（客户端改名 vs 描述数值修订），谁强信谁；合并 bigram 会互相稀释（珠光莲花名字 1.00 曾输给合并 0.44 的错误候选）。
- 通道分 = 平滑 containment `hits/(|cand|+3)`，交集 <3 记 0——小 bigram 语料（「获得随机纹章」类短描述）不再碰瓷。
- 垃圾过滤：name_zh 含 `@`/`。` 的条目（GainGold、奥索任务恩赐——名字即效果模板）整条剔出候选集。
- **台服→国服词汇归一 `_CANON`**（弈子→英雄、潘朵拉→潘多拉、机率→几率、备战席→备战区…）：CDragon zh_cn 实为台服系用词，两侧归一后描述通道才可比。
- **国服主题化改名是主要矛盾**：实测 12 卡中 10 卡屏幕名 ≠ 一切公开表（腾讯 `game.gtimg.cn/.../hex.js` 与 CDragon 同为旧译名；例：刃马合一=征战之路、腰带过载=腰带溢流、黄金豪赌=黄金赌约）。两层覆盖表应对：`display_names_zh.json`（人工核对种子，首批 10 条）+ `name_overrides.json`（**live 自举**：描述锚定身份而名字对不上时自动记屏幕名，攒几局即与客户端同步）。
- 同族 I/II/III 变体：margin 仅 0.01–0.05 且 OCR 罗马数字单帧会翻（珠光莲花 I↔II）——文字通道不解决，待卡面 tier 色带仲裁 + 多帧投票。

**运行时行为**：未锁定每 1.2s / 锁定后每 3s 一次 OCR（~1.7s/次 CPU，决策窗口静止可接受）；**2 帧一致才锁定**（`locked`）；选项组签名变化 = **重掷**（实录实锤：3-2 一次、4-2 两次，且是单卡独立重掷非整组刷新）→ 自动重投票再锁、track 再记一条。神明卡**两步匹配**：称号锚定神明（9 选 1）→ 该神 offering 内配祝福；评级用该神恩赐海克斯 `*GodAugment*` 代理；**零证据门槛** god_confidence<0.3 整次读取作废（走动段防垃圾锁定，实测已拦）。x-4 两张卡来自本局两位神明各一张（实录验证：亚索·风暴格 vs 伊芙琳·12金币）。

**输出三处**：① state `decision` 块（api + 国服显示名 + tier S–D + `tags_zh` 中文标签）② track `decision` 事件（锁定一条、重掷新组再一条 → track.json 即整局识别+评级档案）③ Dashboard DECISION 区（中文 PIL 渲染 + 5 色 tier 徽章）。**诊断内建**：识别器没起来（缺 rapidocr）显示 `OCR UNAVAILABLE` + 原因；识别中 READING 行附 `runs/lines/cols/耗时`——卡住时一眼定位卡在哪一环（首次 Windows 实测就是靠「静默降级无提示」这个教训加的）。

**评级数据**（`tft_mvp/reco/metatft_tiers.py`）：MetaTFT `augments_tiers` JSON（免鉴权，实为 100T Spencer 人工评级 5 档 S–D），每日首启抓取、缓存 `assets/cache/`、失败回退旧缓存不阻塞感知。tag 词表仅 6 个（econ/combat/items/trait/scaling/misc），`TAGS_ZH` 译中文（经济/战斗/装备/羁绊/成长/功能）。神明 offering（`TFT17_Benefit_*` 金币/经验类）无公开评级，暂显神明代理 tier。

⬜ **残留**：Windows 全分辨率实测跑通一局；卡 ROI 改由 PhaseMarker 面板边界供给（现为固定 band；GOD_BAND 未正式标定，浮层帧 2/2 命中）；「选哪张」一句话推荐话术；神之祝福 4 选 1（`4-7`）未做。

## 脏区检测：ROI 级「变化 → 按需识别」

除顶栏时钟外，所有 ROI 用同一套机制管理（商店 / 装备 / 备战 / 棋盘 / 数值），不为每类元素单独写触发逻辑：

```
对每个激活的 ROI:
  1. 截当前帧该区域
  2. 与上一帧缓存图做差分
  3. 没变  → 直接复用上次识别结果（零成本）
  4. 变了  → 重跑对应识别器，更新缓存
```

- 每个 ROI 独立：买一个棋子只刷新那一个商店槽；金币变了只重读金币。
- 没变的字段沿用上次的高 confidence，避免重识别引入抖动。

**关键坑**：差分必须能区分「真变化」vs「噪声 / 动画」。云顶 UI 里头像待机光效、悬停高亮描边、法力条流动、棋子待机微动都会让朴素逐像素差分一直误触发。因此：
- 用**阈值化**差分（缩略图哈希 / 直方图 / 降采样后差异度），而非逐像素全等。
- 按 ROI 调灵敏度：文本区可严格，动画多的头像区要宽容。
- 必要时只对 ROI 中心区做差分，屏蔽已知会动的子区域（如边框光效）。

与阶段判定互补：战斗阶段棋盘单位一直动、差分疯狂触发，但此时不属于 planning 分支 → 棋盘**不激活、不识别**，沿用上一帧备战快照。

## 捕获策略：轮询 + 差分 + 鼠标辅助触发

**捕获节奏与识别节奏解耦**：高频轮询截图盯变化，只在变化时才花算力识别。

- **轮询 + 差分 = 免费的事件驱动**：差分本身就是「事件探测器」。你点鼠标买棋子 → 商店那一槽变空 → 下一次轮询差分立刻捕获 → 才触发重识别。想要的「点击后才识别」效果，差分天然给你，**不需要 hook 游戏输入**。
- **为什么不靠纯事件驱动**：纯「只在点击时抓」会瞎掉一大半变化——被动金币利息、对手把你血量打掉、倒计时驱动的 planning→combat（没人点鼠标）、海克斯弹窗、选秀开始、断线重连，全漏。所以差分轮询始终是**唯一真值来源**。
- **鼠标 track = 辅助触发（非唯一来源）**：OS 层读全局鼠标（**非游戏 hook，无 anti-cheat 风险**），定位为辅助——点击只给「什么时候」，差分给「变成了什么」（点击与画面稳定之间有买入动画 / 卡片滑出的时间差）。用法：平时低频轮询 + 差分；**点击后触发一小段高频抓拍 burst + 差分**，抓住稳定后的结果（低延迟又不全程高频）。
- 轮询频率可按阶段自适应：planning（商店热路径）快一点（0.3–0.5s），combat 慢一点。

## 各元素识别方法（难度递增）

| 元素 | 方法 | 备注 |
|---|---|---|
| 回合 / 倒计时 / 金币 / 等级 / 血量 | 数字模板匹配 | 字体固定、位置固定，最稳；时钟与数值复用同一套 |
| 商店棋子费用 | 查 catalog（非识别） | 认出英雄即知费用；边框颜色仅作校验 / 剪枝 |
| 商店棋子身份 | 模板匹配 | 头像干净固定，无需 ML；每赛季维护模板 |
| 装备栏装备 | 图标模板匹配 | 区分散件 / 成装 |
| 备战席棋子 | 头像分类 + 星级（头像上方星标） | 比棋盘好做 |
| **棋盘棋子** | **最难** | 六边形+透视投影；3D 模型识别；遮挡 |

### 费用来自 catalog，边框颜色仅作校验 / 剪枝

棋子费用是**固定属性**（阿狸永远 2 费），不需要识别——认出是哪个英雄后，从 catalog manifest 里 `apiName → cost` 直接查。**输出的 `cost` 字段来自 catalog，而非图像识别。**

边框颜色（1费灰 / 2费绿 / 3费蓝 / 4费紫 / 5费橙）不用来读费用，而是辅助英雄识别的可选优化：**交叉校验**（分类器说 2 费但边框金色 → 一定错了，降 confidence）；**候选集剪枝**（先用边框定费用档，只在该档英雄里比对）。MVP 可先不做。

### 星级识别（优先读颜色，数量做校验）

头顶星标用数量和颜色双重编码等级（1 星青铜 / 2 星银 / 3 星金）。逐颗数星星在低分辨率 + 发光描边下不稳；判「星标区域主色调」用直方图 / HSV 主色则很鲁棒，三档颜色差异大。两条线索不一致时降 confidence。
- **备战席**：规整网格，星标在头像上方固定偏移 → 固定 ROI，最稳，先做。
- **棋盘**：星标浮在 3D 模型头顶，屏幕位置 = 格子透视位置 + **随英雄模型身高变化的**偏移 → ROI 不能写死，从格子坐标推区域再上扩容差搜；靠备战阶段单位静止做多帧颜色投票平掉抖动 / 遮挡。

### 棋盘双档可信度 + 身份靠追踪「搬运」

- **必做（高可信）**：格子是否有棋子 + 站位（六边形坐标）+ 星级。
- **尽力（可低 confidence）**：棋盘单位身份 + 携带装备（最难，不准就如实给低分，别硬猜）。

**棋盘单位身份优先靠追踪，不靠硬认 3D 模型**：身份在备战席 / 商店是干净 2D 头像，认得准；单位上场只能从「备战席拖到棋盘」或「买入直接上场」。所以追踪 owned units 的位置转移（备战席消失 + 对应棋盘格出现），把已知身份「搬」到棋盘格，准确率直接继承备战席。「读最终阵容硬认 3D」是退路，不是主路。

## 持久化对局状态 + StateTracker

模块从一局开始就维护一份**对局状态 JSON**，每帧观测是**合并进**这份状态，而不是从零重建。这是「干净时刻读 → 沿用」原则在**时间维度**上的终点：棋盘单位身份、星级、已选海克斯这些「难形态」，都靠在容易的时刻读到、之后作为状态记住。

**生命周期**：`game_start` / `game_end` 这两个 phase-marker 用于**初始化 / 重置**状态；一局内只增量更新，对局结束清空。

**两类字段，更新方式不同**：
- **纯瞬时读**（金币 / 等级 / 经验 / 血量 / 回合 / 倒计时）：无需累积，每帧 ROI 直接覆盖。
- **累积追踪**（自己的棋子、装备绑定、已选海克斯）：靠事件转移维护身份，跨帧沿用 confidence。

**战队棋子状态（owned units）** = 备战席 + 棋盘 合并去重的「我方阵容」。每个单位带：内部实例 id（`iid`）、身份（`apiName`）、星级、位置（bench slot / board hex）、携带装备、各字段 confidence 与 `last_seen`。

**核心难点是「对账」(reconciliation)，不是识别**：
- 追踪会漂移（漏掉一次拖拽 / 出售 / 三星合成）。所以用「干净读」周期性**重对齐**：备战席任意备战阶段都能干净读 → 每个 planning 阶段拿备战席真值校准 owned units，纠正漂移。
- **三星合成**：3 个同名同星 → 合成更高一星，拥有数突变（−3 + 1 高星），追踪器要把它识别为一个事件而非「凭空消失 / 出现」。
- **装备绑定**：装备拖到单位上 → 维护 item→unit 绑定。
- **观测与预测冲突时**：信观测、改状态、记日志。

由 `StateTracker` 承担，挂在流水线末端（识别结果 → 合并进状态 → 输出）。

## 输出 Schema（草案，会改）

- `scene` 字段标当前阶段。
- 顶栏时钟字段进 `player_state`：`stage` + `round`（如 `3-2` → stage 3, round 2）、`countdown`（秒）。
- `player_state` 含 `gold` / `level` / `xp`（当前 / 升级所需）/ `health`。
- 商店槽位能表达「空槽」和「已购买」状态。
- `timestamp` 毫秒。每个字段尽量带 `confidence`。
- 装备区分 `component`（散件）/ `completed`（成装）。
- `decision` 块：处于选择类阶段（god_boon / augment_select / blessing / pve_loot）时输出可选项——这是推荐引擎的直接输入。每个选项带 `tier`（MetaTFT 评级 S–D）与 `tags_zh`（中文标签），另有 `locked`/`votes`（多帧一致锁定状态）；锁定的决策同时落一条 track `decision` 事件。
- `augments` 块：**已选定**的海克斯（跨阶段累积，3 选 1 时读到、之后沿用），区别于 `decision` 里的「当前可选」。
- `gods` 块：本局神明池（1-1 介绍读到的两位神明）+ 已选祝福（`x-4` 各次的选择结果，跨阶段累积）。
- `unit_id` / `item_id` 直接用 catalog 的 `apiName`（Set 17 多为 `TFT17_xxx`，但不统一）。
- **输出 = 持久化对局状态快照**，非纯本帧观测。累积字段补 `last_seen`（毫秒）；备战席 / 棋盘单位补内部实例 `iid`，跨帧稳定。

planning 阶段示例：
```json
{
  "timestamp": 1782230000000,
  "screen": { "width": 1920, "height": 1080, "layout_profile": "16_9" },
  "scene": "planning",
  "player_state": { "stage": 3, "round": 2, "countdown": 18, "gold": 42, "level": 6, "health": 80 },
  "augments": [
    { "iid": "aug1", "augment_id": "TFT...", "name": "...", "tier": "gold", "confidence": 0.95, "last_seen": 1782210000000 }
  ],
  "shop": [
    { "slot": 1, "unit_id": "TFT17_Jinx", "name": "Jinx", "cost": 2, "confidence": 0.94 },
    { "slot": 2, "state": "empty" }
  ],
  "items": [
    { "slot": 1, "item_id": "TFT_Item_RecurveBow", "name": "Recurve Bow", "type": "component", "confidence": 0.96 }
  ],
  "bench": [
    { "iid": "u17", "slot": 1, "unit_id": "TFT17_Briar", "name": "Briar", "star": 2, "items": [], "confidence": 0.91, "last_seen": 1782229990000 }
  ],
  "board": [
    { "iid": "u12", "unit_id": "TFT17_Jhin", "name": "Jhin", "star": 2,
      "position": { "row": 3, "col": 4 },
      "items": ["TFT_Item_BlueBuff"], "confidence": 0.88, "last_seen": 1782229990000,
      "id_source": "tracked" }
  ]
}
```

选择类阶段示例（实际输出形状，其余识别块可缺省）：
```json
{
  "timestamp": 1782231000000,
  "scene": "augment_select",
  "decision": {
    "type": "augment_select",
    "locked": true,
    "votes": 2,
    "options": [
      { "slot": 1, "api": "TFT_Augment_BeltOverflow", "name_zh": "腰带过载",
        "tier": "B", "tags_zh": ["装备", "战斗"], "confidence": 1.0,
        "screen_title": "腰带过载", "channels": { "name": 1.0, "desc": 0.84 } }
    ]
  }
}
```

## 数据资产 / 版本

- 每赛季英雄、装备、图标、坐标全换；UI 坐标可能随补丁微调。
- 需按**赛季版本**组织参考资产库（头像模板、图标模板、坐标 profile）。
- `layout_profile` 需能带版本号。
- 长期维护成本主要在资产库，而非识别算法本身。

**已下载（类别一目录素材，Set 17 / patch 16.13.1）**：`scripts/download_assets.py` → `assets/set17/`
- `champions/` 63 张（512×512，商店可购买池；已剔除 Boss/PVE/铁砧）
- `items/` 332 张（128×128，散件+成装，跨赛季超集）
- `traits/` 44 张（32×32）
- `manifest.json`（中英文名 / cost / traits / composition / 图标相对路径）
- 注：这些是干净分卡图，**实机模板仍需从截图裁取校准**（类别二）。
- 运行：`micromamba run -n YunDing_MVP python scripts/download_assets.py`（`--set N` 指定赛季，`--no-images` 仅 manifest，`--refresh` 重下 json）。
- **文字通道识别语料（已就位，2026-07-03）**：`augments_zh.json`（276 项海克斯中英文名 + 中文描述 + effects，含 17 个神明恩赐 GodAugment；源 = CDragon zh_cn；仅 2 个奥索任务型子恩赐缺描述）+ `gods_zh.json`（9 位神明名字/称号 + 128 个按 stage 分组的 offering；源 = Blitz `utils.iesdev.com/static/json/tftTest/set17/zh_cn/gods-v2`，需浏览器 UA）。均由 `download_assets.py` 生成。识别走**文字优先**（图标对比仅备选交叉校验，icon_url 留在语料里），匹配配方见「决策卡识别与评级」章。另有两个**非下载资产**：`display_names_zh.json`（人工核对的国服改名种子表）、`name_overrides.json`（live 自举自动生成，核对后并入种子表）。

## MVP 范围

**第一刀（最短价值闭环，从「终极目标」倒推）**：`1920×1080` 单一 profile，链路 = `截图 → 顶栏时钟 + 阶段判定 → 决策窗口识别 → 查外部库 → 推荐`。**先做海克斯 3 选 1 推荐**，端到端打通「感知 → 查库 → 推荐」整条链路，再回头铺商店（商店推荐依赖阵容识别，是第二刀）。

**落地顺序**：
1. ✅ 顶栏时钟 + 数字识别基建（stage-round + 倒计时）
2. ✅ ClockTrack + RoundTrack：时间轴 track（完整一局 37 回合已验证，特殊回合结构已摸清）
3. 🟨 阶段判定收尾：✅ augment_select 迟滞 + bp 仲裁、✅ god 检测重构（`loading` / `god_intro` / `god_boon` 回合先验 + 回合内锁存，完整局回放验证）；⬜ 4 小阶段 → planning/combat 映射（四层叠加）
4. ✅ **海克斯识别 + 评级闭环**（2026-07-04 打通，详见「决策卡识别与评级」章）：OCR 文字通道打分 v2（离线 12/12，基线 4/12）→ CardRecognizer 挂 Pipeline → `decision` 块 + track `decision` 事件 + Dashboard DECISION 区（中文 tag + tier 徽章），实录帧回放验证。⬜ 收尾在该章「残留」清单（Windows 实测、ROI 接 marker、一句话推荐）
5. 🟨 神明祝福 2 选 1（`x-4`）：两步匹配初版已通（实录浮层帧 2/2；评级用神明恩赐代理，offering 无公开评级）。⬜ GOD_BAND 正式标定；1-1 神明池先验剪枝；神之祝福 4 选 1（`4-7`）未做
6. ⬜ 商店五槽 + 数值（金币 / 等级 / 血量，复用数字匹配器）——服务「买/D 推荐」，需先读出 bench/board 的**阵容组成**（哪些英雄几星即可，不需要精确格子/装备绑定）
7. ⬜ 装备 → 备战席 → 棋盘 → StateTracker 完整对账（推荐 v1 只要阵容组成，完整追踪后置）

**两个实操注意**：决策卡 OCR 统一在 1280 宽跑（CardRecognizer 内部降采样，实验证明够用）——`frames_out` 的 1280 帧因此可直接回放决策识别；国服客户端是中文名，外部评级库是英文 apiName 键，靠语料的中英文映射 + `apiName` 对齐。

**坐标系 = 归一化**：ROI 用 16:9 参考系的相对坐标（0–1）定义，`LayoutMapper` 运行时按实际帧分辨率换算成像素。这样 4K 录像帧可直接喂入、1080p 上线零重标。`LayoutMapper` 预留多 profile 接口，MVP 先填 1920×1080 一套。

## 待定 / 开放问题

- ~~外部数据库选型~~ **已定（2026-07-03 调研）**：Set 17 海克斯/神明**胜率无任何公开源**（Riot 2023-09 从 match API 删除海克斯数据后全行业断供；tactics.tools 的 Set 17 augment 接口实测返回空，MetaTFT 海克斯页实为职业选手 100T Spencer 人工评级）。推荐 v1 用 **MetaTFT 评级 JSON**：`https://api-hc.metatft.com/tft-stat-api/augments_tiers`（免鉴权，5 档 S/A/B/C/D → augment apiName 列表，与识别输出天然对齐）。备用：`d3.tft.tools/stats2/general/1100/{patch}/{rank}` 有 Set 17 英雄/装备/羁绊真胜率（第 6 步商店推荐可用；patch 编码 = 16000+(x+7)*10，17.6→16130）。国服「云顶营地」可能有真胜率，未验证。
- ~~神明祝福的图标资产数据源~~ 已解决：识别走文字通道，语料 `gods_zh.json` 已下载（见数据资产一节）；图标如需可从 MetaTFT CDN（`cdn.metatft.com/file/metatft/gods/`）或 CDragon icon_url 补。
- `game_end` 结算画面素材仍缺：完整局实录里玩家 6-5 被淘汰后是观战视角，未见结算大画面（可能直接退出了），game_end marker 无从标定。
- 顶栏时钟（stage-round / 倒计时 / phase_text）与各阶段 phase-marker 的 ROI 坐标，需从真实帧标定（完整局 517 帧已到位）。
- 转场缓冲帧数 N、合法转移表的具体内容，可用完整局 track 的转场序列标定。
- StateTracker 实现细节：实例 id 分配策略、三星合成事件的判定规则、对账冲突的仲裁优先级、漂移检测阈值。
- 数据来源确认：云顶**无**对局内棋盘状态的官方接口（不同于 LoL Live Client API），截图基本是唯一路径。
- 目标平台是否只 PC 客户端，还是后续要兼容手游端。
- 棋盘单位身份识别是否需要训练 ML 模型（模板匹配对 3D 模型大概率失效）。

## 部署约束：Windows 运行 / Linux 开发

游戏跑在 **Windows**，但当前开发在**远程 Linux server**。设计上把「平台相关 I/O」与「平台无关核心」分离：

- **平台无关（Linux 上开发 / 测试，用静态截图 / 录像帧当输入）**：识别管线、TopBarClock、SceneClassifier、差分、catalog、模板匹配、数字识别。
- **平台相关（必须在 Windows）**：实时抓屏、鼠标 track、ROI 标定基准采集、实机模板采集。
- `ScreenCapture` 做成可替换薄接口：`FileCapture`（Linux 开发，从 PNG / 录像帧读）/ `MSSCapture`（Windows 实时抓屏）。下游只吃「图 + 分辨率 + layout」，不关心来源。
- 代码用 `pathlib`、避免硬编码路径，保证跨平台。

### Windows 端需要准备的东西

1. **真实游戏素材**：`video/` 那段 4K 录像仅 ~4.3 分钟（早期阶段，survey/planning_seq 帧由它抽取）。**已用 `--save-frames` 补录完整一局**（34.7min、`1-1`→`6-5` 共 37 回合、`track.json` 3992 样本 + `frames_out/` 517 帧）：覆盖 加载 / 神明介绍(1-1) / 三次海克斯 / 三次神明祝福(x-4) / 神之祝福(4-7) / PVE / 后期备战棋盘。**仍缺**：`game_end` 结算画面、多局样本（验证回合结构与 marker 阈值的稳定性）。用途：① 测试素材 ② 标定 ROI ③ 裁实机模板（类别二，校准干净分卡图）。
2. **显示设置固定**：游戏用**无边框全屏 + 100% 缩放 / 原生 1920×1080**。Windows DPI 缩放（125%/150%）会让截图像素与逻辑分辨率错位、ROI 全偏；layout profile 需记录此前提。
3. **环境可复现**：`environment.yml` 在 Windows 上 `micromamba create -f environment.yml` 重建即可。注意 pip 段依赖（rapidocr）在**已有环境**上不会自动补，需手动 pip install（见「环境」一节的坑）。
4. **运行期抓屏目标**：`MSSCapture` 需能定位游戏窗口（全屏抓 / 指定窗口抓），上线时在 Windows 实测。

### 数据采集工作流：`tools/live.py`（Windows 录 → Linux 审）

无需第三方录屏软件——`live.py` 边跑边采。Windows 上一条命令走完整局，产出发回 Linux 审查 / 标定：

```
python tools/live.py --game-monitor 1 --display-monitor 2 --record track.json --save-frames frames_out
```

- **`--record track.json`**：整局时间轴 track（数值，小）。逐帧样本含 `stage/round/countdown/sr_status` + `scene/n_panels/bluepurple`（marker 结果一并落盘，供离线校准阈值）；**事件里含 `decision`（每次锁定的海克斯/神明选项 + 评级 + 中文 tag，重掷再记一条）**——track.json 就是整局识别档案，回来逐回合核对对错。退出时落盘、每 40 帧增量存防崩溃。
- **`--save-frames frames_out`**：录制时**自动存降采样关键帧**（宽 1280，~1MB/张；**每 `--frame-interval`（默认 4s）一张 + 一进海克斯/神明即存**）。文件名 `f_{ts}_{stage-round}_{scene}.png`，**ts 与 track.json 对齐**——一眼看出每帧是哪个回合、检测器判成什么。替代"录整段视频"（视频 GB 级难传，帧集小且我方直接可用）。
- **回环**：Windows 采集 → 发回 `track.json` + `frames_out/` + `assets/set17/name_overrides.json`（若生成，live 自举学到的国服改名，核对后并入种子表）→ Linux 用帧标定 ROI（备战席 / 棋盘）、裁实机模板、多帧校准 marker、核对 decision 事件正确率。首局完整实录已经用这条回环确认了：海克斯回合 = `2-1/3-2/4-2`、`x-4` 是神明祝福回合（Set 17 无选秀）、`4-7` 是神之祝福、god 检测语义要重构。这就是「平台相关采集 / 平台无关分析」分离的落地闭环。
