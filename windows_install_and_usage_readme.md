# Windows 安装与使用说明

云顶之弈感知层 —— 在 **Windows** 上实时识别屏幕，仪表盘实时显示**回合 / 倒计时**，并在**海克斯 3 选 1 / 神明祝福 2 选 1** 弹出时自动识别选项、给出 **MetaTFT 评级（S–D 徽章）+ 中文标签**（经济/战斗/装备/羁绊/成长/功能）。

典型用法：**主屏开云顶，副屏放仪表盘实时刷新。**

> 当前已实现：顶栏时钟 + 时间轴 track + 阶段检测（海克斯/神明浮层）+ **决策卡识别与评级**（DECISION 区）。**分辨率无关**（1600×900 及以上都行）。
>
> **本次要你做的事**：开一把游戏验证决策识别，打完把 `track.json` + `frames_out\` + `assets\set17\name_overrides.json`（若生成）发回审查（见第 5.1 节）。

---

## 1. 把项目拿到 Windows

**首选 git**（仓库 `gaoshang-strong/YunDing_MVP`，日常更新只需 `git pull`）：

```powershell
git clone git@github.com:gaoshang-strong/YunDing_MVP.git
```

仓库里已含代码 + `assets/set17/` 的语料和数字模板；图标素材（champions/items/traits，不入库）如需可在建好环境后 `python scripts/download_assets.py` 补拉——**当前功能（时钟 + 决策识别）不需要它们**。

> 每次 `git pull` 后留意：若 `environment.yml` 有变化，按第 3 节「已有环境」补装 pip 依赖。

---

## 2. 安装 micromamba（**短路径装法，避开长路径坑**）

> **为什么不用官方一键脚本**：micromamba 默认把环境 / 包目录放在用户目录（`C:\Users\你的名字\...`，本身就长），而 conda 包内部文件层级又深，两者叠加极易超过 Windows 的 **260 字符路径上限（MAX_PATH）**，导致创建环境时解压失败、报 `path too long` 之类的错。解法：把 micromamba 的 root 和包目录放到 `C:\` 下的**短目录**。

以**管理员身份**打开 PowerShell，整段贴进去执行：

```powershell
# 1. 允许当前用户跑 PowerShell 脚本（后面 shell init 需要）
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned -Force

# 2. 建短路径目录（关键：避开 260 字符长路径限制）
New-Item -ItemType Directory -Force "C:\micromamba_bin" | Out-Null
New-Item -ItemType Directory -Force "C:\micromamba"     | Out-Null
New-Item -ItemType Directory -Force "C:\mamba_pkgs"     | Out-Null

# 3. 下载 micromamba.exe 到 C:\micromamba_bin
Invoke-WebRequest `
  -Uri "https://github.com/mamba-org/micromamba-releases/releases/latest/download/micromamba-win-64" `
  -OutFile "C:\micromamba_bin\micromamba.exe"

# 4. 本会话立即生效
$env:Path = "C:\micromamba_bin;" + $env:Path
$env:MAMBA_ROOT_PREFIX = "C:\micromamba"
$env:MAMBA_PKGS_DIRS   = "C:\mamba_pkgs"

# 5. 永久生效（用户级）
#    注意：MAMBA_ROOT_PREFIX / MAMBA_PKGS_DIRS 值短，用 setx 没问题；
#    但 PATH 不能用 `setx PATH`——setx 有 1024 字符上限会静默截断、把 PATH 写坏。
#    故 PATH 改用 .NET API（无长度限制、只动用户 PATH）。
setx MAMBA_ROOT_PREFIX "C:\micromamba" | Out-Null
setx MAMBA_PKGS_DIRS   "C:\mamba_pkgs" | Out-Null
$userPath = [Environment]::GetEnvironmentVariable("Path","User")
[Environment]::SetEnvironmentVariable("Path", "C:\micromamba_bin;$userPath", "User")

# 6. 验证 + 初始化 PowerShell 集成
micromamba --version
micromamba shell init -s powershell -p C:\micromamba
```

执行完**关掉 PowerShell、重开一个**，确认能用：

```powershell
micromamba --version
```

> 若下载或建环境时报 **SSL / certificate / revocation** 类错误（部分网络对证书吊销检查会失败），再执行下面两句后重试即可：
> ```powershell
> $env:CURL_SSL_NO_REVOKE = "1"
> setx CURL_SSL_NO_REVOKE "1" | Out-Null
> ```

---

## 3. 建运行环境

**首次安装**（项目根目录，含 `environment.yml` 的那层）：

```powershell
micromamba create -f environment.yml
```

之后每条命令都用 `micromamba run -n YunDing_MVP python ...` 跑（不必手动 activate）。

**已有环境、git pull 更新代码后**——注意 `environment.yml` 里的 **pip 段依赖不会自动补装**（micromamba 只在 create 时装 pip 段），新增依赖要手动装。当前需要的：

```powershell
micromamba run -n YunDing_MVP pip install rapidocr-onnxruntime==1.4.4
```

装完验证（打出 OK 才算装对，注意别装到别的环境里）：

```powershell
micromamba run -n YunDing_MVP python -c "from rapidocr_onnxruntime import RapidOCR; print('OK')"
```

> 依赖：opencv 5.0 / numpy / python-mss / pillow / requests（conda 段）+ rapidocr-onnxruntime（pip 段，决策卡 OCR）。Windows 的 conda-forge opencv 自带 GUI，仪表盘窗口能正常弹出。
>
> **缺 rapidocr 的症状**：程序照常跑、时钟正常，但进海克斯/神明界面后 DECISION 区显示红色 `OCR UNAVAILABLE`（旧版则是永远 READING）——这就是 pip 段没装上，跑上面两条命令即可。

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
micromamba run -n YunDing_MVP python tools/live.py --game-monitor 1 --display-monitor 2 --record track.json --save-frames frames_out
```

