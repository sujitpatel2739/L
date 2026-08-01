# Screen Assistant

A lightweight desktop AI assistant for Windows.

The application listens for a global hotkey, captures the current screen, crops configurable regions, extracts text using OCR, sends the extracted information to a configurable LLM, and displays the response in a transparent desktop overlay.

---

## Features

- Global hotkey activation
- Full-screen screenshot
- DPI-aware cropping
- Fast local OCR (RapidOCR)
- Configurable system prompt
- OpenAI-compatible LLM API
- Transparent click-through overlay
- Non-blocking processing
- Multi-monitor support
- Thread-safe architecture
- Configurable settings
- Logging
- Error overlay

---

## Project Structure

```
ScreenAssistant/
│
├── main.py
├── config.py
├── capture.py
├── hotkeys.py
├── llm.py
├── ocr.py
├── overlay.py
├── worker.py
├── utils.py
│
├── settings.json
├── requirements.txt
└── README.md
```

---

## Installation

Create virtual environment

```
python -m venv .venv
```

Activate

Windows

```
.venv\Scripts\activate
```

Install packages

```
pip install -r requirements.txt
```

---

## Running

```
python main.py
```

---

## Default Hotkeys

Capture

```
Ctrl + Alt + Space
```

Quit

```
Ctrl + Alt + Q
```

---

## OCR Engine

RapidOCR ONNX Runtime

Runs completely locally.

---

## Supported LLM Providers

Any OpenAI-compatible endpoint.

Examples

- OpenAI
- OpenRouter
- LM Studio
- Ollama
- vLLM

---

## Configuration

Edit

```
settings.json
```

No code changes required.

---

## Logging

Application log

```
logs/app.log
```

---

## License

Personal Project