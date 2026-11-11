/**
 * ego-bridge: EGO1 FPGA physical control panel for DeepSeek Harness.
 *
 * This round wires two things:
 *
 *   1. `agent/status` -> `STATE_SET`, so the dual-lamp pattern follows the
 *      agent. FREE = K6 off, RUNNING = K6 slow blink, WAITING = K6 steady.
 *   2. System time -> `TIME_SYNC` on every successful serial connection, and
 *      again whenever the board reports that it has no valid time (which is
 *      what a board reboot looks like from here).
 *
 * The question and approval seams (`user-questions/request`,
 * `approval/request`) are NOT attached in this round. The serial layer and
 * the protocol already carry `QUESTION_OPEN` / `QUESTION_CLOSE` / `ANSWER`,
 * so those can be verified with the CLI without the plugin; see
 * `docs/SERIAL-PROTOCOL.md` section 7 for the intended wiring.
 *
 * Everything here is fail-safe by construction:
 *
 *   - no serial port means no frames, never an exception: the board is
 *     optional hardware and an unplugged board must not affect the host,
 *   - the backend process is restarted with backoff and its absence never
 *     blocks a turn,
 *   - every event handler is wrapped so a throw cannot escape into the
 *     harness event bus.
 *
 * @module dsh-ego-bridge
 */
import { Backend } from "./backend.js";
import { BOARD_STATE_NAMES, deriveBoardState, sameBoardState, } from "./state.js";
import { boardRestarted, boardTimeFrom, timeIsValid } from "./time.js";
export const name = 'ego-bridge';
export const inject = [];
function resolve(raw) {
    return {
        port: raw.port,
        baudRate: raw.baudRate ?? 115200,
        simulate: raw.simulate ?? false,
        python: raw.python,
        backendDir: raw.backendDir,
        resyncHourly: raw.resyncHourly ?? false,
        session: raw.session ?? 0,
        verbose: raw.verbose ?? false,
    };
}
export function apply(ctx, rawConfig = {}) {
    const config = resolve(rawConfig);
    //------------------------------------------------------------------
    // Mutable host-side facts. The board state is derived from these, never
    // set directly, so a stale field cannot linger.
    //------------------------------------------------------------------
    let agentRunning = false;
    let pendingInteractions = 0;
    let lastSent;
    let lastUptime;
    let linkUp = false;
    let backendReady = false;
    let resyncTimer;
    const log = (text) => {
        // The harness owns stdout; a plugin must not print there.
        if (config.verbose)
            process.stderr.write(`[ego-bridge] ${text}\n`);
    };
    const backend = new Backend({
        python: config.python,
        cwd: config.backendDir,
        port: config.simulate ? 'sim' : config.port,
        baudRate: config.baudRate,
        resyncHourly: config.resyncHourly,
    }, {
        onEvent: (event) => {
            try {
                handleBackendEvent(event);
            }
            catch (error) {
                log(`event handler threw: ${String(error)}`);
            }
        },
        onExit: (code, signal) => {
            backendReady = false;
            linkUp = false;
            lastSent = undefined;
            lastUptime = undefined;
            log(`backend exited (code=${String(code)} signal=${String(signal)})`);
        },
        onStderr: (line) => {
            log(line);
        },
    });
    /** Push the current derived state to the board, if it changed. */
    const pushState = () => {
        const next = deriveBoardState(agentRunning, pendingInteractions, config.session);
        if (sameBoardState(lastSent, next))
            return;
        const ok = backend.send({
            cmd: 'set_state',
            state: next.state,
            session: next.session,
            pending: next.pending,
        });
        if (ok) {
            lastSent = next;
            log(`STATE_SET -> ${BOARD_STATE_NAMES[next.state] ?? next.state}`);
        }
    };
    /** Send the host clock to the board. */
    const pushTime = () => {
        const ok = backend.send({ cmd: 'sync_time', epoch_ms: Date.now() });
        if (ok)
            log('TIME_SYNC sent');
    };
    /** Redo the whole connection handshake. */
    const handshake = () => {
        pushTime();
        lastSent = undefined; // force a STATE_SET even if the value is the same
        pushState();
    };
    const handleBackendEvent = (event) => {
        switch (event.evt) {
            case 'ready': {
                backendReady = true;
                log(`backend ready (port=${String(event.port)} sim=${String(event.sim)})`);
                // `ready` means the process is up, not that the port is open. The
                // `link` event is what triggers the handshake.
                return;
            }
            case 'link': {
                const up = event.up === true;
                if (up === linkUp)
                    return;
                linkUp = up;
                log(`link ${up ? 'up' : 'down'} (${String(event.reason ?? '')})`);
                if (up) {
                    // The board was just opened: set the clock and the state.
                    handshake();
                }
                else {
                    lastUptime = undefined;
                }
                return;
            }
            case 'status': {
                const flags = typeof event.flags === 'number' ? event.flags : 0;
                const uptime = typeof event.uptime_s === 'number' ? event.uptime_s : 0;
                // A reset board answers with uptime 0 and "time invalid". Both are
                // signs that the handshake has to be redone, and they are checked
                // here rather than only on connect because the board can reboot
                // while the PC keeps its port open.
                const restarted = boardRestarted(lastUptime, uptime);
                lastUptime = uptime;
                if (restarted) {
                    log('board restarted (uptime went backwards)');
                    handshake();
                    return;
                }
                if (!timeIsValid(flags)) {
                    log('board reports no valid time; sending TIME_SYNC');
                    pushTime();
                }
                // The board echoes its own view; if it disagrees with ours, ours
                // wins and is pushed again. This heals a frame lost during a
                // reconnect without needing acknowledgements.
                const reported = typeof event.state === 'number' ? event.state : 0;
                const expected = deriveBoardState(agentRunning, pendingInteractions, config.session);
                if (reported !== expected.state) {
                    log(`board state ${reported} != expected ${expected.state}; resending`);
                    lastSent = undefined;
                    pushState();
                }
                return;
            }
            case 'key':
            case 'answer':
                // The question seam is not wired in this round. Answers are still
                // surfaced in the log so a manual CLI run and a plugin run behave
                // the same way.
                log(`board event: ${JSON.stringify(event)}`);
                return;
            case 'error': {
                log(`backend error: ${String(event.code)} ${String(event.message)}`);
                return;
            }
            case 'pong':
            default:
                return;
        }
    };
    //------------------------------------------------------------------
    // Host events
    //------------------------------------------------------------------
    // `agent/status` is scope-filtered and fires per agent. A subagent
    // reporting `running` should not light the panel, because the panel is
    // about the session the human is watching. Any running turn counts here:
    // the board has a single activity lamp and "something is working" is the
    // honest summary.
    ctx.on('agent/status', (payload) => {
        try {
            agentRunning = payload.status === 'running';
            pushState();
        }
        catch (error) {
            log(`agent/status handler threw: ${String(error)}`);
        }
    });
    //------------------------------------------------------------------
    // Lifecycle
    //------------------------------------------------------------------
    ctx.effect(() => {
        backend.start();
        if (config.resyncHourly) {
            // Defensive resync for a board that drifts. Off by default: the board
            // counts its own seconds and a one-second drift per hour is not worth
            // the traffic.
            resyncTimer = setInterval(() => {
                if (linkUp)
                    pushTime();
            }, 3_600_000);
            resyncTimer.unref();
        }
        return () => {
            if (resyncTimer !== undefined) {
                clearInterval(resyncTimer);
                resyncTimer = undefined;
            }
            backend.dispose();
        };
    }, 'ego-bridge: serial backend');
}
