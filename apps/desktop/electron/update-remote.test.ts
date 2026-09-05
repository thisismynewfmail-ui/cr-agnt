/**
 * Tests for electron/update-remote.ts — the remote-detection helpers that
 * keep passive update checks off the SSH origin for official installs.
 *
 * Run with: node --test electron/update-remote.test.ts
 * (Wired into npm test:desktop:platforms in package.json.)
 *
 * Why this matters: a public install can carry
 * origin=git@github.com:thisismynewfmail-ui/cru.git. A background
 * `git fetch origin` then authenticates over SSH and, with a FIDO2/passkey
 * key, triggers an unexplained hardware-touch prompt. isOfficialSshRemote
 * must reliably recognize the official SSH remote (in every URL form,
 * case-insensitively) so the caller can swap in the anonymous HTTPS path —
 * while NOT misclassifying forks, other hosts, or the HTTPS remote (which
 * never prompts and should keep the normal fetch path).
 *
 * The repository name is deliberately never written out below. It was, and
 * the copies drifted: the module's HTTPS URL named one repository and its
 * canonical string named another, so isOfficialSshRemote answered false for
 * every install that existed — and these tests, asserting against the same
 * mismatched constant, agreed with it. Comparisons here are made against the
 * exported slugs, so a rename that reaches only half the module fails.
 */

import assert from 'node:assert/strict'

import { test } from 'vitest'

import {
  canonicalGitHubRemote,
  isOfficialRemote,
  isOfficialSshRemote,
  isSshRemote,
  OFFICIAL_REPO_CANONICAL,
  OFFICIAL_REPO_CANONICALS,
  OFFICIAL_REPO_HTTPS_URL,
  OFFICIAL_REPO_SLUGS
} from './update-remote'

const SLUG = OFFICIAL_REPO_SLUGS[0]

test('canonicalGitHubRemote normalizes SSH and HTTPS forms to the same value', () => {
  assert.equal(canonicalGitHubRemote(`git@github.com:${SLUG}.git`), OFFICIAL_REPO_CANONICAL)
  assert.equal(canonicalGitHubRemote(`git@github.com:${SLUG}`), OFFICIAL_REPO_CANONICAL)
  assert.equal(canonicalGitHubRemote(`ssh://git@github.com/${SLUG}.git`), OFFICIAL_REPO_CANONICAL)
  assert.equal(canonicalGitHubRemote(`https://github.com/${SLUG}.git`), OFFICIAL_REPO_CANONICAL)
  // Case-insensitive: an uppercased owner still canonicalizes to the same repo.
  assert.equal(canonicalGitHubRemote(`git@github.com:${SLUG.toUpperCase()}.git`), OFFICIAL_REPO_CANONICAL)
  // Trailing slashes are stripped.
  assert.equal(canonicalGitHubRemote(`https://github.com/${SLUG}/`), OFFICIAL_REPO_CANONICAL)
})

test('canonicalGitHubRemote is empty for falsy input', () => {
  assert.equal(canonicalGitHubRemote(''), '')
  assert.equal(canonicalGitHubRemote(null), '')
  assert.equal(canonicalGitHubRemote(undefined), '')
})

test('isSshRemote detects scp-like and ssh:// forms only', () => {
  assert.equal(isSshRemote(`git@github.com:${SLUG}.git`), true)
  assert.equal(isSshRemote(`ssh://git@github.com/${SLUG}.git`), true)
  assert.equal(isSshRemote(`https://github.com/${SLUG}.git`), false)
  assert.equal(isSshRemote(''), false)
  assert.equal(isSshRemote(null), false)
})

test('isOfficialSshRemote is true only for the official repo over SSH', () => {
  assert.equal(isOfficialSshRemote(`git@github.com:${SLUG}.git`), true)
  assert.equal(isOfficialSshRemote(`git@github.com:${SLUG}`), true)
  assert.equal(isOfficialSshRemote(`ssh://git@github.com/${SLUG}.git`), true)
  // Case-insensitive owner/repo match.
  assert.equal(isOfficialSshRemote(`git@github.com:${SLUG.toUpperCase()}.git`), true)
})

test('every name the repo has been published under is still recognized', () => {
  // GitHub redirects a renamed repository, so an install made under a former
  // name is still the official checkout. Dropping one here would send those
  // users back to the SSH fetch path this module exists to keep them off.
  for (const slug of OFFICIAL_REPO_SLUGS) {
    assert.equal(isOfficialSshRemote(`git@github.com:${slug}.git`), true, slug)
    assert.equal(isOfficialRemote(`https://github.com/${slug}.git`), true, slug)
  }
})

test('isOfficialSshRemote does NOT match forks, other hosts, or HTTPS', () => {
  // A fork over SSH belongs to the user — fetching it is their own remote,
  // not the official upstream, so the SSH-avoidance swap must not apply.
  assert.equal(isOfficialSshRemote('git@github.com:someuser/curie-agent.git'), false)
  // Same repo name on a different host is not the official repo.
  assert.equal(isOfficialSshRemote(`git@gitlab.com:${SLUG}.git`), false)
  // HTTPS to the official repo never prompts for SSH/FIDO2, so it keeps the
  // normal fetch path — must not be flagged as an official SSH remote.
  assert.equal(isOfficialSshRemote(`https://github.com/${SLUG}.git`), false)
  assert.equal(isOfficialSshRemote(''), false)
  assert.equal(isOfficialSshRemote(null), false)
})

test('OFFICIAL_REPO_HTTPS_URL canonicalizes to OFFICIAL_REPO_CANONICAL', () => {
  // Invariant: the URL we substitute in must be the same repo we detect.
  // This is the assertion the drifted constants broke — it was asserted
  // against a canonical string no URL in the module could ever produce.
  assert.equal(canonicalGitHubRemote(OFFICIAL_REPO_HTTPS_URL), OFFICIAL_REPO_CANONICAL)
  assert.ok(OFFICIAL_REPO_CANONICALS.includes(OFFICIAL_REPO_CANONICAL))
})
