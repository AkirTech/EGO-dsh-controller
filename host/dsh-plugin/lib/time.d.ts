/**
 * Time synchronisation: the board's clock is set from the host clock.
 *
 * The board free-runs at 1 Hz once a `TIME_SYNC` frame arrives, so the PC
 * only has to send one per connection. Two extra triggers exist and both are
 * cheap:
 *
 *   - the board reports `flags.bit1 == 0` ("time never set") in its
 *     heartbeat, which happens after the board reboots while the PC stays
 *     connected. That is the case a single send-per-connection misses.
 *   - `uptime_s` going backwards means the board restarted, so the whole
 *     handshake is redone.
 *
 * Weekday convention: **Monday = 1 .. Sunday = 7**, chosen in the design and
 * written into the protocol contract. `getDay()`-style 0 = Sunday numbering
 * is therefore NOT used; this is the single place the convention is applied.
 *
 * @module dsh-ego-bridge/time
 */
/** A wall-clock instant broken into the fields the board needs. */
export interface BoardTime {
    readonly hour: number;
    readonly minute: number;
    readonly second: number;
    /** 1..7 with Monday = 1. */
    readonly day: number;
}
/**
 * Break a JavaScript `Date` into the board's fields, in **local** time.
 *
 * The board shows the time on a desk next to the user, so local time is the
 * right choice; UTC would be an hour or more off and look like a bug.
 *
 * @param date - instant to convert. Defaults to now.
 * @returns the four wire fields.
 */
export declare function boardTimeFrom(date?: Date): BoardTime;
/**
 * Whether a heartbeat says the board lost its time.
 *
 * Bit 1 of the flags byte is "time is valid". After a board reset it reads 0
 * until the next `TIME_SYNC`, which is exactly the condition worth reacting
 * to.
 *
 * @param flags - the flags byte from a `STATUS_RPT`.
 */
export declare function timeIsValid(flags: number): boolean;
/**
 * Detect a board restart from the uptime counter.
 *
 * `uptime_s` saturates at 65535 and never wraps below that on its own, so any
 * decrease means a reset.
 *
 * @param previous - last uptime seen, or `undefined` for the first sample.
 * @param current - uptime from the latest `STATUS_RPT`.
 */
export declare function boardRestarted(previous: number | undefined, current: number): boolean;
