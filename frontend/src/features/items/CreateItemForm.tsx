/**
 * Creating a work item.
 *
 * The `Idempotency-Key` is generated **once, when the form is submitted**, and travels as
 * a mutation variable. That is what makes the retry policy in `query/client.ts` safe: a
 * retried request carries the same key and the server replays the first result instead of
 * creating a second item. Generating the key inside the request function would defeat the
 * whole mechanism, so it is worth being explicit about where it comes from.
 *
 * The assignee list is the team's own roster. Work can only be assigned to a member of the
 * item's team — the database enforces it with a composite foreign key — so offering
 * anybody else would be offering a `422`.
 */

import { type FormEvent, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '../../api/client'
import type { Priority, WorkItemType } from '../../api/types'
import { ErrorState } from '../../components/states'
import { PRIORITIES, TYPES, priorityLabel, typeLabel } from '../../lib/format'
import { newIdempotencyKey } from '../../lib/idempotency'
import { keys } from '../../query/client'
import { can } from '../../lib/permissions'
import type { Role } from '../../api/types'

interface CreateVariables {
  idempotencyKey: string
  title: string
  description: string
  type: WorkItemType
  priority: Priority
  assigneeId: string | null
}

export function CreateItemForm({
  teamId,
  role,
  onCreated,
}: {
  teamId: string
  role: Role
  onCreated: (itemId: string) => void
}) {
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [type, setType] = useState<WorkItemType>('task')
  const [priority, setPriority] = useState<Priority>('medium')
  const [assigneeId, setAssigneeId] = useState('')

  const members = useQuery({
    queryKey: keys.members(teamId),
    queryFn: ({ signal }) => api.members(teamId, signal),
    enabled: open,
  })

  const create = useMutation({
    // Safe to retry: the key is part of the variables, so every attempt is the same
    // logical operation and the server will replay rather than duplicate.
    retry: 2,
    mutationFn: (variables: CreateVariables) =>
      api.createItem(
        teamId,
        {
          title: variables.title,
          description: variables.description,
          type: variables.type,
          priority: variables.priority,
          assignee_id: variables.assigneeId,
        },
        variables.idempotencyKey,
      ),
    onSuccess: async (item) => {
      await queryClient.invalidateQueries({ queryKey: ['teams', teamId, 'items'] })
      await queryClient.invalidateQueries({ queryKey: keys.teamActivity(teamId) })
      reset()
      setOpen(false)
      onCreated(item.id)
    },
  })

  function reset() {
    setTitle('')
    setDescription('')
    setType('task')
    setPriority('medium')
    setAssigneeId('')
    create.reset()
  }

  if (!can(role, 'create_item')) return null

  if (!open) {
    return (
      <button type="button" className="button button--primary" onClick={() => setOpen(true)}>
        New work item
      </button>
    )
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    create.mutate({
      idempotencyKey: newIdempotencyKey(),
      title,
      description,
      type,
      priority,
      assigneeId: assigneeId === '' ? null : assigneeId,
    })
  }

  return (
    <form className="card create-form" onSubmit={submit} aria-label="New work item">
      <h2 className="card__title">New work item</h2>

      <label className="field">
        <span className="field__label">Title</span>
        <input
          name="title"
          required
          maxLength={200}
          value={title}
          onChange={(event) => setTitle(event.target.value)}
        />
      </label>

      <label className="field">
        <span className="field__label">Description</span>
        <textarea
          name="description"
          rows={3}
          maxLength={20000}
          value={description}
          onChange={(event) => setDescription(event.target.value)}
        />
      </label>

      <div className="field-row">
        <label className="field">
          <span className="field__label">Type</span>
          <select value={type} onChange={(event) => setType(event.target.value as WorkItemType)}>
            {TYPES.map((value) => (
              <option key={value} value={value}>
                {typeLabel(value)}
              </option>
            ))}
          </select>
        </label>

        <label className="field">
          <span className="field__label">Priority</span>
          <select
            value={priority}
            onChange={(event) => setPriority(event.target.value as Priority)}
          >
            {PRIORITIES.map((value) => (
              <option key={value} value={value}>
                {priorityLabel(value)}
              </option>
            ))}
          </select>
        </label>

        <label className="field">
          <span className="field__label">Assignee</span>
          <select
            value={assigneeId}
            disabled={members.isPending}
            onChange={(event) => setAssigneeId(event.target.value)}
          >
            <option value="">Unassigned</option>
            {(members.data ?? []).map((member) => (
              <option key={member.user_id} value={member.user_id}>
                {member.display_name}
              </option>
            ))}
          </select>
        </label>
      </div>

      {members.isError ? <ErrorState error={members.error} /> : null}
      {create.isError ? <ErrorState error={create.error} /> : null}

      <div className="card__actions">
        <button type="submit" className="button button--primary" disabled={create.isPending}>
          {create.isPending ? 'Creating…' : 'Create item'}
        </button>
        <button
          type="button"
          className="button button--quiet"
          onClick={() => {
            reset()
            setOpen(false)
          }}
        >
          Cancel
        </button>
      </div>
    </form>
  )
}
