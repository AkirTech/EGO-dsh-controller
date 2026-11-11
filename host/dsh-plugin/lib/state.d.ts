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
export declare const BOARD_FREE = 0;
export declare const BOARD_RUNNING = 1;
export declare const BOARD_WAITING = 2;
/** Human names, for logs. */
export declare const BOARD_STATE_NAMES: Readonly<Record<number, string>>;
/**
 * Aggregated view of what the board should display.
 *
 * Kept as one object rather than three independent `send()` calls so that a
 * state change can never be split across two `STATE_SET` frames, which would
 * show a transient wrong lamp for one heartbeat.
 */
export interface BoardState {
    readonly state: number;
    readonly session: number;
    readonly pending: number;
}
/**
 * Derive the board state from the host's facts.
 *
 * @param running - whether any agent turn is in progress.
 * @param pendingInteractions - unanswered user questions plus pending approvals.
 * @param session - session slot, 0..15, display only.
 */
export declare function deriveBoardState(running: boolean, pendingInteractions: number, session?: number): BoardState;
/** Clamp a count into the wire's 0..255. */
export declare function clampByte(value: number): number;
/** Clamp a session number into the wire's 0..15. */
export declare function clampSession(value: number): number;
/** Whether two board states are the same as far as the board is concerned. */
export declare function sameBoardState(a: BoardState | undefined, b: BoardState): boolean;
