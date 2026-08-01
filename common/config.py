"""
config.py

Application configuration.

Loads settings.json
Validates configuration
Creates strongly-typed config objects.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
import json
from pathlib import Path


# ============================================================
# Dataclasses
# ============================================================

@dataclass(slots=True)
class HotkeysConfig:
    capture: str
    capture_area: str
    quit: str
    chat_focus: str
    chat_input: str
    chat_close: str
    chat_new: str


@dataclass(slots=True)
class CropConfig:
    top_cm: float
    bottom_cm: float


@dataclass(slots=True)
class CaptureConfig:
    monitor: int
    crop: CropConfig


@dataclass(slots=True)
class OCRConfig:
    engine: str


@dataclass(slots=True)
class LocalLLMConfig:
    model_key: str
    n_ctx: int
    n_gpu_layers: int
    n_threads: int
    temperature: float
    max_tokens: int


@dataclass(slots=True)
class ApiLLMConfig:
    base_url: str
    model: str
    api_key_env: str
    api_key: str
    temperature: float
    max_tokens: int


@dataclass(slots=True)
class LLMConfig:
    backend: str
    local: LocalLLMConfig
    api: ApiLLMConfig


@dataclass(slots=True)
class OverlayConfig:
    opacity: float
    font_size: int
    font_family: str
    padding: int

    text_color: str

    background_color: str
    background_alpha: float

    width_percent: float

    position: str


@dataclass(slots=True)
class ChatConfig:
    width_percent: float
    height_percent: float
    margin_px: int

    font_size: int
    font_family: str
    text_color: str

    background_color: str
    background_alpha: float

    user_accent_color: str
    assistant_accent_color: str

    input_height_px: int


@dataclass(slots=True)
class AppConfig:
    theme: str  # "dark" | "light"
    accent_color: str
    start_with_windows: bool
    check_for_updates: bool


@dataclass(slots=True)
class PersonalizationConfig:
    username: str
    about_you: str
    system_prompt: str
    things_to_avoid: str


@dataclass(slots=True)
class BillingConfig:
    credit_limit_per_prompt: float  # 0 = no limit


@dataclass(slots=True)
class PromptConfig:
    system: str


@dataclass(slots=True)
class LoggingConfig:
    enabled: bool
    directory: str
    level: str


@dataclass(slots=True)
class Settings:

    hotkeys: HotkeysConfig
    capture: CaptureConfig
    ocr: OCRConfig
    llm: LLMConfig
    overlay: OverlayConfig
    chat: ChatConfig
    app: AppConfig
    personalization: PersonalizationConfig
    billing: BillingConfig
    prompt: PromptConfig
    logging: LoggingConfig


# ============================================================
# Loader
# ============================================================

SETTINGS_FILE = Path("settings.json")


def load_settings() -> Settings:

    if not SETTINGS_FILE.exists():

        raise FileNotFoundError(
            "settings.json not found."
        )

    with open(
        SETTINGS_FILE,
        "r",
        encoding="utf-8",
    ) as f:

        data = json.load(f)

    return Settings(

        hotkeys=HotkeysConfig(
            **data["hotkeys"]
        ),

        capture=CaptureConfig(

            monitor=data["capture"]["monitor"],

            crop=CropConfig(
                **data["capture"]["crop"]
            ),
        ),

        ocr=OCRConfig(
            **data["ocr"]
        ),

        llm=LLMConfig(

            backend=data["llm"]["backend"],

            local=LocalLLMConfig(
                **data["llm"]["local"]
            ),

            api=ApiLLMConfig(
                **data["llm"]["api"]
            ),
        ),

        overlay=OverlayConfig(
            **data["overlay"]
        ),

        chat=ChatConfig(
            **data["chat"]
        ),

        app=AppConfig(
            **data["app"]
        ),

        personalization=PersonalizationConfig(
            **data["personalization"]
        ),

        billing=BillingConfig(
            **data["billing"]
        ),

        prompt=PromptConfig(
            **data["prompt"]
        ),

        logging=LoggingConfig(
            **data["logging"]
        ),
    )


# ============================================================
# Saver
# ============================================================

def _settings_to_dict(settings: Settings) -> dict:

    return {

        "hotkeys": dataclasses.asdict(settings.hotkeys),

        "capture": {
            "monitor": settings.capture.monitor,
            "crop": dataclasses.asdict(settings.capture.crop),
        },

        "ocr": dataclasses.asdict(settings.ocr),

        "llm": {
            "backend": settings.llm.backend,
            "local": dataclasses.asdict(settings.llm.local),
            "api": dataclasses.asdict(settings.llm.api),
        },

        "overlay": dataclasses.asdict(settings.overlay),

        "chat": dataclasses.asdict(settings.chat),

        "app": dataclasses.asdict(settings.app),

        "personalization": dataclasses.asdict(settings.personalization),

        "billing": dataclasses.asdict(settings.billing),

        "prompt": dataclasses.asdict(settings.prompt),

        "logging": dataclasses.asdict(settings.logging),
    }


def save_settings(settings: Settings) -> None:
    """
    Writes the (possibly mutated in place) Settings object back to
    settings.json, atomically -- write to a temp file first, then
    replace, so a crash mid-save never leaves a corrupt/partial file.

    Called by the settings UI after the user applies a change. The
    caller is responsible for calling validate() first and for
    triggering whatever live-reload is needed (LLM client, hotkeys,
    theme) -- this function only persists to disk.
    """

    data = _settings_to_dict(settings)

    tmp_path = SETTINGS_FILE.with_suffix(SETTINGS_FILE.suffix + ".tmp")

    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)

    tmp_path.replace(SETTINGS_FILE)


# ============================================================
# Validation
# ============================================================

def validate(settings: Settings):

    if settings.capture.monitor < 1:
        raise ValueError(
            "Monitor index must be >= 1."
        )

    if settings.overlay.opacity <= 0:
        raise ValueError(
            "Overlay opacity must be > 0."
        )

    if settings.overlay.opacity > 1:
        raise ValueError(
            "Overlay opacity must be <= 1."
        )

    if settings.overlay.width_percent <= 0:
        raise ValueError(
            "Overlay width_percent must be > 0."
        )

    if settings.overlay.width_percent > 1:
        raise ValueError(
            "Overlay width_percent must be <= 1."
        )

    if not (0 < settings.chat.width_percent <= 1):
        raise ValueError(
            "chat.width_percent must be > 0 and <= 1."
        )

    if not (0 < settings.chat.height_percent <= 1):
        raise ValueError(
            "chat.height_percent must be > 0 and <= 1."
        )

    if not (0 < settings.chat.background_alpha <= 1):
        raise ValueError(
            "chat.background_alpha must be > 0 and <= 1."
        )

    if settings.app.theme not in ("dark", "light"):
        raise ValueError(
            "app.theme must be 'dark' or 'light'."
        )

    if settings.billing.credit_limit_per_prompt < 0:
        raise ValueError(
            "billing.credit_limit_per_prompt cannot be negative."
        )

    if settings.llm.backend not in ("local", "api"):
        raise ValueError(
            "llm.backend must be 'local' or 'api'."
        )

    active_llm = (
        settings.llm.local
        if settings.llm.backend == "local"
        else settings.llm.api
    )

    if active_llm.temperature < 0:
        raise ValueError(
            "Temperature cannot be negative."
        )

    if active_llm.max_tokens < 1:
        raise ValueError(
            "max_tokens must be positive."
        )

    if settings.capture.crop.top_cm < 0:
        raise ValueError(
            "top_cm cannot be negative."
        )

    if settings.capture.crop.bottom_cm < 0:
        raise ValueError(
            "bottom_cm cannot be negative."
        )