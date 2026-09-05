import { $gateway } from '@/store/gateway'
import { $activeSessionId, setUnlockActive } from '@/store/session'

export type GatewayRequester = <T = unknown>(method: string, params?: Record<string, unknown>) => Promise<T>

/**
 * Toggle per-session UNLOCK (approval bypass) via gateway `config.set` — the same
 * session-scoped flag as the TUI's Shift+Tab. It does NOT touch the global
 * `approvals.mode` config, so CLI / TUI / cron behavior is unaffected.
 */
export async function setSessionUnlock(
  requestGateway: GatewayRequester,
  sessionId: string,
  enabled: boolean
): Promise<boolean> {
  const result = await requestGateway<{ value?: string }>('config.set', {
    key: 'unlock',
    session_id: sessionId,
    value: enabled ? '1' : '0'
  })

  const active = result?.value === '1'

  setUnlockActive(active)

  return active
}

/**
 * Toggle GLOBAL UNLOCK (approval bypass) via gateway `config.set` with
 * `scope: 'global'`. This flips the persistent `approvals.mode` in config.yaml
 * between `off` (bypass on) and `manual` (bypass off), affecting every session,
 * the CLI, the TUI, and cron — and it survives restarts. Triggered by
 * Shift+clicking the status-bar zap.
 */
export async function setGlobalUnlock(requestGateway: GatewayRequester, enabled: boolean): Promise<boolean> {
  const result = await requestGateway<{ value?: string }>('config.set', {
    key: 'unlock',
    scope: 'global',
    value: enabled ? '1' : '0'
  })

  const active = result?.value === '1'

  setUnlockActive(active)

  return active
}

/**
 * Set UNLOCK to an explicit state from a surface that has no React context — the
 * ⌘K rows. `useSlashCommand` keeps its own `requestGateway` (it already holds
 * one, with the reconnect handling), so this reaches the active gateway
 * directly rather than growing a second requester abstraction.
 *
 * With no session yet the flag is armed locally; the session-create path
 * (use-session-actions) applies it on the first message, exactly as a bare
 * `/unlock` in a fresh draft does.
 */
export async function setUnlockEnabled(enabled: boolean): Promise<boolean> {
  const sessionId = $activeSessionId.get()

  if (!sessionId) {
    setUnlockActive(enabled)

    return enabled
  }

  const gateway = $gateway.get()

  if (!gateway) {
    throw new Error('Curie gateway unavailable')
  }

  return setSessionUnlock((method, params) => gateway.request(method, params), sessionId, enabled)
}
