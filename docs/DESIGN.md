# EGO-dsh-controller 设计方案

> 版本 v4 · 2026-11-11
> 目标板卡：EGO1（xc7a35tcsg324-1）· Vivado 2017.1 · 100 MHz
> 配套主机：DeepSeek Harness（DSH）

---

## 0. 一句话概括

用 EGO1 板卡做 DSH 的**物理控制面板**：八个七段数码管显示会话状态与时间，
按键完成"选择/切换/回答"，两个指示灯映射 Agent 的三态；
中间由 Python 进程通过串口在 FPGA 与 DSH 之间做协议桥接，
DSH 插件再通过 stdin/stdout JSON 行驱动这个 Python 进程。

---

## 0.1 上板实测结论（2026-11-11 更新）

这一节是**实机探测结果**，不是推测。所有结论都有对应的 Vivado 寄存器读数或
Windows 设备枚举作依据。

### 板卡接口布局（用户在板上逐一确认）

| 编号 | 用途 | 说明 |
|---|---|---|
| **J22** | USB-JTAG | 插上即供电 + Vivado 烧写。**能烧 SRAM，但读不到 Flash ID** |
| **J8** | **USB-UART** | 独立接口，需要单独的线。**CP2102，VID_10C4/PID_EA60** |
| **J3** | 6-pin JTAG 排针 | 外接下载器用 |
| **J4** | USB-A | **只能接键鼠**（经 PIC24FJ128 转 PS/2），不能传数据 |
| **J5** | 2×18 扩展 IO | 可引出 UART，需自制接线 |
| 三针跳线 | QSPI / 中针 / JTAG | **必须保持「中针 ↔ QSPI」**；切到 JTAG 侧而未接外部下载器会导致 USB 枚举失败（error 43）|

> **没有独立 DC 供电口。** 板子只能靠 J22 或 J8 的 USB 供电，因此「换接口」等于断电，
> SRAM 内容随之丢失。这是「每次都要重烧」的真正原因。

### M2 综合与实现（本轮完成）

之前 batch 综合报 `invalid command name "rt-undefined"`，本轮**重试后消失**
（疑为当时残留状态导致）。完整流程已跑通：

| 阶段 | 结果 |
|---|---|
| `synth_design` | 0 errors, 0 critical warnings |
| `opt/place/route` | DRC 0 errors |
| **时序** | **WNS +4.059 ns，WHS +0.106 ns，全部 MET，0 失败路径** |
| **Slice LUTs** | **941 (4.52%)**  ← M1 是 570 |
| **Slice Registers** | **978 (2.35%)** ← M1 是 337 |
| **Bonded IOB** | **34** ← M1 是 32（+uart_tx/+uart_rx） |
| bitstream | `prj_out/m2_spix4.bit`（2.2 MB，含 `SPI_BUSWIDTH=4`） |

**已烧进板子 SRAM 并 `DONE=1`。**

### 关于那 141 个综合警告

绝大多数是 `8-3332` / `8-3331` / `8-6014`，内容为「未使用位被移除」：

- `msg_payload[63:48]` 等高位（协议最长 48 位）
- `len_r[7:4]`（LEN 最大 8）
- 被优化器内联改名的中间信号（`ms_div`、`uptime` 等）

**已用 `-flatten_hierarchy none` 复核**：`uptime` 有 40 个 cell、`elapsed` 有 37 个
cell，功能完整保留。**这些警告是 Vivado 的命名优化噪声，不是缺陷。**

### 已确认阻塞的

| 事项 | 证据 | 影响 |
|---|---|---|
| ~~CP2102 缺驱动~~ | ~~错误码 28~~ | ✅ **已解决**：装好驱动后 COM11 正常，端到端已通 |
| ~~Flash 读不到 ID~~ | ~~`Mfg ID: 0 ... 27-2251`~~ | ✅ **已解决**：真正原因是烧写时**未勾选 PRM 文件**。勾选后烧写成功，掉电保持已验证 |
| **蓝牙模块扫不到** | BLE 扫描 146 个设备，无 CC41-A、无 FFE0/FFE1 服务 | 需先确认模块是否上电/广播 |

### 文档更正（重要）

| 旧说法 | 实际 |
|---|---|
| "CP2102 芯片"（v1–v3）→ "没有 CP210x"（v4 早期） | **两个都不完全对**。板上**有**独立 CP2102（J8 口），但**不在 J22 这条线上**。之前所有探测都插着 J22 |
| "hw_server 已改 6000" | 补丁在，但 Vivado 实际仍连 `3121` 且成功。3121 当前可用 |

---

## 1. 当前进度

| 项目 | 状态 | 说明 |
|---|---|---|
| 8 位数码管驱动 | ✅ 完成 | `disp_scan8.v` + `disp_seg.v`；两组 4 位锁步扫描，一帧 4 步，占空比 25% |
| 显示内容 | ✅ 完成 | 三页：时间页 / 状态页 / 提问页；小数点作分隔符 |
| 按键驱动 | ✅ 完成 | `key_debounce.v`：两级同步 + 10 ms 消抖 + 800 ms 长按 |
| 状态机 | ✅ 完成 | `status_fsm.v`：FREE / RUNNING / WAITING |
| 指示灯 | ✅ 完成 | `led_state_drv.v`：双灯 K1/K6，灭·慢闪·常亮编码三态 |
| 时序收敛 | ✅ 完成 | WNS +4.523 ns，32 个 IO 全部绑定（M1 数据） |
| 实板验证 | ✅ 完成 | 用户已上板确认显示、按键、指示灯全部正常（M1） |
| Git 仓库 | ✅ 完成 | `K:\Verilog\LessonProject\EGO-dsh-controller`，已推到 GitHub |
| hw_server 端口 | 🟡 见 §0.1 | 3121 当前可用；补丁在但未生效 |
| **串口通信层（FPGA）** | ✅ **完成** | `uart_rx` / `uart_tx` / `frame_parser` / `frame_builder` / `link_ctrl`；仿真 73 用例 0 失败；**综合+实现通过**（WNS +4.059 ns，941 LUT），**已烧板** |
| **串口通信层（Python）** | ✅ **完成** | `host/ego_bridge/`；244 个 pytest 用例全绿 |
| **DSH host 插件** | 🟡 **部分完成** | 已挂 `agent/status` + 连接时校时；**提问/审批两个 seam 留到下一轮** |

### M2 新增交付物

| 文件 | 说明 |
|---|---|
| `LessonProject.srcs/sources_1/new/uart_tx.v` | 8N1 发送器，`ready/valid` 握手，无分频时钟 |
| `LessonProject.srcs/sources_1/new/uart_rx.v` | 8N1 接收器，三级同步 + 中点采样 + 毛刺拒绝 |
| `LessonProject.srcs/sources_1/new/frame_parser.v` | 帧头同步 + LEN 校验 + CRC8，错帧整帧丢弃 |
| `LessonProject.srcs/sources_1/new/frame_builder.v` | 组帧发送，增量 CRC，一级保持寄存器 |
| `LessonProject.srcs/sources_1/new/link_ctrl.v` | 协议层：消息解析、状态权威、发送仲裁、心跳、NACK |
| `LessonProject.srcs/sources_1/new/tb_m2.v` | 自检测试平台，**73 用例 0 失败** |
| `LessonProject.srcs/constrs_1/new/const1.xdc` | 新增 T4 / N5 串口引脚 |
| `host/ego_bridge/*.py` | Python 桥接包（8 个模块） |
| `host/dsh-plugin/` | DSH 插件（TypeScript） |
| `docs/SERIAL-PROTOCOL.md` | 契约 **v2.1** |

