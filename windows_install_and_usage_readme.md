# Windows 安装与使用说明

云顶之弈感知层 —— 在 **Windows** 上实时识别屏幕，把检测到的**回合数 / 倒计时**（后续会扩展到商店、装备、备战席、棋盘等）显示在一个仪表盘窗口里。

典型用法：**主屏开云顶，副屏放仪表盘实时刷新。**

> 当前已实现：顶栏时钟（stage-round + 倒计时）+ **时间轴 track**（逐帧把回合/倒计时记成序列并派生事件），整备 / 战斗阶段都能读，**分辨率无关**（1600×900 及以上都行）。仪表盘可继续扩展。
>
> **本次要你做的事**：开一把游戏，用 `--record` 把整局 track 录成 JSON，打完把 JSON 发回来审查（见第 5.1 节）。

---

## 1. 把项目拷到 Windows

把整个 `MVP` 目录拷到 Windows（U 盘 / 网盘 / git 均可）。需要带上：
- `tft_mvp/`（代码）、`tools/`（脚本）、`assets/set17/`（图标 + 数字模板）、`environment.yml`
- 可不拷 `video/` 和 `assets/frames/`（那是 Linux 端开发素材，体积大）

---

## 2. 安装 micromamba

PowerShell 里执行（官方一键脚本）：

```powershell
Invoke-Expression ((Invoke-WebRequest -Uri https://micro.mamba.pm/install.ps1).Content)
```

装完**重开一个 PowerShell**，确认能用：

```powershell
micromamba --version
```

---

## 3. 建运行环境

在项目根目录（含 `environment.yml` 的那层）：

```powershell
micromamba create -f environment.yml
```

之后每条命令都用 `micromamba run -n YunDing_MVP python ...` 跑（不必手动 activate）。

> 依赖：opencv 5.0 / numpy / python-mss / pillow / requests。Windows 的 conda-forge opencv 自带 GUI，仪表盘窗口能正常弹出。

---

## 4. 确认你的显示设置（不确定也没关系，工具能自查）

识别对屏幕布局有两个前提，先对一下：

**① 必须「整屏 == 游戏画面」** —— 云顶用**全屏**或**无边框全屏**运行。
为什么：程序抓的是整块屏幕，再按比例定位 UI。如果游戏是带标题栏的小窗口，UI 位置就对不上。
- 云顶里：设置 → 显示 → 窗口模式选「无边框」或「全屏」。

**② 屏幕比例建议 16:9**（1920×1080 / 2560×1440 / 3840×2160 都行）。
分辨率不挑（程序自动适配），但**比例**目前按 16:9 标定。16:10 / 21:9 带鱼屏顶栏位置会偏，需要重新标定（见第 7 节，截图发回即可）。

**③ 建议把 Windows 缩放设为 100%。**
设置 → 系统 → 显示 → 缩放。125%/150% 一般不影响全屏游戏的实际像素，但 100% 最省心。

**不确定当前是什么设置？两条命令自查：**

```powershell
# 看有哪些屏、各自分辨率和坐标（也能确认游戏在哪个屏）
micromamba run -n YunDing_MVP python tools/live.py --list

# 抓一帧主屏存成图，自己打开看看（也可发回给我核对/校准 ROI）
micromamba run -n YunDing_MVP python tools/live.py --game-monitor 1 --snapshot my_screen.png
```

`--list` 输出形如：
```
  [0] 5120x1440  @ (0,0) (全部拼合)
  [1] 2560x1440  @ (0,0)
  [2] 2560x1440  @ (2560,0)
```
`[1] [2]` 是两块物理屏。记下**游戏所在那块**的编号。

---

## 5. 跑起来

主屏（假设 `[1]`）开云顶，仪表盘放副屏（`[2]`）：

```powershell
micromamba run -n YunDing_MVP python tools/live.py --game-monitor 1 --display-monitor 2
```

- 进游戏到**整备 / 战斗**阶段，仪表盘上的 STAGE-ROUND 和 COUNTDOWN 就会实时跳动。
- 刷新间隔默认 0.5s，可调：`--interval 0.3`。
- 窗口内按 **q 或 Esc** 退出。

**只有一块屏**也能用（窗口会盖在游戏上，自己挪一下）：
```powershell
micromamba run -n YunDing_MVP python tools/live.py --game-monitor 1
```

**不开游戏先看看效果**（用自己的截图当输入）：
```powershell
micromamba run -n YunDing_MVP python tools/live.py --image my_screen.png
```

---

## 5.1 录制整局 track（**本次重点，打完发我审查**）

加 `--record`，开一把完整对局，程序会把**每一帧的回合/倒计时 + 派生事件**记进一个 JSON：

```powershell
micromamba run -n YunDing_MVP python tools/live.py --game-monitor 1 --display-monitor 2 --record track.json
```

- 从**进对局就开始跑**，尽量覆盖：开局 / 选神明 / 备战 / 战斗 / **海克斯选择** / 选秀。
- 中途每 40 帧自动存一次，**按 q/Esc 正常退出**会写入完整数据（`track.json` 就在项目根目录）。
- 控制台会实时打印事件，如 `[event] round_advance ...` / `[event] countdown_reset ...`，看到就说明在正常记。
- 打完把 `track.json` 发回给我。我会核对：回合是否单调、倒计时归零/重置事件是否踩在真实转场上、海克斯期间时钟是否如预期变 `miss`（浮层会调暗顶栏，属已知现象）。

> 只想快速验证录制能跑，可用静态图：`... --image my_screen.png --record track.json`（会循环同一帧，事件不多，仅测通路）。

---

## 6. 仪表盘看什么

- **STAGE - ROUND**：当前阶段-回合（如 `2-1`）。
- **COUNTDOWN**：倒计时秒数（整备=备战剩余时间；战斗=战斗计时）。
- 每个读数下方**色条**＝置信度：绿(高) / 黄(中) / 红(低)。
- **CLOCK** 区：分辨率、读取状态、各项置信度。
- **TRACK** 区：已记录帧数、倒计时趋势（falling/rising/flat）、事件数、最近一个事件。
- 后续加的商店 / 装备 / 数值识别，会作为新区块往下追加。

---

## 7. 识别不对怎么办

| 现象 | 多半原因 | 处理 |
|---|---|---|
| 读数一直 `--` | 抓错屏 / 游戏不是全屏 / 比例非 16:9 | 确认 `--game-monitor` 对、游戏无边框全屏 |
| 数字偶尔跳错 | 转场动画帧 | 正常，下一帧自动恢复（已防抖） |
| 整体框都偏 | 屏幕比例非 16:9，或有标题栏 | 截图发回重新标定 ROI |
| 窗口弹不出来 | 副屏编号填错 | 用 `--list` 核对，或先不加 `--display-monitor` |

**最有效的排查**：抓一帧发回——
```powershell
micromamba run -n YunDing_MVP python tools/live.py --game-monitor 1 --snapshot my_screen.png
```
有了你真实屏幕的截图，就能确认 ROI 是否对齐、必要时按你的分辨率/比例重新标定。

---

## 8. 当前能力与后续

- **现在**：顶栏时钟（回合 + 倒计时）+ 时间轴 track（`--record` 导出 JSON），实时仪表盘。
- **接下来**（按设计依次叠加）：用真实对局 track 数据定阈值 → 阶段判定 → 商店五槽 → 数值（金币/等级/血量）→ 装备 → 备战席 → 棋盘。每加一项，仪表盘多一个区块，用法不变。
