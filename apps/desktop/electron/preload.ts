import { contextBridge, ipcRenderer, webFrame, webUtils } from 'electron'

// Which translucency the OS can back. Asked synchronously because the renderer
// needs it before its first paint, and answered by main because deciding it
// needs `os.release()` — a sandboxed preload may only require electron, events,
// timers and url, so importing node:os here throws before contextBridge runs
// and takes the ENTIRE bridge down with it (window.curieDesktop undefined =>
// "Desktop IPC bridge is unavailable"). No reply means no glass, which degrades
// to an ordinary opaque window rather than a page thinned over nothing.
const translucencySupport = ipcRenderer.sendSync('curie:translucency:support')
const hudWindowing = ipcRenderer.sendSync('curie:hud:windowing')
const hudNativeDrag = hudWindowing?.nativeDrag === true
const launchFlags = ipcRenderer.sendSync('curie:launch-flags')

contextBridge.exposeInMainWorld('curieDesktop', {
  glassSupported: translucencySupport?.glass === true,
  translucencySupported: translucencySupport?.translucency === true,
  // Launch-flag fact: the app was started with --local, so the renderer may
  // show the local-models surfaces. Static for the window's lifetime.
  localModelsEnabled: launchFlags?.localModels === true,
  getConnection: profile => ipcRenderer.invoke('curie:connection', profile),
  // Registry-scoped backend resolution: { connectionId, profile } → descriptor.
  getConnectionFor: payload => ipcRenderer.invoke('curie:connection:for', payload),
  getProfileRoutes: profiles => ipcRenderer.invoke('curie:plugin-profile-routes', profiles),
  revalidateConnection: () => ipcRenderer.invoke('curie:connection:revalidate'),
  touchBackend: profile => ipcRenderer.invoke('curie:backend:touch', profile),
  getGatewayWsUrl: profile => ipcRenderer.invoke('curie:gateway:ws-url', profile),
  // Registry-scoped fresh WS URL: { connectionId, profile } → result shape of
  // getGatewayWsUrl, minted against that connection's backend.
  getGatewayWsUrlFor: payload => ipcRenderer.invoke('curie:gateway:ws-url-for', payload),
  // Union agent roster across every registered connection.
  getAgentRoster: () => ipcRenderer.invoke('curie:agents:roster'),
  openSessionWindow: (sessionId, opts) => ipcRenderer.invoke('curie:window:openSession', sessionId, opts),
  openSessionInTerminal: (sessionId, opts) => ipcRenderer.invoke('curie:window:openInTerminal', sessionId, opts),
  openWindow: () => ipcRenderer.invoke('curie:window:openInstance'),
  openBrowserWindow: tabId => ipcRenderer.invoke('curie:window:openBrowser', tabId),
  onBrowserPopoutClosed: callback => {
    const listener = (_event, tabId) => callback(tabId)
    ipcRenderer.on('curie:browser-popout:closed', listener)

    return () => ipcRenderer.removeListener('curie:browser-popout:closed', listener)
  },
  claimAmbientCue: key => ipcRenderer.invoke('curie:ambient:claim', key),
  wakeIndicator: {
    getState: () => ipcRenderer.invoke('curie:wake-indicator:get'),
    setState: state => ipcRenderer.send('curie:wake-indicator:set', state),
    onState: callback => {
      const listener = (_event, state) => callback(state)
      ipcRenderer.on('curie:wake-indicator:state', listener)

      return () => ipcRenderer.removeListener('curie:wake-indicator:state', listener)
    }
  },
  petOverlay: {
    // Main renderer → main process: window lifecycle + drag. `request` is
    // `{ bounds, screen }`; resolves with the screen bounds it actually used.
    open: request => ipcRenderer.invoke('curie:pet-overlay:open', request),
    close: () => ipcRenderer.invoke('curie:pet-overlay:close'),
    setBounds: bounds => ipcRenderer.send('curie:pet-overlay:set-bounds', bounds),
    setIgnoreMouse: ignore => ipcRenderer.send('curie:pet-overlay:ignore-mouse', ignore),
    // Flip the overlay focusable (and focus it) while the composer needs keys.
    setFocusable: focusable => ipcRenderer.send('curie:pet-overlay:set-focusable', focusable),
    // Main renderer → overlay (forwarded by main): push the latest pet state.
    pushState: payload => ipcRenderer.send('curie:pet-overlay:state', payload),
    // Overlay → main renderer (forwarded by main): pop back in / composer submit.
    control: payload => ipcRenderer.send('curie:pet-overlay:control', payload),
    // Overlay subscribes to state pushes.
    onState: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('curie:pet-overlay:state', listener)

      return () => ipcRenderer.removeListener('curie:pet-overlay:state', listener)
    },
    // Main renderer subscribes to overlay control messages.
    onControl: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('curie:pet-overlay:control', listener)

      return () => ipcRenderer.removeListener('curie:pet-overlay:control', listener)
    }
  },
  // HUD mode: the chrome-free floating chat. A full app renderer (own gateway)
  // sized as a floating bar, so it mounts the real composer. Main owns the
  // window; `onChanged` keeps every window's toggle truthful.
  hud: {
    nativeDrag: hudNativeDrag,
    windowing: {
      clientPlacement: hudWindowing?.clientPlacement !== false,
      controlDrag: hudWindowing?.controlDrag === true,
      nativeDrag: hudNativeDrag,
      solid: hudWindowing?.solid === true,
      workspaceTransfer: hudWindowing?.workspaceTransfer === true
    },
    open: request => ipcRenderer.invoke('curie:hud:open', request),
    close: () => ipcRenderer.invoke('curie:hud:close'),
    setIgnoreMouse: ignore => ipcRenderer.send('curie:hud:ignore-mouse', ignore),
    beginMove: () => ipcRenderer.send('curie:hud:begin-move'),
    endMove: () => ipcRenderer.send('curie:hud:end-move'),
    moveBy: delta => ipcRenderer.send('curie:hud:move-by', delta),
    setWorkspaceTransfer: transferring => ipcRenderer.send('curie:hud:workspace-transfer', transferring),
    setBounds: bounds => ipcRenderer.send('curie:hud:set-bounds', bounds),
    resetLayout: () => ipcRenderer.invoke('curie:hud:reset-layout'),
    // Whether the band covers the window below the bar. Main pairs it with the
    // user's translucency setting to decide the native frost (macOS vibrancy /
    // Windows 11 DWM backdrop) — see hudFrostFor.
    setFrost: showing => ipcRenderer.invoke('curie:hud:frost', showing),
    // The HUD tells main which session it is on; main hands that back to the
    // app window when the HUD closes, so the app can re-home onto it.
    setSession: sessionId => ipcRenderer.send('curie:hud:session', sessionId),
    onGoto: callback => {
      const listener = (_event, sessionId) => callback(sessionId)
      ipcRenderer.on('curie:hud:goto', listener)

      return () => ipcRenderer.removeListener('curie:hud:goto', listener)
    },
    onChanged: callback => {
      const listener = (_event, state) => callback(state)
      ipcRenderer.on('curie:hud:changed', listener)

      return () => ipcRenderer.removeListener('curie:hud:changed', listener)
    },
    // Linux only, and silent elsewhere: where the cursor is, in page
    // coordinates, or null when it has left the window. Stands in for the
    // mousemove that `setIgnoreMouseEvents(true, { forward: true })` delivers on
    // macOS and Windows but not here.
    onCursor: callback => {
      const listener = (_event, point) => callback(point)
      ipcRenderer.on('curie:hud:cursor', listener)

      return () => ipcRenderer.removeListener('curie:hud:cursor', listener)
    },
    // Main's game-overlay watch: whether a fullscreen app (a game) is under
    // the HUD, so the renderer can step back to the low-opacity overlay
    // treatment while one owns the screen.
    onGameOverlay: callback => {
      const listener = (_event, state) => callback(state)
      ipcRenderer.on('curie:hud:game-overlay', listener)

      return () => ipcRenderer.removeListener('curie:hud:game-overlay', listener)
    }
  },
  // Quick Entry: the global-hotkey mini composer window. Main owns the OS
  // shortcut + the persisted preference; the quick window only captures text
  // and hands it back, and the primary renderer submits it through the normal
  // prompt path.
  quickEntry: {
    getSettings: () => ipcRenderer.invoke('curie:quick-entry:settings:get'),
    setSettings: patch => ipcRenderer.invoke('curie:quick-entry:settings:set', patch),
    submit: payload => ipcRenderer.send('curie:quick-entry:submit', payload),
    dismiss: () => ipcRenderer.send('curie:quick-entry:dismiss'),
    // Primary renderer → main → quick window: gateway connection state + the
    // recent-session options the target picker offers. Main caches the latest
    // payload so a freshly spawned quick window starts from truth.
    pushState: payload => ipcRenderer.send('curie:quick-entry:state', payload),
    // Quick window subscribes to those pushes.
    onState: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('curie:quick-entry:state', listener)

      return () => ipcRenderer.removeListener('curie:quick-entry:state', listener)
    },
    // Main → primary renderer: a submit captured by the quick window.
    onSubmit: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('curie:quick-entry:submit', listener)

      return () => ipcRenderer.removeListener('curie:quick-entry:submit', listener)
    },
    // Main → quick window: you were just summoned (reset draft + refocus).
    onShown: callback => {
      const listener = () => callback()
      ipcRenderer.on('curie:quick-entry:shown', listener)

      return () => ipcRenderer.removeListener('curie:quick-entry:shown', listener)
    }
  },
  getBootProgress: () => ipcRenderer.invoke('curie:boot-progress:get'),
  getConnectionConfig: profile => ipcRenderer.invoke('curie:connection-config:get', profile),
  saveConnectionConfig: payload => ipcRenderer.invoke('curie:connection-config:save', payload),
  applyConnectionConfig: payload => ipcRenderer.invoke('curie:connection-config:apply', payload),
  testConnectionConfig: payload => ipcRenderer.invoke('curie:connection-config:test', payload),
  // Opt-in OS-keychain encryption for stored gateway secrets (default off —
  // see secret-storage-policy.ts). get never touches the OS keychain.
  getSecretStorageEncryption: () => ipcRenderer.invoke('curie:secret-storage:get'),
  setSecretStorageEncryption: (on: boolean) => ipcRenderer.invoke('curie:secret-storage:set', on),
  // v2 multi-connection registry: named agent sources (local / remote / cloud / ssh).
  connections: {
    list: () => ipcRenderer.invoke('curie:connections:list'),
    save: payload => ipcRenderer.invoke('curie:connections:save', payload),
    remove: id => ipcRenderer.invoke('curie:connections:remove', id),
    setPrimary: id => ipcRenderer.invoke('curie:connections:set-primary', id),
    setLaunchMode: mode => ipcRenderer.invoke('curie:connections:set-launch-mode', mode),
    setLastUsed: id => ipcRenderer.invoke('curie:connections:set-last-used', id),
    test: id => ipcRenderer.invoke('curie:connections:test', id),
    updateManaged: id => ipcRenderer.invoke('curie:connections:update-managed', id),
    // Fan out `curie update` to every eligible registered connection.
    // Optional excludeIds skips rows the caller updates through another path.
    updateAll: options => ipcRenderer.invoke('curie:connections:update-all', options),
    // Registry lifecycle push (main → renderer): a connection was removed or
    // materially edited, so secondaries scoped to it must be disposed (and,
    // for edits, re-dialed at the new target).
    onChanged: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('curie:connections:changed', listener)

      return () => ipcRenderer.removeListener('curie:connections:changed', listener)
    }
  },
  sshConfigHosts: () => ipcRenderer.invoke('curie:ssh-config:hosts'),
  sshResolveHost: host => ipcRenderer.invoke('curie:ssh-config:resolve', host),
  probeConnectionConfig: remoteUrl => ipcRenderer.invoke('curie:connection-config:probe', remoteUrl),
  oauthLoginConnectionConfig: remoteUrl => ipcRenderer.invoke('curie:connection-config:oauth-login', remoteUrl),
  oauthLogoutConnectionConfig: remoteUrl => ipcRenderer.invoke('curie:connection-config:oauth-logout', remoteUrl),
  // Curie Cloud: one portal login powers discovery + silent per-agent sign-in
  // (cloud-auto-discovery Phase 3).
  cloud: {
    status: () => ipcRenderer.invoke('curie:cloud:status'),
    login: () => ipcRenderer.invoke('curie:cloud:login'),
    logout: () => ipcRenderer.invoke('curie:cloud:logout'),
    discover: org => ipcRenderer.invoke('curie:cloud:discover', org),
    agentSignIn: dashboardUrl => ipcRenderer.invoke('curie:cloud:agent-sign-in', dashboardUrl)
  },
  profile: {
    get: () => ipcRenderer.invoke('curie:profile:get'),
    remember: name => ipcRenderer.invoke('curie:profile:remember', name),
    set: name => ipcRenderer.invoke('curie:profile:set', name)
  },
  api: request => ipcRenderer.invoke('curie:api', request),
  notify: payload => ipcRenderer.invoke('curie:notify', payload),
  requestMicrophoneAccess: () => ipcRenderer.invoke('curie:requestMicrophoneAccess'),
  readWindowBelow: () => ipcRenderer.invoke('curie:window:readBelow'),
  readFileDataUrl: filePath => ipcRenderer.invoke('curie:readFileDataUrl', filePath),
  readFileDataUrlForAttach: filePath => ipcRenderer.invoke('curie:readFileDataUrlForAttach', filePath),
  dataUrlReadMax: {
    get: () => ipcRenderer.invoke('curie:data-url-read-max:get'),
    set: maxMb => ipcRenderer.invoke('curie:data-url-read-max:set', maxMb)
  },
  readFileText: filePath => ipcRenderer.invoke('curie:readFileText', filePath),
  readPluginSource: (filePath: string) => ipcRenderer.invoke('curie:readPluginSource', filePath),
  selectPaths: options => ipcRenderer.invoke('curie:selectPaths', options),
  selectSavePath: options => ipcRenderer.invoke('curie:selectSavePath', options),
  writeClipboard: text => ipcRenderer.invoke('curie:writeClipboard', text),
  readClipboard: () => ipcRenderer.invoke('curie:readClipboard'),
  saveGatewayFile: payload => ipcRenderer.invoke('curie:saveGatewayFile', payload),
  saveImageFromUrl: url => ipcRenderer.invoke('curie:saveImageFromUrl', url),
  contextMenuEdit: command => ipcRenderer.invoke('curie:context-menu:edit', command),
  contextMenuCopyImage: () => ipcRenderer.invoke('curie:context-menu:copy-image'),
  contextMenuSpellcheck: action => ipcRenderer.invoke('curie:context-menu:spellcheck', action),
  contextMenuGuestAddWord: payload => ipcRenderer.invoke('curie:context-menu:guest-add-word', payload),
  onContextMenuSpellcheck: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('curie:context-menu-spellcheck', listener)

    return () => ipcRenderer.removeListener('curie:context-menu-spellcheck', listener)
  },
  saveImageBuffer: (data, ext, name) => ipcRenderer.invoke('curie:saveImageBuffer', { data, ext, name }),
  capturePreview: payload => ipcRenderer.invoke('curie:capturePreview', payload),
  saveClipboardImage: () => ipcRenderer.invoke('curie:saveClipboardImage'),
  getPathForFile: file => {
    try {
      return webUtils.getPathForFile(file) || ''
    } catch {
      return ''
    }
  },
  normalizePreviewTarget: (target, baseDir) => ipcRenderer.invoke('curie:normalizePreviewTarget', target, baseDir),
  watchPreviewFile: url => ipcRenderer.invoke('curie:watchPreviewFile', url),
  watchDirectory: dir => ipcRenderer.invoke('curie:watchDirectory', dir),
  stopPreviewFileWatch: id => ipcRenderer.invoke('curie:stopPreviewFileWatch', id),
  setActiveWork: payload => ipcRenderer.send('curie:active-work', payload),
  setTitleBarTheme: payload => ipcRenderer.send('curie:titlebar-theme', payload),
  setNativeTheme: mode => ipcRenderer.send('curie:native-theme', mode),
  setTranslucency: payload => ipcRenderer.send('curie:translucency', payload),
  setKeepAwake: on => ipcRenderer.send('curie:keep-awake', on),
  setDisableF12: blocked => ipcRenderer.send('curie:devtools:disable-f12', blocked),
  setPreviewShortcutActive: active => ipcRenderer.send('curie:previewShortcutActive', Boolean(active)),
  openExternal: url => ipcRenderer.invoke('curie:openExternal', url),
  mcpOauth: {
    // One-shot loopback listener for MCP OAuth against remote backends: bind
    // on this machine, hand redirectUri to mcp.servers.oauth.start, then wait
    // for the provider redirect and relay code/state via oauth.callback.
    listen: () => ipcRenderer.invoke('curie:mcp-oauth:listen'),
    wait: (id, timeoutMs) => ipcRenderer.invoke('curie:mcp-oauth:wait', id, timeoutMs),
    cancel: id => ipcRenderer.invoke('curie:mcp-oauth:cancel', id)
  },
  openPreviewInBrowser: url => ipcRenderer.invoke('curie:openPreviewInBrowser', url),
  reachPreviewUrl: url => ipcRenderer.invoke('curie:preview:reach', url),
  setActiveConnectionRoute: route => ipcRenderer.send('curie:connection:active-route', route),
  fetchLinkTitle: url => ipcRenderer.invoke('curie:fetchLinkTitle', url),
  resolveFavicon: url => ipcRenderer.invoke('curie:resolveFavicon', url),
  sanitizeWorkspaceCwd: cwd => ipcRenderer.invoke('curie:workspace:sanitize', cwd),
  settings: {
    getDefaultProjectDir: () => ipcRenderer.invoke('curie:setting:defaultProjectDir:get'),
    setDefaultProjectDir: dir => ipcRenderer.invoke('curie:setting:defaultProjectDir:set', dir),
    pickDefaultProjectDir: () => ipcRenderer.invoke('curie:setting:defaultProjectDir:pick')
  },
  zoom: {
    // Current zoom of this window, as { level, percent }.
    get: () => ipcRenderer.invoke('curie:zoom:get'),
    // Synchronous zoom factor (1 = 100%). Coordinate math needs it in the
    // same tick as the event it converts, so no IPC round-trip here.
    factor: () => webFrame.getZoomFactor(),
    setPercent: percent => ipcRenderer.send('curie:zoom:set-percent', percent),
    // Fires on every zoom change, including the Ctrl/Cmd +/-/0 shortcuts,
    // so the settings UI can stay in sync with the keyboard.
    onChanged: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('curie:zoom:changed', listener)

      return () => ipcRenderer.removeListener('curie:zoom:changed', listener)
    }
  },
  revealLogs: () => ipcRenderer.invoke('curie:logs:reveal'),
  getRecentLogs: () => ipcRenderer.invoke('curie:logs:recent'),
  // Fire-and-forget: persists a renderer error-boundary catch (with component
  // stack) to desktop.log so crashes survive the window (#79428).
  reportRendererError: report => ipcRenderer.send('curie:logs:renderer-error', report),
  readDir: dirPath => ipcRenderer.invoke('curie:fs:readDir', dirPath),
  gitRoot: startPath => ipcRenderer.invoke('curie:fs:gitRoot', startPath),
  revealPath: targetPath => ipcRenderer.invoke('curie:fs:reveal', targetPath),
  openDir: dirPath => ipcRenderer.invoke('curie:fs:openDir', dirPath),
  desktopPluginsRoot: () => ipcRenderer.invoke('curie:fs:desktopPluginsRoot'),
  logsRoot: () => ipcRenderer.invoke('curie:fs:logsRoot'),
  agentPluginsRoot: () => ipcRenderer.invoke('curie:fs:agentPluginsRoot'),
  renamePath: (targetPath, newName) => ipcRenderer.invoke('curie:fs:rename', targetPath, newName),
  writeTextFile: (filePath, content) => ipcRenderer.invoke('curie:fs:writeText', filePath, content),
  trashPath: targetPath => ipcRenderer.invoke('curie:fs:trash', targetPath),
  git: {
    worktreeList: repoPath => ipcRenderer.invoke('curie:git:worktreeList', repoPath),
    worktreeAdd: (repoPath, options) => ipcRenderer.invoke('curie:git:worktreeAdd', repoPath, options),
    worktreeRemove: (repoPath, worktreePath, options) =>
      ipcRenderer.invoke('curie:git:worktreeRemove', repoPath, worktreePath, options),
    branchSwitch: (repoPath, branch) => ipcRenderer.invoke('curie:git:branchSwitch', repoPath, branch),
    branchList: repoPath => ipcRenderer.invoke('curie:git:branchList', repoPath),
    baseBranchList: repoPath => ipcRenderer.invoke('curie:git:baseBranchList', repoPath),
    repoStatus: repoPath => ipcRenderer.invoke('curie:git:repoStatus', repoPath),
    fileDiff: (repoPath, filePath) => ipcRenderer.invoke('curie:git:fileDiff', repoPath, filePath),
    scanRepos: (roots, options) => ipcRenderer.invoke('curie:git:scanRepos', roots, options),
    review: {
      list: (repoPath, scope, baseRef) => ipcRenderer.invoke('curie:git:review:list', repoPath, scope, baseRef),
      diff: (repoPath, filePath, scope, baseRef, staged) =>
        ipcRenderer.invoke('curie:git:review:diff', repoPath, filePath, scope, baseRef, staged),
      stage: (repoPath, filePath) => ipcRenderer.invoke('curie:git:review:stage', repoPath, filePath),
      unstage: (repoPath, filePath) => ipcRenderer.invoke('curie:git:review:unstage', repoPath, filePath),
      revert: (repoPath, filePath) => ipcRenderer.invoke('curie:git:review:revert', repoPath, filePath),
      revParse: (repoPath, ref) => ipcRenderer.invoke('curie:git:review:revParse', repoPath, ref),
      commit: (repoPath, message, push) => ipcRenderer.invoke('curie:git:review:commit', repoPath, message, push),
      commitContext: repoPath => ipcRenderer.invoke('curie:git:review:commitContext', repoPath),
      push: repoPath => ipcRenderer.invoke('curie:git:review:push', repoPath),
      shipInfo: repoPath => ipcRenderer.invoke('curie:git:review:shipInfo', repoPath),
      prList: (repoPath, branches, numbers) =>
        ipcRenderer.invoke('curie:git:review:prList', repoPath, branches, numbers),
      fetchPrComment: (repoPath, url) => ipcRenderer.invoke('curie:git:review:fetchPrComment', repoPath, url),
      createPr: repoPath => ipcRenderer.invoke('curie:git:review:createPr', repoPath)
    }
  },
  terminal: {
    attach: id => ipcRenderer.invoke('curie:terminal:attach', id),
    cwd: id => ipcRenderer.invoke('curie:terminal:cwd', id),
    dispose: id => ipcRenderer.invoke('curie:terminal:dispose', id),
    resize: (id, size) => ipcRenderer.invoke('curie:terminal:resize', id, size),
    start: options => ipcRenderer.invoke('curie:terminal:start', options),
    write: (id, data) => ipcRenderer.invoke('curie:terminal:write', id, data),
    onData: (id, callback) => {
      const channel = `curie:terminal:${id}:data`
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on(channel, listener)

      return () => ipcRenderer.removeListener(channel, listener)
    },
    onExit: (id, callback) => {
      const channel = `curie:terminal:${id}:exit`
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on(channel, listener)

      return () => ipcRenderer.removeListener(channel, listener)
    }
  },
  onClosePreviewRequested: callback => {
    const listener = () => callback()
    ipcRenderer.on('curie:close-preview-requested', listener)

    return () => ipcRenderer.removeListener('curie:close-preview-requested', listener)
  },
  onPreviewNav: callback => {
    const listener = (_event, command) => callback(command)
    ipcRenderer.on('curie:preview-nav', listener)

    return () => ipcRenderer.removeListener('curie:preview-nav', listener)
  },
  onOpenFolderRequested: callback => {
    const listener = () => callback()
    ipcRenderer.on('curie:open-folder-requested', listener)

    return () => ipcRenderer.removeListener('curie:open-folder-requested', listener)
  },
  onOpenUpdatesRequested: callback => {
    const listener = () => callback()
    ipcRenderer.on('curie:open-updates', listener)

    return () => ipcRenderer.removeListener('curie:open-updates', listener)
  },
  onDeepLink: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('curie:deep-link', listener)

    return () => ipcRenderer.removeListener('curie:deep-link', listener)
  },
  signalDeepLinkReady: () => ipcRenderer.invoke('curie:deep-link-ready'),
  probePluginRepo: payload => ipcRenderer.invoke('curie:plugin:probe', payload),
  installDesktopPlugin: payload => ipcRenderer.invoke('curie:plugin:installDesktop', payload),
  onWindowStateChanged: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('curie:window-state-changed', listener)

    return () => ipcRenderer.removeListener('curie:window-state-changed', listener)
  },
  onFocusSession: callback => {
    const listener = (_event, sessionId) => callback(sessionId)
    ipcRenderer.on('curie:focus-session', listener)

    return () => ipcRenderer.removeListener('curie:focus-session', listener)
  },
  onNotificationAction: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('curie:notification-action', listener)

    return () => ipcRenderer.removeListener('curie:notification-action', listener)
  },
  onNotificationActivate: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('curie:notification-activate', listener)

    return () => ipcRenderer.removeListener('curie:notification-activate', listener)
  },
  onPreviewFileChanged: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('curie:preview-file-changed', listener)

    return () => ipcRenderer.removeListener('curie:preview-file-changed', listener)
  },
  onBackendExit: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('curie:backend-exit', listener)

    return () => ipcRenderer.removeListener('curie:backend-exit', listener)
  },
  // Soft gateway-mode apply finished tearing down the primary backend. Renderer
  // should wipe session lists + re-dial without a window reload.
  onConnectionApplied: callback => {
    const listener = () => callback()
    ipcRenderer.on('curie:connection:applied', listener)

    return () => ipcRenderer.removeListener('curie:connection:applied', listener)
  },
  onPowerResume: callback => {
    const listener = () => callback()
    ipcRenderer.on('curie:power-resume', listener)

    return () => ipcRenderer.removeListener('curie:power-resume', listener)
  },
  // AC ↔ battery transitions; renderers slow their backstop polls on battery.
  getOnBattery: () => ipcRenderer.invoke('curie:power-battery:get'),
  onBatteryChanged: callback => {
    const listener = (_event, onBattery) => callback(Boolean(onBattery))
    ipcRenderer.on('curie:power-battery', listener)

    return () => ipcRenderer.removeListener('curie:power-battery', listener)
  },
  onBootProgress: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('curie:boot-progress', listener)

    return () => ipcRenderer.removeListener('curie:boot-progress', listener)
  },
  // First-launch bootstrap progress -- emitted by the install.ps1 stage
  // runner in main.ts (apps/desktop/electron/bootstrap-runner.ts).
  // Renderer's install overlay subscribes to live events and queries the
  // current snapshot via getBootstrapState() to recover after a devtools
  // reload mid-bootstrap.
  getBootstrapState: () => ipcRenderer.invoke('curie:bootstrap:get'),
  continueBootstrapLocal: () => ipcRenderer.invoke('curie:bootstrap:continue-local'),
  recycleBackend: profile => ipcRenderer.invoke('curie:backend:recycle', profile),
  resetBootstrap: () => ipcRenderer.invoke('curie:bootstrap:reset'),
  repairBootstrap: () => ipcRenderer.invoke('curie:bootstrap:repair'),
  cancelBootstrap: () => ipcRenderer.invoke('curie:bootstrap:cancel'),
  onBootstrapEvent: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('curie:bootstrap:event', listener)

    return () => ipcRenderer.removeListener('curie:bootstrap:event', listener)
  },
  getVersion: () => ipcRenderer.invoke('curie:version'),
  relaunchApp: () => ipcRenderer.invoke('curie:app:relaunch'),
  getRemoteDisplayReason: () => ipcRenderer.invoke('curie:get-remote-display-reason'),
  uninstall: {
    summary: () => ipcRenderer.invoke('curie:uninstall:summary'),
    run: mode => ipcRenderer.invoke('curie:uninstall:run', { mode })
  },
  updates: {
    check: () => ipcRenderer.invoke('curie:updates:check'),
    apply: opts => ipcRenderer.invoke('curie:updates:apply', opts),
    getBranch: () => ipcRenderer.invoke('curie:updates:branch:get'),
    setBranch: name => ipcRenderer.invoke('curie:updates:branch:set', name),
    onProgress: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('curie:updates:progress', listener)

      return () => ipcRenderer.removeListener('curie:updates:progress', listener)
    }
  },
  themes: {
    fetchMarketplace: id => ipcRenderer.invoke('curie:vscode-theme:fetch', id),
    searchMarketplace: query => ipcRenderer.invoke('curie:vscode-theme:search', query)
  },
  // Find-in-page (Ctrl/Cmd+F): delegates to Electron's
  // webContents.findInPage on the IPC sender's window so a Cmd+F pressed
  // in a secondary session window searches THAT window, not the primary.
  // `onFoundInPage` returns the unsubscribe fn; the renderer wires it via
  // `initFindInPageListener` in store/find-in-page.ts and tears it down
  // when the FindBar unmounts.
  findInPage: (query, options) => ipcRenderer.invoke('curie:find-in-page', query, options),
  stopFindInPage: () => ipcRenderer.invoke('curie:stop-find-in-page'),
  onFoundInPage: callback => {
    const listener = (_event, result) => callback(result)
    ipcRenderer.on('curie:found-in-page', listener)

    return () => ipcRenderer.removeListener('curie:found-in-page', listener)
  },
  // Main-process `before-input-event` forwards Ctrl/Cmd+F here so renderer
  // can open the FindBar even when the GTK compositor has already grabbed
  // the chord at the windowing layer (#81727).
  onOpenFindBarRequested: callback => {
    const listener = () => callback()
    ipcRenderer.on('curie:open-find-bar', listener)

    return () => ipcRenderer.removeListener('curie:open-find-bar', listener)
  }
})