### M1 已交付物

| 文件 | 说明 |
|---|---|
| `disp_seg.v` / `disp_scan8.v` / `key_debounce.v` / `status_fsm.v` | M1 显示与按键基础模块 |
| `led_state_drv.v` / `option_sel.v` / `disp_content.v` | 灯、选项、内容组织 |
| `tb_m1.v` | 自检测试平台，68 用例 |
| `prj_out/m1_buttons_leds.bit` | M1 可烧录 bit 文件 |

## 2. 关键约定：数码管字符顺序（务必先读）

**这是本项目最容易踩的坑，已经踩过一次。**

`chars` 是 48 位，8 个字符各 6 位。**DK1 是最左边的位，它占 `chars[5:0]`；
DK8 是最右边的位，占 `chars[47:42]`。**

```
DK1  DK2  DK3  DK4  DK5  DK6  DK7  DK8      ← 人眼阅读顺序
 ↓    ↓    ↓    ↓    ↓    ↓    ↓    ↓
[5:0][11:6][17:12][23:18][29:24][35:30][41:36][47:42]   ← 位范围
```

原因：Verilog 拼接 `{a, b, c}` 里 **`a` 落在最高位**。所以写赋值时必须
**先写 DK8、最后写 DK1**：

```verilog
chars = {dk8, dk7, dk6, dk5, dk4, dk3, dk2, dk1};
```

**如果按阅读顺序（DK1 在前）写，整个显示会左右镜像。**
`disp_content.v` 里现在统一用命名信号 `dk1..dk8` 组装，位序只在一个地方出现，
就是为了让这个错误不可能再犯。

`dps`（小数点）同理：`dps[0]` 属于 DK1，`dps[7]` 属于 DK8。

---

## 3. 硬件事实（已核实，可直接用）

### 3.1 按键引脚

官方用户手册 5.1 节。**高有效**：默认低电平，按下输出高电平。

| 手册编号 | 原理图标号 | FPGA 球位 | 本项目用途 |
|---|---|---|---|
| S0 | PB0 | **R11** | **右** —— 切换显示页 |
| S1 | PB1 | **R17** | **下** —— 选项 −1 |
| S2 | PB2 | **R15** | **中** —— 确认 / M1 调试循环三态 |
| S3 | PB3 | **V1** | **左** —— 切换显示页 |
| S4 | PB4 | **U4** | **上** —— 选项 +1 |

> 五个键全部启用。用户最初指定"中=R15、左=V1、右=R11"，
> 后续补充"上=U4、下=R17"用于切换选项，与手册引脚完全吻合。

**必须消抖。** 机械按键抖动典型 5–10 ms，厂商指导书自己也提到"按一下 LED 跳 2 下"。
已实现 `key_debounce.v`：两级同步器 + 10 ms 计数器消抖 + 800 ms 长按识别。

### 3.2 指示灯

> **重要纠正：EGO1 基础板上没有 RGB 三色灯。**
> 手册 5.3 节列出的 16 个板载 LED（D0–D15）颜色栏**全部是 Green**。

"红绿黄三色灯"来自**交通灯实验子板**（指导书实验七原文：
"子板实现所有显示方面的功能，包括十进制倒数计数和红绿黄三色灯的轮流点亮"）。

指导书给的交通灯约束是：

```
out_LED3_NS[2] = K2    out_LED3_WE[2] = H4
out_LED3_NS[1] = J2    out_LED3_WE[1] = J4
out_LED3_NS[0] = J3    out_LED3_WE[0] = G3
```

回查手册 5.3 节，这 6 个球位就是**主板 D7/D6/D5 与 D4/D3/D2**（全是绿色 LED）。

**结论：三色灯的物理归属必须在板子上确认。** 三种可能：

| 情况 | 表现 | XDC 怎么写 |
|---|---|---|
| **(a)** 子板插在主板 LED 排上，引脚复用 | 插上后 D2–D7 位置变彩色 | 直接用 K2/J2/J3 + H4/J4/G3 |
| **(b)** 没有子板，只用板载绿 LED | 只有基础板 | 用 D13/D14/D15（L1/M1/K3）靠**位置**区分 |
| **(c)** 子板独立，接 J5 扩展口 | 子板带排线 | 从 J5 的 32 个脚里挑 3 个（如 B16/B17/A15） |

**设计上的保护**：RTL 端口固定叫 `led_state[2:0]`，不写死引脚。
三种情况**只需改 XDC 的 3 行，RTL 一行不动**。

### 3.3 串口

**桥接芯片型号：手册未给出。** 实机枚举时整块板在 USB 上只有一个节点
`VID_03FD&PID_0008`（Xilinx USB Cable，`Class_FF`，驱动 `WinDriver6`），
**没有 COMx**。详细的枚举证据与影响见
[`SERIAL-PROTOCOL.md` §1.1](./SERIAL-PROTOCOL.md)。

| 信号 | 原理图网络 | FPGA 球位 | 方向（按手册括注） |
|---|---|---|---|
| UART_TX | UART_RX (桥接芯片视角) | **T4** | FPGA → PC |
| UART_RX | UART_TX (桥接芯片视角) | **N5** | PC → FPGA |

> **注意方向**：手册括注"FPGA串口发送端/接收端"与网络名 `UART_RX`/`UART_TX`
> 字面相反。上面按括注取，**首次联调必须实测**，搞反则完全不通。

> **更正**：v3 之前写的"CP2102 芯片，USB-CDC，Windows 枚举为 COMx"是错的。
> 手册没有任何地方提到 CP2102，实际枚举里也没有 CP210x 设备。
> **当前状态：串口通路尚未打通**，这是本项目目前唯一的硬阻塞。

---

## 4. 核心问题：如何捕获"Agent 正在等用户回答"

这是整个方案的技术核心。**结论：可以精确捕获，用 `user-questions/request` 事件。**

### 4.1 为什么 `agent/status` 不够

DSH 的 Agent 状态只有两态：

```ts
// packages/core/agent/src/runtime-types.ts:136
export type AgentStatus = 'idle' | 'running'
```

`running` 覆盖了**整个 turn**，而"等待用户回答"发生在 turn 内部，
所以从 `agent/status` 看不出"它在等我"。

之前的调研也验证过：在 `packages/core`、`interaction`、`api` 全部源码里
检索 `'agent/awaiting`、`'agent/waiting`、`'approval/pending`、`'session/waiting`、
`awaitingApproval` —— **零命中**。Host 侧确实没有独立的 waiting 状态事件。

### 4.2 正确的捕获点

`ctx.userQuestions.ask()` 会派发一个 **waterfall 事件**，这是唯一的、精确的挂载点：

