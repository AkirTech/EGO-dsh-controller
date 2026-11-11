/**
 * Unit tests for the pure mapping logic.
 *
 * These run without a cordis context at all: `state.ts` and `time.ts` are
 * deliberately pure so the rules that matter (which lamp, which weekday,
 * when to resync) can be tested without a serial port or a Python process.
 *
 * Run with `node --test lib/` after `tsc -p tsconfig.json`.
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'

import {
  BOARD_FREE,
  BOARD_RUNNING,
  BOARD_WAITING,
  clampByte,
  clampSession,
  deriveBoardState,
  sameBoardState,
} from '../src/state.ts'
import {
  boardRestarted,
  boardTimeFrom,
  timeIsValid,
} from '../src/time.ts'

// ---------------------------------------------------------------- state

test('free when idle with nothing pending', () => {
  const s = deriveBoardState(false, 0)
  assert.equal(s.state, BOARD_FREE)
  assert.equal(s.pending, 0)
})

test('running while a turn is in progress', () => {
  assert.equal(deriveBoardState(true, 0).state, BOARD_RUNNING)
})

test('waiting outranks running when something is pending', () => {
  // This is the whole point of the three-state panel: a blocked agent is
  // "running" from the host's point of view but must ask for a button.
  assert.equal(deriveBoardState(true, 1).state, BOARD_WAITING)
})

test('waiting while idle but something is pending', () => {
  assert.equal(deriveBoardState(false, 2).state, BOARD_WAITING)
})

test('pending count is clamped to the wire byte', () => {
  assert.equal(deriveBoardState(true, 300).pending, 255)
  assert.equal(deriveBoardState(true, -5).pending, 0)
  assert.equal(deriveBoardState(true, Number.NaN).pending, 0)
})

test('session is clamped to the wire nibble', () => {
  assert.equal(clampSession(20), 15)
  assert.equal(clampSession(-1), 0)
  assert.equal(clampByte(999), 255)
})

test('sameBoardState compares all three fields', () => {
  const a = deriveBoardState(true, 0, 1)
  assert.equal(sameBoardState(a, deriveBoardState(true, 0, 1)), true)
  assert.equal(sameBoardState(a, deriveBoardState(true, 0, 2)), false)
  assert.equal(sameBoardState(a, deriveBoardState(false, 0, 1)), false)
  assert.equal(sameBoardState(undefined, a), false)
})

// ---------------------------------------------------------------- time

test('weekday is Monday=1 .. Sunday=7', () => {
  // 2026-11-09 is a Monday.
  assert.equal(boardTimeFrom(new Date(2026, 10, 9, 12, 0, 0)).day, 1)
  // 2026-11-14 is a Saturday.
  assert.equal(boardTimeFrom(new Date(2026, 10, 14, 12, 0, 0)).day, 6)
  // 2026-11-15 is a Sunday: the value that would be 0 under getDay().
  assert.equal(boardTimeFrom(new Date(2026, 10, 15, 12, 0, 0)).day, 7)
})

test('time fields come from local wall-clock time', () => {
  const d = new Date(2026, 10, 7, 23, 59, 58)
  const t = boardTimeFrom(d)
  assert.deepEqual({ h: t.hour, m: t.minute, s: t.second }, { h: 23, m: 59, s: 58 })
})

test('flags bit1 means the board has a valid time', () => {
  assert.equal(timeIsValid(0b000), false)
  assert.equal(timeIsValid(0b010), true)
  assert.equal(timeIsValid(0b111), true)
})

test('a backwards uptime means the board restarted', () => {
  assert.equal(boardRestarted(undefined, 0), false)
  assert.equal(boardRestarted(10, 11), false)
  assert.equal(boardRestarted(10, 10), false)
  // Board reset while the PC kept the port open.
  assert.equal(boardRestarted(500, 0), true)
})
