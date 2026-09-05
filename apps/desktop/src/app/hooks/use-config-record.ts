import { useQuery } from '@tanstack/react-query'

import { getCurieConfigRecord, type ProfileScope, profileScopeKey } from '@/curie'
import { queryClient, writeCache } from '@/lib/query-client'
import type { CurieConfigRecord } from '@/types/curie'

// One shared cache for the whole profile config record (`GET /api/config`).
// Every settings surface (MCP, model, config) reads and writes through this key
// so a save in one shows in the others, and revisiting a tab paints the cache
// instead of blanking on a fresh fetch.
//
// Distinct from session/hooks/use-curie-config.ts, which is side-effecting —
// it pushes personality/cwd/voice/… into the session stores for live chat.
export const CURIE_CONFIG_KEY = ['curie-config-record'] as const

// Per-scope cache key. The base key (no suffix) is the app-wide active
// profile, unchanged for every caller that passes nothing. An explicit scope —
// the Capabilities scope selector configuring ANOTHER profile, possibly on
// another registered gateway — gets its own suffixed key so switching the
// selector refetches and never paints stale cross-profile config (the
// AGENTS.md scope-in-key rule). profileScopeKey folds a remote pin's
// connection id into the suffix, so two gateways' same-named profiles never
// share a cache row.
export const curieConfigKey = (profile?: ProfileScope) =>
  profile == null ? CURIE_CONFIG_KEY : ([...CURIE_CONFIG_KEY, profileScopeKey(profile)] as const)

// staleTime 0 → serve cache instantly, background-revalidate on every mount.
// `profile` scopes both the query key and the fetch; omitting it preserves the
// exact app-wide behavior (base key, `profileScoped(undefined)` fallback).
export const useCurieConfigRecord = (profile?: ProfileScope) =>
  useQuery({
    queryKey: curieConfigKey(profile),
    // null/undefined both mean "no override" → fetch with undefined so
    // capabilityScoped falls back to the app-wide active profile (passing null
    // would wrongly target the primary backend).
    queryFn: () => getCurieConfigRecord(profile ?? undefined),
    staleTime: 0
  })

// setCurieConfigCache writes the app-wide (base-key) record. Pass a profile to
// write the suffixed per-profile cache instead — keeps the selector's optimistic
// write-through landing on the same key its query reads.
export const setCurieConfigCache = writeCache<CurieConfigRecord>(CURIE_CONFIG_KEY)
export const curieConfigCacheWriter = (profile?: ProfileScope) =>
  writeCache<CurieConfigRecord>(curieConfigKey(profile))

export const invalidateCurieConfig = (profile?: ProfileScope) =>
  queryClient.invalidateQueries({ queryKey: curieConfigKey(profile) })