```ts
// packages/interaction/user-questions/src/types.ts:174
'user-questions/request'(
  this: Scoped<Agent>,
  request: AskUserQuestionRequestEvent,
  next: () => Promise<AskUserQuestionAnswer>,
): Promise<AskUserQuestionAnswer>
```

派发位置（`packages/interaction/user-questions/src/index.ts:346-352`）：

```ts
return await (agent === undefined
  ? this.ctx.waterfall('user-questions/request', request, noAnswerer)
  : this.ctx.waterfall(scopeTarget(agent, agent), 'user-questions/request',
      { ...request, agent }, noAnswerer))
```

**waterfall 的语义正是我们需要的**：

- 监听器**返回答案** → 认领该请求，Agent 继续
- 监听器调用 `next()` → 放弃，交给下一个 answerer

所以 host 插件可以挂在这个事件上，做到：

1. **捕获到提问的瞬间** → 点亮状态灯为"等待"，数码管显示提示
2. **等待用户在板子上按键** → 按键值构造 `AskUserQuestionAnswer` 返回
3. **返回即完成回答** → Agent 立刻继续，不需要用户在 GUI 上再点一次

**这是一条双赢路径**：既解决了状态检测，又解决了硬件回答。不需要轮询、不需要试探。

### 4.3 实现骨架

```ts
// host 插件：挂 user-questions/request
ctx.on('user-questions/request', async (request, next) => {
  // 1. 通知板卡进入 WAITING
  bridge.send({ type: 'QUESTION_OPEN', nQuestions: request.questions.length })

  // 2. 等硬件按键（带超时与取消）
  const decision = await bridge.waitForKey(request.signal)

  // 3. 用户没按（超时/取消）→ 让给 GUI，保持原有行为
  if (decision === undefined) return next()

  // 4. 用硬件答案直接认领
  return {
    answers: request.questions.map((q, i) => ({
      id: q.id,
      selected: decision.answers[i] ?? [],
    })),
  }
})
```

**关键设计：超时后 `return next()`**。这样即使板子没插、按键坏了，
GUI 依然能正常工作，**不会让 DSH 卡死**。这是 fail-safe 的必需设计。

### 4.4 两个必须注意的边界

**边界 1：`ask_user_question` 工具当前是 `legacy` 模式。**

```ts
// packages/interaction/tool-ask-user/src/index.ts:29-31
mode: z.union(['legacy', 'timed']).default('legacy'),
timeout: ... .default(120),
```

当前 web profile 里 `tool-ask-user` **没有配 `config`**，
所以走默认的 `legacy` 模式，即**无限期等待**。

对硬件方案来说这**反而是好事**：阻塞式等待，语义简单。
但意味着 `request.wait` 字段不存在，`request.signal` 是唯一的取消通道 —— 代码里必须处理。

**边界 2：`approval` 是另一条通道，不是 `user-questions`。**

权限审批走 `ctx.approval.request()` / `approval/request` 事件
（`packages/interaction/user-approval/src/index.ts`），与 `userQuestions` **是两个不同的 seam**。

所以"同意/拒绝 Agent 的操作"这类审批，需要**额外再挂一个监听器**：

```ts
ctx.on('approval/request', async (request, next) => {
  bridge.send({ type: 'APPROVAL_OPEN', toolName: request.toolName })
  const ok = await bridge.waitForApproval(request.signal)
  if (ok === undefined) return next()
  return ok ? 'allowed-once' : 'rejected'
})
```

**两个 seam 都要挂，否则会漏掉一半交互。** 这是本方案最容易踩的坑。

### 4.5 状态映射表

| DSH 内部状态 | 判定依据 | 灯 | 数码管 |
|---|---|---|---|
| **FREE** | `agent/status` = `idle` | 绿 | 显示时间 |
| **RUNNING** | `agent/status` = `running`，且无 pending 请求 | 黄 | 显示会话 + 跑马 |
| **WAITING** | `user-questions/request` 或 `approval/request` 已进入未返回 | 红 | 显示 `A` 闪烁 |

> 注意：`approval/request` 在 `never` 策略下**不会派发给 listener**
> （`user-approval/src/index.ts:301` 在派发前就 `return 'rejected'`），
> 所以 `never` 策略下不会亮"等待"。这是合理的（没在等人），
> 但调试时容易误判，需在日志里区分。

---

## 5. 系统架构

```
┌──────────────────────────────────────────────────────────┐
│  PC                                                       │
│                                                           │
│  ┌────────────────────────┐      ┌─────────────────────┐ │
│  │ DSH (Node/TS)          │      │ 串口                │ │
│  │                        │      │ COMx @ 115200 8N1   │ │
│  │  ctx.on('agent/status')│◄────►│                     │ │
│  │  ctx.on('user-quest...')│     │  帧: AA 55 | TYPE   │ │
│  │  ctx.on('approval/...') │     │      LEN | PAYLOAD  │ │
│  │                        │      │      ... | CRC8     │ │
│  │  host 插件 (ego-bridge)│      └──────────┬──────────┘ │
│  └────────────────────────┘                 │            │
└─────────────────────────────────────────────┼────────────┘
                                              │ USB
                              ┌───────────────▼────────────┐
                              │  EGO1 (xc7a35t)            │
                              │                            │
                              │  uart_rx ──► frame_parser  │
                              │                │           │
                              │                ▼           │
                              │          status_fsm        │
                              │        FREE/RUN/WAITING    │
                              │           │      │         │
                              │     ┌─────┘      └────┐    │
                              │     ▼                 ▼    │
                              │  disp_scan8      led_state │
                              │  (8位数码管)      (指示灯)  │
                              │     ▲                 ▲    │
                              │     └──── key_scan ──┘     │
                              │        (R15/V1/R11)        │
                              └────────────────────────────┘
```

### 5.1 模块划分（FPGA 侧）

**M1 已实现的模块：**

| 模块 | 职责 | 状态 |
|---|---|---|
| `smg_ip` | 顶层，例化全部子模块 | ✅ |
| `disp_scan8` | 8 位锁步动态扫描，两组各 4 位，逐位小数点 | ✅ |
| `disp_seg` | 字符码 → 8 位段码，DP 独立输入 | ✅ |
| `disp_content` | 三页内容组织 + HH:MM:SS 计时 | ✅ |
| `key_debounce` ×5 | 两级同步 + 10 ms 消抖 + 800 ms 长按 | ✅ |
| `status_fsm` | FREE / RUNNING / WAITING 三态，FREE=00 上电安全 | ✅ |
| `led_state_drv` | 双灯 K1/K6 状态编码 | ✅ |
| `option_sel` | 选项高亮（上/下增减、两端回绕）、确认锁存 | ✅ |

**M2 待实现的模块：**

| 模块 | 职责 |
|---|---|
| `uart_rx` / `uart_tx` | 8N1，115200 |
| `frame_parser` | 帧头同步 + 长度 + CRC8 + 分发 |
| `cmd_builder` | 组帧上报（KEY_EVT / ANSWER / STATUS_RPT） |

