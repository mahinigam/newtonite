/**
 * The role matrix, mirrored from `backend/app/policy/roles.py`.
 *
 * **This is advisory.** It exists so the interface does not offer a control that the
 * server will refuse, and so a reviewer can see the intended boundary in one place. It is
 * never the boundary itself: every write is authorized again on the server against the
 * caller's membership, and the server's answer is what the interface renders. A client
 * that ignored this file entirely would still be correctly refused.
 *
 * Keep it in step with `roles.py`; a divergence shows up as a button that fails with
 * `403`, which is a bug in this file rather than a security hole.
 */

import type { Role } from '../api/types'

export type Permission =
  | 'view_team'
  | 'create_item'
  | 'comment'
  | 'claim_item'
  | 'edit_item'
  | 'transition_work'
  | 'assign_others'
  | 'approve'
  | 'close_item'
  | 'manage_members'

const VIEWER: Permission[] = ['view_team']

const MEMBER: Permission[] = [
  ...VIEWER,
  'create_item',
  'comment',
  'claim_item',
  'edit_item',
  'transition_work',
]

const LEAD: Permission[] = [...MEMBER, 'assign_others', 'approve', 'close_item']

const ADMIN: Permission[] = [...LEAD, 'manage_members']

const MATRIX: Record<Role, Permission[]> = {
  viewer: VIEWER,
  member: MEMBER,
  lead: LEAD,
  admin: ADMIN,
}

export function can(role: Role | null | undefined, permission: Permission): boolean {
  if (!role) return false
  return MATRIX[role].includes(permission)
}
