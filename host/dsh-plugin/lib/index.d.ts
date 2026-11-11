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
import type { Context } from '@deepseek-ai/cordis';
/** Plugin configuration, supplied from `cordis.patch.yml`. */
export interface Config {
    /** Serial port name, e.g. `COM7`. Empty means auto-discover. */
    port?: string;
    /** Line rate. The board is built for 115200. */
    baudRate?: number;
    /** Force the Python simulator instead of a real port. For demos and tests. */
    simulate?: boolean;
    /** Python executable name or path. */
    python?: string;
    /** Directory that contains the `ego_bridge` Python package. */
    backendDir?: string;
    /** Send `TIME_SYNC` every hour instead of only when the board needs it. */
    resyncHourly?: boolean;
    /** Session slot shown on the board, 0..15. Display only. */
    session?: number;
    /** Log every frame the board reports. Noisy; off by default. */
    verbose?: boolean;
}
export declare const name = "ego-bridge";
export declare const inject: never[];
export declare function apply(ctx: Context, rawConfig?: Config): void;