**架构要点**：全设计**只用 100 MHz 单时钟**，其余全部用单周期 `ce` 使能实现
（扫描、1 Hz 计时、波特率）。因此不存在真正的跨时钟域，不需要异步 FIFO 或握手，
只有异步按键和 UART 接收线需要两级同步器。

### 5.2 显示内容设计

8 位共三页，左右键切换：

**第 0 页 —— 时间页（默认）**
```
┌──┬──┬──┬──┬──┬──┬──┬──┐
│ 1│  │ 0│ 0.│ 0│ 0.│ 0│ 0│
└──┴──┴──┴──┴──┴──┴──┴──┘
 DK1 DK2 DK3 DK4 DK5 DK6 DK7 DK8
 星期 空  时十 时个 分十 分个 秒十 秒个
            ↑       ↑
         小数点当冒号用
```

读数形如 `1 00.00.00` = 星期 1，0 时 0 分 0 秒。

**为什么第一位是星期几而不是会话号？** 会话号挂在时间页尾端是个纯噪声数字，
扫一眼毫无信息量。星期几是时间页唯一有价值的日历信息，且值域 1–7 正好一个字符。
（M1 阶段固定为 1，M2 由 `TIME_SYNC` 帧装载。）

**为什么用小数点当分隔符？** 7 段没有冒号字符。若用两位表示 `:`，
`HH:MM:SS` 就要 10 位，超了。小数点（DP）是**免费**的——它是段码的 bit7，
不占额外位置，所以 `HH.MM.SS` 6 位就够。

> 注意：DP 点亮后**贴在数字右下角**，实际观感是 `00.` 而不是 `00:`。
> 这是 7 段硬件的限制，但足以区分时分秒。

**第 1 页 —— 状态页**
```
┌──┬──┬──┬──┬──┬──┬──┬──┐
│ 0│ S│ H│  │ F│  │  │  │
└──┴──┴──┴──┴──┴──┴──┴──┘
 DK1 DK2 DK3 DK4 DK5 DK6 DK7 DK8
「DSH」（0 代 D，H 代 K） 空  状态  空 空 空
```

DK1–DK3 = `0SH`，代表 **DSH**（deepseek harness）。7 段写不出 D 和 K，
沿用 `deepseek` 横幅的约定：D→0、K→H。

DK5 = 状态字母：`F`=FREE / `r`=RUNNING / `A`=WAITING

**第 2 页 —— 提问页（M2+ 才真正有用）**
```
┌──┬──┬──┬──┬──┬──┬──┬──┐
│ A│ S│ H│ 1│ -│ 3│  │  │
└──┴──┴──┴──┴──┴──┴──┴──┘
 "ASH" = ASK      ↑   └─ 当前高亮的选项号
                问题序号
```

- **DK1–DK3** = `ASH`（表里没有 K，用 H 顶）
- **DK4** = 当前是第几个问题
- **DK5** = 分隔符
- **DK6** = **当前高亮的选项号**，由上/下键增减

> **为什么第 6 位是"已选选项"而不是"问题总数"？**
> 因为提问时用户最需要知道的是"我现在选的是第几个"，而不是"一共几个"。
> 后者是静态的、看一眼就记住的；前者是**每次按键都要确认的**。
> 选项总数改由 Python 层保证不超过 9 个（见 `SERIAL-PROTOCOL.md`）。

**提问时的按键行为：**

| 键 | 引脚 | 动作 |
|---|---|---|
| **上** | **U4** | 选项 +1（到 8 后回绕到 0） |
| **下** | **R17** | 选项 −1（到 0 后回绕到 8） |
| 中 | R15 | 确认当前选项 |
| 左/右 | V1/R11 | 平时切显示页；提问时页面被锁定在第 2 页 |

> **提问期间页面强制锁定在第 2 页。** 否则用户可能正在看时间页，
> 按上下键却看不到任何变化，会以为按键坏了。
>
> **为什么"上"是 +1？** 菜单项从上往下排列，光标上移就是选到列表里更靠后的项
> ——这是列表导航的通用直觉。如果反了会很别扭。

> **选项上限 9（索引 0–8）**：第 6 位只能显示一个字符，超过 9 就没法表示。
> Python 层负责在超过 9 个选项时不上报、表现为 `---`，板卡不处理"10"。
> 这是刻意的分工：FPGA 只管一个数字，复杂的判断留给上位机。

> M1 阶段没有串口，`q_index` 是常量 1，所以默认显示 `ASH 1-0`（选项 0）。
> 按中键循环到 WAITING 态后即可用上/下键验证选项数字变化。

### 5.3 指示灯逻辑

**只用两个灯**，不是三个一 hot：

| 状态 | K1（D8）| K6（D12）|
|---|---|---|
| **FREE** | 常亮 | **灭** |
| **RUNNING** | 常亮 | **1 Hz 慢闪** |
| **WAITING** | 常亮 | **常亮** |

设计意图：**K1 是"板子活着"的电源灯**——只要通电就亮，不代表任何状态，
所以永远不闪。**K6 是"状态活动灯"**，承担全部语义：

- **灭** = 没事干
- **慢闪** = 正在干活
- **常亮** = 有事等你

**为什么 A（等回答）用常亮而不是快闪？** 因为"常亮"在余光里最容易被察觉，
而任何闪烁都容易被大脑归类为"正在忙，别管"。**需要人介入的状态应该最安静但最显眼。**

> 引脚核对：手册 5.3 节 `D8=K1`、`D12=K6`。
> 用户口述的"D9/D13"与手册的 D 编号对不上，但**引脚名 K1/K6 是明确给出的**，
> 已按引脚名实现。若面板丝印另有编号，只改 XDC 两行，RTL 不动。

---

## 6. 通信协议

> **完整契约见 [`SERIAL-PROTOCOL.md`](./SERIAL-PROTOCOL.md)**（帧格式、CRC 参考实现、
> 全部消息的字节级定义、Python 层建议结构、M2 验收清单）。
> 本节只保留概览，避免两处描述漂移。

### 6.1 帧结构

```
+--------+--------+--------+--------+----------+--------+--------+
| 0xAA   | 0x55   | TYPE   | LEN    | PAYLOAD  | ...    | CRC8   |
+--------+--------+--------+--------+----------+--------+--------+
  帧头1    帧头2    类型     载荷长   LEN 字节载荷            校验
       └──────────── CRC 覆盖 TYPE, LEN, PAYLOAD ──────────────┘
```

| 字段 | 说明 |
|---|---|
| 帧头 | 双字节 `AA 55`，用于重同步 |
| TYPE | bit7 = 方向（0=PC→FPGA，1=FPGA→PC）；bit6..0 = 消息号 |
| LEN | 载荷字节数，0–16 |
| CRC8 | CRC-8/ATM，多项式 0x07，初值 0x00（标准向量 `"123456789"` → `0xF4`） |
| 多字节数值 | **小端**（低位在前） |
| 错误处理 | CRC 错/长度越界 → 静默丢弃，回到搜帧头。不回 NACK，避免错误风暴 |

最长帧 = 4 + 16 + 1 = 21 字节，115200 下约 1.8 ms。

### 6.2 消息类型

