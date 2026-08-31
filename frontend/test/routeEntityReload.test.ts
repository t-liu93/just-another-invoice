import assert from 'node:assert/strict'
import test from 'node:test'
import { nextTick, ref } from 'vue'
import { useRouteEntityReload } from '../src/composables/useRouteEntityReload.ts'

test('route entity reload resets once and only the newest id remains current', async () => {
  const id = ref<string | undefined>('A')
  const calls: string[] = []
  const reload = useRouteEntityReload(id, () => calls.push('reset'), async (value, generation) => { calls.push(`${value}:${generation}`) })
  await reload.reload()
  id.value = 'B'
  await nextTick()
  assert.deepEqual(calls, ['reset', 'A:1', 'reset', 'B:2'])
  assert.equal(reload.isCurrent(1), false)
  assert.equal(reload.isCurrent(2), true)
})
