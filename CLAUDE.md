# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository layout

This working directory (`C:\VoiceTranslateApp`) is **not** itself a git repo. It contains two distinct things:

- **`main.py`** (root) — a standalone prototype FastAPI service ("CuraGo 護家通", elder-care voice
  assistant) that runs on port 8001. It loads three models directly via `transformers`/`faster-whisper`
  at import time (ASR pipeline `adi-gov-tw/Taiwan-Tongues-ASR-CE-pretrained-v2.0`, a zero-shot NLU
  classifier `hfl/chinese-bert-wwm-ext`, and Facebook `m2m100_418M` for translation), does keyword-based
  Taiwanese/Hakka dialect detection against `Taiwan-Tongues-ASR-CE/api/keywords.json`, and exposes a
  single endpoint `POST /api/voice/listen-elder`. This is throwaway/experimental glue code, not part of
  the upstream ASR project.
- **`Taiwan-Tongues-ASR-CE/`** — a separate git repository (the actual project — see its own README).
  This is the "real" codebase: an ASR training pipeline plus a production FastAPI service. Almost all
  substantive work happens here; treat this subdirectory as the project root when editing code in it.
- **`venv/`** (root) — Python 3.10.11 venv for `main.py`. Do not confuse with `Taiwan-Tongues-ASR-CE`'s
  own `train_env/` / `asr_api/` venvs (see below) — the three are independent and install different,
  sometimes incompatible, package versions.

Everything below describes `Taiwan-Tongues-ASR-CE/`.

## Two independent Python environments — do not mix

The project deliberately keeps **two separate venvs** because the training stack and the API stack need
conflicting `transformers` versions:

- `train_env/` (root `requirements.txt`): `transformers>=5,<6`. Used for `train_asr.py`, `evaluate_asr.py`.
- `asr_api/` (`api/requirements.txt`): `transformers>=4.24,<5`, pinned down by the `zhpr` punctuation
  package. Used for everything under `api/`.

Setup (auto-detects Python >=3.10, picks CUDA 12.4 torch if `nvidia-smi` finds a GPU, else CPU torch):

```bash
# training env
bash setup_train_env.sh          # or setup_train_env.bat on Windows
# api env
bash api/setup_api_env.sh        # or api\setup_api_env.bat on Windows
```

`train.sh`/`train.bat` and `api/start_app.sh`/`api/start_app.bat` auto-activate the correct venv — you
normally don't need to activate manually. If invoking Python scripts directly, activate the matching venv
first (`train_env` for root-level scripts, `asr_api` for anything in `api/`).

## Common commands

```bash
# Fine-tune / train (auto-activates train_env)
bash train.sh                    # Linux/macOS/Git Bash — or train.bat on Windows

# Multi-lingual mixed training: override DATASET_CONFIG_NAME to combine datasets,
# each tagged with its own Whisper language code via "ds:lang"
DATASET_CONFIG_NAME="train_ds_01:zh+train_ds_02:en+train_ds_id:id" bash train.sh

# Batch transcription + CER evaluation against a folder of audio (needs models/ populated)
python evaluate_asr.py <audio_folder_path>

# Start the combined API service (port 5000: file ASR + auth + streaming ASR)
bash api/start_app.sh            # or api\start_app.bat — auto-detects asr_api/train_env/.venv/venv

# Manual smoke test of the running API (see api/README.md "手動驗證" for the full sequence)
curl http://127.0.0.1:5000/api/health
curl http://127.0.0.1:5000/stream/health
```

There is no automated test suite yet (`pytest`/`httpx` are reserved in `api/requirements.txt` for future
use). `api/test_ai_module.py` and `api/check_module.py` are manual ad-hoc scripts (Gemini-key smoke
tests), not part of a CI-run suite.

Before running the API, copy `api/.env.example` to `api/.env` and set `ASR_API_JWT_SECRET` and
`ASR_API_BOOTSTRAP_ADMIN_PASSWORD` — the service falls back to insecure defaults if `.env` is missing
(fine for local demo, never for anything shared).

