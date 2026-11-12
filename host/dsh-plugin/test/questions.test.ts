/**
 * Tests for the question/approval mapping.
 *
 * These pin the rules that are easy to get subtly wrong and impossible to
 * notice by staring at the board: which requests the panel refuses, and how a
 * single digit becomes a label the harness accepts.
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'

import {
  MAX_BOARD_OPTIONS,
  approvalOutcomeFor,
  buildAnswers,
  planQuestions,
  type QuestionItem,
} from '../src/questions.ts'

const opts = (...labels: string[]): QuestionItem['options'] =>
  labels.map(label => ({ label }))

const q = (id: string, labels: string[], extra: Partial<QuestionItem> = {}): QuestionItem => ({
  id,
  question: `question ${id}`,
  options: opts(...labels),
  ...extra,
})

// ---------------------------------------------------------------- planning

test('a simple two-option question plans one board question', () => {
  const plan = planQuestions([q('a', ['Yes', 'No'])])
  assert.equal(plan.refused, undefined)
  assert.equal(plan.questions.length, 1)
  assert.equal(plan.questions[0]?.qIndex, 1)
  assert.deepEqual(plan.questions[0]?.labels, ['Yes', 'No'])
  assert.equal(plan.questions[0]?.id, 'a')
})

test('nine options is the last acceptable count', () => {
  // The ASK page holds one digit, so index 8 is the largest selectable.
  const nine = Array.from({ length: MAX_BOARD_OPTIONS }, (_, i) => `o${i}`)
  const plan = planQuestions([q('a', nine)])
  assert.equal(plan.refused, undefined)
  assert.equal(plan.questions[0]?.labels.length, 9)
})

test('ten options is refused, not truncated', () => {
  // Truncating would silently hide an option the user might want, and any
  // index they picked would be ambiguous.
  const ten = Array.from({ length: 10 }, (_, i) => `o${i}`)
  const plan = planQuestions([q('a', ten)])
  assert.equal(plan.refused, 'too-many-options')
  assert.equal(plan.questions.length, 0)
})

test('a question without options is refused (needs a keyboard)', () => {
  const plan = planQuestions([{ id: 'a', question: 'type something' }])
  assert.equal(plan.refused, 'no-options')
})

test('multi-select is refused', () => {
  const plan = planQuestions([q('a', ['x', 'y'], { multiSelect: true })])
  assert.equal(plan.refused, 'multi-select')
})

test('one multi-select among several refuses the WHOLE request', () => {
  // Answering the first two on the board and the third in the GUI would
  // assemble one decision from two interaction modes.
  const plan = planQuestions([
    q('a', ['x', 'y']),
    q('b', ['p', 'q'], { multiSelect: true }),
  ])
  assert.equal(plan.refused, 'multi-select')
  assert.equal(plan.questions.length, 0)
})

test('multiple questions get increasing qIndex and distinct reqId', () => {
  const plan = planQuestions([q('a', ['x']), q('b', ['y']), q('c', ['z'])], 10)
  assert.deepEqual(plan.questions.map(p => p.qIndex), [1, 2, 3])
  assert.deepEqual(plan.questions.map(p => p.reqId), [10, 11, 12])
})

test('reqId wraps inside a byte', () => {
  const plan = planQuestions([q('a', ['x']), q('b', ['y'])], 255)
  assert.deepEqual(plan.questions.map(p => p.reqId), [255, 0])
})

test('an empty request is refused', () => {
  assert.equal(planQuestions([]).refused, 'no-answer')
})

// ---------------------------------------------------------------- answering

test('a board digit becomes the label the harness expects', () => {
  const plan = planQuestions([q('a', ['Yes (Recommended)', 'No'])])
  // The board reports index 0; the harness must receive the LABEL.
  const answers = buildAnswers(plan.questions, new Map([[1, 0]]))
  assert.deepEqual(answers, [{ id: 'a', selected: ['Yes (Recommended)'] }])
})

test('each question of a multi-question request keeps its own answer', () => {
  const plan = planQuestions([q('a', ['x', 'y']), q('b', ['p', 'q'])])
  const answers = buildAnswers(plan.questions, new Map([[1, 1], [2, 0]]))
  assert.deepEqual(answers, [
    { id: 'a', selected: ['y'] },
    { id: 'b', selected: ['p'] },
  ])
})

test('an unanswered question yields no batch at all', () => {
  // Returning a partial batch would either omit a question (the tool rejects
  // that) or fabricate an empty selection, which the tool reads as "skipped".
  const plan = planQuestions([q('a', ['x']), q('b', ['y'])])
  assert.equal(buildAnswers(plan.questions, new Map([[1, 0]])), undefined)
})

test('an out-of-range digit yields no batch', () => {
  const plan = planQuestions([q('a', ['x', 'y'])])
  assert.equal(buildAnswers(plan.questions, new Map([[1, 7]])), undefined)
})

// ---------------------------------------------------------------- approval

test('the approve label maps to allowed-once', () => {
  assert.equal(approvalOutcomeFor(['允许', '拒绝'], 0, '允许'), 'allowed-once')
})

test('any other option maps to rejected', () => {
  // "Reject" is expressed by choosing the other entry, which is the whole
  // reason this design has no dedicated reject button.
  assert.equal(approvalOutcomeFor(['允许', '拒绝'], 1, '允许'), 'rejected')
})

test('an out-of-range digit is not a decision', () => {
  assert.equal(approvalOutcomeFor(['允许', '拒绝'], 5, '允许'), undefined)
})

test('a missing approve label is not a decision', () => {
  // Without a declared approve label there is no way to tell which option
  // grants access, and guessing would be a security bug.
  assert.equal(approvalOutcomeFor(['A', 'B'], 0, undefined), undefined)
})
