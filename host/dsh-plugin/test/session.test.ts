/**
 * Tests for the board session queue.
 *
 * The tricky parts are the ones a manual test would miss: stale answers from a
 * previous request, the timeout re-arming per question, and the single-slot
 * invariant when a second request arrives while the first is still waiting.
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'

import { SessionQueue, type SessionOutcome } from '../src/session.ts'

interface Shown {
  readonly reqId: number
  readonly index: number
}

/** Build a queue that records what the plugin would put on the panel. */
function makeQueue(): { queue: SessionQueue; shown: Shown[]; hidden: number[] } {
  const shown: Shown[] = []
  const hidden: number[] = []
  const queue = new SessionQueue({
    show: (reqId, index) => { shown.push({ reqId, index }) },
    hide: (reqId) => { hidden.push(reqId) },
  })
  return { queue, shown, hidden }
}

const answered = (o: SessionOutcome<Map<number, number>>): Map<number, number> => {
  assert.equal(o.kind, 'answered', `expected answered, got ${JSON.stringify(o)}`)
  return (o as { kind: 'answered'; value: Map<number, number> }).value
}

// ---------------------------------------------------------------- single question

test('one question shows immediately and settles on the first answer', async () => {
  const { queue, shown } = makeQueue()
  const pending = queue.walk({ reqId: 5, total: 1 })
  // show() is called synchronously so the board lights up without a tick.
  assert.deepEqual(shown, [{ reqId: 5, index: 1 }])
  assert.equal(queue.busy, true)

  assert.equal(queue.offer({ reqId: 5, qIndex: 1, option: 2 }), true)
  const answers = answered(await pending)
  assert.deepEqual([...answers], [[1, 2]])
  assert.equal(queue.busy, false)
})

// ---------------------------------------------------------------- multi question

test('a three-question walk advances one question at a time', async () => {
  const { queue, shown } = makeQueue()
  const pending = queue.walk({ reqId: 10, total: 3 })

  // Question 1 is on screen; reqId is the base id.
  assert.deepEqual(shown.at(-1), { reqId: 10, index: 1 })
  queue.offer({ reqId: 10, qIndex: 1, option: 0 })
  assert.deepEqual(shown.at(-1), { reqId: 11, index: 2 })

  queue.offer({ reqId: 11, qIndex: 2, option: 1 })
  assert.deepEqual(shown.at(-1), { reqId: 12, index: 3 })

  queue.offer({ reqId: 12, qIndex: 3, option: 2 })
  const answers = answered(await pending)
  assert.deepEqual([...answers.entries()], [[1, 0], [2, 1], [3, 2]])
})

test('an answer for the wrong question is ignored', async () => {
  const { queue } = makeQueue()
  const pending = queue.walk({ reqId: 1, total: 2 })
  // The board is on question 1; a frame claiming question 2 is stale.
  assert.equal(queue.offer({ reqId: 2, qIndex: 2, option: 1 }), false)
  assert.equal(queue.busy, true)
  queue.offer({ reqId: 1, qIndex: 1, option: 0 })
  queue.offer({ reqId: 2, qIndex: 2, option: 1 })
  answered(await pending)
})

test('an answer with the wrong reqId is ignored', async () => {
  const { queue } = makeQueue()
  const pending = queue.walk({ reqId: 7, total: 1 })
  // Same question index, different request: belongs to an earlier request.
  assert.equal(queue.offer({ reqId: 6, qIndex: 1, option: 0 }), false)
  assert.equal(queue.busy, true)
  queue.offer({ reqId: 7, qIndex: 1, option: 0 })
  answered(await pending)
})

test('reqId wraps inside a byte across a long walk', async () => {
  const { queue, shown } = makeQueue()
  const pending = queue.walk({ reqId: 255, total: 2 })
  queue.offer({ reqId: 255, qIndex: 1, option: 0 })
  // Second question wraps to 0.
  assert.deepEqual(shown.at(-1), { reqId: 0, index: 2 })
  queue.offer({ reqId: 0, qIndex: 2, option: 0 })
  answered(await pending)
})

// ---------------------------------------------------------------- abandonment

test('a timeout abandons the walk', async () => {
  const { queue } = makeQueue()
  const outcome = await queue.walk({ reqId: 1, total: 1, timeoutMs: 20 })
  assert.deepEqual(outcome, { kind: 'abandoned', reason: 'timeout' })
  assert.equal(queue.busy, false)
})

test('the timeout re-arms for each question', async () => {
  // A single 60 ms deadline would kill a three-question walk; each answer must
  // start a fresh window so a slow-but-progressing user is not cut off.
  const { queue } = makeQueue()
  const pending = queue.walk({ reqId: 1, total: 3, timeoutMs: 120 })
  await new Promise(r => setTimeout(r, 80))
  queue.offer({ reqId: 1, qIndex: 1, option: 0 })
  await new Promise(r => setTimeout(r, 80))
  // Past the original deadline, but the timer was re-armed.
  assert.equal(queue.busy, true, 'should still be waiting after re-arm')
  queue.offer({ reqId: 2, qIndex: 2, option: 0 })
  queue.offer({ reqId: 3, qIndex: 3, option: 0 })
  answered(await pending)
})

test('an already-aborted signal refuses without occupying the panel', async () => {
  const { queue, shown } = makeQueue()
  const controller = new AbortController()
  controller.abort()
  const outcome = await queue.walk({ reqId: 1, total: 1, signal: controller.signal })
  assert.deepEqual(outcome, { kind: 'abandoned', reason: 'cancelled' })
  assert.equal(queue.busy, false)
  // Nothing was ever drawn on the panel.
  assert.deepEqual(shown, [])
})

test('aborting mid-walk cancels it', async () => {
  const { queue } = makeQueue()
  const controller = new AbortController()
  const pending = queue.walk({ reqId: 1, total: 2, signal: controller.signal })
  controller.abort()
  assert.deepEqual(await pending, { kind: 'abandoned', reason: 'cancelled' })
  assert.equal(queue.busy, false)
})

test('a second request preempts the first', async () => {
  // The panel has one ASK page, so the newest question wins and the older one
  // goes back to the GUI. Without this the older request would hang forever.
  const { queue, shown } = makeQueue()
  const first = queue.walk({ reqId: 1, total: 1 })
  const second = queue.walk({ reqId: 2, total: 1 })

  assert.deepEqual(await first, { kind: 'abandoned', reason: 'preempted' })
  assert.equal(queue.busy, true)
  assert.deepEqual(shown.at(-1), { reqId: 2, index: 1 })

  queue.offer({ reqId: 2, qIndex: 1, option: 0 })
  answered(await second)
})

test('dispose abandons the active request', async () => {
  const { queue } = makeQueue()
  const pending = queue.walk({ reqId: 1, total: 1 })
  queue.dispose()
  assert.deepEqual(await pending, { kind: 'abandoned', reason: 'disposed' })
  assert.equal(queue.busy, false)
})

test('hide is called when the request ends', async () => {
  const { queue, hidden } = makeQueue()
  const pending = queue.walk({ reqId: 4, total: 1 })
  queue.offer({ reqId: 4, qIndex: 1, option: 0 })
  await pending
  assert.deepEqual(hidden, [4], 'the board must be told the question closed')
})

test('an answer while idle is rejected', () => {
  const { queue } = makeQueue()
  assert.equal(queue.offer({ reqId: 1, qIndex: 1, option: 0 }), false)
})
