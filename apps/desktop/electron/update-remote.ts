/**
 * Pure helpers for choosing a remote URL during passive update checks.
 *
 * A public install can end up with `origin=git@github.com:thisismynewfmail-ui/cru.git`.
 * If the user's GitHub SSH key is FIDO2/passkey-backed, a background `git fetch
 * origin` triggers an unexplained hardware-touch prompt. For passive checks
 * against the official repo we substitute the public HTTPS `ls-remote` path,
 * which needs no auth and cannot prompt. Active update/apply flows are left
 * unchanged.
 *
 * Extracted from main.ts so the security-critical remote detection is unit
 * testable without booting Electron (main.ts requires('electron') at load).
 */

/**
 * The official repository, as an `owner/name` slug, followed by the names it
 * has been published under before. Every URL and comparison below is derived
 * from this list rather than written out again, because the two forms had
 * already drifted: the HTTPS URL named one repository and the canonical
 * string named another, so `isOfficialSshRemote` answered false for every
 * real install and the substitution this module exists to perform never once
 * happened.
 *
 * Old names stay recognised. GitHub keeps redirecting a repository's previous
 * name after a rename, so an install made under one is still the official
 * checkout — and treating it as third-party is exactly the FIDO2 prompt this
 * module is here to avoid.
 */
const OFFICIAL_REPO_SLUGS = ['thisismynewfmail-ui/cru', 'thisismynewfmail-ui/Cur-Agnt']

const OFFICIAL_REPO_HTTPS_URL = `https://github.com/${OFFICIAL_REPO_SLUGS[0]}.git`
const OFFICIAL_REPO_CANONICAL = `github.com/${OFFICIAL_REPO_SLUGS[0]}`.toLowerCase()
const OFFICIAL_REPO_CANONICALS = OFFICIAL_REPO_SLUGS.map(slug =>
  `github.com/${slug}`.toLowerCase(),
)

// Normalize common GitHub remote URL forms to `host/owner/repo` (lowercased,
// no trailing slash, no .git suffix) so SSH and HTTPS forms of the same repo
// compare equal.
function canonicalGitHubRemote(url) {
  if (!url) {
    return ''
  }

  let value = String(url).trim()

  if (value.startsWith('git@github.com:')) {
    value = `github.com/${value.slice('git@github.com:'.length)}`
  } else if (value.startsWith('ssh://git@github.com/')) {
    value = `github.com/${value.slice('ssh://git@github.com/'.length)}`
  } else {
    try {
      const parsed = new URL(value)

      if (parsed.hostname && parsed.pathname) {
        value = `${parsed.hostname}${parsed.pathname}`
      }
    } catch {
      // Leave non-URL forms unchanged.
    }
  }

  value = value.trim().replace(/\/+$/, '')

  if (value.endsWith('.git')) {
    value = value.slice(0, -4)
  }

  return value.toLowerCase()
}

function isSshRemote(url) {
  const value = String(url || '')
    .trim()
    .toLowerCase()

  return value.startsWith('git@') || value.startsWith('ssh://')
}

function isOfficialRemote(url) {
  return OFFICIAL_REPO_CANONICALS.includes(canonicalGitHubRemote(url))
}

function isOfficialSshRemote(url) {
  return isSshRemote(url) && isOfficialRemote(url)
}

export {
  canonicalGitHubRemote,
  isOfficialRemote,
  isOfficialSshRemote,
  isSshRemote,
  OFFICIAL_REPO_CANONICAL,
  OFFICIAL_REPO_CANONICALS,
  OFFICIAL_REPO_HTTPS_URL,
  OFFICIAL_REPO_SLUGS,
}
