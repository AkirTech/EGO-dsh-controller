# host/ —— Python 串口桥接 + DSH 插件

上位机侧的两个交付物：**`ego_bridge`**（Python，独占串口）和
**`dsh-plugin`**（TypeScript，挂 DSH 事件）。

> 串口契约见 [`../docs/SERIAL-PROTOCOL.md`](../docs/SERIAL-PROTOCOL.md)。
> 本文件只讲**怎么跑**和**已知边界**，不重复协议定义。

---

## 目录

```
host/
├── ego_bridge/          Python 包：独占串口，翻译协议
│   ├── protocol.py      帧编解码 + CRC8（自检锚点 0xF4）
│   ├── link.py          字节流帧同步状态机
│   ├── transport.py     端口发现（CP210x VID/PID 优先，排除蓝牙）
│   ├── messages.py      各消息 dataclass + pack/unpack
│   ├── bridge.py        链路管理、心跳、重连、校时
│   ├── sim.py           假 FPGA：无板卡也能跑通全链路
│   ├── service.py       stdin/stdout JSON Lines 服务（插件后端）
│   └── cli.py           手动调试入口
├── dsh-plugin/          DSH host 插件
│   ├── src/backend.ts   管理 Python 子进程
│   ├── src/state.ts     三态映射（纯函数）
│   ├── src/time.ts      星期/校时（纯函数）
│   ├── src/index.ts     挂 agent/status，装配事件
│   ├── lib/             构建产物（已提交，loader 直接 import）
│   └── test/            node:test
└── tests/               pytest（244 个用例）
```

**为什么 Python 守串口、插件 spawn 子进程**：
`serialport` 是原生模块，Windows + Node 24 需要编译工具链，装不上就全盘阻塞；
而 Python 侧本来就该有个能独立跑的 CLI 用来手动验证。插件只做事件翻译，
两层可以分别测试和分别重启。

---

## 快速开始

**前提：J8（USB-UART）已插好，CP210x 驱动已装，出现 `COMx`。**

### 1. 找端口

```powershell
cd host
python -m ego_bridge.cli ports
```

只列 CP210x（VID 0x10C4 / PID 0xEA60），**蓝牙虚拟串口会被自动排除**。
本机有 8 个 `BTHENUM` 串口，不排除的话会选错。

### 2. 监控板卡

```powershell
python -m ego_bridge.cli monitor
```

应该每秒看到一帧 `StatusReport`。**如果一帧都没有，先看下面的排查表。**

### 3. 发命令

```powershell
python -m ego_bridge.cli state 1        # RUNNING：K6 慢闪
python -m ego_bridge.cli state 2        # WAITING：K6 常亮
python -m ego_bridge.cli state 0        # FREE：K6 灭
python -m ego_bridge.cli sync-time      # 用本机时间校时，数码管应显示当前时间
```

### 4. 验收清单第 1 条（契约 §5）

```powershell
python -m ego_bridge.cli send "AA 55 01 04 0C 22 38 05 91"
```

期望：板卡显示 `5 12.34.56`（星期五 12:34:56）。
这一帧的 CRC 与文档示例逐字节一致，**已实测通过**。

### 5. 提问通路（Q&A）

插件已挂 `user-questions/request` 与 `approval/request`。当 Agent 调用
`ask_user_question` 时：

1. **GUI 照常弹出提问卡片**（能看到题目和选项文字）
2. **板卡同时**显示 `ASH 题号-选项号`
3. 上/下键切换高亮选项，**中键确认**

**谁先答谁赢**：在 GUI 点选则板卡释放；在板卡按键则 Agent 立刻收到答案。

> 板卡先答时 GUI 卡片不会自动消失（DSH 的 lifecycle 限制），
> 手动关一下即可。原因见下方「已知边界」。

手工验证串口层的 question 通路（不经过插件）：

