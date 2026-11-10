import { useCallback } from 'react'
import { navigate } from '../router'

/**
 * An anchor that navigates in-app.
 *
 * Its own file rather than living beside the router because a module that
 * exports both a component and plain functions breaks Fast Refresh's
 * granularity — editing a route constant would remount the whole tree.
 *
 * It stays a real `<a href>`, and modified or middle clicks are left alone,
 * so "open in new tab" keeps working.
 */
export default function Link({
  to,
  className,
  children,
  ...rest
}: {
  to: string
  className?: string
  children: React.ReactNode
} & React.AnchorHTMLAttributes<HTMLAnchorElement>) {
  const onClick = useCallback(
    (event: React.MouseEvent<HTMLAnchorElement>) => {
      // Modified clicks and middle clicks stay real navigations, so
      // "open in new tab" keeps working on what is a genuine <a href>.
      if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.shiftKey) return
      if (event.button !== 0) return
      event.preventDefault()
      navigate(to)
    },
    [to],
  )

  return (
    <a href={to} className={className} onClick={onClick} {...rest}>
      {children}
    </a>
  )
}
