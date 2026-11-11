# 交付说明 —— EGO-dsh-controller

> 最后更新：2026-11-11
> 板卡：EGO1（xc7a35tcsg324-1）· Vivado 2017.1 · 100 MHz
> 主机侧：Windows + Python 3.12 + pyserial · DSH host 插件

---

## 一、现在能做什么（已验证）

**整条链路已在真板上跑通，不是仿真。**

```
DSH / Python ──USB-UART──> CP2102 (J8) ──> FPGA ──> 数码管 / 指示灯
                              ↑
                    已实测：心跳、校时、状态、按键
```

| 功能 | 验证方式 | 结果 |
|---|---|---|
| 板卡 → PC 心跳 | 监听 COM11 | ✅ 每秒一帧 `STATUS_RPT`，0 CRC 错 |
| PC → 板卡校时 | 发 `TIME_SYNC` 12:34:56 星期五 | ✅ `flags` 位 0→2，CRC 与文档逐字节一致 |
| PC → 板卡状态 | 发 `STATE_SET` 0/1/2 | ✅ 状态与灯同步变化 |
| 板卡 → PC 按键 | 按左/右键 | ✅ 收到 `KEY_EVT(key_id=1/2)` |
| 掉电保持 | 烧 Flash + 长按 PROG | ✅ 已验证 |

**实测输出**（真板）：

```
frames=6  bytes_in=66  frames_ok=6  bad_crc=0  bad_len=0  stray_bytes=0
StatusReport(state=0, session=0, pending=0, flags=0, uptime_s=72)

发送 TIME_SYNC 12:34:56 day=5
  AA 55 01 04 0C 22 38 05 91          ← 与 SERIAL-PROTOCOL.md 示例完全一致
之后： StatusReport(..., flags=2, ...)  ← bit1 = 时间已有效

发送 STATE_SET RUNNING
之后： StatusReport(state=1, ...)

按键： KeyEvent(key_id=2, event=0, state=0)
       KeyEvent(key_id=1, event=0, state=0)
```

---

## 二、仓库结构

```
EGO-dsh-controller/
├── LessonProject.xpr                 Vivado 工程
├── LessonProject.srcs/
│   ├── constrs_1/new/const1.xdc      引脚约束（含 SPI_BUSWIDTH=4、T4/N5 串口）
│   └── sources_1/new/
│       ├── smg_ip.v                  顶层
│       ├── disp_scan8.v              8 位锁步扫描
│       ├── disp_seg.v                段码表 + 小数点
│       ├── disp_content.v            三页内容 + 时间计数（可 TIME_SYNC 装载）
│       ├── key_debounce.v            消抖 + 单击/长按
│       ├── status_fsm.v              FREE/RUNNING/WAITING
│       ├── led_state_drv.v           双灯编码
│       ├── option_sel.v              选项高亮（上限由 QUESTION_OPEN 给出）
│       ├── uart_tx.v / uart_rx.v     8N1 收发器
│       ├── frame_parser.v            帧头 + LEN + CRC8
│       ├── frame_builder.v           组帧发送（增量 CRC）
│       ├── link_ctrl.v               协议层：解析/仲裁/心跳/NACK
│       ├── tb_m1.v                   自检平台（68 用例）
│       └── tb_m2.v                   自检平台（73 用例）
├── prj_out/                          交付固件
│   ├── m2_spix4.bit                  最新固件（SRAM 烧写用）
│   ├── m2_spix4.mcs                  Flash 烧写用（SPIx4）
│   ├── m2_spix4.prm                  **烧 Flash 时必须勾选**
│   ├── m2_timing.rpt / m2_utilization.rpt
│   └── m1_*.{bit,rpt}                M1 历史归档
├── host/                             上位机
│   ├── ego_bridge/                   Python 桥接包（8 模块）
│   ├── tests/                        pytest（244 用例）
│   ├── dsh-plugin/                   DSH 插件（TypeScript）
│   └── README.md                     上位机用法
├── scripts/program_flash.tcl         Flash 烧写脚本
└── docs/
    ├── DESIGN.md                     总体方案 + 实机结论
    └── SERIAL-PROTOCOL.md            串口契约 v2.1
```

---

## 三、硬件接线（关键）

