/**
 * A value that trails behind its input.
 *
 * Used for the search box: typing should not fire a request per keystroke, and the request
 * must still be the server's (the list is filtered in PostgreSQL, never in the browser).
 * The delay is short enough that the result feels immediate.
 */

import { useEffect, useState } from 'react'

export function useDebouncedValue<T>(value: T, delayMs = 250): T {
  const [settled, setSettled] = useState(value)

  useEffect(() => {
    const timer = setTimeout(() => {
      setSettled(value)
    }, delayMs)
    return () => {
      clearTimeout(timer)
    }
  }, [value, delayMs])

  return settled
}