```powershell
python -m ego_bridge.cli question 5 3 1
# 板卡显示 "ASH 1-0"，操作按键后 CLI 打印 ANSWER
```

### 6. 没有板卡：跑模拟器

```powershell
python -m ego_bridge.cli monitor --sim
```

---

## 实测结果（真板，2026-11-11）

```
frames=6  bytes_in=66  frames_ok=6  bad_crc=0  bad_len=0  stray_bytes=0
StatusReport(state=0, session=0, pending=0, flags=0, uptime_s=72)

→ 发 TIME_SYNC 12:34:56 day=5
  AA 55 01 04 0C 22 38 05 91
→ StatusReport(..., flags=2, ...)        time_valid 置位

→ 发 STATE_SET RUNNING
→ StatusReport(state=1, ...)

→ 按键
  KeyEvent(key_id=2, event=0, state=0)
  KeyEvent(key_id=1, event=0, state=0)
```

**零 CRC 错、零丢帧。**

---

## 测试

```powershell
# Python：244 个用例
cd host
python -m pytest

# 插件：20 个用例（11 纯函数 + 5 端到端 + 4 生命周期）
cd host\dsh-plugin
node --experimental-strip-types --test "test/*.test.ts"
```

端到端那 5 个会真的 spawn `python -m ego_bridge.service --sim`；
机器上没有 Python 或 `ego_bridge` 时自动 skip。

---

## 已知边界（重要）

### 模拟器不是真板

| 差异 | 影响 |
|---|---|
| 没有 UART 位时序，字节整体交付 | 半帧截断由 `link.py` 的单测覆盖，模拟器不产生这种时序 |
| `uptime_s` 走墙钟秒 | 短会话里一直是 0，「重启识别」测不出来 |
| 不回 `ACK` | 与真板一致（v2 下 FPGA 不主动发 ACK） |

### 提问/审批：GUI 与板卡并行（**已知限制**）

两个 seam 都已挂上。行为是 **`next()` 一定会被调用**，所以 GUI 照常渲染提问卡片，
板卡同时显示 `ASH 题号-选项号`，**谁先答谁赢**：

| 场景 | 结果 |
|---|---|
| 在 GUI 点选 | GUI 赢，板卡释放（`sessions.abandon`） |
| 在板卡按键 | 板卡赢，Agent 立刻收到答案 |
| 板卡不出答案 / 链路断 | 全部交还 GUI |

> **为什么 `next()` 必须调用**：waterfall 里不调用 `next()` 直接返回 = **短路**，
> GUI 的 `answerQuestion` 根本不会执行，卡片也不会 `publish()`。
> 早期版本就是短路写的，现象是「GUI 完全不显示提问，只能翻详情看」。
> `test/race.test.ts` 第一条用例专门锁死这个回归。

**已知限制：板卡先答时，GUI 卡片不会自动消失。**

原因是 DSH 的 GUI 卡片只在 `waterfall.result` 结算时销毁，而结算的触发源只有四个：

| 触发源 | 板卡答完后是否发生 |
|---|---|
| GUI 用户点选 → `channel.resolve()` | ❌ 用户没点 |
| `channel.delegate()` | ❌ 无此路径 |
| `channel.reject()` | ❌ 不适用 |
| `request.signal` abort | ❌ `exec.signal` 不随工具结束而 abort |

插件**无权 abort 那个 signal**，DSH 也没有提供从外部关闭卡片的 API
（`ctx.userQuestions.answer()` 只对 **timed 模式**的 `continued` 问题有效，
`ask_user_question` 默认是 **legacy 模式**，用不上）。

**功能本身是正确的**：按下后答案确实传给了 Agent，只是 UI 没刷新。
手动点一下卡片的关闭按钮即可。

要彻底消除这个现象，只能把 `tool-ask-user` 改成 `mode: timed`（有 `callId`
就能用 `answer()` 主动关闭），但那会引入超时语义，与 legacy 的「无限期等待」不同。

### 连续手动发帧的已知问题（不影响生产）

