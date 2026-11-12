/**
 * One active board interaction at a time.
 *
 * The board has a single ASK page, so two concurrent requests cannot both be
 * served. This class serialises them: the newest request wins the panel, and
 * any older one is released back to the GUI by settling as "no decision".
 *
 * Multi-question requests walk one question at a time. Each centre-key press
 * confirms the question currently on the ASK page; the caller then sends the
 * next QUESTION_OPEN and the waiter's expected index advances. The batch is
 * handed back only once every question has an answer.
 *
 * Fail-safe by construction: a timeout, a cancellation, or a refusal settles
 * the wait with `abandoned`, which every caller treats as "not mine, hand it to
 * the next answerer".
 *
 * @module dsh-ego-bridge/session
 */
/**
 * Serialises board interactions.
 *
 * Only one waiter is tracked. Starting a new one settles the previous with
 * `'preempted'`, which is what makes "the newest question wins the panel" true
 * without any queueing policy to reason about.
 */
export class SessionQueue {
    waiter;
    hooks;
    constructor(hooks) {
        this.hooks = hooks;
    }
    /** Whether a request currently owns the panel. */
    get busy() {
        return this.waiter !== undefined;
    }
    /** The question index the board should be showing, or 0 when idle. */
    get showing() {
        return this.waiter?.current ?? 0;
    }
    /**
     * Claim the panel and walk a request's questions.
     *
     * @param options.reqId - base request id; each question uses `reqId + i`.
     * @param options.total - number of questions.
     * @param options.timeoutMs - per-question deadline; 0 or negative waits forever.
     * @param options.signal - harness cancellation.
     * @returns every answer, or why the walk ended early.
     */
    async walk(options) {
        this.abandon('preempted');
        if (options.total < 1) {
            return { kind: 'abandoned', reason: 'cancelled' };
        }
        return await new Promise((resolve) => {
            const waiter = {
                total: options.total,
                current: 1,
                reqId: options.reqId,
                answers: new Map(),
                timeoutMs: options.timeoutMs ?? 0,
                timer: undefined,
                resolve,
                detach: undefined,
                settled: false,
            };
            // Already cancelled: settle without occupying the slot.
            if (options.signal?.aborted === true) {
                resolve({ kind: 'abandoned', reason: 'cancelled' });
                return;
            }
            this.waiter = waiter;
            if (options.signal !== undefined) {
                const signal = options.signal;
                const onAbort = () => this.settle(waiter, { kind: 'abandoned', reason: 'cancelled' });
                signal.addEventListener('abort', onAbort, { once: true });
                waiter.detach = () => signal.removeEventListener('abort', onAbort);
            }
            this.armTimer(waiter);
            // Ask the board to show the first question, under THIS question's own
            // request id (base + index - 1), not the request's base id.
            this.hooks.show(this.reqIdFor(waiter, waiter.current), waiter.current);
        });
    }
    /**
     * Feed one board answer to the active waiter.
     *
     * @returns true when the answer was consumed by the current request.
     */
    offer(event) {
        const waiter = this.waiter;
        if (waiter === undefined)
            return false;
        // Only the question on screen can be answered. A stale frame from a
        // previous request must not be counted here, and neither can an answer for
        // a question the user has not reached yet.
        if (event.qIndex !== waiter.current)
            return false;
        if (event.reqId !== this.reqIdFor(waiter, waiter.current))
            return false;
        waiter.answers.set(waiter.current, event.option);
        if (waiter.current < waiter.total) {
            // Advance: show the next question under its own request id, and re-arm
            // the timeout so a long walk is not cut short by the first deadline.
            waiter.current += 1;
            this.armTimer(waiter);
            this.hooks.show(this.reqIdFor(waiter, waiter.current), waiter.current);
            return true;
        }
        // Last question answered: hand the batch back.
        const answers = new Map(waiter.answers);
        this.hooks.hide(this.reqIdFor(waiter, waiter.current));
        this.settle(waiter, { kind: 'answered', value: answers });
        return true;
    }
    /** Abandon the active request, if any. */
    abandon(reason) {
        const waiter = this.waiter;
        if (waiter === undefined)
            return;
        this.hooks.hide(this.reqIdFor(waiter, waiter.current));
        this.settle(waiter, { kind: 'abandoned', reason });
    }
    /** Release the panel and mark the queue dead. */
    dispose() {
        this.abandon('disposed');
    }
    /** The request id used on the wire for question `index` (1-based). */
    reqIdFor(waiter, index) {
        return (waiter.reqId + index - 1) & 0xff;
    }
    armTimer(waiter) {
        if (waiter.timer !== undefined) {
            clearTimeout(waiter.timer);
            waiter.timer = undefined;
        }
        const ms = waiter.timeoutMs;
        if (ms <= 0)
            return;
        waiter.timer = setTimeout(() => {
            this.hooks.hide(this.reqIdFor(waiter, waiter.current));
            this.settle(waiter, { kind: 'abandoned', reason: 'timeout' });
        }, ms);
        waiter.timer.unref?.();
    }
    settle(waiter, outcome) {
        if (waiter.settled)
            return;
        waiter.settled = true;
        if (waiter.timer !== undefined) {
            clearTimeout(waiter.timer);
            waiter.timer = undefined;
        }
        waiter.detach?.();
        waiter.detach = undefined;
        if (this.waiter === waiter)
            this.waiter = undefined;
        waiter.resolve(outcome);
    }
}
