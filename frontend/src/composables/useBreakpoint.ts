/**
 * Responsive breakpoint composable.
 *
 * Returns a reactive `isDesktop` ref that is `true` when the viewport is at
 * least 992 px wide (Naive UI's `l` breakpoint), and `false` below that.
 *
 * Pattern follows `composables/useTheme.ts`: plain `window.matchMedia` with a
 * change listener that is cleaned up on component unmount.
 *
 * No external dependencies are added.
 */

import { ref, onMounted, onUnmounted } from 'vue'

const DESKTOP_QUERY = '(min-width: 992px)'

export function useBreakpoint() {
  const isDesktop = ref(
    typeof window !== 'undefined'
      ? window.matchMedia(DESKTOP_QUERY).matches
      : true,
  )

  let mediaQuery: MediaQueryList | null = null

  function handleChange(e: MediaQueryListEvent) {
    isDesktop.value = e.matches
  }

  onMounted(() => {
    mediaQuery = window.matchMedia(DESKTOP_QUERY)
    isDesktop.value = mediaQuery.matches
    mediaQuery.addEventListener('change', handleChange)
  })

  onUnmounted(() => {
    mediaQuery?.removeEventListener('change', handleChange)
  })

  return { isDesktop }
}
