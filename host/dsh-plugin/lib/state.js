/**
 * Maps DeepSeek Harness state onto the board's three-state vocabulary.
 *
 * The board knows FREE / RUNNING / WAITING. DSH's `AgentStatus` only has
 * `idle` and `running`, which is not enough on its own: "running" covers a
 * whole turn, so it cannot express "the agent is blocked waiting for the
 * human". WAITING is therefore derived from the presence of an open
 * interaction, not from the status alone.
 *
 * This round of the bridge only wires `agent/status` plus the clock, so
 * WAITING is only reachable through the optional `pending` counter that an
 * external caller may set. The user-question and approval seams are the
 * intended future source -- see docs/SERIAL-PROTOCOL.md section 7.
 *
 * @module dsh-ego-bridge/state
 */
/** The board's state encoding. Must match `status_fsm.v` and the wire format. */
export const BOARD_FREE = 0;
export const BOARD_RUNNING = 1;
export const BOARD_WAITING = 2;
/** Human names, for logs. */
export const BOARD_STATE_NAMES = {
    [BOARD_FREE]: 'FREE',
    [BOARD_RUNNING]: 'RUNNING',
    [BOARD_WAITING]: 'WAITING',
};
/**
 * Derive the board state from the host's facts.
 *
 * @param running - whether any agent turn is in progress.
 * @param pendingInteractions - unanswered user questions plus pending approvals.
 * @param session - session slot, 0..15, display only.
 */
export function deriveBoardState(running, pendingInteractions, session = 0) {
    const pending = clampByte(pendingInteractions);
    let state;
    if (pending > 0) {
        // Something is blocked on the human. This outranks "running" because it
        // is the only state that asks for a button press.
        state = BOARD_WAITING;
    }
    else if (running) {
        state = BOARD_RUNNING;
    }
    else {
        state = BOARD_FREE;
    }
    return { state, session: clampSession(session), pending };
}
/** Clamp a count into the wire's 0..255. */
export function clampByte(value) {
    if (!Number.isFinite(value))
        return 0;
    const n = Math.trunc(value);
    if (n < 0)
        return 0;
    if (n > 255)
        return 255;
    return n;
}
/** Clamp a session number into the wire's 0..15. */
export function clampSession(value) {
    if (!Number.isFinite(value))
        return 0;
    const n = Math.trunc(value);
    if (n < 0)
        return 0;
    if (n > 15)
        return 15;
    return n;
}
/** Whether two board states are the same as far as the board is concerned. */
export function sameBoardState(a, b) {
    if (a === undefined)
        return false;
    return a.state === b.state && a.session === b.session && a.pending === b.pending;
}
