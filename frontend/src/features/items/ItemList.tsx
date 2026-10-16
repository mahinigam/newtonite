/**
 * The list of work items, as returned by the server.
 *
 * Each row is a link to the item, and the whole row is the target rather than a small
 * "open" link — an operations list is scanned quickly, and the thing being scanned should
 * be the thing you can click.
 *
 * Nothing here sorts or filters. The order is the server's `(created_at, id)` descending
 * keyset order and the filters are the server's; the component's job is to render what it
 * was given and to ask for the next page.
 */

import type { WorkItemSummary } from '../../api/types'
import { formatRelative, priorityLabel, statusLabel, typeLabel } from '../../lib/format'

function Badge({ kind, label }: { kind: string; label: string }) {
  return <span className={`badge badge--${kind}`}>{label}</span>
}

export function ItemRow({ item, teamKey }: { item: WorkItemSummary; teamKey: string }) {
  return (
    <li className="item-row">
      <a className="item-row__link" href={`#/teams/${teamKey}/items/${item.id}`}>
        <div className="item-row__main">
          <span className="item-row__title">{item.title}</span>
          <span className="item-row__meta">
            <Badge kind={`status-${item.status}`} label={statusLabel(item.status)} />
            <Badge kind={`priority-${item.priority}`} label={priorityLabel(item.priority)} />
            <span className="item-row__type">{typeLabel(item.type)}</span>
          </span>
        </div>
        <div className="item-row__side">
          <span className="item-row__assignee">
            {item.assignee ? item.assignee.display_name : 'Unassigned'}
          </span>
          <span className="item-row__updated">updated {formatRelative(item.updated_at)}</span>
        </div>
      </a>
    </li>
  )
}

export function ItemList({ items, teamKey }: { items: WorkItemSummary[]; teamKey: string }) {
  return (
    <ul className="item-list">
      {items.map((item) => (
        <ItemRow key={item.id} item={item} teamKey={teamKey} />
      ))}
    </ul>
  )
}