| 接口 | 用途 | 说明 |
|---|---|---|
| **J22** | USB-JTAG | 供电 + Vivado 烧写 |
| **J8** | **USB-UART** | CP2102，通信口（COM11） |
| J3 | 6-pin JTAG 排针 | 外接下载器 |
| J4 | USB-A | **只能接键鼠**（转 PS/2），不能传数据 |
| J5 | 2×18 扩展 IO | 备用 |

**硬性要求：**

1. **三针跳线必须保持「中针 ↔ QSPI」**。切到 JTAG 侧会导致 USB 枚举失败（error 43）。
2. **J8 需要 CP210x 驱动**。装好后出现 `Silicon Labs CP210x (COMx)`。
3. **没有独立 DC 供电口**，板子只能靠 J22/J8 的 USB 供电。

---

## 四、烧写固件

### 方式 A：烧 Flash（掉电保持，推荐）

**关键：必须勾选 PRM 文件**，否则烧不进去。

1. Vivado → Open Hardware Manager → Auto Connect
2. 右键器件 → `Add Configuration Memory Device`
3. 器件选 **`n25q32-3.3v-spi-x1_x2_x4`**（3.3V，不是 1.8V）
4. Configuration file 选 `prj_out/m2_spix4.mcs`
5. **✅ 勾选 PRM 文件** `prj_out/m2_spix4.prm`
6. 勾选 Erase / Program / Verify
7. 烧完后**长按 PROG（S5）** 重新加载

> **Interface 必须是 SPIx4**。bit 里设了 `SPI_BUSWIDTH=4`，用 SPIx1 会报
> `[Writecfgmem 68-20]`。

### 方式 B：烧 SRAM（快速迭代）

```powershell
$env:PATH = "D:\Vivado\Vivado\2017.1\bin;$env:PATH"
cd K:\Verilog\LessonProject\EGO-dsh-controller
vivado.bat -mode batch -source scripts\program_sram.tcl
```

断电即失。适合改代码后快速验证。

---

## 五、上位机使用

### 找到端口

```powershell
cd host
python -m ego_bridge.cli ports
```

自动按 **CP210x（VID=0x10C4 / PID=0xEA60）** 匹配，并排除蓝牙串口。

### 监控

```powershell
python -m ego_bridge.cli monitor
```

### 手动发命令

```powershell
python -m ego_bridge.cli state 1          # K6 慢闪（RUNNING）
python -m ego_bridge.cli sync-time        # 用本机时间校时
python -m ego_bridge.cli send "AA 55 01 04 0C 22 38 05 91"
```

### 测试

```powershell
cd host && python -m pytest                    # 244 用例
cd dsh-plugin && node --experimental-strip-types --test "test/*.test.ts"   # 20 用例
```

---

## 六、性能指标（M2 实测）

| 项目 | 数值 |
|---|---|
| 时序 | **WNS +4.059 ns，WHS +0.106 ns，0 失败路径** |
| Slice LUTs | **941 (4.52%)** |
| Slice Registers | **978 (2.35%)** |
| Bonded IOB | **34** |
| 仿真 | tb_m1 68/68、tb_m2 73/73 |
| Python 测试 | 244 全部通过 |
| bitstream | 2.2 MB |

---

## 七、已知限制

| # | 限制 | 影响 | 备注 |
|---|---|---|---|
| 1 | DSH 插件只接了 `agent/status` + 校时 | 提问/审批通路未接 | 串口层已支持 `QUESTION_OPEN`/`ANSWER`，可用 CLI 手动验证 |
| 2 | DSH 插件目前配置为 `simulate: true` | 默认跑模拟器 | 改成 `false` 接真板 |
| 3 | Vivado batch 综合曾报 `rt-undefined` | 偶发 | 重试即可，本轮已跑通 |
| 4 | 蓝牙模块扫不到 | 未使用 | 已有原理图，LED2 可判断状态 |

---

## 八、文档地图

| 想知道 | 看 |
|---|---|
| 总体设计、为什么这么选 | [`docs/DESIGN.md`](docs/DESIGN.md) |
| 帧格式、CRC、消息定义 | [`docs/SERIAL-PROTOCOL.md`](docs/SERIAL-PROTOCOL.md) |
| Python 层怎么用、怎么排查 | [`host/README.md`](host/README.md) |
| 快速上手 | 本文件 |
