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
  readonly reqId: number
  readonly qIndex: number
  readonly option: number
  readonly elapsedMs?: number
}

/** Why a wait ended without a usable answer. */
export type AbandonReason = 'timeout' | 'cancelled' | 'preempted' | 'disposed'

/** The outcome of one board interaction. */
export type SessionOutcome<T> =
  | { readonly kind: 'answered'; readonly value: T }
  | { readonly kind: 'abandoned'; readonly reason: AbandonReason }

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
  show: (reqId: number, index: number) => void
  /** Hide the question, e.g. after the last answer or on abandonment. */
  hide: (reqId: number) => void
}

interface Waiter {
  /** How many questions this request has. */
  total: number
  /** Question index the board is currently showing (1-based). */
  current: number
  /** Request id currently on the board. */
  reqId: number
  /** Answers collected so far, keyed by question index. */
  answers: Map<number, number>
  /** Timeout in ms, 0 for none. */
  timeoutMs: number
  /** Re-armed after every confirmation, so a slow user does not time out mid-way. */
  timer: NodeJS.Timeout | undefined
  /** Resolve the outer promise. */
  resolve: (outcome: SessionOutcome<Map<number, number>>) => void
  /** Detach the abort listener. */
  detach: (() => void) | undefined
  /** Whether the promise has already settled. */
  settled: boolean
}

/**
 * Serialises board interactions.
 *
 * Only one waiter is tracked. Starting a new one settles the previous with
 * `'preempted'`, which is what makes "the newest question wins the panel" true
 * without any queueing policy to reason about.
 */
export class SessionQueue {
  private waiter: Waiter | undefined
  private readonly hooks: SessionHooks

  constructor(hooks: SessionHooks) {
    this.hooks = hooks
  }

  /** Whether a request currently owns the panel. */
  get busy(): boolean {
    return this.waiter !== undefined
  }

  /** The question index the board should be showing, or 0 when idle. */
  get showing(): number {
    return this.waiter?.current ?? 0
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
  async walk(options: {
    reqId: number
    total: number
    timeoutMs?: number
    signal?: AbortSignal
  }): Promise<SessionOutcome<Map<number, number>>> {
    this.abandon('preempted')

    if (options.total < 1) {
      return { kind: 'abandoned', reason: 'cancelled' }
    }

    return await new Promise<SessionOutcome<Map<number, number>>>((resolve) => {
      const waiter: Waiter = {
        total: options.total,
        current: 1,
        reqId: options.reqId,
        answers: new Map(),
        timeoutMs: options.timeoutMs ?? 0,
        timer: undefined,
        resolve,
        detach: undefined,
        settled: false,
      }

      // Already cancelled: settle without occupying the slot.
      if (options.signal?.aborted === true) {
        resolve({ kind: 'abandoned', reason: 'cancelled' })
        return
      }

      this.waiter = waiter

      if (options.signal !== undefined) {
        const signal = options.signal
        const onAbort = (): void => this.settle(waiter, { kind: 'abandoned', reason: 'cancelled' })
        signal.addEventListener('abort', onAbort, { once: true })
        waiter.detach = () => signal.removeEventListener('abort', onAbort)
      }

      this.armTimer(waiter)
      // Ask the board to show the first question, under THIS question's own
      // request id (base + index - 1), not the request's base id.
      this.hooks.show(this.reqIdFor(waiter, waiter.current), waiter.current)
    })
  }

  /**
   * Feed one board answer to the active waiter.
   *
   * @returns true when the answer was consumed by the current request.
   */
  offer(event: AnswerEvent): boolean {
    const waiter = this.waiter
    if (waiter === undefined) return false

    // Only the question on screen can be answered. A stale frame from a
    // previous request must not be counted here, and neither can an answer for
    // a question the user has not reached yet.
    if (event.qIndex !== waiter.current) return false
    if (event.reqId !== this.reqIdFor(waiter, waiter.current)) return false

    waiter.answers.set(waiter.current, event.option)

    if (waiter.current < waiter.total) {
      // Advance: show the next question under its own request id, and re-arm
      // the timeout so a long walk is not cut short by the first deadline.
      waiter.current += 1
      this.armTimer(waiter)
      this.hooks.show(this.reqIdFor(waiter, waiter.current), waiter.current)
      return true
    }

    // Last question answered: hand the batch back.
    const answers = new Map(waiter.answers)
    this.hooks.hide(this.reqIdFor(waiter, waiter.current))
    this.settle(waiter, { kind: 'answered', value: answers })
    return true
  }

  /** Abandon the active request, if any. */
  abandon(reason: AbandonReason): void {
    const waiter = this.waiter
    if (waiter === undefined) return
    this.hooks.hide(this.reqIdFor(waiter, waiter.current))
    this.settle(waiter, { kind: 'abandoned', reason })
  }

  /** Release the panel and mark the queue dead. */
  dispose(): void {
    this.abandon('disposed')
  }

  /** The request id used on the wire for question `index` (1-based). */
  private reqIdFor(waiter: Waiter, index: number): number {
    return (waiter.reqId + index - 1) & 0xff
  }

  private armTimer(waiter: Waiter): void {
    if (waiter.timer !== undefined) {
      clearTimeout(waiter.timer)
      waiter.timer = undefined
    }
    const ms = waiter.timeoutMs
    if (ms <= 0) return
    waiter.timer = setTimeout(() => {
      this.hooks.hide(this.reqIdFor(waiter, waiter.current))
      this.settle(waiter, { kind: 'abandoned', reason: 'timeout' })
    }, ms)
    waiter.timer.unref?.()
  }

  private settle(waiter: Waiter, outcome: SessionOutcome<Map<number, number>>): void {
    if (waiter.settled) return
    waiter.settled = true
    if (waiter.timer !== undefined) {
      clearTimeout(waiter.timer)
      waiter.timer = undefined
    }
    waiter.detach?.()
    waiter.detach = undefined
    if (this.waiter === waiter) this.waiter = undefined
    waiter.resolve(outcome)
  }
}