| TYPE | 名称 | 方向 | LEN | 说明 |
|---|---|---|---|---|
| `0x00` | ACK | 双向 | 1 | 载荷 = 被确认的 TYPE |
| `0x01` | TIME_SYNC | PC→FPGA | 4 | 校时：hour, min, sec, **day(星期)** |
| `0x02` | STATE_SET | PC→FPGA | 3 | 状态置位：state, session, pending |
| `0x03` | QUESTION_OPEN | PC→FPGA | 3 | 提问开始：req_id, n_options, q_index |
| `0x04` | QUESTION_CLOSE | PC→FPGA | 1 | 提问结束：req_id |
| `0x7F` | NACK | 双向 | 1 | 拒绝，载荷 = 原 TYPE |
| `0x81` | KEY_EVT | FPGA→PC | 3 | 按键：key_id, event, state |
| `0x82` | ANSWER | FPGA→PC | 4 | 回答：req_id, q_index, option, ms |
| `0x83` | STATUS_RPT | FPGA→PC | 6 | 心跳/状态上报 |

字节级定义见 [`SERIAL-PROTOCOL.md`](./SERIAL-PROTOCOL.md) §3。

### 6.3 按键映射

| keyId | 物理键 | 单击 | 长按（>800 ms） |
|---|---|---|---|
| 0 | R15（中） | **确认当前选项** | 预留 |
| 1 | V1（左） | 切换显示页 | 预留 |
| 2 | R11（右） | 切换显示页 | 预留 |
| **3** | **R17（下）** | **选项 −1** | 预留 |
| **4** | **U4（上）** | **选项 +1** | 预留 |

> 上/下键只在 WAITING 态生效，且此时显示页被强制锁定在第 2 页。
> 这样用户按上/下时一定能看到选项数字变化。

> **本设计不提供"拒绝"动作**（见 §9 已确认决策）。拒绝通过"选中另一个选项"表达，
> 所以 FPGA 上报的是**原始按键事件**，不是"同意/拒绝"决策。
> 长按目前全部预留，未分配语义。

### 6.4 交互时序

```
PC (DSH)                              FPGA
   |                                     |
   |  AA 55 03 03 05 02 01 <crc>         |  QUESTION_OPEN: req=5, total=2, index=1
   |────────────────────────────────────>|  状态 → WAITING（K6 常亮）
   |                                     |  显示页 → "ASH 1-2"
   |                                     |
   |                                     |  用户按「中」键
   |  AA 55 81 03 00 00 02 <crc>         |  KEY_EVT: key=0(中), 单击, state=WAITING
   |<────────────────────────────────────|
   |                                     |
   |  AA 55 82 04 05 01 00 xx <crc>      |  ANSWER: req=5, index=1, option=0
   |<────────────────────────────────────|
   |                                     |
   |  AA 55 04 01 05 <crc>               |  QUESTION_CLOSE: req=5
   |────────────────────────────────────>|  状态 → RUNNING（K6 慢闪）
   |                                     |  显示页 → 时间页
```

> 注意指示灯：WAITING 是 **K6 常亮**，RUNNING 是 **K6 1 Hz 慢闪**（见 §5.3）。
> 早期文档里写的"红灯/黄灯"是三灯方案的历史描述，已废弃。

---

## 7. Host 侧实现

### 7.1 为什么必须是 host 插件

之前调研过的三条路线对比：

| 路线 | 能拿到 agent 状态 | 能拿到提问 | 能注入回答 | 对现有 GUI 生效 |
|---|---|---|---|---|
| MCP server | ❌ | ❌ | ❌ | — |
| SDK JSON-RPC | ✅ | ✅ | ✅ | ❌（另起 runtime，不接管当前会话） |
| **host 插件** | ✅ | ✅ | ✅ | ✅ |

**只有 host 插件满足"控制当前正在用的会话"这个需求。**

### 7.2 插件结构

```
~/.dsh/profiles/web/           (或做成独立 npm 包)
ego-bridge/
├── package.json
├── cordis.patch.yml
└── src/
    ├── index.ts        # 插件入口，挂事件
    ├── serial.ts       # 串口收发
    ├── protocol.ts     # 帧编解码 + CRC8
    └── state.ts        # 状态聚合
```

挂载方式（`cordis.patch.yml`）：

```yaml
- id: ego-bridge
  name: dsh-ego-bridge
  config:
    port: COM7          # 留空则自动搜索
    baudRate: 115200
    keyTimeoutMs: 30000 # 硬件无响应则交还 GUI
```

### 7.3 串口方案

**`pnpm-lock.yaml` 里没有 serialport**，需要新增依赖。三个选择：

| 方案 | 优点 | 缺点 |
|---|---|---|
| **Node `serialport`** | 原生、异步、事件驱动 | 原生模块，需编译 |
| PowerShell `System.IO.Ports` | 零依赖，本机已实测可用 | 要 spawn 子进程，跨平台差 |
| Python `pyserial` 子进程 | 生态成熟 | 多一层进程 |

**推荐 `serialport`**：吞吐极低（< 100 字节/秒），但事件驱动模型最贴合插件架构。
若原生模块编译失败，退回 PowerShell 方案（沙箱不限制设备访问，
`sandbox.md:11` "governs filesystem effects only"，串口不触发审批）。

### 7.4 关键设计决策

**决策 1：串口断开不能让 DSH 卡死。**
所有 `waitForKey` 必须有超时（建议 30 s），超时 `return next()` 交还 GUI。

**决策 2：状态推送用"变化即发 + 1 Hz 心跳"。**
只发变化不够——断开重连后板卡不知道当前状态。心跳保证最终一致。

**决策 3：FPGA 不解析文本。**
7 段数码管无法显示中文/长文本，协议里载荷只传**索引和计数**，
正文永远留在 PC 屏幕上。

---

## 8. 实施计划

### M1 —— 按键 + 指示灯（纯 FPGA，不碰串口）✅ 已完成

**范围**：`key_debounce`（5 路全部启用）+ `status_fsm` + `led_state_drv` +
`disp_content` + `disp_seg` + `option_sel` + 显示页切换

**板上验收**：
1. 上电 K1 常亮，数码管显示时间页 `1 00.00.00`
2. 左/右键切换 3 个显示页
3. 按键单独按一次只切换一次（快速连按不跳页，验证消抖）
4. **按「中」键循环三态**（DEBUG_MODE=1），观察 K6：
   - FREE：K6 灭
   - RUNNING：K6 以 1 Hz 慢闪
   - WAITING：K6 常亮
5. 状态页显示 `0SH F` / `0SH r` / `0SH A` 对应三态
6. 时间页**从左到右**依次是 星期、空、时、分、秒，中间用小数点分隔

**实测结果**：
- 仿真 **68 用例 0 失败**
- 综合 **0 错误 0 关键警告**
- 资源 **570 LUT (2.74%) / 337 FF (0.81%) / 32 IO**
- 时序 **WNS +4.523 ns，WHS +0.205 ns，0 失败路径**
- bit 文件：`prj_out/m1_buttons_leds.bit`

