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
/** What the board reported for one confirmed question. */
export interface AnswerEvent {
    readonly reqId: number;
    readonly qIndex: number;
    readonly option: number;
    readonly elapsedMs?: number;
}
/** Why a wait ended without a usable answer. */
export type AbandonReason = 'timeout' | 'cancelled' | 'preempted' | 'disposed';
/** The outcome of one board interaction. */
export type SessionOutcome<T> = {
    readonly kind: 'answered';
    readonly value: T;
} | {
    readonly kind: 'abandoned';
    readonly reason: AbandonReason;
};
/**
 * Callbacks the waiter needs from the plugin.
 *
 * The queue does not know how to talk to the board; it only knows when to ask.
 */
export interface SessionHooks {
    /**
     * Show question `index` (1-based) for request `reqId`.
     * Called for the first question immediately, and again after each confirmation.
     */
    show: (reqId: number, index: number) => void;
    /** Hide the question, e.g. after the last answer or on abandonment. */
    hide: (reqId: number) => void;
}
/**
 * Serialises board interactions.
 *
 * Only one waiter is tracked. Starting a new one settles the previous with
 * `'preempted'`, which is what makes "the newest question wins the panel" true
 * without any queueing policy to reason about.
 */
export declare class SessionQueue {
    private waiter;
    private readonly hooks;
    constructor(hooks: SessionHooks);
    /** Whether a request currently owns the panel. */
    get busy(): boolean;
    /** The question index the board should be showing, or 0 when idle. */
    get showing(): number;
    /**
     * Claim the panel and walk a request's questions.
     *
     * @param options.reqId - base request id; each question uses `reqId + i`.
     * @param options.total - number of questions.
     * @param options.timeoutMs - per-question deadline; 0 or negative waits forever.
     * @param options.signal - harness cancellation.
     * @returns every answer, or why the walk ended early.
     */
    walk(options: {
        reqId: number;
        total: number;
        timeoutMs?: number;
        signal?: AbortSignal;
    }): Promise<SessionOutcome<Map<number, number>>>;
    /**
     * Feed one board answer to the active waiter.
     *
     * @returns true when the answer was consumed by the current request.
     */
    offer(event: AnswerEvent): boolean;
    /** Abandon the active request, if any. */
    abandon(reason: AbandonReason): void;
    /** Release the panel and mark the queue dead. */
    dispose(): void;
    /** The request id used on the wire for question `index` (1-based). */
    private reqIdFor;
    private armTimer;
    private settle;
}
