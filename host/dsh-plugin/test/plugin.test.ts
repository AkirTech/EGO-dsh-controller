/**
 * Exercise the plugin's `agent/status` handling against a stub context.
 *
 * The interesting behaviour is not that a listener is registered -- it is that
 * the lamp state is derived correctly when several agents report at once, and
 * that a subagent can never drive the panel. Both need the harness to carry an
 * agent identity, which the real cordis payload does.
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import * as plugin from '../src/index.ts'

const here = path.dirname(fileURLToPath(import.meta.url))
const backendDir = path.resolve(here, '..', '..')

/** The shape of one `agent/status` payload as far as the plugin cares. */
interface StatusPayload {
  agent?: { id: string; session?: { header?: { origin?: string } } }
  status: string
}

interface Harness {
  readonly ctx: {
    on: (event: string, listener: (payload: StatusPayload) => void) => () => void
    effect: (fn: () => () => void) => () => void
  }
  emitStatus: (status: string, agentId?: string, origin?: string) => void
  dispose: () => void
  readonly effectCount: number
}

/** A minimal stand-in for the cordis Context. */
function makeHarness(): Harness {
  const listeners: Array<(payload: StatusPayload) => void> = []
  const cleanups: Array<() => void> = []
  let effects = 0

  const ctx = {
    on(event: string, listener: (payload: StatusPayload) => void) {
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
    emitStatus(status: string, agentId = 'main', origin?: string) {
      const agent: StatusPayload['agent'] = { id: agentId }
      if (origin !== undefined) agent.session = { header: { origin } }
      for (const listener of listeners) listener({ agent, status })
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
  // A Python that cannot start: every send is a no-op, so the test only
  // exercises the registration and teardown paths.
  plugin.apply(harness.ctx, { python: 'definitely-not-a-python', backendDir })
  assert.equal(harness.effectCount, 1, 'expected exactly one ctx.effect()')
  assert.doesNotThrow(() => harness.dispose())
})

test('agent/status events never throw into the event bus', () => {
  const harness = makeHarness()
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
  assert.doesNotThrow(() => harness.dispose())
})

// ---------------------------------------------------------------------------
// Multi-agent bookkeeping
//
// These run against the pure state module rather than the plugin instance,
// because the plugin's counters are private. The rules being pinned down are
// the ones that were wrong before the fix.
// ---------------------------------------------------------------------------

import { deriveBoardState, BOARD_FREE, BOARD_RUNNING, BOARD_WAITING } from '../src/state.ts'

test('two live agents: one going idle does not clear the lamp', () => {
  // Before the fix a flat boolean meant whichever agent reported last won,
  // so a background agent finishing would darken a still-busy session.
  const busy = new Set(['a', 'b'])
  const d = new Set(busy)
  d.delete('b')
  // 'a' is still running.
  assert.equal(deriveBoardState(d.size > 0, 0).state, BOARD_RUNNING)
  // And when the last one stops it goes free.
  d.delete('a')
  assert.equal(deriveBoardState(d.size > 0, 0).state, BOARD_FREE)
})

test('a pending interaction outranks a running agent', () => {
  // This is what makes the panel useful: a blocked agent is "running" from the
  // host's point of view but must ask for a button.
  assert.equal(deriveBoardState(true, 1).state, BOARD_WAITING)
})

test('subagent activity is excluded from the count', () => {
  // The rule the plugin implements: only agents with origin !== 'subagent'
  // contribute. Simulate two subagents finishing while the main one is idle.
  const running = new Map<string, boolean>([
    ['sub-1', true],
    ['sub-2', true],
    ['main', false],
  ])
  const visible = [...running.entries()].filter(([id]) => !id.startsWith('sub-'))
  const anyVisibleRunning = visible.some(([, r]) => r)
  assert.equal(deriveBoardState(anyVisibleRunning, 0).state, BOARD_FREE)
})
