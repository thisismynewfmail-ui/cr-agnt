import { describe, expect, it } from 'vitest'

import {
  normalizeCurieOpenString,
  pathFromCurieDeepLink,
  pathFromOpenDeepLink,
  resolveCurieOpenPath
} from './curie-open-target'

describe('normalizeCurieOpenString', () => {
  it('accepts hash-router paths and strips a leading hash', () => {
    expect(normalizeCurieOpenString('/index-network/intent/1')).toBe('/index-network/intent/1')
    expect(normalizeCurieOpenString('#/index-network/intent/1')).toBe('/index-network/intent/1')
  })

  it('maps plugin-scoped curie:// deep links to the same path', () => {
    expect(normalizeCurieOpenString('curie://index-network/intent/1')).toBe('/index-network/intent/1')
    expect(normalizeCurieOpenString('curie://index-network/intent/1?focus=true')).toBe(
      '/index-network/intent/1?focus=true'
    )
  })

  it('maps curie://open/… deep links by stripping the open host', () => {
    expect(normalizeCurieOpenString('curie://open/index-network/intent/1')).toBe('/index-network/intent/1')
    expect(normalizeCurieOpenString('curie://open/settings/plugins')).toBe('/settings/plugins')
  })

  it('rejects reserved curie kinds and unsafe paths', () => {
    expect(normalizeCurieOpenString('curie://blueprint/morning-brief')).toBeNull()
    expect(normalizeCurieOpenString('curie://plugin/install')).toBeNull()
    expect(normalizeCurieOpenString('https://example.com/x')).toBeNull()
    expect(normalizeCurieOpenString('/../etc/passwd')).toBeNull()
    expect(normalizeCurieOpenString('index-network')).toBeNull()
  })
})

describe('resolveCurieOpenPath', () => {
  it('merges structured path + params', () => {
    expect(resolveCurieOpenPath({ path: '/index-network/intent/1', params: { focus: 'true' } })).toBe(
      '/index-network/intent/1?focus=true'
    )
  })

  it('resolves href the same as a bare string', () => {
    expect(resolveCurieOpenPath({ href: 'curie://index-network/intent/1' })).toBe('/index-network/intent/1')
  })
})

describe('pathFromCurieDeepLink', () => {
  it('builds the navigate path from a plugin-scoped deep-link payload', () => {
    expect(pathFromCurieDeepLink('index-network', 'intent/1')).toBe('/index-network/intent/1')
  })

  it('builds the navigate path from curie://open/… payloads', () => {
    expect(pathFromOpenDeepLink('index-network/intent/1')).toBe('/index-network/intent/1')
    expect(pathFromCurieDeepLink('open', 'agent/42')).toBe('/agent/42')
  })

  it('ignores reserved kinds', () => {
    expect(pathFromCurieDeepLink('blueprint', 'morning-brief')).toBeNull()
    expect(pathFromCurieDeepLink('plugin', 'install')).toBeNull()
  })
})
