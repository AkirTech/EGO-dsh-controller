# EGO-dsh-controller

用 EGO1（Xilinx Artix-7 xc7a35tcsg324-1）开发板做 **DeepSeek Harness 的物理控制面板**：
8 位数码管显示时间与 Agent 状态，5 个按键做选择和确认，2 个指示灯直观映射三态。

---

## 当前状态

| 层 | 状态 |
|---|---|
| **FPGA（Verilog）M1** | ✅ 完成，已上板验证 |
| **FPGA（Verilog）M2 串口** | ✅ **完成：综合+实现通过，已烧板** |
| **串口通信（Python）** | ✅ 完成，pytest 244 用例全绿 |
| **DSH host 插件** | 🟡 只接了 `agent/status` + 校时；提问/审批下一轮 |
| **Python ↔ 真板通路** | ✅ **已打通（COM11，端到端实测通过）** |
| **掉电保持** | ✅ **已验证（烧 Flash + 长按 PROG）** |

> **2026-11-11 实机结论：整条链路已在真板上跑通**
>
> | 功能 | 结果 |
> |---|---|
> | 板卡 → PC 心跳 | ✅ 每秒一帧 `STATUS_RPT`，0 CRC 错 |
> | PC → 板卡校时 | ✅ `flags` 位 0→2，CRC 与文档逐字节一致 |
> | PC → 板卡状态 | ✅ 状态与 K6 灯同步变化 |
> | 板卡 → PC 按键 | ✅ `KEY_EVT(key_id=1/2)` |
> | 掉电保持 | ✅ 烧 Flash + 长按 PROG |
>
> **硬件要点：**
> - `J22`=USB-JTAG（供电+烧写）、`J8`=**USB-UART**（CP2102，通信口）
> - **三针跳线必须保持「中针 ↔ QSPI」**，切到 JTAG 侧会导致 USB 枚举失败
> - 无独立 DC 供电口，只能靠 USB 供电
> - **烧 Flash 时必须勾选 PRM 文件**（这是之前一直失败的真因）
---

## 文档

| 文档 | 内容 |
|---|---|
| [`docs/DESIGN.md`](docs/DESIGN.md) | **总体方案** —— 架构、显示/灯设计、里程碑、DSH 接入点 |
| [`docs/SERIAL-PROTOCOL.md`](docs/SERIAL-PROTOCOL.md) | **串口契约 v2.1** —— 帧格式、CRC、消息定义、Python 层结构 |
| [`host/README.md`](host/README.md) | **上位机用法** —— Python CLI、插件配置、故障排查 |

> **接手前先读 `DESIGN.md` 的 §2**（数码管字符顺序）。这个坑已经踩过一次，
> 写反会导致整块屏左右镜像。

---

## 目录结构

```
EGO-dsh-controller/
├── LessonProject.xpr                 Vivado 工程
├── LessonProject.srcs/
│   ├── constrs_1/new/const1.xdc      引脚约束（含 T4/N5 串口）
│   └── sources_1/new/
│       ├── smg_ip.v                  顶层
│       ├── disp_scan8.v              8 位锁步扫描引擎
│       ├── disp_seg.v                段码表 + 小数点
│       ├── disp_content.v            三页内容组织 + 时间计数（支持 TIME_SYNC 装载）
│       ├── key_debounce.v            按键消抖 + 单击/长按
│       ├── status_fsm.v              FREE/RUNNING/WAITING 三态机
│       ├── led_state_drv.v           双灯状态编码
│       ├── option_sel.v              选项高亮（上限由 QUESTION_OPEN 动态给出）
│       ├── uart_tx.v / uart_rx.v     8N1 收发器                        ← M2
│       ├── frame_parser.v            帧头同步 + LEN + CRC8              ← M2
│       ├── frame_builder.v           组帧发送（增量 CRC）                ← M2
│       ├── link_ctrl.v               协议层：消息解析/仲裁/心跳/NACK      ← M2
│       ├── tb_m1.v                   自检测试平台（68 用例）
│       ├── tb_m2.v                   自检测试平台（73 用例）              ← M2
│       └── ctrl_led.v, tb.v          历史文件，已排除出综合
├── host/                             上位机侧
│   ├── ego_bridge/                   Python 桥接包
│   ├── dsh-plugin/                   DSH 插件（TypeScript）
│   └── tests/                        pytest
├── prj_out/                          交付物（仅 M1 的 bit 与报告）
└── docs/
```

---

## 快速开始

### 烧录（让设计掉电不丢）

`.bit` 只写进 FPGA 的 SRAM，**断电即失**。要掉电保持必须写进板载
**N25Q32 QSPI Flash**（4 MB，3.3 V）。三步：

```powershell
# 1. 生成 bitstream（GUI: Generate Bitstream）
#    注意 const1.xdc 里已设 SPI_BUSWIDTH 4，改过就要重新生成

# 2. 烧进 Flash（脚本会自己生成 .mcs 并写入）
$env:PATH = "D:\Vivado\Vivado\2017.1\bin;$env:PATH"
vivado.bat -mode batch -source scripts\program_flash.tcl

# 只想生成镜像、不碰硬件的预演：
$env:EGO_FLASH_DRY_RUN = "1"
vivado.bat -mode batch -source scripts\program_flash.tcl
```

**若烧完不启动**：按住 **S5（PROG）** 再上电清除配置，然后用 `-tclargs` 换成
匹配的位宽重烧：

