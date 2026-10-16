/**
 * Labels and formatting.
 *
 * The API stores values, not prose: `in_progress`, `customer_issue`, `urgent`. Turning
 * them into words is a presentation concern and lives here, so a status can be renamed
 * without touching a component, and so there is exactly one place that knows the order
 * of the workflow.
 */

import type { ActivityType, Priority, Role, Status, WorkItemType } from '../api/types'

export const STATUS_ORDER: Status[] = [
  'open',
  'in_progress',
  'awaiting_approval',
  'resolved',
  'closed',
]

const STATUS_LABELS: Record<Status, string> = {
  open: 'Open',
  in_progress: 'In progress',
  awaiting_approval: 'Awaiting approval',
  resolved: 'Resolved',
  closed: 'Closed',
}

const PRIORITY_LABELS: Record<Priority, string> = {
  low: 'Low',
  medium: 'Medium',
  high: 'High',
  urgent: 'Urgent',
}

const TYPE_LABELS: Record<WorkItemType, string> = {
  incident: 'Incident',
  customer_issue: 'Customer issue',
  payment: 'Payment',
  compliance: 'Compliance',
  task: 'Task',
}

const ROLE_LABELS: Record<Role, string> = {
  viewer: 'Viewer',
  member: 'Member',
  lead: 'Lead',
  admin: 'Admin',
}

const ACTIVITY_LABELS: Record<ActivityType, string> = {
  created: 'created this item',
  claimed: 'claimed this item',
  assigned: 'assigned this item',
  unassigned: 'unassigned this item',
  status_changed: 'changed the status',
  approved: 'approved this item',
  rejected: 'sent this item back',
  priority_changed: 'changed the priority',
  due_date_changed: 'changed the due date',
  type_changed: 'changed the type',
  title_changed: 'changed the title',
  description_changed: 'changed the description',
  commented: 'commented',
}

export const statusLabel = (status: Status): string => STATUS_LABELS[status]
export const priorityLabel = (priority: Priority): string => PRIORITY_LABELS[priority]
export const typeLabel = (type: WorkItemType): string => TYPE_LABELS[type]
export const roleLabel = (role: Role): string => ROLE_LABELS[role]
export const activityLabel = (type: ActivityType): string => ACTIVITY_LABELS[type]

export const PRIORITIES: Priority[] = ['low', 'medium', 'high', 'urgent']
export const TYPES: WorkItemType[] = ['incident', 'customer_issue', 'payment', 'compliance', 'task']

/** The label for a transition target, phrased as the action the button performs. */
const TRANSITION_LABELS: Partial<Record<Status, string>> = {
  in_progress: 'Start work',
  awaiting_approval: 'Submit for approval',
  resolved: 'Approve',
  closed: 'Close',
  open: 'Reopen',
}

export const transitionLabel = (target: Status): string =>
  TRANSITION_LABELS[target] ?? `Move to ${statusLabel(target)}`

export function formatDateTime(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function formatDate(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
}

const RELATIVE_UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ['year', 365 * 24 * 60 * 60 * 1000],
  ['month', 30 * 24 * 60 * 60 * 1000],
  ['day', 24 * 60 * 60 * 1000],
  ['hour', 60 * 60 * 1000],
  ['minute', 60 * 1000],
]

export function formatRelative(value: string, now: Date = new Date()): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const delta = date.getTime() - now.getTime()
  const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' })
  for (const [unit, milliseconds] of RELATIVE_UNITS) {
    if (Math.abs(delta) >= milliseconds) {
      return formatter.format(Math.round(delta / milliseconds), unit)
    }
  }
  return formatter.format(Math.round(delta / 1000), 'second')
}

/** An ISO date for a `datetime-local` input, in the browser's own time zone. */
export function toLocalInputValue(value: string | null): string {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  const pad = (part: number) => String(part).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(
    date.getHours(),
  )}:${pad(date.getMinutes())}`
}

/** The inverse: a `datetime-local` value as an ISO string the API will accept. */
export function fromLocalInputValue(value: string): string | null {
  if (!value) return null
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? null : date.toISOString()
}
