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
/** The board accepts indices 0..8, i.e. nine options. */
export const MAX_BOARD_OPTIONS = 9;
/** The ASK page holds one digit for the question number. */
export const MAX_BOARD_QUESTIONS = 16;
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
export function planQuestions(questions, firstReqId = 1) {
    if (questions.length === 0)
        return { questions: [], refused: 'no-answer' };
    const planned = [];
    for (let i = 0; i < questions.length; i += 1) {
        const question = questions[i];
        if (question.multiSelect === true) {
            // One digit cannot express a subset.
            return { questions: [], refused: 'multi-select' };
        }
        const options = question.options ?? [];
        if (options.length === 0) {
            // A question without options needs free text; the board has no keyboard.
            return { questions: [], refused: 'no-options' };
        }
        if (options.length > MAX_BOARD_OPTIONS) {
            return { questions: [], refused: 'too-many-options' };
        }
        if (i >= MAX_BOARD_QUESTIONS) {
            return { questions: [], refused: 'no-answer' };
        }
        planned.push({
            id: question.id,
            qIndex: i + 1,
            labels: options.map(option => option.label),
            reqId: (firstReqId + i) & 0xff,
        });
    }
    return { questions: planned };
}
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
export function buildAnswers(questions, answers) {
    const items = [];
    for (const question of questions) {
        const optionIndex = answers.get(question.qIndex);
        if (optionIndex === undefined)
            return undefined;
        const label = question.labels[optionIndex];
        if (label === undefined)
            return undefined;
        items.push({ id: question.id, selected: [label] });
    }
    return items;
}
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
export function approvalOutcomeFor(labels, optionIndex, approveLabel) {
    const label = labels[optionIndex];
    if (label === undefined)
        return undefined;
    if (approveLabel === undefined)
        return undefined;
    return label === approveLabel ? 'allowed-once' : 'rejected';
}
