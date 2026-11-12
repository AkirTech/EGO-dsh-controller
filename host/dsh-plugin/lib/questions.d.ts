/**
 * Question/approval planning: what to show on the board, and how to turn a
 * board answer back into what the harness expects.
 *
 * Everything here is pure so the mapping rules can be tested without a serial
 * port, a board, or a cordis context. The plugin only wires these functions to
 * events.
 *
 * The board can display exactly ONE digit for the selected option, which drives
 * most of the constraints below:
 *
 *   - at most 9 options (indices 0..8) can be shown; beyond that the request is
 *     handed back to the GUI rather than silently truncated,
 *   - the plugin has to remember which label each index stands for, because the
 *     board only ever reports an index,
 *   - multi-select questions cannot be answered on the board at all: one digit
 *     cannot express a subset.
 *
 * @module dsh-ego-bridge/questions
 */
/** One option as the harness describes it. Only `label` matters on the board. */
export interface QuestionOption {
    readonly label: string;
    readonly description?: string;
}
/** One question from `user-questions/request`. */
export interface QuestionItem {
    readonly id: string;
    readonly question: string;
    readonly header?: string;
    readonly detail?: string;
    readonly options?: readonly QuestionOption[];
    readonly multiSelect?: boolean;
}
/** How one question should be presented on the board. */
export interface BoardQuestion {
    /** The harness question id, echoed back in the answer batch. */
    readonly id: string;
    /** Question index shown on the ASK page (1-based, max 16). */
    readonly qIndex: number;
    /** Option labels in board order; index i is what the digit i selects. */
    readonly labels: readonly string[];
    /** Request id the board echoes back in ANSWER. */
    readonly reqId: number;
}
/** Why a request cannot be shown on the board. */
export type RefusalReason = 'no-options' | 'too-many-options' | 'multi-select' | 'no-answer';
/** A plan for one whole request: a queue of questions plus the reason it was refused. */
export interface QuestionPlan {
    /** Questions to walk through, in order. Empty when refused. */
    readonly questions: readonly BoardQuestion[];
    /** Set when the board cannot serve this request at all. */
    readonly refused?: RefusalReason;
}
/** The board accepts indices 0..8, i.e. nine options. */
export declare const MAX_BOARD_OPTIONS = 9;
/** The ASK page holds one digit for the question number. */
export declare const MAX_BOARD_QUESTIONS = 16;
/**
 * Decide how to present a request on the board.
 *
 * A request is refused as a WHOLE rather than partially: if the third question
 * of five is multi-select, answering the first two on the board and the rest in
 * the GUI would split one decision across two places, and the agent would then
 * see an answer batch assembled from two different interaction modes.
 *
 * @param questions - the request's questions.
 * @param firstReqId - request id to start numbering from; wraps at 256.
 * @returns the plan, or a refusal reason.
 */
export declare function planQuestions(questions: readonly QuestionItem[], firstReqId?: number): QuestionPlan;
/**
 * Build the answer batch the harness expects.
 *
 * `selected` holds LABELS, not indices, because that is what
 * `AskUserQuestionAnswerItem.selected` documents and what the GUI produces. The
 * board reports an index, so this is the one place the two representations meet.
 *
 * An empty digit (the user pressed nothing) must not be expressed as an empty
 * `selected`: the tool treats `selected: []` with no `custom` as "the user
 * explicitly skipped". A skipped question is therefore omitted from the result
 * entirely, and the caller is expected to refuse the whole request instead.
 *
 * @param questions - the planned questions, in order.
 * @param answers - raw board answers keyed by question index (1-based).
 * @returns one answer item per question, or undefined if any is unanswered.
 */
export declare function buildAnswers(questions: readonly BoardQuestion[], answers: ReadonlyMap<number, number>): Array<{
    id: string;
    selected: string[];
}> | undefined;
/**
 * Map a board answer to approval outcomes.
 *
 * Approval questions always have exactly two or three options and the harness
 * names them via `intent`. Rather than guessing from the digit, the plugin
 * looks the label up and compares it with the declared action labels, which is
 * the same rule the service itself uses to validate them.
 *
 * @param labels - option labels in board order.
 * @param optionIndex - the digit the user confirmed.
 * @param approveLabel - the label that means "allow once".
 * @returns the closed outcome, or `undefined` when the digit selects neither
 *   action (which the caller should treat as "no decision").
 */
export declare function approvalOutcomeFor(labels: readonly string[], optionIndex: number, approveLabel: string | undefined): 'allowed-once' | 'rejected' | undefined;
