"""
model_manager.py

Local LLM model download & storage manager.

Responsibilities
----------------
- Know which local model to use (model catalog)
- Detect whether it's already downloaded
- Download it on first run (resumable, streaming, progress-reporting)
- Return the local file path for llm.py to load

No API keys. No internet required after the first successful run.

License note
------------
All models in MODEL_CATALOG are distributed under permissive,
commercial-friendly licenses (Apache-2.0), which is required since
this app is being sold. Always re-check the "LICENSE" file in the
model's Hugging Face repo before shipping a build, since a maintainer
could in theory change it upstream.
"""

from __future__ import annotations

import os
import shutil
import threading
from pathlib import Path
from typing import Callable, Optional

import requests


class DownloadCancelled(Exception):
    """Raised (and caught internally) when a user cancels an in-flight download."""
    pass


def gpu_offload_supported() -> bool:
    """
    Best-effort check for whether GPU layer offload is actually usable
    on this machine, used to drive the GPU Layers control in the
    settings UI (shows only "None" when this is False).

    Primary signal: llama-cpp-python exposes llama_supports_gpu_offload()
    on GPU-capable builds (CUDA/Metal/Vulkan wheels). The plain CPU
    wheel either lacks it or returns False.

    Fallback: if that API isn't available in the installed version,
    fall back to detecting an NVIDIA driver via `nvidia-smi` as a
    weaker secondary signal -- it doesn't guarantee the installed
    llama-cpp-python build can use it, but it's better than nothing.
    """

    try:
        import llama_cpp

        if hasattr(llama_cpp, "llama_supports_gpu_offload"):
            return bool(llama_cpp.llama_supports_gpu_offload())

    except Exception:
        pass

    try:
        return shutil.which("nvidia-smi") is not None
    except Exception:
        return False


def max_gpu_layers_for(key: str) -> int:
    """
    Total transformer layer count for a catalog entry -- the upper
    bound for "how many layers could possibly be offloaded" for that
    specific model.
    """

    if key not in MODEL_CATALOG:
        raise ValueError(f"Unknown model key '{key}'")

    return MODEL_CATALOG[key]["num_layers"]


# ============================================================
# Model catalog
# ============================================================
#
# repo_id / filename point at the exact quantized GGUF file.
# All entries below are re-quantized, license-preserved builds of
# Qwen2.5-Coder-Instruct (Apache-2.0) -- strong at reasoning, code,
# and general Q&A, and fast enough to run on CPU via llama.cpp.
#
MODEL_CATALOG = {

    "qwen2.5-coder-7b": {
        "repo_id": "bartowski/Qwen2.5-Coder-7B-Instruct-GGUF",
        "filename": "Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf",
        "license": "Apache-2.0",
        "context_length": 32768,
        "approx_size_gb": 4.7,
        "num_layers": 28,
        "description": (
            "Best overall quality: reasoning, coding, Q&A. "
            "Recommended default."
        ),
    },

    "qwen2.5-coder-3b": {
        "repo_id": "bartowski/Qwen2.5-Coder-3B-Instruct-GGUF",
        "filename": "Qwen2.5-Coder-3B-Instruct-Q4_K_M.gguf",
        "license": "Apache-2.0",
        "context_length": 32768,
        "approx_size_gb": 2.0,
        "num_layers": 36,
        "description": "Faster / lighter, still solid at coding & reasoning.",
    },

    "qwen2.5-coder-1.5b": {
        "repo_id": "bartowski/Qwen2.5-Coder-1.5B-Instruct-GGUF",
        "filename": "Qwen2.5-Coder-1.5B-Instruct-Q4_K_M.gguf",
        "license": "Apache-2.0",
        "context_length": 32768,
        "approx_size_gb": 1.1,
        "num_layers": 28,
        "description": "Lowest latency, for low-end machines.",
    },
}

DEFAULT_MODEL_KEY = "qwen2.5-coder-7b"

HF_DOWNLOAD_URL = "https://huggingface.co/{repo_id}/resolve/main/{filename}"


