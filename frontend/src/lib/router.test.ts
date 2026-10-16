/**
 * The hash router.
 *
 * Pure parsing, so the cases that matter are cheap to state: the two real routes, the
 * landing hash, and the inputs that must not be mistaken for a route — a malformed item id
 * in particular, because a URL that looks like an item but is not one should not produce a
 * request with a nonsense id.
 */

import { describe, expect, it } from 'vitest'

import { ITEM_ID } from '../test/fixtures'
import { parseHash, pathFor } from './router'

describe('parseHash', () => {
  it('treats the empty hash as the landing route', () => {
    expect(parseHash('')).toEqual({ name: 'home' })
    expect(parseHash('#')).toEqual({ name: 'home' })
    expect(parseHash('#/')).toEqual({ name: 'home' })
  })

  it('parses a team item list', () => {
    expect(parseHash('#/teams/platform')).toEqual({ name: 'items', teamKey: 'platform' })
  })

  it('parses one item', () => {
    expect(parseHash(`#/teams/platform/items/${ITEM_ID}`)).toEqual({
      name: 'item',
      teamKey: 'platform',
      itemId: ITEM_ID,
    })
  })

  it('parses the team activity feed', () => {
    expect(parseHash('#/teams/platform/activity')).toEqual({
      name: 'teamActivity',
      teamKey: 'platform',
    })
  })

  it('refuses an item id that is not UUID-shaped', () => {
    expect(parseHash('#/teams/platform/items/not-an-id')).toEqual({
      name: 'notFound',
      path: '/teams/platform/items/not-an-id',
    })
  })

  it('refuses unknown shapes instead of guessing', () => {
    expect(parseHash('#/nope')).toEqual({ name: 'notFound', path: '/nope' })
    expect(parseHash('#/teams')).toEqual({ name: 'notFound', path: '/teams' })
    expect(parseHash('#/teams/platform/items')).toEqual({
      name: 'notFound',
      path: '/teams/platform/items',
    })
    expect(parseHash(`#/teams/platform/items/${ITEM_ID}/extra`)).toEqual({
      name: 'notFound',
      path: `/teams/platform/items/${ITEM_ID}/extra`,
    })
  })
})

describe('pathFor', () => {
  it('round-trips every route it produces', () => {
    const routes = [
      { name: 'home' },
      { name: 'items', teamKey: 'platform' },
      { name: 'item', teamKey: 'platform', itemId: ITEM_ID },
      { name: 'teamActivity', teamKey: 'platform' },
    ] as const

    for (const route of routes) {
      expect(parseHash(pathFor(route))).toEqual(route)
    }
  })
})