```powershell
vivado.bat -mode batch -source scripts\program_flash.tcl -tclargs prj_out\m1_buttons_leds.bit 1
```

详细原理与 `SPI_BUSWIDTH` 的取舍见 `docs/DESIGN.md` §10.5。

> `prj_out/` 里现有的 bit 是 **M1**（`DEBUG_MODE=1`，中键循环三态）。
> 想要 M2 的串口行为，需把 `smg_ip.v` 的 `DEBUG_MODE` 改成 **0** 再生成 bit。

### 运行仿真

```powershell
$env:PATH = "D:\Vivado\Vivado\2017.1\bin;$env:PATH"
cd LessonProject.srcs\sources_1\new
xvlog.bat disp_seg.v disp_scan8.v key_debounce.v status_fsm.v led_state_drv.v `
          disp_content.v option_sel.v uart_tx.v uart_rx.v frame_parser.v `
          frame_builder.v link_ctrl.v smg_ip.v tb_m1.v tb_m2.v
xelab.bat -debug typical tb_m2 -snapshot tb_m2
'run -all
exit' | Set-Content -Encoding ASCII run.tcl
xsim.bat tb_m2 -tclbatch run.tcl
```

| 测试平台 | 期望输出 |
|---|---|
| `tb_m1` | `TOTAL_CASES = 68 / FAILED = 0 / RESULT = PASS` |
| `tb_m2` | `TOTAL_CASES = 73 / FAILED = 0 / RESULT = PASS` |

> **注意**：`xsim.bat -R` 在 `$finish` 后常不退出，用 `-tclbatch` 那套写法。

### 上位机

```powershell
cd host
python -m ego_bridge.cli ports          # 找 CP210x（自动排除蓝牙串口）
python -m ego_bridge.cli monitor --sim  # 无板卡跑模拟器
python -m pytest                        # 244 用例
```

详见 [`host/README.md`](host/README.md)。

### 上板操作

| 按键 | 引脚 | 功能 |
|---|---|---|
| 中 | R15 | 确认当前选项 |
| 左 | V1 | 上一页 |
| 右 | R11 | 下一页 |
| **上** | **U4** | **选项 +1**（提问时） |
| **下** | **R17** | **选项 −1**（提问时） |

> 提问期间显示页强制锁定在第 3 页（`ASH 问题序号-选项号`）。
> 选项上限由 `QUESTION_OPEN` 的 `n_options` 决定（1–9），两端回绕。

| 灯 | 引脚 | 含义 |
|---|---|---|
| K1 | K1 | 常亮（板子活着） |
| K6 | K6 | 灭=FREE / 慢闪=RUNNING / 常亮=WAITING |

---

## 实现指标

| 项目 | 数值 | 出处 |
|---|---|---|
| 器件 | xc7a35tcsg324-1 | `.xpr` |
| 时钟 | 100 MHz (P17) | `const1.xdc` |
| LUT | 570 (2.74%) | **M1 数据**，M2 未重新综合 |
| FF | 337 (0.81%) | **M1 数据** |
| IO | 32 → **34**（M2 增加 uart_tx/uart_rx） | 计算值 |
| 时序 | WNS +4.523 ns | **M1 数据** |
| 仿真 M1 | 68 用例 0 失败 | 实跑 |
| 仿真 M2 | 73 用例 0 失败 | 实跑 |
| pytest | 244 用例全绿 | 实跑 |
| 插件测试 | 20 用例全绿 | 实跑 |

> **M2 的综合/时序数字缺失**：本机 Vivado 2017.1 批处理综合会报
> `invalid command name "rt-undefined"`（Vivado 内部 Tcl 缺陷，与设计无关）。
> M1 的数字是在 GUI 流程下得到的。补 M2 数字需要在 GUI 里重跑
> synthesis + implementation。

---

## 环境备注

### hw_server 端口

本机 TCP 3121 落在 Windows 保留段 `3023-3122`（Hyper-V/WSL 占用），导致
`hw_server` 启动即报 `Socket bind error`。已给
`D:\Vivado\Vivado\2017.1\bin\hw_server.bat` 打补丁改用 **6000** 端口
（原文件备份为 `hw_server.bat.orig`）。Vivado 重装会覆盖此补丁。

### Vivado 批处理综合

`vivado -mode batch -source synth.tcl` 在本机会在 `synth_design` 阶段失败：

```
invalid command name "rt-undefined"
    while executing "$rt::db resetHdlParse"
```

这是 Vivado 2017.1 实时的 realtime 监控脚本在批处理模式下的内部缺陷，
**不是设计错误**。仿真（`xvlog`/`xelab`/`xsim`）完全正常。

### 源文件编码

所有 `.v` 和 `.xdc` 均为**纯 ASCII**。Vivado 2017.1 的编辑器不认 UTF-8，
中文注释会被当 GBK 显示成乱码，一旦在里面按保存就会**不可逆损坏**。
因此本项目代码注释全部用英文。

> 例外：`ctrl_led.v` 和 `tb.v` 仍含中文（历史文件），它们在 `.xpr` 里是
> `AutoDisabled="1"`，不参与综合，不要打开编辑。
>
> `host/` 下的 `.py` / `.md` 用 UTF-8（无 BOM），与 Verilog 侧规则不同。

---

## 许可证

见 [LICENSE](LICENSE)。