**测试覆盖**：
| 类别 | 用例数 | 内容 |
|---|---|---|
| 消抖 | 5 | 干净按下 1 脉冲、抖动串 1 脉冲、短毛刺被抑制、长按 1 次、短按不触发 |
| 状态机 | 5 | 复位到 FREE、三态循环、is_waiting/is_running 标志 |
| 灯 | 5 | K1 三态恒亮、K6 灭/慢闪/常亮、闪灯确实亮灭交替 |
| 段码 | 9 | 6 个字符码 + 未定义码全灭 + 3 个 DP 组合 |
| 显示 | 28 | **字序回归**、星期位、三页内容、时间递增、BCD 翻转、DP 位置 |
| **选项选择** | **16** | 上/下增减、两端回绕、确认锁存、脉冲宽度、非激活态复位 |

> **字序回归测试**（`tb_m1.v` 第 9 组）专门断言 `DK1 == chars[5:0]`。
> 这是防止左右镜像 bug 复发的护栏，任何改动若再把顺序写反会立刻失败。

**开发中修掉的三个真实缺陷**：

1. **POR 计数器未初始化** —— `X != 8'hFF` 结果是 X 而非 1，计数器卡死在 X、复位永不释放。

2. **测试台位宽漏写** —— `reg dc_state = 2'd0;` 少写 `[1:0]`，状态被截断成 1 位，
   WAITING(2) 变成 FREE(0)，导致 page1 一直显示 `f` 而非 `A`。

3. **字符拼接左右镜像（已上板确认）** —— `disp_content` 按阅读顺序
   `{hour, min, sec, ...}` 拼接，但拼接第一个元素落在最高位 = 最右位，
   于是整块屏左右翻转。上板现象是显示 `1(空)100000` 而不是 `00.00.01`。
   **修复方式**：改用命名信号 `dk1..dk8` 组装，位序只在一个地方出现
   （见本文 §2）。

**用户实板验证（2026-11-06）**：
- 显示、按键、指示灯全部正常，M1 验收通过
- 期间修掉：时间页左右镜像、小数点缺失
- 后续优化：时间页首位改星期、状态页改 `0SH`、指示灯改双灯模式

### M2 —— 串口打通（板卡可被串口助手驱动）

**范围**：`uart_rx`/`uart_tx`/`frame_parser`/`cmd_builder`

**板上验收**（用串口调试助手或 Python 脚本，不需要 DSH）：
1. 发 TIME_SYNC `12:34:56` 星期五 → 显示 `5 12.34.56`
2. 发 STATE_SET state=1 → K6 开始 1 Hz 慢闪
3. 发 STATE_SET state=2 → K6 变常亮
4. 发 STATE_SET state=0 → K6 灭
5. 发 QUESTION_OPEN → 显示 `ASH 1-3`
6. 按中键 → 收到 `KEY_EVT` + `ANSWER`
7. 故意改坏 1 个 CRC 字节 → 显示不变，板卡不死机
8. 不操作 5 秒 → 每秒收到一帧 `STATUS_RPT` 心跳
9. 拔插串口 → 板卡不锁死，重新握手后恢复

**关键**：这一步**必须用 LED 探针先确认 T4/N5 方向**，别等到联调才踩。

> **注意**：M2 落地时要把 `smg_ip.v` 的 `DEBUG_MODE` 参数置 0，
> 否则中键会与 PC 抢状态机控制权。

### M3 —— Host 插件 + 端到端

**范围**：`ego-bridge` 插件，挂 `user-questions/request` + `approval/request` + `agent/status`

**验收**：
1. 发起一个 `ask_user_question` → 板卡 **K6 常亮**、显示 `ASH 1/1`
2. 按「中」→ **DSH 收到答案继续执行**，板卡回 K6 慢闪
3. 30 s 不按 → 自动交还 GUI，板卡回 K6 慢闪，GUI 弹窗正常出现
4. 拔掉串口 → DSH 完全不受影响（fail-safe 验证）
5. Agent 空闲时 K6 灭，执行时 K6 慢闪，提问时 K6 常亮

### M4 —— 打磨

时间同步、会话切换、多问题翻页、亮度调节等。

---

## 9. 已确认的决策

| # | 决策 | 结论 |
|---|---|---|
| 1 | **部署方式** | **同一台电脑**。DSH 与板子插同一台机器，host 插件直接开串口，无需网络转发 |
| 2 | **回答方式** | **上下键选择建议答案，中键接受。不做"拒绝"** |
| 3 | **键位分配** | 中(R15)=确认，左(V1)/右(R11)=选择与切页 |
| 4 | **调试模式** | M1 期间中键循环三态，M2 起由 PC 主导 |

### 关于"不做拒绝"的说明

用户的理由很实在：**如果 Agent 给的建议答非所问，用户需要自己写答案，
那自然会用键鼠，不会继续用 FPGA 硬答。** 这个判断是对的，理由有三：

1. **7 段数码管显示不了问题的正文。** 用户看板卡只能看到"有问题等你"，
   真正的选项文字在屏幕上。既如此，读题和答题都在屏幕前完成更顺手。
2. **FPGA 的定位是"轻量确认"。** 它的价值在于**你不用切回窗口、不用点鼠标**，
   扫一眼灯就知道该不该按。一旦需要读长文本、打字，这个价值就没有了。
3. **去掉拒绝键后交互简化为：左/右选，中键定。** 三个键两种动作，
   没有长按、没有组合键，误操作概率最低。

**因此按键语义定为**：
- 中(R15) = 确认当前选项
- 上(U4) / 下(R17) = 切换高亮选项
- 左(V1) / 右(R11) = 切换显示页（提问时页面锁定第 2 页）

**注意一个后果**：既然不提供拒绝，那么"接受"就等价于"选中的那个选项"。
如果 Agent 的问题是是非题，左/右应该能选到"否"这个选项本身——
也就是说**拒绝通过"选另一个选项"来表达，而不是通过独立的拒绝动作**。
协议上体现为：FPGA 上报的是**按键事件 + 选项索引**，不是"同意/拒绝"。

## 10. 待确认问题

| # | 问题 | 影响 | 状态 |
|---|---|---|---|
| 1 | **T4/N5 哪个是 FPGA 的 TX** | 搞反则串口完全不通 | ⬜ 需要串口先能用 |
| 2 | 波特率最终定 115200 还是 9600 | 手册示例用 9600 | ⬜ 同上 |
| 3 | 小数点分隔的观感 | 硬件限制，已实板确认可接受 | ✅ 已关闭 |
| 4 | 指示灯 K1/K6 引脚 | 已实板确认正常 | ✅ 已关闭 |
| 5 | 交通灯子板是否真实存在 | 已改用板载 LED，不影响任何功能 | 低优先级 |
| 6 | **板子的 COM 口从哪来** | 拿不到就没有 Python↔FPGA 通路 | 🔴 **最高优先级，见 §10.6** |
| 7 | 是否需要掉电保持 | 影响是否必须烧 Flash | ⬜ 见 §10.6.4 |

---

## 10.5 上板结论与后续方案（2026-11-11）

### 10.5.1 烧 mcs 是不是正确做法？

