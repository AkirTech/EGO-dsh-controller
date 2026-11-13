<div align="center">

# EGO-dsh-controller

**把一块 FPGA 开发板，变成 DeepSeek Harness 的物理控制面板**

8 位数码管显示时间与 Agent 状态 · 5 个按键完成选择与确认 · 2 个指示灯直观映射三态

[![Verilog](https://img.shields.io/badge/Verilog-2001-%23B2B7F8?style=for-the-badge&logo=v&logoColor=white)](https://en.wikipedia.org/wiki/Verilog)
[![Vivado](https://img.shields.io/badge/Vivado-2017.1-%23E01F27?style=for-the-badge&logo=xilinx&logoColor=white)](https://www.xilinx.com/products/design-tools/vivado.html)
[![Python](https://img.shields.io/badge/Python-3.12-%233776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.x-%233178C6?style=for-the-badge&logo=typescript&logoColor=white)](https://www.typescriptlang.org/)
[![DeepSeek Harness](https://img.shields.io/badge/DeepSeek-Harness-4D6BFE?style=for-the-badge)](https://github.com/deepseek-ai/deepseek-harness)

[![RTL Tests](https://img.shields.io/badge/RTL_tests-182_passing-%232ea44f?style=for-the-badge)](LessonProject.srcs/sources_1/new/)
[![Python Tests](https://img.shields.io/badge/pytest-244_passing-%232ea44f?style=for-the-badge)](host/tests/)
[![Plugin Tests](https://img.shields.io/badge/plugin_tests-58_passing-%232ea44f?style=for-the-badge)](host/dsh-plugin/test/)
[![Timing](https://img.shields.io/badge/WNS-%2B4.543_ns-%232ea44f?style=for-the-badge)]()
[![License](https://img.shields.io/badge/license-MIT-blue?style=for-the-badge)](LICENSE)

[**安装**](#安装) · [**快速开始**](#快速开始) · [**硬件**](#硬件接线) · [**架构**](#架构) · [**文档**](#文档)

</div>

---

## 这是什么

让 Agent 的状态**不用切窗口就能扫一眼看到**，并且**按一下板子就能回答它的问题**。

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│   DeepSeek Harness  ──── DSH 插件 (TypeScript)              │
│         │                    │                              │
│         │                Python 桥接 (ego_bridge)           │
│         │                    │                              │
│         │              USB-UART (CP2102) @ 115200           │
│         │                    │                              │
│   ┌─────▼────────────────────▼──────────────────────────┐   │
│   │  EGO1 · Artix-7 xc7a35tcsg324-1                     │   │
│   │                                                      │   │
│   │   uart_rx ─► frame_parser ─► link_ctrl               │   │
│   │                                    │                 │   │
│   │            ┌───────────────────────┼──────────┐       │   │
│   │            ▼                       ▼          ▼       │   │
│   │      disp_content            led_state_drv  frame_builder
│   │      (8 位数码管)             (K1/K6 双灯)       │    │   │
│   │            ▲                       ▲          │       │   │
│   │            └──── key_debounce ◄────┴──────────┘       │   │
│   │                  (5 个按键)                           │   │
│   └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

**三态映射**

| 状态 | K6 灯 | 含义 |
|---|---|---|
| **FREE** | 灭 | 没事干 |
| **RUNNING** | 1 Hz 慢闪 | 正在干活 |
| **WAITING** | 常亮 | **有事等你** |

> 需要人介入的状态用「常亮」而不是闪烁：余光里最容易被察觉，
> 而闪烁容易被大脑归类成「正忙，别管」。

---

## 功能

| 能力 | 状态 | 说明 |
|---|:---:|---|
| 8 位数码管 · 5 按键 · 双灯 | ✅ | 已上板验证 |
| 时间同步（含**星期跨日**） | ✅ | `TIME_SYNC`，秒级跳动 + 每 24h 星期 +1 |
| Agent 状态 → 指示灯 | ✅ | `agent/status` → `STATE_SET` |
| 提问 / 审批（Q&A） | ✅ | 板子按键作答，与 GUI 并行 |
| 掉电保持 | ✅ | 烧 QSPI Flash，长按 PROG 重载 |
| 模拟器（无板卡） | ✅ | 全链路可离线验证 |

**已验证的实测数据**

| | 结果 |
|---|---|
| RTL 仿真 | **182 用例**（tb_m1 68 / tb_m2 73 / tb_day_rollover 30 / tb_uart_rx 11） |
| Python | **244 用例** |
| 插件 | **58 用例** |
| 时序 | **WNS +4.543 ns · WHS +0.132 ns · 0 失败路径** |
| 资源 | 967 LUT (4.65%) · 978 FF (2.35%) · 34 IOB |

---

## 安装

### 前置条件

| 需要 | 版本 | 用在哪 |
|---|---|---|
| **Python** | 3.10+（开发用 3.12） | `host/ego_bridge` |
| **Node.js** | 20+ | `host/dsh-plugin` 构建 |
| **DeepSeek Harness** | 0.2.x | 宿主 |
| **Vivado** | 2017.1（可选） | 只在要改 RTL 时需要 |

> **不接真板也能装**：模拟器模式下 Python 侧无需 pyserial 之外的依赖，
> 插件会自动拉起 `--sim` 后端。

### 🚀 让 Agent 帮你装

把下面整段**原样**贴给 DeepSeek Harness（或任何能执行命令的 Agent）：

> 请帮我在 `K:\Verilog\LessonProject\EGO-dsh-controller` 安装 ego-dsh-controller 的 host 侧，
> 并按下面步骤执行，每一步做完告诉我结果，遇到失败先停下来问我：
>
> 1. **检查环境**：`python --version`、`node --version`，确认 Python ≥ 3.10、Node ≥ 20。
> 2. **装 Python 依赖**：`cd host && python -m pip install -r requirements.txt`
> 3. **跑 Python 测试**：`cd host && python -m pytest`，期望 **244 passed**。
> 4. **构建插件**：`cd host/dsh-plugin`，先装依赖（若 `node_modules` 不存在），
>    然后 `npx tsc -p tsconfig.json`，期望 0 错误，产出 `lib/index.js`。
> 5. **跑插件测试**：`cd host/dsh-plugin && node --experimental-strip-types --test "test/*.test.ts"`，
>    期望 **58 passing**。
> 6. **探查串口**：`cd host && python -m ego_bridge.cli ports`。
>    - 看到 `Silicon Labs CP210x (COMx)` → 记下端口号
>    - 没有 → 检查 J8（USB-UART）是否插好、CP210x 驱动是否安装，
>      参见 `host/README.md` 的故障排查
> 7. **接入 DSH**：编辑 `~/.dsh/profiles/web/cordis.patch.yml`，在**末尾**追加：
>    ```yaml
>    - insert:
>        - id: ego-bridge
>          name: 'K:/Verilog/LessonProject/EGO-dsh-controller/host/dsh-plugin/lib/index.js'
>          config:
>            simulate: false        # 没有板卡时改 true
>            baudRate: 115200
>            backendDir: 'K:/Verilog/LessonProject/EGO-dsh-controller/host'
>            session: 0
>            questionTimeoutMs: 0
>            verbose: true
>    ```
>    追加前先备份该文件。
> 8. **验证加载**：重启 `dsh web`，终端应出现
>    `[ego-bridge] backend ready (port=COMx sim=false)` 和 `[ego-bridge] link up`。
> 9. **汇总**：告诉我端口号、各步测试结果、以及有没有哪一步报错。

> ⚠️ 第 7 步会改动你的 DSH 全局配置。Agent 应当**先备份**再写。
> 想跳过 DSH 集成，做到第 6 步就够——`host/README.md` 里所有命令都能单独用。

### 手动安装（三行）

```powershell
cd K:\Verilog\LessonProject\EGO-dsh-controller\host
python -m pip install -r requirements.txt
python -m pytest                      # 期望 244 passed
```

插件要单独构建：

```powershell
cd K:\Verilog\LessonProject\EGO-dsh-controller\host\dsh-plugin
npx tsc -p tsconfig.json              # 产出 lib/，loader 直接 import 它
```

> **`lib/` 已提交进仓库**，普通用户不用构建。只有改了 `src/` 才需要重跑 tsc。

---

## 快速开始

### 没有板卡：先跑模拟器

```powershell
cd host
python -m ego_bridge.cli monitor --sim
```

插件也可以 `simulate: true` 跑模拟器，用来验证 DSH 侧逻辑。

### 有板卡

```powershell
cd host
python -m ego_bridge.cli ports           # 找 CP210x，自动排除蓝牙串口
python -m ego_bridge.cli monitor         # 每秒一帧 STATUS_RPT 即链路正常
python -m ego_bridge.cli sync-time       # 用本机时间校时
python -m ego_bridge.cli state 1         # K6 慢闪（RUNNING）
```

**首个联调用例**（契约 §5 第 1 条，CRC 已实算核对）：

```powershell
python -m ego_bridge.cli send "AA 55 01 04 0C 22 38 05 91" --raw
```

期望板卡显示 `5 12.34.56`（星期五 12:34:56）。

> **`--raw` 是必须的**：不带它时 CLI 会走 Bridge 握手，而握手会发一条
> 用**本机时间**的 `TIME_SYNC`，把你刚设的值覆盖掉。

### 烧录固件

`.bit` 只进 SRAM，**断电即失**。掉电保持要写 Flash：

```powershell
$env:PATH = "D:\Vivado\Vivado\2017.1\bin;$env:PATH"
cd K:\Verilog\LessonProject\EGO-dsh-controller

# 烧进 QSPI Flash（掉电保持）
vivado.bat -mode batch -source scripts\program_flash.tcl

# 只烧 SRAM（改代码后快速验证，2 秒）
vivado.bat -mode batch -source scripts\program_sram.tcl
```

**烧 Flash 必须同时提供 `.mcs` 和 `.prm`**，否则擦写会静默失败。
烧完**长按 S5（PROG）**重载。

**若烧完不启动**：按住 S5 上电清除配置，再用匹配的位宽重烧：

```powershell
vivado.bat -mode batch -source scripts\program_flash.tcl -tclargs prj_out\m2_daysync.bit 1
```

---

## 硬件接线

**这是最容易卡住的地方，先看完再动手。**

| 接口 | 用途 | 说明 |
|---|---|---|
| **J22** | USB-JTAG | 供电 + Vivado 烧写。**读不到 Flash ID** |
| **J8** | **USB-UART** | CP2102，通信口（`COMx`） |
| J3 | 6-pin JTAG 排针 | 外接下载器 |
| J4 | USB-A | **只能接键鼠**（经 PIC24FJ128 转 PS/2） |
| J5 | 2×18 扩展 IO | 备用，可引出 UART |
| 三针跳线 | QSPI / 中针 / JTAG | **必须保持「中针 ↔ QSPI」** |

### 三个必须知道的坑

**1. 三针跳线不能乱动**

切到「中针 ↔ JTAG」而没有接外部下载器，整块板会枚举成
`Unknown USB Device (error 43)`，**连编程口都消失**。保持「中针 ↔ QSPI」。

**2. J8 需要 CP210x 驱动**

Windows 里第一次插上会显示 `错误码 28`。设备管理器 → 右键 → 更新驱动程序 →
自动搜索即可，之后出现 `Silicon Labs CP210x (COMx)`。

**3. 没有独立 DC 供电口**

只能靠 J22 或 J8 的 USB 供电。**换接口 = 断电 = SRAM 丢失**。
所以改代码时用 `program_sram.tcl`，正式使用前务必烧 Flash。

### 按键

| 按键 | 引脚 | 功能 |
|---|---|---|
| 中 | R15 | 确认当前选项 |
| 左 | V1 | 上一页 |
| 右 | R11 | 下一页 |
| **上** | **U4** | **选项 +1**（提问时） |
| **下** | **R17** | **选项 −1**（提问时） |

提问期间显示页**强制锁定**在 `ASH 题号-选项号`，选项范围 0–8 两端回绕。

### 灯

| 灯 | 引脚 | 含义 |
|---|---|---|
| K1 | K1 | 常亮（板子活着） |
| K6 | K6 | 灭=FREE / 慢闪=RUNNING / 常亮=WAITING |

---

## 架构

### 目录结构

```
EGO-dsh-controller/
├── README.md                    ← 你在这里
├── DELIVERY.md                  交付说明（一页看懂现状）
│
├── docs/                        设计与契约
│   ├── DESIGN.md                总体方案 · 架构 · 硬件事实 · 里程碑
│   └── SERIAL-PROTOCOL.md       串口契约 v2.1 · 帧格式 · CRC · 消息定义
│
├── LessonProject.xpr            Vivado 工程
├── LessonProject.srcs/
│   ├── constrs_1/new/const1.xdc 引脚约束（T4/N5 串口 · SPI_BUSWIDTH=4）
│   └── sources_1/new/           RTL
│       ├── smg_ip.v             顶层
│       ├── disp_scan8.v         8 位锁步扫描
│       ├── disp_seg.v           段码表 + 小数点
│       ├── disp_content.v       三页内容 + 时间计数（含星期跨日）
│       ├── key_debounce.v       消抖 + 单击/长按
│       ├── status_fsm.v         FREE/RUNNING/WAITING
│       ├── led_state_drv.v      双灯编码
│       ├── option_sel.v         选项高亮（上限动态）
│       ├── uart_tx.v            8N1 发送
│       ├── uart_rx.v            8N1 接收（中点采样）
│       ├── frame_parser.v       帧头 + LEN + CRC8
│       ├── frame_builder.v      组帧（增量 CRC）
│       ├── link_ctrl.v          协议层：解析/仲裁/心跳/NACK
│       └── tb_*.v               4 个自检测试台（182 用例）
│
├── host/                        上位机
│   ├── ego_bridge/              Python 桥接包
│   │   ├── protocol.py          帧编解码 + CRC8
│   │   ├── link.py              字节流帧同步状态机
│   │   ├── transport.py         端口发现（CP210x 优先，排除蓝牙）
│   │   ├── messages.py          消息 dataclass
│   │   ├── bridge.py            链路管理 · 心跳 · 重连 · 校时
│   │   ├── sim.py               假 FPGA（无板卡全链路）
│   │   ├── service.py           JSON Lines 后端（插件用）
│   │   └── cli.py               手动调试入口
│   ├── dsh-plugin/              DSH 插件（TypeScript）
│   │   ├── src/                 源码
│   │   ├── lib/                 构建产物（已提交）
│   │   └── test/                58 用例
│   ├── tests/                   pytest（244 用例）
│   ├── requirements.txt
│   └── README.md                上位机用法 · 故障排查
│
├── scripts/
│   ├── program_flash.tcl        烧 QSPI Flash（掉电保持）
│   └── program_sram.tcl         烧 SRAM（快速迭代）
│
└── prj_out/                     交付物
    ├── m2_daysync.bit           最新固件（SRAM）
    ├── m2_daysync.mcs           Flash 镜像（SPIx4）★ 需与 .prm 同选
    ├── m2_daysync.prm           Flash 参数文件
    ├── m2_daysync_timing.rpt    WNS +4.543 ns
    ├── m2_daysync_utilization.rpt
    └── m1_*.{bit,rpt}           M1 历史归档
```

### 分层职责

| 层 | 位置 | 为什么在这里 |
|---|---|---|
| **RTL** | `*.v` | 只做协议解析、显示、灯。**不解析文本**，载荷只传索引和计数 |
| **Python** | `ego_bridge/` | **独占串口**。只做协议翻译和链路健康，**不做 DSH 语义** |
| **插件** | `dsh-plugin/` | 只做事件翻译。spawn Python 子进程，不碰串口 |

**为什么 Python 守串口、插件 spawn 子进程**：`serialport` 是原生模块，
Windows + Node 24 需要编译工具链；而 Python 侧本来就该有个能独立跑的 CLI
用来手动验证。两层可以分别测试、分别重启。

### 关键设计决策

**1. 数码管字符顺序（最容易踩的坑）**

`chars` 是 48 位，**DK1 是最左边的位，占 `chars[5:0]`**。Verilog 拼接第一个元素
落在最高位，所以必须**先写 DK8、最后写 DK1**：

```verilog
chars = {dk8, dk7, dk6, dk5, dk4, dk3, dk2, dk1};
```

写反整块屏会**左右镜像**。这个 bug 已经出过一次，`tb_m1` 里有专门的回归用例。

**2. 错误处理的两个方向**

| 错误类型 | 处理 | 原因 |
|---|---|---|
| CRC 错 · LEN 越界 | **静默丢弃** | 传输意外。回 NACK 会引来更多错误帧 |
| state=3 · 字段越界 | **回 NACK** | 语义错误。静默丢弃会让对方以为发送成功 |

判据：**重发同一帧会不会有不同结果**——不会就丢弃，会就 NACK。

**3. 提问时 GUI 与板卡并行**

`next()` **一定要被调用**，否则 waterfall 被短路，GUI 的提问卡片根本不会渲染
（用户连题目都看不到）。两者用 `Promise.race` 竞争，谁先答谁赢。

---

## 文档

| 文档 | 内容 |
|---|---|
| [`DELIVERY.md`](DELIVERY.md) | **交付说明** —— 一页看懂现在能做什么、限制在哪 |
| [`docs/DESIGN.md`](docs/DESIGN.md) | **总体方案** —— 架构、显示/灯设计、硬件事实、里程碑 |
| [`docs/SERIAL-PROTOCOL.md`](docs/SERIAL-PROTOCOL.md) | **串口契约 v2.1** —— 帧格式、CRC、字节级消息定义 |
| [`host/README.md`](host/README.md) | **上位机用法** —— CLI、插件配置、故障排查 |

> **接手前先读 `DESIGN.md` §2**（数码管字符顺序）。

### 已知限制

| # | 限制 | 影响 |
|---|---|---|
| 1 | 板卡先答时 GUI 卡片不自动消失 | DSH lifecycle 限制，手动关一下即可。功能正确 |
| 2 | 连续两次 `cli send` 第二帧可能失效 | 仅调试时；`frame_parser` 无空闲超时。生产不受影响 |
| 3 | 选项 > 9 / 需打字 / 多选 | 自动交还 GUI，不在板卡上截断 |

---

## 开发

### 跑仿真

```powershell
$env:PATH = "D:\Vivado\Vivado\2017.1\bin;$env:PATH"
cd LessonProject.srcs\sources_1\new
xvlog.bat disp_seg.v disp_scan8.v key_debounce.v status_fsm.v led_state_drv.v `
          disp_content.v option_sel.v uart_tx.v uart_rx.v frame_parser.v `
          frame_builder.v link_ctrl.v smg_ip.v tb_m1.v tb_m2.v `
          tb_day_rollover.v tb_uart_rx.v
xelab.bat -debug typical tb_m2 -snapshot tb_m2
'run -all
exit' | Set-Content -Encoding ASCII run.tcl
xsim.bat tb_m2 -tclbatch run.tcl
```

| 测试台 | 期望 |
|---|---|
| `tb_m1` | `TOTAL_CASES = 68 / FAILED = 0` |
| `tb_m2` | `TOTAL_CASES = 73 / FAILED = 0` |
| `tb_day_rollover` | `TOTAL_CASES = 30 / FAILED = 0` |
| `tb_uart_rx` | `TOTAL_CASES = 11 / FAILED = 0` |

> `xsim.bat -R` 在 `$finish` 后常不退出，用 `-tclbatch` 那套写法。

### 重新构建固件

`const1.xdc` 里设了 `SPI_BUSWIDTH 4`，所以生成 `.mcs` 时必须用 `-interface SPIx4`：

```tcl
write_cfgmem -force -format MCS -size 4 -interface SPIx4 \
    -loadbit "up 0x00000000 m2_daysync.bit" m2_daysync.mcs
```

不匹配会报 `[Writecfgmem 68-20]`。

---

## 环境备注

### 源文件编码

`.v` / `.xdc` 全部**纯 ASCII**。Vivado 2017.1 编辑器不认 UTF-8，中文注释会被当
GBK 显示，一旦在里面保存会**不可逆损坏**。所以 RTL 注释一律英文。

`host/` 下的 `.py` / `.md` / `.ts` 用 **UTF-8 无 BOM**。

> ⚠️ 写 `.ps1` 时 PowerShell 5.1 的 `Set-Content -Encoding UTF8` 会加 BOM，
> 需要用 `[System.Text.UTF8Encoding]::new($false)` 显式指定。

### Vivado 批处理

`vivado -mode batch -source x.tcl *> log.txt` 这种重定向在某些情况下会**静默卡住**
（日志 0 字节）。**分步执行**（synth → opt → place → route → bitstream）更可靠，
每步几十秒。

### hw_server 端口

本机 TCP 3121 落在 Windows 保留段（Hyper-V/WSL 占用），已给
`hw_server.bat` 打补丁改用 6000（备份为 `.orig`）。Vivado 重装会覆盖。

---

## 许可证

[MIT](LICENSE)
