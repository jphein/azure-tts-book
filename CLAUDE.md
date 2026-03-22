# CLAUDE.md — unshuffled

Azure TTS text-to-audio converter. Turns text files into narrated MP3s using Azure AI Speech REST API.

## What This Is

A single-script CLI tool that converts text files to high-quality audio (MP3). Handles long texts by chunking on paragraph boundaries, synthesizing each chunk via Azure TTS, and concatenating the results. Includes automatic paragraph normalization to fix Notion-exported text.

## Tech Stack

- Python 3.7+
- Azure AI Speech REST API (not the SDK — direct HTTP)
- SSML for voice markup
- Dependencies: `requests`, `python-dotenv`

## Commands

```bash
# Install
pip install -r requirements.txt

# Convert text to audio
python3 tts_convert.py input.txt
python3 tts_convert.py input.txt -o output.mp3 -v en-US-Andrew:DragonHDLatestNeural

# List available voices
python3 tts_convert.py --list-voices

# Run tests
pytest test_tts_convert.py -v
```

## Key Files

| File | Purpose |
|------|---------|
| `tts_convert.py` | Main script — chunking, SSML generation, Azure API calls, reporting |
| `test_tts_convert.py` | Unit tests (pytest) — audit, normalize, split, SSML, formatting |
| `requirements.txt` | Python deps: requests, python-dotenv |
| `text/` | Source text files (git-ignored) |
| `audio/` | Generated MP3 output (git-ignored) |
| `.env` | Credentials (git-ignored) — copy from `.env.example` |

## Credentials

Priority order: CLI flags (`-k`, `-r`, `-v`) > env vars (`AZURE_SPEECH_KEY`, `AZURE_SPEECH_REGION`, `AZURE_SPEECH_VOICE`) > `tts_defaults.json`

## How It Works

1. Reads input text, audits for oversized paragraphs (> 1200 words)
2. Auto-normalizes paragraph breaks if needed (single `\n` -> double `\n\n`), writes back in-place
3. Splits text into chunks respecting word limit per Azure ~10 min audio cap
4. Converts each chunk to SSML with XML escaping and 800ms paragraph pauses
5. POSTs to Azure TTS endpoint, caches chunks to disk, retries on network errors
6. Concatenates chunk MP3s into final output, prints per-chunk timing report

## Gotchas

- Azure HD voices (`*:DragonHDLatestNeural`) fail silently with `InvalidChunkLength` on oversized paragraphs — the auto-normalize step prevents this
- Output format is `audio-48khz-192kbitrate-mono-mp3` (hardcoded)
- Chunk cache directory (`output.mp3.chunks/`) is created during synthesis and cleaned up after — useful for resuming interrupted conversions
- The script modifies the input file in-place when normalizing paragraph structure
