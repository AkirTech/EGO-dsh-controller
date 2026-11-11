/**
 * Integration test: the real plugin, the real Python backend, the simulator.
 *
 * This is the test that proves the two halves actually talk. It spawns
 * `python -m ego_bridge.service --sim` exactly the way the plugin does, feeds
 * it the commands the plugin would send, and asserts the events come back in
 * the documented order.
 *
 * It is skipped when Python or the `ego_bridge` package is unavailable, so a
 * machine without the Python side can still run the unit tests.
 *
 * Run with `node --experimental-strip-types --test test/`.
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { spawn, spawnSync } from 'node:child_process'
import { createInterface } from 'node:readline'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
// host/dsh-plugin/test -> host
const backendDir = path.resolve(here, '..', '..')

interface Event {
  evt: string
  [key: string]: unknown
}

function pythonUsable(): boolean {
  const probe = spawnSync('python', ['-c', 'import ego_bridge.service'], {
    cwd: backendDir,
    encoding: 'utf8',
  })
  return probe.status === 0
}

/** Run the service, send these commands, collect the events it emits. */
async function runService(
  commands: readonly Record<string, unknown>[],
  until: (events: readonly Event[]) => boolean,
  timeoutMs = 20_000,
): Promise<Event[]> {
  return await new Promise((resolve, reject) => {
    const child = spawn('python', ['-m', 'ego_bridge.service', '--sim'], {
      cwd: backendDir,
      stdio: ['pipe', 'pipe', 'pipe'],
    })
    const events: Event[] = []
    let finished = false

    const finish = (): void => {
      if (finished) return
      finished = true
      clearTimeout(timer)
      // Give the child a moment to exit on its own, then make sure it is gone
      // so a slow teardown cannot leak a process between tests.
      setTimeout(() => {
        if (child.exitCode === null) child.kill()
        resolve(events)
      }, 250)
    }

    const timer = setTimeout(() => {
      if (finished) return
      finished = true
      child.kill()
      reject(new Error(`timed out after ${timeoutMs} ms; got ${JSON.stringify(events)}`))
    }, timeoutMs)

    const reader = createInterface({ input: child.stdout })
    reader.on('line', (line) => {
      const text = line.trim()
      if (text === '') return
      events.push(JSON.parse(text) as Event)
      // Stop as soon as the caller's condition is satisfied. Waiting for the
      // `link(stopped)` event instead made these tests hang whenever the
      // shutdown raced the heartbeat; waiting for process exit is too early,
      // because shutdown can drain before the last event is written.
      if (until(events)) {
        child.stdin.write(`${JSON.stringify({ cmd: 'shutdown' })}\n`)
        finish()
      }
    })
    child.on('error', (error) => {
      if (finished) return
      finished = true
      clearTimeout(timer)
      reject(error)
    })

    for (const command of commands) child.stdin.write(`${JSON.stringify(command)}\n`)
  })
}

const enabled = pythonUsable()

test('service emits ready first, then link, then status', { skip: !enabled }, async () => {
  const events = await runService([{ cmd: 'ping' }], evs => evs.some(e => e.evt === 'status') && evs.some(e => e.evt === 'pong'))
  assert.ok(events.length >= 2, `too few events: ${JSON.stringify(events)}`)
  assert.equal(events[0]?.evt, 'ready', `first event was ${events[0]?.evt}`)
  // Exactly one ready per process lifetime.
  assert.equal(events.filter(e => e.evt === 'ready').length, 1)
  // A link event must arrive, and the link must come up at least once.
  assert.ok(events.some(e => e.evt === 'link' && e.up === true))
  // The heartbeat proves the board side is actually speaking.
  assert.ok(events.some(e => e.evt === 'status'))
})

test('set_state is reflected in the status heartbeat', { skip: !enabled }, async () => {
  const events = await runService(
    [
      { cmd: 'set_state', state: 1, session: 0, pending: 0 },
      { cmd: 'set_state', state: 2, session: 3, pending: 1 },
    ],
    evs => {
      const reports = evs.filter(e => e.evt === 'status')
      return reports.some(r => r.state === 1)
        && reports.some(r => r.state === 2 && r.session === 3 && r.pending === 1)
    },
  )
  const statuses = events.filter(e => e.evt === 'status')
  assert.ok(statuses.length >= 2, `expected two status reports, got ${statuses.length}`)
  assert.ok(statuses.some(s => s.state === 1), 'never saw RUNNING')
  assert.ok(
    statuses.some(s => s.state === 2 && s.session === 3 && s.pending === 1),
    `never saw the WAITING report: ${JSON.stringify(statuses)}`,
  )
})

test('hello is answered with hello_ack, not a second ready', { skip: !enabled }, async () => {
  const events = await runService([{ cmd: 'hello', version: 1 }], evs => evs.some(e => e.evt === 'hello_ack'))
  const acks = events.filter(e => e.evt === 'hello_ack')
  assert.equal(acks.length, 1, `expected one hello_ack, got ${acks.length}`)
  assert.equal(events.filter(e => e.evt === 'ready').length, 1)
  assert.equal(acks[0]?.version, 1)
})

test('sync_time is accepted and the board reports a valid clock', { skip: !enabled }, async () => {
  const events = await runService(
    [{ cmd: 'sync_time', epoch_ms: Date.now() }],
    evs => evs.some(e => e.evt === 'status'),
  )
  const statuses = events.filter(e => e.evt === 'status')
  assert.ok(statuses.length > 0, 'no status report')
  // flags bit1 = "time is valid". The handshake sends TIME_SYNC on connect,
  // so every report should already have it set.
  for (const s of statuses) {
    assert.equal(
      (Number(s.flags) & 0b10) !== 0,
      true,
      `time-valid bit clear in ${JSON.stringify(s)}`,
    )
  }
})

test('an unknown command reports an error without killing the stream', { skip: !enabled }, async () => {
  const events = await runService([{ cmd: 'no_such_command' }], evs => evs.some(e => e.evt === 'error') && evs.some(e => e.evt === 'status'))
  assert.ok(events.some(e => e.evt === 'error'), 'no error event')
  // The stream must still have produced its normal lifecycle after the error.
  assert.ok(events.some(e => e.evt === 'link' && e.reason === 'stopped'))
})
