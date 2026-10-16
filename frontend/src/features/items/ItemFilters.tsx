/**
 * Filtering the list, on the server.
 *
 * Every control here changes the *request*. Nothing filters a loaded page, because the
 * browser never holds the whole dataset: a team's items are paged 25 at a time and the
 * filter is applied by PostgreSQL. That is the requirement, and it is also what makes
 * "show me every unassigned urgent item" correct rather than "show me the unassigned
 * urgent items that happen to be in the first page I already have".
 */

import type { ItemFilters, Priority, Status, WorkItemType } from '../../api/types'
import {
  PRIORITIES,
  STATUS_ORDER,
  TYPES,
  priorityLabel,
  statusLabel,
  typeLabel,
} from '../../lib/format'

export const EMPTY_FILTERS: ItemFilters = {
  statuses: [],
  priorities: [],
  types: [],
  assignee: null,
  search: '',
}

/** A stable identity for a filter set, so the query key changes only when it should. */
export function filterKey(filters: ItemFilters): string {
  return JSON.stringify({
    statuses: [...filters.statuses].sort(),
    priorities: [...filters.priorities].sort(),
    types: [...filters.types].sort(),
    assignee: filters.assignee,
    search: filters.search.trim(),
  })
}

export function isFiltered(filters: ItemFilters): boolean {
  return (
    filters.statuses.length > 0 ||
    filters.priorities.length > 0 ||
    filters.types.length > 0 ||
    filters.assignee !== null ||
    filters.search.trim() !== ''
  )
}

function toggle<T>(values: T[], value: T): T[] {
  return values.includes(value) ? values.filter((entry) => entry !== value) : [...values, value]
}

export function ItemFiltersBar({
  filters,
  onChange,
  searchInput,
  onSearchInput,
  resultCount,
}: {
  filters: ItemFilters
  onChange: (next: ItemFilters) => void
  searchInput: string
  onSearchInput: (value: string) => void
  resultCount: number | null
}) {
  return (
    <section className="filters" aria-label="Filters">
      <div className="filters__row">
        <label className="field field--grow">
          <span className="field__label">Search</span>
          <input
            type="search"
            name="q"
            placeholder="Title and description…"
            value={searchInput}
            onChange={(event) => onSearchInput(event.target.value)}
          />
        </label>

        <label className="field">
          <span className="field__label">Assignee</span>
          <select
            value={filters.assignee ?? ''}
            onChange={(event) =>
              onChange({ ...filters, assignee: event.target.value === '' ? null : event.target.value })
            }
          >
            <option value="">Anyone</option>
            <option value="me">Assigned to me</option>
            <option value="none">Unassigned</option>
          </select>
        </label>
      </div>

      <fieldset className="filters__group">
        <legend>Status</legend>
        <div className="filters__chips">
          {STATUS_ORDER.map((status: Status) => (
            <label key={status} className="chip">
              <input
                type="checkbox"
                checked={filters.statuses.includes(status)}
                onChange={() => onChange({ ...filters, statuses: toggle(filters.statuses, status) })}
              />
              <span>{statusLabel(status)}</span>
            </label>
          ))}
        </div>
      </fieldset>

      <fieldset className="filters__group">
        <legend>Priority</legend>
        <div className="filters__chips">
          {PRIORITIES.map((priority: Priority) => (
            <label key={priority} className="chip">
              <input
                type="checkbox"
                checked={filters.priorities.includes(priority)}
                onChange={() =>
                  onChange({ ...filters, priorities: toggle(filters.priorities, priority) })
                }
              />
              <span>{priorityLabel(priority)}</span>
            </label>
          ))}
        </div>
      </fieldset>

      <fieldset className="filters__group">
        <legend>Type</legend>
        <div className="filters__chips">
          {TYPES.map((type: WorkItemType) => (
            <label key={type} className="chip">
              <input
                type="checkbox"
                checked={filters.types.includes(type)}
                onChange={() => onChange({ ...filters, types: toggle(filters.types, type) })}
              />
              <span>{typeLabel(type)}</span>
            </label>
          ))}
        </div>
      </fieldset>

      <div className="filters__footer">
        <span className="filters__count">
          {resultCount === null ? 'Counting…' : `${resultCount} loaded`}
        </span>
        <button
          type="button"
          className="button button--quiet button--small"
          onClick={() => {
            onSearchInput('')
            onChange(EMPTY_FILTERS)
          }}
        >
          Clear filters
        </button>
      </div>
    </section>
  )
}