- `--record track.json`：数值 track（小，必开）。**事件里含 `decision`**——每次海克斯/神明选项锁定时记一条（选项 + 评级 + 中文 tag，重掷再记一条），track.json 就是整局识别档案。
- `--save-frames frames_out`：顺手存降采样关键帧到 `frames_out\`（每 4s 一张 + 一进海克斯/神明就存），供离线核对错判帧、标定 ROI。一局约几百张、每张 ~1MB，打完把**整个 `frames_out` 文件夹**（可打包 zip）连同 `track.json` 一起发回。
- 从**进对局就开始跑**，尽量覆盖：开局 / 神明介绍(1-1) / 备战 / 战斗 / **海克斯(2-1/3-2/4-2)** / **神明祝福(x-4)** / **PVE(x-7)** / 神之祝福(4-7)。
- 中途每 40 帧自动存一次，**按 q/Esc 正常退出**会写入完整数据（`track.json` 就在项目根目录）。
- 控制台会实时打印事件，如 `[event] round_advance ...` / `[event] decision ...`，看到就说明在正常记。
- **打完发回三样**：`track.json`、`frames_out\`、以及 `assets\set17\name_overrides.json`（若生成——那是本局自动学到的国服海克斯改名，核对后会并入语料）。

> 只想快速验证录制能跑，可用静态图：`... --image my_screen.png --record track.json`（会循环同一帧，事件不多，仅测通路）。

---

## 6. 仪表盘看什么

- **STAGE - ROUND**：当前阶段-回合（如 `2-1`）。
- **COUNTDOWN**：倒计时秒数（整备=备战剩余时间；战斗=战斗计时）。
- 每个读数下方**色条**＝置信度：绿(高) / 黄(中) / 红(低)。
- **DECISION** 区（进海克斯/神明界面自动出现）：每张卡一行——中文卡名 + 中文标签 + 置信度 + 右侧**评级徽章**（S 金 / A 绿 / B 蓝 / C 灰 / D 红；灰色 `-` = 评级表里没有）。右上角 `LOCKED`（多帧确认完成）或 `READING n/2`（识别中）。
  - READING 下的小字 `runs= lines= cols= ms` 是诊断行：跑了几次 OCR / 认到几行字 / 聚成几列 / 耗时——卡住时把这行发我。
  - **神明回合（x-4）大部分时间 READING 是正常的**：2 选 1 浮层只在回合开头几秒出现，其余走动捡宝珠时间无卡可读。海克斯浮层是静止的，正常 2~3 秒内 LOCKED。
  - 红色 `OCR UNAVAILABLE` = rapidocr 没装上，见第 3 节。
- **CLOCK** 区：分辨率、读取状态、各项置信度。
- **TRACK** 区：已记录帧数、倒计时趋势（falling/rising/flat）、**当前小阶段**（策划/转场/战斗，每回合 4 段）、事件数、最近一个事件（决策识别时为省空间会暂时隐藏）。
- 后续加的商店 / 装备 / 数值识别，会作为新区块往下追加。

---

## 7. 识别不对怎么办

| 现象 | 多半原因 | 处理 |
|---|---|---|
| 读数一直 `--` | 抓错屏 / 游戏不是全屏 / 比例非 16:9 | 确认 `--game-monitor` 对、游戏无边框全屏 |
| 数字偶尔跳错 | 转场动画帧 | 正常，下一帧自动恢复（已防抖） |
| 整体框都偏 | 屏幕比例非 16:9，或有标题栏 | 截图发回重新标定 ROI |
| 窗口弹不出来 | 副屏编号填错 | 用 `--list` 核对，或先不加 `--display-monitor` |
| DECISION 显示 `OCR UNAVAILABLE` | rapidocr 没装进 YunDing_MVP 环境 | 第 3 节的 pip install + 验证命令 |
| 海克斯界面一直 READING | 看诊断行：`runs=0` 没在跑；`lines=0` OCR 没认到字；`cols≠3` 分列不对 | 把诊断行数字 + 一张截图发回 |
| 神明回合一直 READING | 走动段本来无卡可读 | 正常；只有回合开头 2 选 1 浮层那几秒能锁定 |
| 卡名对但评级是灰色 `-` | 该项不在 MetaTFT 评级表（如神明 offering） | 正常，属已知数据缺口 |
| 决策识别期间画面顿挫 | OCR ~2s/次（CPU） | 正常，决策窗口是静止画面不影响 |

**最有效的排查**：抓一帧发回——
```powershell
micromamba run -n YunDing_MVP python tools/live.py --game-monitor 1 --snapshot my_screen.png
```
有了你真实屏幕的截图，就能确认 ROI 是否对齐、必要时按你的分辨率/比例重新标定。

---

## 8. 当前能力与后续

- **现在**：顶栏时钟（回合 + 倒计时）+ 时间轴 track（`--record` 导出 JSON，含 decision 事件）+ 阶段检测（海克斯/神明浮层）+ **决策卡识别与评级**（DECISION 区：中文卡名 + 中文标签 + MetaTFT S–D 徽章，锁定/重掷自动跟踪）。
- **接下来**（按设计依次叠加）：「选哪张」一句话推荐 → planning/combat 阶段判定 → 商店五槽 → 数值（金币/等级/血量）→ 装备 → 备战席 → 棋盘。每加一项，仪表盘多一个区块，用法不变。
