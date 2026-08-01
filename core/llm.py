"""
llm.py

LLM Client -- local (on-device GGUF via llama.cpp) or API
(any OpenAI-compatible chat-completions endpoint, e.g. OpenRouter).

The active backend is picked at runtime from settings.llm.backend.
Both client classes expose the same interface:

    generate(user_input) -> str
        Blocking call, returns the full response.

    generate_stream(user_input) -> Iterator[str]
        Yields the response incrementally, piece by piece, as the
        model produces it. Each yielded value is a small chunk of
        NEW text (a "delta"), not the accumulated text so far.

user_input accepts two shapes:
    - str: a single user turn (used by the screen-capture pipeline).
      Wrapped into [{"role": "user", "content": user_input}].
    - list[dict]: a pre-built list of {"role", "content"} turns, e.g.
      prior assistant messages + the latest user message (used by the
      chat overlay). The system prompt is still added automatically.

so worker.py / chat_worker.py don't need to know or care which
backend they're holding, or build the system turn themselves.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Iterator, List, Optional, Union

import requests
from dotenv import load_dotenv

load_dotenv()


ChatInput = Union[str, List[dict]]


def _build_messages(system_prompt: str, user_input: ChatInput) -> List[dict]:

    if isinstance(user_input, str):
        turns = [{"role": "user", "content": user_input}]
    else:
        turns = list(user_input)

    return [{"role": "system", "content": system_prompt}, *turns]


# ============================================================
# Local backend (on-device, no network, no API key)
# ============================================================

class LocalLLMClient:

    def __init__(self, settings, logger, model_path: Path):

        from llama_cpp import Llama  # imported lazily: not needed for API backend

        self.logger = logger
        self.system_prompt = settings.prompt.system

        local_cfg = settings.llm.local

        n_threads = local_cfg.n_threads or (os.cpu_count() or 4)

        self.max_tokens = local_cfg.max_tokens
        self.temperature = local_cfg.temperature

        self.logger.info(
            "Loading local model: %s (n_ctx=%d, n_gpu_layers=%d, n_threads=%d)",
            model_path,
            local_cfg.n_ctx,
            local_cfg.n_gpu_layers,
            n_threads,
        )

        # n_gpu_layers=-1 offloads every layer it can if the installed
        # llama-cpp-python build has GPU support compiled in; on the
        # plain CPU wheel this is simply ignored and it runs on CPU.
        self.llm = Llama(
            model_path=str(model_path),
            n_ctx=local_cfg.n_ctx,
            n_gpu_layers=local_cfg.n_gpu_layers,
            n_threads=n_threads,
            n_batch=512,
            verbose=False,
        )

        # llama.cpp keeps state (KV cache) on this single Llama instance.
        # It's shared between the screen-capture pipeline and the chat
        # overlay, so concurrent generate calls must be serialized --
        # otherwise two in-flight generations would corrupt each other.
        self._lock = threading.Lock()

        self.logger.info("Local model loaded.")

    # --------------------------------------------------------

    def generate_stream(self, user_input: ChatInput) -> Iterator[str]:

        self.logger.info("Streaming response from local model...")

        messages = _build_messages(self.system_prompt, user_input)

        with self._lock:

            stream = self.llm.create_chat_completion(
                messages=messages,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                stream=True,
            )

            got_any = False

            for chunk in stream:

                try:
                    delta = chunk["choices"][0]["delta"]
                except (KeyError, IndexError):
                    continue

                content = delta.get("content")

                if content:
                    got_any = True
                    yield content

            if not got_any:
                raise RuntimeError("Model returned an empty response.")

    # --------------------------------------------------------

    def generate(self, user_input: ChatInput) -> str:
        """
        Blocking convenience wrapper: collects the full stream.
        """

        return "".join(self.generate_stream(user_input)).strip()


# ============================================================
# API backend (any OpenAI-compatible chat completions endpoint)
# ============================================================

class ApiLLMClient:

    def __init__(self, settings, logger):

        self.logger = logger
        self.system_prompt = settings.prompt.system

        api_cfg = settings.llm.api

        self.base_url = api_cfg.base_url
        self.model = api_cfg.model
        self.temperature = api_cfg.temperature
        self.max_tokens = api_cfg.max_tokens

        # Resolve the API key: settings.json value wins if set,
        # otherwise fall back to the named environment variable
        # (loaded from .env via python-dotenv above).
        self.api_key = api_cfg.api_key or os.getenv(api_cfg.api_key_env, "")

        if not self.api_key:
            raise RuntimeError(
                f"No API key found. Set '{api_cfg.api_key_env}' in your "
                f".env / environment, or put a key directly in "
                f"settings.json under llm.api.api_key."
            )

    # --------------------------------------------------------

    def generate_stream(self, user_input: ChatInput) -> Iterator[str]:

        self.logger.info("Streaming request to API backend (%s)...", self.model)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            # Disable compression.
            "Accept-Encoding": "identity",
            "Cache-Control": "no-cache",
        }

        payload = {
            "model": self.model,
            "messages": _build_messages(self.system_prompt, user_input),
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": True,
        }

        response = requests.post(
            self.base_url,
            headers=headers,
            json=payload,
            stream=True,
            timeout=120,
        )

        if response.status_code != 200:

            raise RuntimeError(
                f"API Error ({response.status_code})\n{response.text}"
            )

        got_any = False

        # Server-Sent-Events format: lines like "data: {json}",
        # terminated by a line "data: [DONE]".
        for raw_line in response.iter_lines(chunk_size=1, decode_unicode=True):

            if not raw_line:
                continue

            if not raw_line.startswith("data:"):
                continue

            data_str = raw_line[len("data:"):].strip()

            if data_str == "[DONE]":
                break

            try:
                chunk = json.loads(data_str)
            except json.JSONDecodeError:
                continue

            try:
                delta = chunk["choices"][0]["delta"]
            except (KeyError, IndexError):
                continue

            content = delta.get("content")

            if content:
                got_any = True
                yield content

        if not got_any:
            raise RuntimeError("Model returned an empty response.")

    # --------------------------------------------------------

    def generate(self, user_input: ChatInput) -> str:
        """
        Blocking convenience wrapper: collects the full stream.
        """

        return "".join(self.generate_stream(user_input)).strip()


# ============================================================
# Factory
# ============================================================

def create_llm_client(settings, logger, model_path: Optional[Path] = None):
    """
    Builds the right client for settings.llm.backend ("local" or "api").

    model_path is required (and only used) for the local backend --
    it comes from model_manager.ModelManager.ensure_model().
    """

    backend = settings.llm.backend

    if backend == "local":

        if model_path is None:
            raise RuntimeError(
                "backend is 'local' but no model_path was provided. "
                "Call ModelManager.ensure_model() first."
            )

        return LocalLLMClient(settings, logger, model_path)

    if backend == "api":
        return ApiLLMClient(settings, logger)

    raise ValueError(
        f"Unknown llm.backend '{backend}' in settings.json "
        f"(expected 'local' or 'api')."
    )