class ModelManager:
    """
    Handles first-run download and local storage/lookup of the
    GGUF model used for on-device inference.
    """

    def __init__(self, settings, logger, models_dir: Optional[Path] = None):

        self.settings = settings
        self.logger = logger

        model_key = getattr(settings.llm.local, "model_key", DEFAULT_MODEL_KEY)

        if model_key not in MODEL_CATALOG:

            self.logger.warning(
                "Unknown model_key '%s' in settings.json, "
                "falling back to default '%s'.",
                model_key,
                DEFAULT_MODEL_KEY,
            )

            model_key = DEFAULT_MODEL_KEY

        self.model_key = model_key
        self.model_info = MODEL_CATALOG[model_key]

        self.models_dir = models_dir or self._default_models_dir()
        self.models_dir.mkdir(parents=True, exist_ok=True)

    # --------------------------------------------------------

    @staticmethod
    def _default_models_dir() -> Path:
        """
        Per-user, writable location that survives app updates/reinstalls.

        Windows -> %LOCALAPPDATA%\\ScreenAssistant\\models
        Other   -> ~/.screenassistant/models
        """

        base = os.getenv("LOCALAPPDATA")

        if base:
            return Path(base) / "ScreenAssistant" / "models"

        return Path.home() / ".screenassistant" / "models"

    # --------------------------------------------------------

    def get_model_path(self) -> Path:
        return self.models_dir / self.model_info["filename"]

    def path_for(self, key: str) -> Path:
        if key not in MODEL_CATALOG:
            raise ValueError(f"Unknown model key '{key}'")
        return self.models_dir / MODEL_CATALOG[key]["filename"]

    # --------------------------------------------------------

    def is_model_present(self) -> bool:
        """
        Cheap presence check. We don't hash-verify on every launch
        (the file can be several GB) -- only existence + non-zero size.
        """

        path = self.get_model_path()

        return path.exists() and path.stat().st_size > 0

    def is_present_for(self, key: str) -> bool:

        path = self.path_for(key)

        return path.exists() and path.stat().st_size > 0

    # --------------------------------------------------------

    def set_active_key(self, key: str) -> None:
        """
        Switches which catalog entry is considered "active" (the one
        llm.py's LocalLLMClient will load), e.g. after the user picks
        a different model in the settings UI. Does NOT download it --
        caller should ensure_model() afterward.
        """

        if key not in MODEL_CATALOG:
            raise ValueError(f"Unknown model key '{key}'")

        self.model_key = key
        self.model_info = MODEL_CATALOG[key]

    # --------------------------------------------------------

    def list_catalog(self) -> list:
        """
        Every catalog entry with its live on-disk status -- what the
        Local Models settings tab renders as a table.
        """

        entries = []

        for key, info in MODEL_CATALOG.items():

            path = self.models_dir / info["filename"]

            entries.append({
                "key": key,
                "description": info["description"],
                "license": info["license"],
                "context_length": info["context_length"],
                "approx_size_gb": info["approx_size_gb"],
                "downloaded": path.exists() and path.stat().st_size > 0,
                "path": path,
                "active": key == self.model_key,
            })

        return entries

    # --------------------------------------------------------

    def delete_model(self, key: str) -> bool:
        """
        Removes a downloaded model file. Returns False (not raises) if
        it wasn't downloaded in the first place.

        Note: on Windows, deleting the file backing a currently-loaded
        llama.cpp model can fail with a PermissionError since it's
        typically memory-mapped -- callers should not allow deleting
        the active model while it's the one actually loaded/in use.
        """

        if key not in MODEL_CATALOG:
            raise ValueError(f"Unknown model key '{key}'")

        path = self.path_for(key)

        if not path.exists():
            return False

        path.unlink()

        self.logger.info("Deleted local model '%s' (%s).", key, path)

        return True

    # --------------------------------------------------------

    def ensure_model(
        self,
        progress_callback: Optional[Callable[[str, float], None]] = None,
    ) -> Path:
        """
        Ensures the ACTIVE model file exists locally, downloading it on
        first run. Returns the local path to the GGUF file.

        Parameters
        ----------
        progress_callback(stage, fraction)
            stage    : "downloading" | "ready"
            fraction : 0.0-1.0 (indeterminate as -1.0 if server omits
                       Content-Length)

            This is intentionally a plain callback (not a Qt signal) so
            it can be wired into a console print today and a GUI
            progress bar later without changing this module.
        """

        return self.ensure_model_for(self.model_key, progress_callback)

    def ensure_model_for(
        self,
        key: str,
        progress_callback: Optional[Callable[[str, float], None]] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> Path:
        """
        Like ensure_model(), but for an arbitrary catalog key -- used
        by the Local Models settings tab to pre-download a model that
        isn't necessarily the active one.

        cancel_event, if provided, is checked between chunks; if set,
        raises DownloadCancelled and leaves the partial ".part" file
        in place so a later download of the same key resumes instead
        of starting over.
        """

        if key not in MODEL_CATALOG:
            raise ValueError(f"Unknown model key '{key}'")

        info = MODEL_CATALOG[key]
        target_path = self.models_dir / info["filename"]

        if target_path.exists() and target_path.stat().st_size > 0:

            self.logger.info("Model already present: %s", target_path)

            if progress_callback:
                progress_callback("ready", 1.0)

            return target_path

        self.logger.info(
            "Downloading model '%s' (%s, ~%.1f GB)...",
            key,
            info["description"],
            info["approx_size_gb"],
        )

        self._download(info, target_path, progress_callback, cancel_event=cancel_event)

        if progress_callback:
            progress_callback("ready", 1.0)

        self.logger.info("Model ready: %s", target_path)

        return target_path

    # --------------------------------------------------------

    def _download(
        self,
        model_info: dict,
        target_path: Path,
        progress_callback: Optional[Callable[[str, float], None]],
        cancel_event: Optional[threading.Event] = None,
        chunk_size: int = 1024 * 1024,
    ) -> None:
        """
        Resumable streaming download straight from the Hugging Face CDN.

        Writes to a `.part` file first, then atomically renames on
        success, so a crash/interrupt mid-download never leaves a
        corrupt file mistaken for a finished one.
        """

        url = HF_DOWNLOAD_URL.format(
            repo_id=model_info["repo_id"],
            filename=model_info["filename"],
        )

        tmp_path = target_path.with_suffix(target_path.suffix + ".part")

        resume_from = tmp_path.stat().st_size if tmp_path.exists() else 0

        headers = {"Range": f"bytes={resume_from}-"} if resume_from else {}

        with requests.get(
            url,
            headers=headers,
            stream=True,
            timeout=60,
        ) as response:

            if response.status_code not in (200, 206):

                raise RuntimeError(
                    f"Failed to download model "
                    f"(HTTP {response.status_code}) from {url}"
                )

            # If we asked to resume but the server ignored Range and
            # sent the whole file again (status 200), start over.
            if resume_from and response.status_code == 200:
                resume_from = 0

            content_length = response.headers.get("Content-Length")

            total_bytes = (
                int(content_length) + resume_from
                if content_length is not None
                else None
            )

            downloaded = resume_from
            mode = "ab" if resume_from else "wb"

            with open(tmp_path, mode) as f:

                for chunk in response.iter_content(chunk_size=chunk_size):

                    if cancel_event is not None and cancel_event.is_set():

                        raise DownloadCancelled(
                            f"Download of '{model_info['filename']}' was cancelled."
                        )

                    if not chunk:
                        continue

                    f.write(chunk)
                    downloaded += len(chunk)

                    if progress_callback:

                        fraction = (
                            downloaded / total_bytes
                            if total_bytes
                            else -1.0
                        )

                        progress_callback("downloading", fraction)

        tmp_path.replace(target_path)

    # --------------------------------------------------------

    def model_context_length(self) -> int:
        return self.model_info["context_length"]