**现象**：用 `cli send` 连续发两次原始帧，第二次可能不生效。

**原因**：`frame_parser` 没有空闲超时，只能靠收满字节回到 IDLE。两次 `send`
之间端口会被关闭重开，`reset_input_buffer` 可能切掉半个字符，留下一个孤立字节
让 parser 停在 `S_BODY`/`S_CRC` 等待后续字节；下一帧的 `AA 55` 被当成载荷吃掉，
于是永远等不到。

**恢复**：拔插 USB，或长按 PROG（S5）。

**为什么不修**：生产路径是插件持续持有端口、链路不断，parser 不会被切断；
只有手动调试时才会触发。加超时计数器需要改 RTL、重新综合、重新验证，
收益只是调试便利。

**调试建议**：需要连续发多帧时，每帧之间重新插拔，或改用 `monitor` 观察心跳
确认链路状态。

### 板卡行为的其他限制

- **`WAITING` 目前只能来自 STATE_SET**。插件已经会透传，但还没有事件源去填
  `pending`（那需要另接 seam）。
- **板卡 `DEBUG_MODE` 必须是 0**（本项目已设为默认），否则中键会与 PC 抢状态机。
- **选项上限 9**：超过就直接交还 GUI，不截断（避免一个决策分散在两处）。
- **多选问题、无选项问题**：一律交还 GUI，一个数字表达不了。

### 星期约定

**周一 = 1 … 周日 = 7**。`time.ts` 里用 `getDay()` 换算，
`0`（周日）映射成 `7`。这是全项目唯一的换算点。

---

## 装载到 DSH profile

写入 `~/.dsh/profiles/web/cordis.patch.yml`：

```yaml
- insert:
    - id: ego-bridge
      name: 'K:/Verilog/LessonProject/EGO-dsh-controller/host/dsh-plugin/lib/index.js'
      config:
        simulate: true        # 改 false 接真板
        baudRate: 115200
        backendDir: 'K:/Verilog/LessonProject/EGO-dsh-controller/host'
        session: 0
        verbose: true         # 看日志；稳定后建议关掉
```

配置项：

| 键 | 默认 | 说明 |
|---|---|---|
| `port` | 自动发现 | 如 `COM7`；留空则按 VID/PID 搜 |
| `baudRate` | 115200 | |
| `simulate` | false | true = 用模拟器，无需板卡 |
| `backendDir` | 插件目录的上一级 | 必须含 `ego_bridge/` 包 |
| `python` | `python` | Python 可执行文件 |
| `resyncHourly` | false | 每小时补发一次校时 |
| `verbose` | false | 打印每帧事件到 stderr |

**改完要重启 `dsh web` 才生效**（loader 只在启动时读 patch 文件）。

### 改完 src/ 要重新构建

```powershell
cd host\dsh-plugin
npx tsc -p tsconfig.json
```

`lib/` 是提交进仓库的（loader 直接 import，没有构建步骤）。
`src/` 是唯一可信来源，**改了 src 不重建 lib，DSH 跑的还是旧代码**。

---

## 故障排查

| 现象 | 先查 |
|---|---|
| 板卡完全没反应 | **T4/N5 方向反了** → 交换 `const1.xdc` 里 `uart_tx`/`uart_rx` 两行，RTL 不动 |
| `ports` 看不到 CP210x | 换 USB 线（有些线只供电）、装 CP210x 驱动 |
| 灯不跟 DSH 变 | `verbose: true` 看有没有 `STATE_SET ->`；没有就是事件没到插件 |
| 时间不动 | 看 `link up` 是否出现；`TIME_SYNC` 只在链路 up 时发 |
| 板卡一直显示 ASK 页 | 上一条 `QUESTION_CLOSE` 丢了 → 发一次 `state 0` 或重插串口 |
| 插件报 `spawn failed` | `python` 不在 PATH；或 `backendDir` 写错 |
