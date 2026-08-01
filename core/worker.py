"""
Background worker.

Runs the expensive pipeline without blocking the UI.

Pipeline

Capture
    ↓
Crop
    ↓
OCR
    ↓
LLM
"""

from __future__ import annotations

import traceback

from PySide6.QtCore import QObject, Signal, Slot

from core.capture import ScreenCapture
from core.chat_models import build_personalization_preamble
from core.ocr import OCR


class Worker(QObject):
    """
    Executes the processing pipeline.

    Signals
    -------
    chunk(str)
        Emitted repeatedly while the LLM is streaming. Carries the
        FULL accumulated response text so far (not just the delta),
        so a listener can just repaint/overwrite with it directly.

    finished(str)
        Final, complete response -- emitted once, after the last
        chunk, when the stream ends.

    error(str)
        Error message.

    busy_changed(bool)
        Worker busy state.
    """

    chunk = Signal(str)
    finished = Signal(str)
    error = Signal(str)
    extracted = Signal(str)
    busy_changed = Signal(bool)

    def __init__(self, settings: dict, logger, llm_client):
        super().__init__()

        self.settings = settings
        self.logger = logger

        self.capture = ScreenCapture(settings, logger)
        self.ocr = OCR(settings, logger)

        # Shared LLM client -- built once in main.py and also handed to
        # the chat overlay, so the (possibly multi-GB) local model is
        # only ever loaded into memory a single time.
        self.llm = llm_client

        self._busy = False


    @Slot(object)
    def process(self, region=None):
        self.run(region)
    
    # -----------------------------------------------------

    @property
    def busy(self) -> bool:
        return self._busy

    # -----------------------------------------------------

    def _set_busy(self, value: bool):

        self._busy = value
        self.busy_changed.emit(value)

    # -----------------------------------------------------

    @Slot(object)
    def run(self, region=None):
        """
        Execute one complete pipeline.
        """

        if self.busy:
            return

        self._set_busy(True)

        try:

            self.logger.info("Capturing screen...")

            if region is not None:
                self.logger.info(
                    "Capturing selected region: %s",
                    str(region),
                )
                left, top, width, height = region
                image = self.capture.capture_region(
                    left,
                    top,
                    width,
                    height,
                )
            else:
                image = self.capture.capture()

            self.logger.info("Running OCR...")

            extracted_text = self.ocr.extract(image)

            # Notify listeners that OCR extracted text is available
            try:
                self.extracted.emit(extracted_text)
            except Exception:
                # best-effort; don't fail the pipeline if no listener
                pass

            if not extracted_text.strip():
                raise RuntimeError("No text detected.")

            prompt = self._build_prompt(extracted_text)

            self.logger.info("Querying LLM (streaming)...")

            accumulated = ""

            for piece in self.llm.generate_stream(prompt):

                accumulated += piece

                self.chunk.emit(accumulated)

            accumulated = accumulated.strip()

            if not accumulated:
                raise RuntimeError("Model returned an empty response.")

            self.finished.emit(accumulated)

            self.logger.info("Finished.")

        except Exception as exc:

            self.logger.exception(exc)

            short_message = f"{type(exc).__name__}: {exc}"

            self.error.emit(short_message)

        finally:

            self._set_busy(False)

    # -----------------------------------------------------

    def _build_prompt(self, text: str) -> str:
        """
        Construct final user-turn prompt. The system prompt itself is
        applied separately, as the "system" role, inside LLMClient.
        """

        personalization = build_personalization_preamble(self.settings)

        return (
            personalization
            + "===== OCR START =====\n\n"
            f"{text}\n\n"
            "===== OCR END ====="
        )

    # -----------------------------------------------------

    def reload_llm(self, settings, llm_client):
        """
        Swap in a new (already-built) LLM client, e.g. after the user
        flips the local/api backend switch in a future settings UI.

        Must not be called while self.busy is True.
        """

        self.settings = settings
        self.llm = llm_client
        self.logger.info("LLM backend switched to '%s'.", settings.llm.backend)

    # -----------------------------------------------------

    def dump_trace(self):

        return traceback.format_exc()