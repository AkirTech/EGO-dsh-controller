# EGO-dsh-controller 设计方案

> 版本 v2 · 2026-11-06
> 目标板卡：EGO1（xc7a35tcsg324-1）· Vivado 2017.1 · 100 MHz
> 配套主机：DeepSeek Harness（DSH）

---

## 0. 一句话概括

用 EGO1 板卡做 DSH 的**物理控制面板**：八个七段数码管显示会话状态与时间，
三个按键完成"选择/切换/回答"，两个指示灯映射 Agent 的三态；
中间由一个本机 host 进程通过 USB 串口在 FPGA 与 DSH 之间做协议桥接。

---

## 1. 当前进度

| 项目 | 状态 | 说明 |
|---|---|---|
| 8 位数码管驱动 | ✅ 完成 | `disp_scan8.v` + `disp_seg.v`；两组 4 位锁步扫描，一帧 4 步，占空比 25% |
| 显示内容 | ✅ 完成 | 三页：时间页 / 状态页 / 提问页；小数点作分隔符 |
| 按键驱动 | ✅ 完成 | `key_debounce.v`：两级同步 + 10 ms 消抖 + 800 ms 长按 |
| 状态机 | ✅ 完成 | `status_fsm.v`：FREE / RUNNING / WAITING |
| 指示灯 | ✅ 完成 | `led_state_drv.v`：双灯 K1/K6，灭·慢闪·常亮编码三态 |
| 时序收敛 | ✅ 完成 | WNS +4.523 ns，32 个 IO 全部绑定 |
| 实板验证 | ✅ 完成 | 用户已上板确认显示、按键、指示灯全部正常 |
| Git 仓库 | ✅ 完成 | `K:\Verilog\LessonProject\EGO-dsh-controller`，已推到 GitHub |
| hw_server 端口 | ✅ 完成 | 3121 落在 Windows 保留段 3023-3122，已打补丁改用 6000 |
| **串口通信层（Python）** | ⬜ **下一步** | 契约已定义，见 `SERIAL-PROTOCOL.md` |
| DSH host 插件 | ⬜ 待做 | M3 |

### M1 已交付物

| 文件 | 说明 |
|---|---|
| `LessonProject.srcs/sources_1/new/disp_seg.v` | 段码表 + 小数点注入（字符码 → 8 位段码） |
| `LessonProject.srcs/sources_1/new/disp_scan8.v` | 8 位锁步扫描，支持逐位小数点 |
| `LessonProject.srcs/sources_1/new/key_debounce.v` | 两级同步 + 10 ms 消抖 + 单击/长按 |
| `LessonProject.srcs/sources_1/new/status_fsm.v` | FREE/RUNNING/WAITING 三态机 |
| `LessonProject.srcs/sources_1/new/led_state_drv.v` | 状态 → 双灯模式映射（K1 常亮 / K6 灭·慢闪·常亮） |
| `LessonProject.srcs/sources_1/new/option_sel.v` | 选项高亮（上/下增减、两端回绕）、确认锁存 |
| `LessonProject.srcs/sources_1/new/disp_content.v` | 三个显示页的内容组织 + 时间计数 |
| `LessonProject.srcs/sources_1/new/tb_m1.v` | 自检测试平台，45 用例 |
| `prj_out/m1_buttons_leds.bit` | 可直接烧录的 bit 文件 |
| `prj_out/m1_simulation_log.txt` | 仿真日志（45 用例 0 失败） |
| `prj_out/m1_timing.rpt` / `m1_utilization.rpt` | 时序与资源报告 |

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

CP2102 芯片，USB-CDC，Windows 枚举为 COMx。

| 信号 | 原理图网络 | FPGA 球位 | 方向（按手册括注） |
|---|---|---|---|
| UART_TX | UART_RX (CP2102 视角) | **T4** | FPGA → PC |
| UART_RX | UART_TX (CP2102 视角) | **N5** | PC → FPGA |

> **注意方向**：手册括注"FPGA串口发送端/接收端"与网络名 `UART_RX`/`UART_TX`
> 字面相反。上面按括注取，**首次联调必须实测**，搞反则完全不通。

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
| 1 | **T4/N5 哪个是 FPGA 的 TX** | 搞反则串口完全不通 | ⬜ **M2 第一步就要测** |
| 2 | 波特率最终定 115200 还是 9600 | 手册示例用 9600 | ⬜ M2 决定 |
| 3 | 小数点分隔的观感 | 硬件限制，已实板确认可接受 | ✅ 已关闭 |
| 4 | 指示灯 K1/K6 引脚 | 已实板确认正常 | ✅ 已关闭 |
| 5 | 交通灯子板是否真实存在 | 已改用板载 LED，不影响任何功能 | 低优先级 |

### 指示灯引脚（已实板验证）

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