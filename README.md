# Azure TTS Convert

Convert text files to audio using Azure AI Speech (Text-to-Speech REST API).

Long texts are automatically chunked by paragraph to stay within Azure API limits, then concatenated into a single MP3. Per-chunk timing is printed live as each request completes, and a summary table is shown at the end.

Before synthesis, the script audits the input file for oversized paragraphs and automatically normalizes paragraph structure if needed (see [Text Audit & Auto-fix](#text-audit--auto-fix)).

## Requirements

- Python 3.7+
- An [Azure Speech Services](https://azure.microsoft.com/en-us/products/ai-services/text-to-speech) API key

```bash
pip install -r requirements.txt
```

## Setup

Copy `.env.example` to `.env` and fill in your credentials:

```bash
cp .env.example .env
```

```ini
AZURE_SPEECH_KEY=your_azure_speech_key_here
AZURE_SPEECH_REGION=westus2
AZURE_SPEECH_VOICE=en-US-Ava:DragonHDLatestNeural
```

`.env` is git-ignored and never committed. The script loads it automatically via `python-dotenv`.

**Priority order:** CLI flags → environment variables (`.env` or shell)

## Usage

```bash
# Basic — reads credentials from .env
python3 tts_convert.py chapter.txt

# Custom output path
python3 tts_convert.py chapter.txt -o audiobook.mp3

# Override voice
python3 tts_convert.py chapter.txt -v en-US-FableTurboMultilingualNeural

# Override region and key via flags
python3 tts_convert.py chapter.txt -r eastus -k YOUR_KEY

# List popular voices
python3 tts_convert.py --list-voices
```

## Options

| Flag | Description |
|------|-------------|
| `input` | Input text file (positional) |
| `-o, --output` | Output MP3 path (default: input filename with `.mp3`) |
| `-v, --voice` | Azure voice name |
| `-r, --region` | Azure region |
| `-k, --key` | Azure Speech API key |
| `--list-voices` | Print popular Azure neural voices and exit |
| `--words-per-chunk` | Max words per API request (default: 1200) |

## Output

- Format: MP3, 48 kHz, 192 kbps mono
- Paragraph breaks are converted to 800ms pauses for natural narration
- Per-chunk progress printed live: size, time, and MB/s
- Summary table on completion: words, audio size, time, and speed per chunk

## Text Audit & Auto-fix

Azure HD voices (`*:DragonHDLatestNeural`) fail silently with an `InvalidChunkLength` error when a single synthesized chunk would produce more than ~10 minutes of audio. This happens when a "paragraph" — the unit the script can't split — is too long.

A common cause is text exported from Notion or similar tools, which uses a single newline (`\n`) between literary paragraphs rather than a blank line (`\n\n`). The chunker splits only on `\n\n`, so an entire section becomes one giant paragraph exceeding the limit.

Before synthesis, the script automatically:

1. **Audits** — splits the file by `\n\n` and flags any paragraph over `--words-per-chunk` (default 1200)
2. **Normalizes** — if issues are found, expands single newlines to double newlines and collapses runs of 3+ newlines to 2, then writes the fixed content back to the file in-place
3. **Re-audits** — confirms the fix worked; warns (but does not abort) if any paragraph is still over the limit after normalization

Example output when a fix is applied:

```
Reading: chapter.txt
  ⚠  2 oversized paragraph(s) detected (>1200 words):
       para 3: 1445 words — "Seren went outside on the fifth day, and it was not their choice.↵↵The communal..."
       para 7: 1680 words — "They meant to go straight back to the alcove.↵↵But the crystalline trees..."
  Normalizing: expanding single newlines to double newlines...
  ✓  312 single newline(s) expanded — file updated in-place
  ✓  Paragraph structure OK (no paragraphs exceed 1200 words)
```

## Files

| File | Committed | Description |
|------|-----------|-------------|
| `tts_convert.py` | ✅ | Main conversion script |
| `requirements.txt` | ✅ | Python dependencies |
| `.env.example` | ✅ | Credential template — copy to `.env` and fill in |
| `.env` | ❌ | Your actual credentials (git-ignored) |
| `text/` | ❌ | Source text files (git-ignored) |
| `audio/` | ❌ | Generated MP3s (git-ignored — large binaries) |
| `.claude/` | ❌ | Local Claude Code context (git-ignored) |
