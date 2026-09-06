"""Keeping two consoles that are open at once telling the same story.

Nothing here talks to another process. It watches the *files* both processes
already write — ``config.yaml``, the session store, the cron job store — and
reports when one of them has moved on since this console last looked.

That is the whole mechanism, and it is deliberate. The console has no daemon,
no socket and no lock manager, and adding one to answer "did the other window
change the skin?" would be a second system to keep alive for a question the
filesystem can already answer. What it needs instead is to stop assuming that
what it read at start-up is still true — which is the actual fault:

* a skin, display mode or indicator set chosen in one window stayed invisible
  in the other until it was restarted, and then whichever window was closed
  *last* wrote its own stale copy back over the choice;
* a conversation started in one window never appeared in the other's logbook,
  because the logbook is only re-read after this console's own turns;
* a scheduled task created in one window was invisible in the other, so the
  second window would happily create a duplicate of it.

A watch is one file and a fingerprint of it. ``changed()`` answers once per
real change: it adopts the new fingerprint as it reports, so a change is
reported to the poller that noticed it and not to every poll afterwards.

Every read is total. A file that does not exist yet, a home that cannot be
read, a clock that goes backwards — all of them resolve to "no change", never
to an exception. This runs on a timer inside a full-screen application, and
an exception on a timer is a traceback painted over the interface.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional


#: SQLite writes the database file lazily and the write-ahead log eagerly, so
#: a conversation committed by another process can leave ``state.db`` itself
#: untouched for a long time. Watching the sidecars is what makes a second
#: window notice a chat the first one just wrote.
_DB_SIDECARS: tuple[str, ...] = ("-wal", "-shm")


def _fingerprint(path: Path) -> tuple:
    """A cheap stand-in for "has this file changed", or ``()`` when absent.

    Size as well as mtime, because a filesystem with one-second timestamp
    resolution (and every network mount) can rewrite a file inside one tick
    of its own clock. Two numbers make that collision much less likely and
    cost nothing extra — ``stat`` returns both.
    """
    try:
        info = path.stat()
    except (OSError, ValueError):
        return ()
    return (getattr(info, "st_mtime_ns", int(info.st_mtime * 1e9)), info.st_size)


@dataclass
class StoreWatch:
    """One store another process can change under this console.

    ``resolve`` is a callable rather than a path because every one of these
    is profile-scoped: the path is a function of ``CURIE_HOME``, which a
    profile switch changes underneath a running process. Resolving on each
    poll costs a path join and means the watch follows the profile instead of
    holding a path that stopped being the answer.
    """

    name: str
    resolve: Callable[[], Optional[Path]]
    #: Extra suffixes appended to the resolved path, watched alongside it.
    #: SQLite's ``-wal`` is the reason this exists.
    sidecars: tuple[str, ...] = ()
    _seen: Optional[tuple] = field(default=None, repr=False)

    def _now(self) -> tuple:
        try:
            path = self.resolve()
        except Exception:
            return ()
        if path is None:
            return ()
        prints = [_fingerprint(path)]
        for suffix in self.sidecars:
            prints.append(_fingerprint(Path(str(path) + suffix)))
        return tuple(prints)

    def sync(self) -> None:
        """Adopt the current state without reporting it as a change.

        Called after this console writes the store itself. Without it every
        setting the reader changes here comes back on the next poll as
        "somebody else changed this", and the console re-applies its own
        write — visibly, because re-applying a display setting repaints.
        """
        self._seen = self._now()

    def changed(self) -> bool:
        """Whether the store moved since the last call. Reports once."""
        current = self._now()
        if self._seen is None:
            # First look. Establish the baseline rather than reporting the
            # store's mere existence as a change — otherwise every console
            # reloads everything one tick after it opens.
            self._seen = current
            return False
        if current == self._seen:
            return False
        self._seen = current
        return True


def config_path() -> Optional[Path]:
    """Where the console's preferences live, for the active profile."""
    try:
        from curie_cli.config import get_config_path

        return Path(get_config_path())
    except Exception:
        return None


def session_store_path() -> Optional[Path]:
    """The conversation store the logbook reads."""
    try:
        from curie_state import _default_db_path

        return Path(_default_db_path())
    except Exception:
        pass
    try:
        from curie_constants import get_curie_home

        return Path(get_curie_home()) / "state.db"
    except Exception:
        return None


def cron_store_path() -> Optional[Path]:
    """The scheduled-task store the SCHEDULE pane reads."""
    try:
        from cron.jobs import _current_cron_store

        return Path(_current_cron_store().jobs_file)
    except Exception:
        pass
    try:
        from curie_constants import get_curie_home

        return Path(get_curie_home()) / "cron" / "jobs.json"
    except Exception:
        return None


def cron_executions_path() -> Optional[Path]:
    """The ledger that says which scheduled tasks are running right now."""
    store = cron_store_path()
    if store is None:
        return None
    return store.parent / "executions.db"


class ConsoleSync:
    """Every store this console shares with another copy of itself.

    One object so the console has a single thing to poll and a single place
    to say "that change was mine". Which stores exist is fixed here rather
    than passed in: they are the console's own dependencies, and a caller
    that could omit one would be a caller that could quietly turn the sync
    off for it.
    """

    def __init__(self) -> None:
        self.settings = StoreWatch("settings", config_path)
        self.sessions = StoreWatch(
            "sessions", session_store_path, sidecars=_DB_SIDECARS
        )
        self.schedule = StoreWatch("schedule", cron_store_path)
        self.executions = StoreWatch(
            "executions", cron_executions_path, sidecars=_DB_SIDECARS
        )

    def watches(self) -> Iterable[StoreWatch]:
        return (self.settings, self.sessions, self.schedule, self.executions)

    def prime(self) -> None:
        """Take a baseline for every store, so the first poll is quiet."""
        for watch in self.watches():
            watch.sync()

    def changes(self) -> set:
        """The names of the stores that moved since the last poll."""
        return {watch.name for watch in self.watches() if watch.changed()}

    def mine(self, *names: str) -> None:
        """Record a write this console just made, so it is not read back."""
        wanted = set(names)
        for watch in self.watches():
            if not wanted or watch.name in wanted:
                watch.sync()


__all__ = [
    "ConsoleSync",
    "StoreWatch",
    "config_path",
    "cron_executions_path",
    "cron_store_path",
    "session_store_path",
]