**是，而且是唯一正确的做法。** 理由链：

| 环节 | 事实 |
|---|---|
| `.bit` 烧到哪里 | FPGA 内部配置 SRAM，**掉电即失**（7 系列的定义如此） |
| 板上有非易失存储器吗 | 有：N25Q32（32 Mbit / **4 MB**，3.3 V QSPI） |
| FPGA 会从它启动吗 | **会**：实测 `MODE_PIN_M[2:0] = 001` = Master SPI |
| 能直接烧 `.bit` 吗 | **不能**。Flash 需要带地址记录的配置流（`.mcs`/`.bin`） |
| 你的 bit 多大 | 2.2 MB，4 MB 装得下 |

所以链路是：`.bit` → `write_cfgmem` → `.mcs` → `program_hw_cfgmem` → 断电重启自动加载。

**已交付脚本**：[`scripts/program_flash.tcl`](../scripts/program_flash.tcl)，
支持 `-tclargs <bit> <buswidth>`，并带 `EGO_FLASH_DRY_RUN=1` 只生成镜像不碰硬件。

### 10.5.2 `SPI_BUSWIDTH` 到底要不要设 4？

**已经写进 `const1.xdc` 设成 4。** 依据：

- N25Q32 支持 x4 读（Vivado `spi.cfg` 里 `X4_READ = TRUE`）
- x1 加载 2.2 MB 约 1.7 s，x4 约 0.4 s

**代价**：改了 XDC 就**必须重新生成 bit**。旧 bit 的头部永远写的是 1，
拿它配 `SPIx4` 会直接报 `[Writecfgmem 68-20]`——这正是你遇到的"不匹配"。

**回退路径**：如果 x4 之后板子不启动，按住 **S5（PROG）** 再上电即可清除配置，
然后改回 1 重新生成。

### 10.5.3 USB-A 口能不能用？

**不能用于本项目。** 手册 §9 明确：J4 上的 USB-A 口是给**键盘鼠标**用的，
经 **PIC24FJ128 转成 PS/2**（`PS2_CLK=K5`、`PS2_DATA=L4`）。

| 事实 | 影响 |
|---|---|
| 它是 USB **Device** 口（转 PS/2），不是 Host | 不能接 U 盘/网卡/串口模块 |
| 手册写"不支持 USB 集线器，只能连接一个鼠标或键盘" | 连键鼠都有限制 |
| 协议终点是 PS/2，不是 USB | 想拿它传数据要自己写 PS/2 从机，且速率低（约 10–16 kHz 时钟） |

**结论：这条路要写的代码远多于它解决的问题，放弃。**

### 10.5.4 蓝牙模块怎么样？会不会简化工作？

**技术上可行，而且是目前唯一"板上自带、随时可用"的无线通路**，但有明确代价。

手册 §13 给出的事实：

| 项目 | 值 |
|---|---|
| 型号 | BLE-CC41-A（**BLE**，不是经典蓝牙 SPP） |
| FPGA 侧引脚 | `BT_RX = N2`（FPGA 发送）、`BT_TX = L3`（FPGA 接收） |
| 波特率 | 1200–230400，**缺省 9600** |
| 控制方式 | 支持 AT 命令 |

优点：

- **不依赖那个拿不到的 COM 口**，是绕开当前阻塞的最短路径
- FPGA 侧改动很小：`uart_tx`/`uart_rx` 是参数化的，换两个引脚、换个波特率即可
- PC 侧用 BLE 虚拟串口（Windows 原生 `BTHENUM` 就能生成 COM 口，本机已有 8 个）

代价（必须提前知道）：

1. **BLE 的吞吐很低**。BLE-CC41-A 这类模块走的是 BLE 透传，实测有效吞吐常在
   几 KB/s 量级，而且**延迟不稳定**。对"按一下键、亮一个灯"完全够用，但比
   115200 有线（约 11 KB/s）差，更比 x4 的启动加载慢。
2. **配对是额外故障点**。Windows 的 BLE 串口需要先配对、且每次重连可能换 COM 号。
3. **默认 9600 需要改**。要么发 AT 命令改模块波特率，要么把 FPGA 侧降到 9600。
4. **本机那 8 个 `BTHENUM` 串口是电脑自带蓝牙的**，不是这块板的。要确认模块
   能被搜到，得实测配对。

**建议：作为 Plan B 并行验证，不作为主线。** 具体见 §10.6.3。

### 10.5.5 有没有更省事的方案？

按"改动量 ÷ 收益"排序：

| 方案 | 改动 | 风险 | 评价 |
|---|---|---|---|
| **A. 修好现有 COM 口** | 装驱动 / 换 USB 线 / 换口 | 低 | **首选**，见 §10.6.1 |
| **B. 用 J5 扩展口 + 外部 USB-TTL** | 改 XDC 两行 + 插一个几块钱的模块 | 低 | 稳，但多一根线 |
| **C. 改用蓝牙** | 改 XDC 两行 + 改波特率 | 中（配对/吞吐） | 无线，可接受 |
| **D. USB-A 转 PS/2** | 写 PS/2 从机 | 高 | 放弃 |
| **E. 纯视觉（不用串口）** | 无 | 无 | 降级方案：板子只显示，不交互 |

**方案 E 值得单独说**：如果串口实在拿不到，项目仍然可以交付一个**只读面板**——
但 DSH 状态怎么送到板子？没有通路。所以 E 实际不成立，除非退回"手动按键切三态"
（就是现在的 M1 行为）。**这说明串口是本项目的硬依赖，必须解决。**

---

## 10.6 串口排查与备选方案

### 10.6.1 方案 A：先把现有口修好（先试这个，5 分钟）

按可能性从高到低：

