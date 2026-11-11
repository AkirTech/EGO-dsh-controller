/**
 * Backend process manager: owns the Python bridge child process and speaks
 * its JSON Lines protocol.
 *
 * The plugin never touches the serial port itself. Everything goes through
 * `python -m ego_bridge.service`, which means:
 *   - no native module to compile (the `serialport` package needs a
 *     toolchain on Windows and is a real risk with Node 24),
 *   - the same code path the CLI uses for manual debugging,
 *   - the bridge can be run and watched by hand when something is wrong.
 *
 * @module dsh-ego-bridge/backend
 */
/** A message the Python side sends up. */
export interface BridgeEvent {
    readonly evt: string;
    readonly [key: string]: unknown;
}
/** Callbacks the plugin registers. */
export interface BackendHandlers {
    onEvent: (event: BridgeEvent) => void;
    onExit: (code: number | null, signal: NodeJS.Signals | null) => void;
    onStderr: (line: string) => void;
}
/** How the backend is launched. */
export interface BackendOptions {
    /** Python executable. Defaults to `python`. */
    python?: string | undefined;
    /** Directory containing the `ego_bridge` package. Defaults to `../` of the plugin. */
    cwd?: string | undefined;
    /** Serial port, `"sim"` for the simulator, or omitted for auto-discovery. */
    port?: string | undefined;
    /** Line rate. */
    baudRate?: number | undefined;
    /** Send TIME_SYNC every hour instead of only once per connection. */
    resyncHourly?: boolean | undefined;
    /** Extra arguments appended to the command line. */
    extraArgs?: readonly string[] | undefined;
}
/**
 * Manages one long-lived Python bridge process.
 *
 * Restart policy is deliberately conservative: the backend is started once
 * and, if it dies, is restarted with exponential backoff. The plugin itself
 * must stay working while the backend is down -- a missing serial port is a
 * normal condition (the board is simply not plugged in), and it must never
 * take the host down with it.
 */
export declare class Backend {
    private child;
    private reader;
    private restartTimer;
    private restartDelayMs;
    private disposed;
    private readonly handlers;
    private readonly options;
    constructor(options: BackendOptions, handlers: BackendHandlers);
    /** Command line, exposed for logging and tests. */
    args(): string[];
    /** Whether a child is currently running. */
    get running(): boolean;
    /** Start the process. Safe to call when already running. */
    start(): void;
    /** Send one command. Returns false when the process is not running. */
    send(command: Record<string, unknown>): boolean;
    /** Stop the process for good. */
    dispose(): void;
    private scheduleRestart;
}
