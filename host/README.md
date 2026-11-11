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

### 5. 没有板卡：跑模拟器

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

### 本轮**没有**做的事

- **插件不挂 `user-questions/request`、`approval/request`**。
  串口层和协议层已支持 `QUESTION_OPEN` / `QUESTION_CLOSE` / `ANSWER`，
  但 DSH 事件到板卡的通路留到下一轮。现在提问只能用 CLI 手动验证。
- **`WAITING` 三态中插件只能产出 FREE / RUNNING**。
  `deriveBoardState()` 支持 WAITING（有 `pending` 就优先生效），
  但没有事件源去填 `pending`——那个源正是上面两个未接的 seam。
- 板卡 `DEBUG_MODE` 上板时要置 **0**，否则中键会与 PC 抢状态机。

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
