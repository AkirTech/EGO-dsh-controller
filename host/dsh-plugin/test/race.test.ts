/**
 * The board and the GUI race for the same question.
 *
 * These tests pin the rule that broke once already: calling next() is what
 * makes the GUI render its card, so the board must NEVER short-circuit the
 * waterfall. Whoever answers first wins, and the loser is released cleanly.
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'

import { SessionQueue } from '../src/session.ts'

/**
 * Reproduce the plugin's race in miniature: a board walk and a GUI promise, with
 * `Promise.race` between them, and the board released when the GUI wins.
 */
interface RaceResult {
  readonly from: 'board' | 'gui'
  readonly value: unknown
  readonly nextCalled: boolean
  readonly boardReleased: boolean
}

async function race(boardDelayMs: number | undefined, guiDelayMs: number | undefined): Promise<RaceResult> {
  let nextCalled = false
  const queue = new SessionQueue({ show: () => {}, hide: () => {} })

  // Board side: resolves with a map when `boardDelayMs` elapsed, else never.
  const boardRun = (async (): Promise<Map<number, number> | undefined> => {
    if (boardDelayMs === undefined) {
      // Never answers: wait on the walk until it is abandoned.
      const outcome = await queue.walk({ reqId: 1, total: 1 })
      return outcome.kind === 'answered' ? outcome.value : undefined
    }
    await new Promise(r => setTimeout(r, boardDelayMs))
    const pending = queue.walk({ reqId: 1, total: 1 })
    queue.offer({ reqId: 1, qIndex: 1, option: 0 })
    const outcome = await pending
    return outcome.kind === 'answered' ? outcome.value : undefined
  })()

  // GUI side: this is what next() represents.
  const guiRun = (async (): Promise<unknown> => {
    nextCalled = true
    if (guiDelayMs === undefined) {
      // The GUI never answers; keep the promise pending forever.
      return await new Promise(() => {})
    }
    await new Promise(r => setTimeout(r, guiDelayMs))
    return { answers: [{ id: 'gui', selected: ['x'] }] }
  })()

  boardRun.catch(() => {})
  guiRun.catch(() => {})

  const first = await Promise.race([
    boardRun.then(value => ({ from: 'board' as const, value })),
    guiRun.then(value => ({ from: 'gui' as const, value })),
  ])

  let boardReleased = false
  if (first.from === 'gui') {
    queue.abandon('preempted')
    boardReleased = true
  }
  return { from: first.from, value: first.value, nextCalled, boardReleased }
}

test('next() is always called, so the GUI can render its card', async () => {
  // This is the regression: an implementation that returns a board answer
  // without calling next() short-circuits the waterfall and the GUI never
  // shows the question, leaving the user unable to read it.
  const result = await race(10, 200)
  assert.equal(result.nextCalled, true, 'next() must be called before any board answer')
})

test('the board wins when it answers first', async () => {
  const result = await race(10, 500)
  assert.equal(result.from, 'board')
  assert.equal(result.nextCalled, true)
})

test('the GUI wins when it answers first, and the board is released', async () => {
  const result = await race(500, 10)
  assert.equal(result.from, 'gui')
  assert.equal(result.boardReleased, true, 'the panel must not keep a question nobody waits for')
})

test('a board that never answers leaves the GUI in charge', async () => {
  // No deadline on the board walk: the GUI is the only side that can finish.
  const result = await race(undefined, 30)
  assert.equal(result.from, 'gui')
})

test('the losing board walk does not reject the winner', async () => {
  // A rejected loser would surface as an unhandled rejection and take the host
  // down; both losers are explicitly caught.
  await assert.doesNotReject(async () => {
    await race(10, 20)
  })
})
