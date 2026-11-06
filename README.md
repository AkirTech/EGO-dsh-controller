# EGO-dsh-controller

用 EGO1（Xilinx Artix-7 xc7a35tcsg324-1）开发板做 **DeepSeek Harness 的物理控制面板**：
8 位数码管显示时间与 Agent 状态，3 个按键做选择和确认，2 个指示灯直观映射三态。

---

## 当前状态

| 层 | 状态 |
|---|---|
| **FPGA（Verilog）** | ✅ 完成，已上板验证 |
| 串口通信（Python） | ⬜ 下一步 |
| DSH host 插件 | ⬜ 待做 |

---

## 文档

| 文档 | 内容 |
|---|---|
| [`docs/DESIGN.md`](docs/DESIGN.md) | **总体方案** —— 架构、显示/灯设计、里程碑、DSH 接入点 |
| [`docs/SERIAL-PROTOCOL.md`](docs/SERIAL-PROTOCOL.md) | **串口契约** —— 帧格式、CRC、消息定义、Python 层结构 |

> **接手前先读 `DESIGN.md` 的 §2**（数码管字符顺序）。这个坑已经踩过一次，
> 写反会导致整块屏左右镜像。

---

## 目录结构

```
EGO-dsh-controller/
├── LessonProject.xpr                 Vivado 工程
├── LessonProject.srcs/
│   ├── constrs_1/new/const1.xdc      引脚约束
│   └── sources_1/new/
│       ├── smg_ip.v                  顶层
│       ├── disp_scan8.v              8 位锁步扫描引擎
│       ├── disp_seg.v                段码表 + 小数点
│       ├── disp_content.v            三页内容组织 + 时间计数
│       ├── key_debounce.v            按键消抖 + 单击/长按
│       ├── status_fsm.v              FREE/RUNNING/WAITING 三态机
│       ├── led_state_drv.v           双灯状态编码
│       ├── tb_m1.v                   自检测试平台（52 用例）
│       └── ctrl_led.v, tb.v          历史文件，已排除出综合
├── prj_out/                          交付物
│   ├── m1_buttons_leds.bit           可烧录 bit 文件
│   ├── m1_simulation_log.txt         仿真日志
│   ├── m1_timing.rpt                 时序报告
│   └── m1_utilization.rpt            资源报告
└── docs/
```

---

## 快速开始

### 烧录

1. 打开 `LessonProject.xpr`（Vivado 2017.1）
2. 或用现成 bit：`prj_out/m1_buttons_leds.bit`

### 运行仿真

```powershell
$env:PATH = "D:\Vivado\Vivado\2017.1\bin;$env:PATH"
cd LessonProject.srcs\sources_1\new
xvlog.bat disp_seg.v disp_scan8.v key_debounce.v status_fsm.v led_state_drv.v disp_content.v smg_ip.v tb_m1.v
xelab.bat -debug typical tb_m1 -snapshot tb_m1
'run -all
exit' | Set-Content -Encoding ASCII run.tcl
xsim.bat tb_m1 -tclbatch run.tcl
```

期望输出：`TOTAL_CASES = 52 / FAILED = 0 / RESULT = PASS`

> **注意**：`xsim.bat -R` 在 `$finish` 后常不退出，用 `-tclbatch` 那套写法。

### 上板操作

| 按键 | 引脚 | 功能 |
|---|---|---|
| 中 | R15 | 循环三态（M1 调试） / 确认选项（M2+） |
| 左 | V1 | 上一页 |
| 右 | R11 | 下一页 |

| 灯 | 引脚 | 含义 |
|---|---|---|
| K1 | — | 常亮（板子活着） |
| K6 | — | 灭=FREE / 慢闪=RUNNING / 常亮=WAITING |

---

## 实现指标

| 项目 | 数值 |
|---|---|
| 器件 | xc7a35tcsg324-1 |
| 时钟 | 100 MHz (P17) |
| LUT | 420 (2.02%) |
| FF | 258 (0.62%) |
| IO | 30 |
| 时序 | WNS +4.628 ns，0 失败路径 |
| 仿真 | 52 用例 0 失败 |

---

## 环境备注

### hw_server 端口

本机 TCP 3121 落在 Windows 保留段 `3023-3122`（Hyper-V/WSL 占用），导致
`hw_server` 启动即报 `Socket bind error`。已给
`D:\Vivado\Vivado\2017.1\bin\hw_server.bat` 打补丁改用 **6000** 端口
（原文件备份为 `hw_server.bat.orig`）。Vivado 重装会覆盖此补丁。

### 源文件编码

所有 `.v` 和 `.xdc` 均为**纯 ASCII**。Vivado 2017.1 的编辑器不认 UTF-8，
中文注释会被当 GBK 显示成乱码，一旦在里面按保存就会**不可逆损坏**。
因此本项目代码注释全部用英文。

> 例外：`ctrl_led.v` 和 `tb.v` 仍含中文（历史文件），它们在 `.xpr` 里是
> `AutoDisabled="1"`，不参与综合，不要打开编辑。

---

## 许可证

见 [LICENSE](LICENSE)。
