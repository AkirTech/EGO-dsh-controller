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
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
/**
 * Manages one long-lived Python bridge process.
 *
 * Restart policy is deliberately conservative: the backend is started once
 * and, if it dies, is restarted with exponential backoff. The plugin itself
 * must stay working while the backend is down -- a missing serial port is a
 * normal condition (the board is simply not plugged in), and it must never
 * take the host down with it.
 */
export class Backend {
    child;
    reader;
    restartTimer;
    restartDelayMs = 1000;
    disposed = false;
    handlers;
    options;
    constructor(options, handlers) {
        this.options = options;
        this.handlers = handlers;
    }
    /** Command line, exposed for logging and tests. */
    args() {
        const args = ['-m', 'ego_bridge.service'];
        if (this.options.port !== undefined && this.options.port !== '') {
            args.push('--port', this.options.port);
        }
        if (this.options.baudRate !== undefined) {
            args.push('--baud', String(this.options.baudRate));
        }
        if (this.options.resyncHourly === true)
            args.push('--resync-hourly');
        if (this.options.extraArgs !== undefined)
            args.push(...this.options.extraArgs);
        return args;
    }
    /** Whether a child is currently running. */
    get running() {
        return this.child !== undefined && this.child.exitCode === null;
    }
    /** Start the process. Safe to call when already running. */
    start() {
        if (this.disposed || this.running)
            return;
        const python = this.options.python ?? 'python';
        const argv = this.args();
        let child;
        try {
            child = spawn(python, argv, {
                cwd: this.options.cwd,
                stdio: ['pipe', 'pipe', 'pipe'],
                windowsHide: true,
            });
        }
        catch (error) {
            // A missing Python is not fatal: report and schedule a retry so that
            // installing Python later picks up without restarting the host.
            this.handlers.onStderr(`spawn failed: ${String(error)}`);
            this.scheduleRestart();
            return;
        }
        this.child = child;
        child.on('error', (error) => {
            this.handlers.onStderr(`process error: ${String(error)}`);
        });
        child.on('exit', (code, signal) => {
            this.reader?.close();
            this.reader = undefined;
            this.child = undefined;
            this.handlers.onExit(code, signal);
            this.scheduleRestart();
        });
        child.stderr.setEncoding('utf8');
        createInterface({ input: child.stderr }).on('line', (line) => {
            if (line.trim() !== '')
                this.handlers.onStderr(line);
        });
        child.stdout.setEncoding('utf8');
        this.reader = createInterface({ input: child.stdout });
        this.reader.on('line', (line) => {
            const text = line.trim();
            if (text === '')
                return;
            let parsed;
            try {
                parsed = JSON.parse(text);
            }
            catch {
                // Never let one malformed line kill the stream.
                this.handlers.onStderr(`unparseable line from backend: ${text.slice(0, 200)}`);
                return;
            }
            if (typeof parsed === 'object' && parsed !== null && typeof parsed.evt === 'string') {
                this.handlers.onEvent(parsed);
            }
        });
    }
    /** Send one command. Returns false when the process is not running. */
    send(command) {
        const child = this.child;
        if (child === undefined || child.exitCode !== null || child.stdin.destroyed)
            return false;
        try {
            child.stdin.write(`${JSON.stringify(command)}\n`);
            return true;
        }
        catch (error) {
            this.handlers.onStderr(`write failed: ${String(error)}`);
            return false;
        }
    }
    /** Stop the process for good. */
    dispose() {
        this.disposed = true;
        if (this.restartTimer !== undefined) {
            clearTimeout(this.restartTimer);
            this.restartTimer = undefined;
        }
        const child = this.child;
        if (child === undefined)
            return;
        // Ask politely first: the service flushes a final link-down event.
        try {
            child.stdin.write(`${JSON.stringify({ cmd: 'shutdown' })}\n`);
            child.stdin.end();
        }
        catch {
            // Already gone; fall through to the kill.
        }
        const killed = setTimeout(() => {
            if (child.exitCode === null)
                child.kill();
        }, 1500);
        killed.unref();
        this.reader?.close();
        this.reader = undefined;
        this.child = undefined;
    }
    scheduleRestart() {
        if (this.disposed || this.restartTimer !== undefined)
            return;
        const delay = this.restartDelayMs;
        this.restartDelayMs = Math.min(this.restartDelayMs * 2, 30_000);
        this.restartTimer = setTimeout(() => {
            this.restartTimer = undefined;
            this.start();
        }, delay);
        this.restartTimer.unref();
    }
}
