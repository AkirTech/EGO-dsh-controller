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
import { approvalOutcomeFor, planQuestions, } from "./questions.js";
import { SessionQueue } from "./session.js";
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
        // 0 = wait forever. The legacy ask_user_question blocks indefinitely, so a
        // deadline would abandon perfectly normal thinking pauses; the abort signal
        // is the real cancellation path.
        questionTimeoutMs: raw.questionTimeoutMs ?? 0,
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
    //------------------------------------------------------------------
    // Question / approval handling
    //
    // The board has one ASK page showing "<question index>-<option index>", so a
    // whole request is walked one question at a time by SessionQueue. What the
    // board cannot express (no options, too many options, multi-select) is handed
    // straight back to the GUI by returning next().
    //
    // Every path that ends without a complete answer returns next(). That is the
    // fail-safe property the design depends on: an unplugged board, a timeout, or
    // a cancelled request must leave the GUI's own question card working exactly
    // as before.
    //------------------------------------------------------------------
    const sessions = new SessionQueue({
        show: (reqId, index) => {
            const question = activePlan[index - 1];
            if (question === undefined)
                return;
            // Tell the board how many options to allow, which question this is, and
            // which request it belongs to.
            backend.send({
                cmd: 'question_open',
                req_id: reqId,
                n_options: question.labels.length,
                q_index: index,
            });
            log(`QUESTION_OPEN req=${reqId} q=${index}/${activePlan.length} options=${question.labels.length}`);
        },
        hide: (reqId) => {
            backend.send({ cmd: 'question_close', req_id: reqId });
            log(`QUESTION_CLOSE req=${reqId}`);
        },
    });
    /** The questions of the request currently on the board, in order. */
    let activePlan = [];
    /** Base request id handed to the next walk; wraps at 256. */
    let nextReqId = 1;
    /**
     * Wait for a full answer, showing the questions on the board.
     *
     * @param questions - the request's questions.
     * @param signal - harness cancellation.
     * @returns the answers, or undefined when the board could not serve them.
     */
    const askOnBoard = async (questions, signal) => {
        const plan = planQuestions(questions, nextReqId);
        if (plan.refused !== undefined || plan.questions.length === 0) {
            log(`board cannot serve this request (${plan.refused ?? 'empty'}); leaving it to the GUI`);
            return undefined;
        }
        // Do not serve the panel while the link is down: the walk would just time
        // out, and the GUI can answer immediately.
        if (!linkUp) {
            log('link down; leaving the request to the GUI');
            return undefined;
        }
        const reqId = plan.questions[0].reqId;
        nextReqId = (reqId + plan.questions.length) & 0xff;
        activePlan = plan.questions;
        const timeoutMs = config.questionTimeoutMs;
        const outcome = await sessions.walk({
            reqId,
            total: plan.questions.length,
            timeoutMs,
            ...signal !== undefined ? { signal } : {},
        });
        activePlan = [];
        if (outcome.kind === 'abandoned') {
            log(`board walk ended: ${outcome.reason}`);
            return undefined;
        }
        return outcome.value;
    };
    //------------------------------------------------------------------
    // Question and approval seams
    //
    // Both are waterfalls: returning a value claims the request, calling next()
    // passes it to the GUI. Every failure path calls next(), so the panel being
    // absent, busy, or unable to express the question never breaks the UI.
    //------------------------------------------------------------------
    ctx.on('user-questions/request', async (request, next) => {
        try {
            // A subagent's question is not for the panel.
            if (isSubagent(request.agent)) {
                log('ignoring user-questions request from a subagent');
                return await next();
            }
            const questions = request.questions ?? [];
            if (questions.length === 0)
                return await next();
            // Can the board serve this one at all? Checking first means an
            // unusable request never disturbs the GUI.
            const plan = planQuestions(questions, nextReqId);
            if (plan.refused !== undefined || plan.questions.length === 0 || !linkUp) {
                log(`board cannot serve this request (${plan.refused ?? (linkUp ? 'empty' : 'link down')})`);
                return await next();
            }
            // ----------------------------------------------------------------
            // Race the board against the GUI.
            //
            // Calling next() is what makes the GUI render its own question card,
            // so it MUST be called. Cutting straight to a board answer (returning
            // without next()) short-circuits the waterfall and the user never sees
            // the question on screen -- which also means they cannot read it,
            // because the board only shows a one-digit option index.
            //
            // Whichever side answers first wins. If the GUI wins, the board walk is
            // abandoned; if the board wins, the GUI's card is torn down by the
            // waterfall settling, which is exactly the desired behaviour.
            // ----------------------------------------------------------------
            const boardRun = askOnBoard(questions, request.signal);
            const guiRun = next();
            // The loser may still reject later (a cancelled GUI wait, a disposed
            // session). Swallow it so it never surfaces as an unhandled rejection.
            boardRun.catch(() => { });
            guiRun.catch(() => { });
            const first = await Promise.race([
                boardRun.then(value => ({ from: 'board', value })),
                guiRun.then(value => ({ from: 'gui', value })),
            ]);
            if (first.from === 'gui') {
                // The user answered on screen. Release the panel so it stops showing
                // a question nobody is waiting for.
                sessions.abandon('preempted');
                log('answered in the GUI; releasing the board');
                return first.value;
            }
            const answers = first.value;
            if (answers === undefined) {
                // The board walk ended without a usable answer. The GUI run is still
                // pending, so hand the request back to it by awaiting next()'s result.
                log('board walk produced no complete answer; waiting for the GUI');
                return await guiRun;
            }
            // Rebuild the batch against the ORIGINAL question objects so ids always
            // match what the caller sent.
            const items = [];
            for (let i = 0; i < questions.length; i += 1) {
                const question = questions[i];
                const optionIndex = answers.get(i + 1);
                const label = optionIndex === undefined
                    ? undefined
                    : (question.options ?? [])[optionIndex]?.label;
                if (label === undefined) {
                    // Incomplete: never fabricate an empty selection, which the tool
                    // reads as "the user explicitly skipped".
                    log('incomplete board answers; waiting for the GUI');
                    return await guiRun;
                }
                items.push({ id: question.id, selected: [label] });
            }
            log(`answered on the board: ${JSON.stringify(items)}`);
            return { answers: items };
        }
        catch (error) {
            log(`user-questions handler threw: ${String(error)}; handing to the GUI`);
            return await next();
        }
    });
    ctx.on('approval/request', async (request, next) => {
        try {
            if (isSubagent(request.agent))
                return await next();
            if (!linkUp)
                return await next();
            // An approval has exactly two answers on this panel: index 0 allows,
            // anything else rejects. Wrapping it as a normal two-option question
            // reuses the whole walk mechanism instead of duplicating it.
            const questions = [{
                    id: 'approval',
                    question: `approve ${request.toolName ?? 'tool'}?`,
                    options: [{ label: 'allow' }, { label: 'reject' }],
                }];
            // Race the board against the GUI for the same reason as user questions:
            // next() is what renders the on-screen approval prompt, and the user
            // must be able to see WHAT they are approving.
            const boardRun = askOnBoard(questions, request.signal);
            const guiRun = next();
            boardRun.catch(() => { });
            guiRun.catch(() => { });
            const first = await Promise.race([
                boardRun.then(value => ({ from: 'board', value })),
                guiRun.then(value => ({ from: 'gui', value })),
            ]);
            if (first.from === 'gui') {
                sessions.abandon('preempted');
                log('approval answered in the GUI; releasing the board');
                return first.value;
            }
            const answers = first.value;
            if (answers === undefined)
                return await guiRun;
            const optionIndex = answers.get(1);
            if (optionIndex === undefined)
                return await guiRun;
            const outcome = approvalOutcomeFor(['allow', 'reject'], optionIndex, 'allow');
            if (outcome === undefined)
                return await guiRun;
            log(`approval ${request.toolName ?? 'tool'} -> ${outcome} (board)`);
            return outcome;
        }
        catch (error) {
            // Fail closed: an approval that cannot be answered must go back to the
            // GUI, not become an accidental grant.
            log(`approval handler threw: ${String(error)}; handing to the GUI`);
            return await next();
        }
    });
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
                    // A lost link cannot serve a question: release whatever is waiting so
                    // the GUI can take over instead of the user staring at a dead board.
                    sessions.abandon('disposed');
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
            case 'answer': {
                // The board confirmed one question. Feed it to the walker; it either
                // advances to the next question or settles the whole batch.
                const event_ = {
                    reqId: typeof event.req_id === 'number' ? event.req_id : -1,
                    qIndex: typeof event.q_index === 'number' ? event.q_index : -1,
                    option: typeof event.option === 'number' ? event.option : -1,
                    ...typeof event.elapsed_ms === 'number' ? { elapsedMs: event.elapsed_ms } : {},
                };
                const consumed = sessions.offer(event_);
                log(`ANSWER req=${event_.reqId} q=${event_.qIndex} option=${event_.option}` +
                    (consumed ? '' : ' (no active question; ignored)'));
                return;
            }
            case 'key':
                // Key presses are reported for completeness. The board already
                // interprets up/down/centre locally, so nothing here changes a
                // question's state -- the ANSWER frame carries the decision.
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
    /**
     * Per-agent running flags.
     *
     * `agent/status` is emitted once per agent, so a flat boolean is wrong: with
     * two agents alive, the subagent going idle would clear the lamp while the
     * main agent is still working, and vice versa. Tracking each agent's own
     * state and deriving "is anything busy" fixes that.
     *
     * Subagents are excluded on purpose. The panel sits on the user's desk to
     * show what the session THEY are watching is doing; a background helper
     * grinding through a search should not make the panel blink. `origin` is the
     * documented way to tell them apart (session/types.ts: 'subagent').
     */
    const runningAgents = new Set();
    const isSubagent = (agent) => {
        const session = agent?.session;
        return session?.header?.origin === 'subagent';
    };
    const recomputeRunning = () => {
        const next = runningAgents.size > 0;
        if (next === agentRunning)
            return;
        agentRunning = next;
        pushState();
    };
    ctx.on('agent/status', (payload) => {
        try {
            const id = payload.agent?.id ?? 'unknown';
            if (isSubagent(payload.agent)) {
                // A subagent's activity is not the panel's business. Its status is
                // still logged so a surprising "no lamp" can be explained.
                log(`ignoring subagent ${id} status=${payload.status}`);
                return;
            }
            if (payload.status === 'running')
                runningAgents.add(id);
            else
                runningAgents.delete(id);
            recomputeRunning();
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
