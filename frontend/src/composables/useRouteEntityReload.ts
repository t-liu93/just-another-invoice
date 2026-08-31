import { watch, type Ref } from 'vue'

/** Latest route entity wins; shared by reused editor views. */
export function useRouteEntityReload(id: Ref<string | undefined>, reset: () => void, load: (id: string | undefined, generation: number) => Promise<void>) {
  let generation = 0
  const reload = async (next = id.value) => {
    const current = ++generation
    reset()
    await load(next, current)
    return current === generation
  }
  watch(id, next => { void reload(next) })
  return { reload, isCurrent: (value: number) => value === generation }
}