| # | 检查 | 怎么做 | 判断 |
|---|---|---|---|
| 1 | **USB 线是数据线吗** | 换一根确定能传数据的线 | 很多线只有电源脚。换线后重新枚举 |
| 2 | **是不是插在了 J3（6-pin JTAG 排针）** | 看线插的是 Type-C 还是排针 | 若插排针则只有 JTAG，没有 UART。**必须用 Type-C** |
| 3 | **换一个 USB 口** | 直连笔记本，不走 Hub | 排除了 Hub 供电/枚举问题 |
| 4 | **装 FTDI 驱动** | 从 [ftdichip.com VCP 驱动](https://ftdichip.com/drivers/vcp-drivers/) 装官方驱动 | 若之前被 WinDriver 独占，装 VCP 后可能出现第二个 COM |
| 5 | **重扫设备** | 设备管理器 → 扫描硬件改动 | |

> **重要判断依据**：如果换线/换口后设备管理器里**多出一个 USB Serial Port / COMx**，
> 说明这条路通了。如果始终只有一个 `Xilinx USB Cable`，说明这颗芯片的
> 第二通道在当前固件/驱动组合下就是不给 Windows 用，**不要再耗时间，直接走 B 或 C**。

**为什么可能真的拿不到**：已实测 `CompatibleIds` 全为 `Class_FF`、
`BusReportedDeviceDesc = "XILINX"`。这是 Xilinx 固件把 FTDI 芯片重新枚举后的
形态，整颗芯片被 WinDriver 独占。这种情况下 Windows 侧**不会**出现第二个接口。
Xilinx 自家板卡（如 Digilent 系列）通常会把 UART 通道作为独立接口暴露，
但这块 EGO1 的固件配置是否如此，**只有实测才知道**。

### 10.6.2 方案 B：J5 扩展口 + 外部 USB-TTL（最稳）

**思路**：绕开板上那个拿不到的桥接，直接用扩展口引两路 IO 接一个 USB-TTL 模块。

| 项目 | 值 |
|---|---|
| 连接器 | J5（2×18，32 路双向 IO，每路有过流过压保护） |
| 可用引脚 | 例如 `B16`、`B17`、`A15`、`A16`、`A13`、`A14` 等（手册 §14） |
| 需要的外部件 | 任意 USB-TTL（CH340 / CP2102 / FT232 模块，几块钱） |
| RTL 改动 | **零**（`uart_tx`/`uart_rx` 是参数化的） |
| XDC 改动 | **两行**：把 `uart_tx`/`uart_rx` 指向 J5 的两个引脚 |

代价：多一个 USB 设备和一根杜邦线；需要确认模块电压是 3.3 V（**不要接 5 V**）。

**这是风险最低的兜底方案**，建议同时准备。

### 10.6.3 方案 C：蓝牙（BLE-CC41-A）

| 项目 | 值 |
|---|---|
| FPGA 引脚 | `BT_RX = N2`（FPGA→模块）、`BT_TX = L3`（模块→FPGA） |
| 波特率 | 模块缺省 **9600**，支持 AT 命令改 |
| PC 侧 | Windows 配对本机 BLE → 生成 COM 口（本机已有 8 个 `BTHENUM` 串口，是电脑自带蓝牙的） |

**必须先验证的三件事**：

1. 模块能被 Windows 搜到并配对（手册没说出厂是否已配对/是否需 AT 初始化）
2. 配对后是否稳定生成 COM 口，重连后 COM 号是否变化
3. 实际吞吐（决定它能不能支撑 115200 的设计；不行就降到 9600）

**FPGA 侧改动**：XDC 两行 + `BAUD` 参数从 115200 改 9600（或发 AT 命令把模块改到 115200）。

**风险**：BLE 透传的延迟不稳定，对"按键→灯"够用，但不适合高频心跳。
若采用，建议把 `STATUS_RPT` 心跳从 1 Hz 放宽到 5 s。

### 10.6.4 断电测试怎么做（回答"是不是每次都要重烧"）

现在 `BOOT_STATUS` 显示本次是**从 Flash 干净启动**的，所以"每次都丢"
这个前提本身需要复验：

1. 关掉 Vivado 的 Hardware Manager（避免它偷偷重配）
2. **彻底拔掉 Type-C**，等 5 秒
3. 插回，观察
4. 判断标准：
   - **数码管亮起并显示 M1 内容** → Flash 里有镜像，**不需要重烧**，
     之前"每次都要重烧"的印象来自更早的某次
   - **只有电源灯 D18 亮、数码管不亮** → Flash 是空的/坏的，
     需要按 §10.5.1 烧一次 mcs
5. 若已烧过 mcs 仍不启动 → `SPI_BUSWIDTH` 不匹配，按 §10.5.2 回退

---

## 10.7 需要你确定的事

| # | 事项 | 说明 |
|---|---|---|
| 1 | **断电测试** | 见 §10.6.4。这能直接验证"要不要重烧"这个前提 |
| 2 | **串口优先走哪条** | A（修现有口，先试）/ B（J5 + USB-TTL，最稳）/ C（蓝牙，无线）。我建议 A→B，C 作为并行验证 |
| 3 | **`DEBUG_MODE` 要不要改 0** | 改成 0 才是 M2 行为（PC 权威）。但改了就**没有中键循环三态了**，纯板测试会不方便 |
| 4 | **是否接受 x4** | 已写进 XDC。若不想承担重烧风险，我改回 1 |
| 5 | **M2 综合仍未跑通** | Vivado 批处理报 `invalid command name "rt-undefined"`。**要么在 GUI 里手动跑一次 synthesis+implementation**，要么接受"只有仿真证据、没有时序/资源数字" |
| 6 | **Flash 里要不要留一个"保底"镜像** | 若担心烧坏，可先用 `BITSTREAM.CONFIG.CONFIGFALLBACK` 做双镜像（golden + update）。当前是简单单镜像，够用但无兜底 |

---

## 10.8 指示灯引脚（已实板验证）

**手册 5.3 节的编号：**

| 手册 | 引脚 |
|---|---|
| D8 | **K1** |
| D9 | H6 |
| D12 | **K6** |
| D13 | L1 |

用户当初口述的是"D9/D13"，与手册的 D 编号对不上，但**引脚名 K1/K6 是明确给出的**。
实现按**引脚名**接线，已上板确认正常：

```tcl
set_property PACKAGE_PIN K1 [get_ports {led_state[0]}]   # 常亮
set_property PACKAGE_PIN K6 [get_ports {led_state[1]}]   # 状态灯
```

**这个不一致已解决**：实板验证 K1/K6 就是期望的两个灯，说明用户给的引脚名是对的，
D 编号只是口述时数错了。

### 小数点当冒号的局限（已接受）

DP 是**贴在数字右下角**的，所以时间页实际看起来是：

```
1  0 0. 0 0. 0 0
```

而不是

```
1 00:00:00
```

这是 7 段硬件的物理限制——DP 的位置固定在右下角，无法做成居中的冒号。

**已上板确认观感可接受**，且它同时满足"6 位显示时间"和"1 位星期"两个约束。

---

## 11. 参考资料

**仓库内文档：**

| 文档 | 内容 |
|---|---|
| [`DESIGN.md`](./DESIGN.md) | 本文 —— 总体方案、架构、里程碑 |
| [`SERIAL-PROTOCOL.md`](./SERIAL-PROTOCOL.md) | **串口契约** —— 帧格式、CRC、消息定义、Python 结构 |

**硬件原始资料（工作区根，未入仓库）：**

| 内容 | 位置 |
|---|---|
| EGO1 官方用户手册（引脚权威） | `K:\Verilog\_tmp_ego1_manual.txt` |
| 厂商实验指导书 | `K:\Verilog\_tmp_ego1.txt` |

**早期调研（工作区根，未入仓库）：**

| 内容 | 位置 |
|---|---|
| FPGA 架构详案 | `_research/fpga-design.md` |
| DSH 接入调研 | `_research/dsh-adapter.md` |
| 硬件核实 | `_research/ego1-hardware.md` |

DSH 关键源码位置：

| 内容 | 路径 |
|---|---|
| `AgentStatus` 定义（只有两态） | `packages/core/agent/src/runtime-types.ts:136` |
| 提问 waterfall 事件 | `packages/interaction/user-questions/src/types.ts:174` |
| 事件派发 | `packages/interaction/user-questions/src/index.ts:346` |
| 审批 seam | `packages/interaction/user-approval/src/index.ts` |
| `ask_user_question` 工具 | `packages/interaction/tool-ask-user/src/index.ts` |