Inference/training model weights (`models/`, `model_for_finetune/`) are not checked in — download from
the [adi-gov-tw Hugging Face org](https://huggingface.co/adi-gov-tw) and place them at the project root.

## Architecture

### Training pipeline (root level)

- `train_asr.py` — HuggingFace `Seq2SeqTrainer`-based fine-tuning script for Whisper-family models.
  Reads TSV-formatted corpora (`path`, `sentence` columns) from `sample_corpus/<dataset>/{train,test,
  validated}.tsv` + `clips/`. Supports mixing multiple datasets with per-dataset language codes via
  `--dataset_config_name "ds1:zh+ds2:en+ds3:id"` (falls back to the single `--language` flag for
  datasets without an explicit `:lang` suffix — kept for backward compatibility).
- `evaluate_asr.py` — batch-transcribes a folder of audio (`.wav/.mp3/.flac/.m4a/.aac`) using the
  CTranslate2 models in `models/`, then runs a fixed Traditional-Chinese post-processing pipeline in
  order: phrase replacement (extend via `replace_words()`) → Simplified→Traditional (OpenCC `s2tw`) →
  Chinese numerals→Arabic (`cn2an`) → fullwidth→halfwidth (NFKC) → strip common CJK/Latin punctuation →
  lowercase. If a matching ground-truth `.txt`/`_transcript.txt`/etc. exists alongside an audio file, it
  auto-computes CER and writes a JSON report.
- `cer.py` — standalone CER computation utility.

### API service (`api/`) — single port 5000

`api/app.py` is the aggregation entrypoint: it mounts `file_asr.py`'s router directly (paths stay
`/api/...`) and mounts `streaming_asr.py`'s whole app under `/stream`, plus registers the streaming
WebSocket route at the top level (`/ws/v1/transcript`). All three sub-apps' startup/shutdown logic is
invoked manually from a shared `lifespan` context manager in `app.py`, since only one app's native
lifespan actually fires when sub-apps are mounted this way — if you add a new sub-app with its own
startup hook, wire it into that `lifespan()` function explicitly.

- **`file_asr.py`** — task-based offline transcription over plain HTTP. Upload creates a task row in
  SQLite (`auth.db`, table `subtitle_tasks`) and processes in the background; poll by task id for status
  (numeric status codes — see `api/README.md` for the full table) and progress, then fetch TXT/SRT/DIA
  output. After transcription completes it hands segments to `punctuation.py` for restoration before
  persisting the final text.
- **`streaming_asr.py`** — real-time WebSocket ASR (`/ws/v1/transcript`, Int16 PCM mono 16kHz in). Built
  on the `stt_streaming/` package: `src/asr/asr_factory.py` selects the ASR backend (`faster_whisper_asr.py`
  wraps `faster-whisper`/CTranslate2), `src/vad/vad_factory.py` selects VAD implementation (`simple_vad.py`
  vs `pyannote_vad.py`), and `src/buffering_strategy/` controls how incoming audio chunks are windowed
  before being sent to ASR. `src/client.py` models one connected WS client's state.
- **`auth_api.py` / `auth_shared.py`** — JWT-based auth (PyJWT, HS256 by default) backed by the same
  SQLite `auth.db`. `auth_shared.py` reads `ASR_API_JWT_SECRET`/`ASR_API_JWT_ALGORITHM` from the
  environment at import time — `.env` must be loaded before this module is imported (see the load order
  comments at the top of `app.py`/`streaming_asr.py`).
- **`punctuation.py`** — lazy-loaded zhpr (`p208p2002/zh-wiki-punctuation-restore`) post-processor that
  adds `，、。？！；` to raw Whisper segments. Any load/inference failure falls back to the unpunctuated
  original text rather than failing the task; controlled via `ASR_API_ENABLE_PUNCTUATION` and related
  `ASR_API_PUNCTUATION_*` env vars.
- **`config.py`** — just `MODEL_DEVICE`/`MODEL_COMPUTE_TYPE` (`auto` resolves via
  `torch.cuda.is_available()` at the point of use in `faster_whisper_asr.py`).
- **Windows CUDA DLL note**: both `app.py` and `streaming_asr.py` manually add the pip-installed
  `nvidia.cudnn`/`nvidia.cublas` package `bin/` dirs to the DLL search path (`os.add_dll_directory`)
  *before* importing `faster_whisper`, because these are PEP 420 namespace packages with no `__file__`.
  If you reorder imports in these files, keep that block ahead of any `faster_whisper` import on Windows.
- **`ai_translator.py`** — an alternate, experimental analysis path using the Gemini API
  (`GEMINI_API_KEY` in `api/.env`) instead of the local M2M100 model in root `main.py`; exercised only by
  the manual scripts `test_ai_module.py`/`check_module.py`, not wired into `app.py`.

### Data format

Corpora under `sample_corpus/<dataset>/` follow Common Voice-style TSV: `train.tsv`/`test.tsv`/
`validated.tsv` with tab-separated `path` (relative to `clips/`) and `sentence` columns; `clips/` holds
the audio and may be nested arbitrarily deep.
