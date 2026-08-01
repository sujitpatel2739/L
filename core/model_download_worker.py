"""
model_download_worker.py

Runs ModelManager downloads on a background QThread so the settings
UI (and the rest of the app) never freezes for a multi-GB download.
Mirrors worker.py / chat_worker.py's pattern.

One worker instance handles exactly ONE in-flight download -- the
Local Models tab creates a new (worker, QThread) pair per model the
user starts downloading, which is what makes multiple simultaneous
downloads possible (each runs on its own thread).
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Signal, Slot

from core.model_manager import DownloadCancelled


class ModelDownloadWorker(QObject):
    """
    Signals
    -------
    progress(key, stage, fraction)
        stage is "downloading" | "ready"; fraction is 0.0-1.0, or -1.0
        if the server didn't report a content length.

    finished(key, path)
        Emitted once the file is confirmed present (already-downloaded
        counts as an immediate finish).

    cancelled(key)
        Emitted when cancel() stopped the download before completion.
        The partial ".part" file is left on disk so a later download
        of the same key resumes instead of restarting.

    error(key, message)
    """

    progress = Signal(str, str, float)
    finished = Signal(str, str)
    cancelled = Signal(str)
    error = Signal(str, str)

    def __init__(self, logger, model_manager):
        super().__init__()

        self.logger = logger
        self.model_manager = model_manager

        self._cancel_event = threading.Event()

    @Slot(str)
    def download(self, key: str) -> None:

        self._cancel_event.clear()

        try:

            def _on_progress(stage: str, fraction: float) -> None:
                self.progress.emit(key, stage, fraction)

            path = self.model_manager.ensure_model_for(
                key,
                progress_callback=_on_progress,
                cancel_event=self._cancel_event,
            )

            self.finished.emit(key, str(path))

        except DownloadCancelled:

            self.logger.info("Download cancelled: %s", key)

            self.cancelled.emit(key)

        except Exception as exc:

            self.logger.exception(exc)

            self.error.emit(key, f"{type(exc).__name__}: {exc}")

    @Slot()
    def cancel(self) -> None:
        """
        Best-effort: checked between chunks inside ModelManager's
        download loop, so this stops promptly without needing to kill
        the thread.
        """

        self._cancel_event.set()