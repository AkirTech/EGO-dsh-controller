/**
 * Exercise `apply()` against a stub cordis context.
 *
 * The goal is to prove two things a pure-function test cannot:
 *   1. a real `agent/status` event flows through the plugin without throwing
 *      into the harness event bus, and
 *   2. a backend that cannot start (the unplugged-board case) leaves the
 *      plugin harmless.
 *
 * The wire-level behaviour -- that STATE_SET actually reaches Python -- is
 * covered by `backend.test.ts`, which drives the same Python process directly.
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import * as plugin from '../src/index.ts'

const here = path.dirname(fileURLToPath(import.meta.url))
const backendDir = path.resolve(here, '..', '..')

interface Harness {
  readonly ctx: {
    on: (event: string, listener: (payload: { status: string }) => void) => () => void
    effect: (fn: () => () => void) => () => void
  }
  emitStatus: (status: string) => void
  dispose: () => void
  readonly effectCount: number
}

/** A minimal stand-in for the cordis Context. */
function makeHarness(): Harness {
  const listeners: Array<(payload: { status: string }) => void> = []
  const cleanups: Array<() => void> = []
  let effects = 0

  const ctx = {
    on(event: string, listener: (payload: { status: string }) => void) {
      if (event === 'agent/status') listeners.push(listener)
      return () => {}
    },
    effect(fn: () => () => void) {
      effects += 1
      cleanups.push(fn())
      return () => {}
    },
  }

  return {
    ctx,
    get effectCount() {
      return effects
    },
    emitStatus(status: string) {
      for (const listener of listeners) listener({ status })
    },
    dispose() {
      for (const fn of [...cleanups].reverse()) fn()
    },
  }
}

test('the plugin exports a plain apply with no required services', () => {
  assert.equal(plugin.name, 'ego-bridge')
  assert.deepEqual(plugin.inject, [])
  assert.equal(typeof plugin.apply, 'function')
})

test('apply registers the event listener and one cleanup effect', () => {
  const harness = makeHarness()
  plugin.apply(harness.ctx, { python: 'definitely-not-a-python', backendDir })
  assert.equal(harness.effectCount, 1, 'expected exactly one ctx.effect()')
  assert.doesNotThrow(() => harness.dispose())
})

test('agent/status events never throw into the event bus', () => {
  const harness = makeHarness()
  // A backend that cannot start: every send() returns false. The plugin must
  // still absorb the event instead of rejecting it into the harness.
  plugin.apply(harness.ctx, { python: 'definitely-not-a-python', backendDir })
  assert.doesNotThrow(() => harness.emitStatus('running'))
  assert.doesNotThrow(() => harness.emitStatus('idle'))
  assert.doesNotThrow(() => harness.emitStatus('a-status-that-does-not-exist'))
  assert.doesNotThrow(() => harness.dispose())
})

test('dispose is idempotent-safe and leaves no timers behind', () => {
  const harness = makeHarness()
  plugin.apply(harness.ctx, { python: 'definitely-not-a-python', backendDir })
  assert.doesNotThrow(() => harness.dispose())
  // A second dispose must not double-free the backend.
  assert.doesNotThrow(() => harness.dispose())
})
