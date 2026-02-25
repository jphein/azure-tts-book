#!/usr/bin/env python3
"""Convert text files to audio using Azure AI Speech (REST API).

Long texts are automatically chunked by paragraph to stay within Azure API
limits, then concatenated into a single MP3. Per-chunk timing is printed live
and a summary table is shown on completion.

Text Audit & Auto-fix
---------------------
Before synthesis, the input file is audited for oversized paragraphs — paragraphs
that exceed the words-per-chunk limit and therefore cannot be split across chunks.
This is the root cause of Azure HD voice InvalidChunkLength failures (empty chunked
response body, even after retries).

If oversized paragraphs are found, the file is automatically normalized in-place:
  - Single newlines are expanded to double newlines (paragraph breaks)
  - Three or more consecutive newlines are collapsed to two

This fixes texts exported from Notion and similar tools that use a single newline
between literary paragraphs rather than a blank line. After normalization the audit
runs again; any paragraph still over the limit triggers a manual-review warning but
does not abort synthesis.

Credentials are resolved in priority order:
  1. CLI flags  (-k / -r / -v)
  2. Environment variables  (AZURE_SPEECH_KEY, AZURE_SPEECH_REGION, AZURE_SPEECH_VOICE)
     Loaded automatically from a .env file in the working directory if present.
  3. tts_defaults.json  (next to this script, fallback / convenience)

Usage:
  python tts_convert.py input.txt [options]

Examples:
  python tts_convert.py chapter1.txt
  python tts_convert.py chapter1.txt -o audiobook.mp3 -v en-US-GuyNeural
  python tts_convert.py chapter1.txt -v en-US-FableTurboMultilingualNeural
  python tts_convert.py --list-voices
"""

import argparse
import json
import re
import requests
import shutil
import xml.sax.saxutils as saxutils
import sys
import os
import time

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed; export env vars manually or use tts_defaults.json

DEFAULTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tts_defaults.json")


def load_defaults():
    """Load defaults from tts_defaults.json next to this script."""
    if os.path.isfile(DEFAULTS_FILE):
        with open(DEFAULTS_FILE, "r") as f:
            return json.load(f)
    return {}

# Azure TTS limit: ~10 min audio per request (~1500 words).
DEFAULT_MAX_WORDS = 1200

POPULAR_VOICES = [
    ("en-US-Ava:DragonHDLatestNeural", "Female, HD, natural (best quality)"),
    ("en-US-Andrew:DragonHDLatestNeural", "Male, HD, natural (best quality)"),
    ("en-US-AvaMultilingualNeural", "Female, multilingual, natural"),
    ("en-US-AndrewMultilingualNeural", "Male, multilingual, natural"),
    ("en-US-JennyNeural", "Female, conversational"),
    ("en-US-GuyNeural", "Male, conversational"),
    ("en-US-AriaNeural", "Female, expressive"),
    ("en-US-DavisNeural", "Male, calm"),
    ("en-GB-SoniaNeural", "Female, British"),
    ("en-GB-RyanNeural", "Male, British"),
]


def audit_text(text, max_words):
    """Check text for paragraphs that exceed max_words.

    Returns a list of (para_num, word_count, snippet) for each oversized paragraph.
    Oversized paragraphs cause Azure HD voice InvalidChunkLength failures because
    a single paragraph can't be split across chunks.
    """
    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    issues = []
    for i, para in enumerate(paragraphs):
        wc = len(para.split())
        if wc > max_words:
            snippet = para[:80].replace("\n", "↵")
            issues.append((i + 1, wc, snippet))
    return issues


def normalize_text(text):
    """Fix paragraph structure by expanding single newlines to double newlines.

    Texts from Notion and similar sources often use a single newline between
    literary paragraphs within a section. split_text() only splits on double
    newlines, so those sections become one giant paragraph that exceeds Azure's
    10-minute audio limit.

    Steps:
      1. Single \\n → \\n\\n  (expand paragraph breaks)
      2. Three or more \\n → \\n\\n  (collapse extra blank lines)

    Returns (normalized_text, single_newline_count) where single_newline_count
    is the number of single newlines that were expanded.
    """
    single_count = len(re.findall(r"\n(?!\n)", text))
    normalized = re.sub(r"\n(?!\n)", "\n\n", text)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized, single_count


def split_text(text, max_words):
    """Split text into chunks by paragraphs, respecting a word limit."""
    paragraphs = text.split("\n\n")
    chunks = []
    current_chunk = []
    current_words = 0
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        word_count = len(para.split())
        if current_words + word_count > max_words and current_chunk:
            chunks.append("\n\n".join(current_chunk))
            current_chunk = [para]
            current_words = word_count
        else:
            current_chunk.append(para)
            current_words += word_count
    if current_chunk:
        chunks.append("\n\n".join(current_chunk))
    return chunks


def text_to_ssml(text, voice):
    escaped = saxutils.escape(text)
    # Convert paragraph breaks to SSML breaks for natural pausing
    escaped = escaped.replace("\n\n", '<break time="800ms"/>\n')
    return (
        f"<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='en-US'>\n"
        f"  <voice name='{voice}'>\n"
        f"    {escaped}\n"
        f"  </voice>\n"
        f"</speak>"
    )


def _fmt_size(n_bytes):
    """Format a byte count as a human-readable MB string."""
    return f"{n_bytes / 1024 / 1024:.1f} MB"


def _fmt_speed(n_bytes, elapsed):
    """Format transfer speed as MB/s, or '—' if elapsed is zero."""
    if elapsed == 0:
        return "—"
    return f"{(n_bytes / 1024 / 1024) / elapsed:.1f} MB/s"


def _print_report(chunk_stats, voice, total_elapsed):
    """Print a summary table of per-chunk and total synthesis timing.

    chunk_stats: list of (word_count, audio_bytes, elapsed_seconds) per chunk
    voice:        voice name shown in the report header
    total_elapsed: wall-clock seconds for the full synthesis loop
    """
    n = len(chunk_stats)
    total_words = sum(s[0] for s in chunk_stats)
    total_bytes = sum(s[1] for s in chunk_stats)

    label = voice if len(voice) <= 34 else voice[:31] + "..."
    header_line = f"  Synthesis Report  ·  {label}"

    print()
    print(f"  ┌─────────────────────────────────────────────────────┐")
    print(f"  │  {header_line:<51} │")
    print(f"  ├───────┬────────┬─────────┬─────────┬───────────────┤")
    print(f"  │ Chunk │  Words │   Audio │    Time │         Speed │")
    print(f"  ├───────┼────────┼─────────┼─────────┼───────────────┤")
    for i, (words, audio_bytes, elapsed) in enumerate(chunk_stats, 1):
        print(
            f"  │ {i:>2}/{n:<2} │ {words:>6} │ {_fmt_size(audio_bytes):>7} │ {elapsed:>6.2f}s │ {_fmt_speed(audio_bytes, elapsed):>13} │"
        )
    print(f"  ├───────┼────────┼─────────┼─────────┼───────────────┤")
    print(
        f"  │ Total │ {total_words:>6} │ {_fmt_size(total_bytes):>7} │ {total_elapsed:>6.2f}s │ {_fmt_speed(total_bytes, total_elapsed):>13} │"
    )
    print(f"  └───────┴────────┴─────────┴─────────┴───────────────┘")


def synthesize_chunk(ssml, chunk_num, total, api_key, tts_url, max_retries=3):
    """POST one SSML chunk to the Azure TTS REST endpoint.

    Returns (audio_bytes, elapsed_seconds). Exits on HTTP error.
    Output format: audio-48khz-192kbitrate-mono-mp3
    Retries up to max_retries times on network errors.
    """
    print(f"  Synthesizing chunk {chunk_num}/{total} ({len(ssml)} bytes SSML)...")
    headers = {
        "Ocp-Apim-Subscription-Key": api_key,
        "Content-Type": "application/ssml+xml",
        "X-Microsoft-OutputFormat": "audio-48khz-192kbitrate-mono-mp3",
    }
    for attempt in range(1, max_retries + 1):
        if attempt > 1:
            wait = 10 * (attempt - 1)
            print(f"  Retry {attempt}/{max_retries} (waiting {wait}s)...")
            time.sleep(wait)
        try:
            t0 = time.time()
            resp = requests.post(tts_url, headers=headers, data=ssml.encode("utf-8"), timeout=300)
            elapsed = time.time() - t0
            if resp.status_code != 200:
                print(f"  ERROR: HTTP {resp.status_code}: {resp.text[:500]}")
                sys.exit(1)
            print(f"  ✓ {_fmt_size(len(resp.content))}  {elapsed:.2f}s  ({_fmt_speed(len(resp.content), elapsed)})")
            return resp.content, elapsed
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout, requests.exceptions.ChunkedEncodingError) as e:
            if attempt == max_retries:
                print(f"  ERROR: Network error after {max_retries} attempts: {e}")
                sys.exit(1)
            print(f"  Network error (attempt {attempt}/{max_retries}): {e}")


def parse_args():
    defaults = load_defaults()
    parser = argparse.ArgumentParser(
        description="Convert text files to audio using Azure AI Speech.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("input", nargs="?", help="Input text file path")
    parser.add_argument("-o", "--output", help="Output MP3 file path")
    parser.add_argument(
        "-v", "--voice",
        default=os.environ.get("AZURE_SPEECH_VOICE") or defaults.get("voice", "en-US-Ava:DragonHDLatestNeural"),
        help="Azure voice name (flag > AZURE_SPEECH_VOICE env > tts_defaults.json)",
    )
    parser.add_argument(
        "-r", "--region",
        default=os.environ.get("AZURE_SPEECH_REGION") or defaults.get("region", "westus2"),
        help="Azure region (flag > AZURE_SPEECH_REGION env > tts_defaults.json)",
    )
    parser.add_argument(
        "-k", "--key",
        default=os.environ.get("AZURE_SPEECH_KEY") or defaults.get("key"),
        help="Azure Speech API key (flag > AZURE_SPEECH_KEY env > tts_defaults.json)",
    )
    parser.add_argument("--list-voices", action="store_true", help="List popular voices and exit")
    parser.add_argument(
        "--words-per-chunk", type=int, default=DEFAULT_MAX_WORDS,
        help=f"Max words per API request (default: {DEFAULT_MAX_WORDS})",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    if args.list_voices:
        print("Popular Azure Neural Voices:")
        for name, desc in POPULAR_VOICES:
            print(f"  {name:45s} {desc}")
        sys.exit(0)

    if not args.input:
        print("Error: input file is required. Use --help for usage.", file=sys.stderr)
        sys.exit(1)

    if not args.key:
        print("Error: Azure API key required. Use -k/--key or set AZURE_SPEECH_KEY env var.", file=sys.stderr)
        sys.exit(1)

    input_path = os.path.expanduser(args.input)
    if not os.path.isfile(input_path):
        print(f"Error: file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    # Default output: same directory/name as input but with .mp3 extension
    if args.output:
        output_path = os.path.expanduser(args.output)
    else:
        output_path = os.path.splitext(input_path)[0] + ".mp3"

    tts_url = f"https://{args.region}.tts.speech.microsoft.com/cognitiveservices/v1"

    print(f"Reading: {input_path}")
    with open(input_path, "r") as f:
        text = f.read().strip()

    # --- Audit & fix paragraph structure ---
    issues = audit_text(text, args.words_per_chunk)
    if issues:
        print(f"  ⚠  {len(issues)} oversized paragraph(s) detected (>{args.words_per_chunk} words):")
        for para_num, wc, snippet in issues:
            print(f"       para {para_num}: {wc} words — \"{snippet}\"")
        print(f"  Normalizing: expanding single newlines to double newlines...")
        text, single_count = normalize_text(text)
        with open(input_path, "w") as f:
            f.write(text)
        print(f"  ✓  {single_count} single newline(s) expanded — file updated in-place")
        remaining = audit_text(text, args.words_per_chunk)
        if remaining:
            print(f"  ⚠  {len(remaining)} paragraph(s) still over limit after normalization (manual review needed):")
            for para_num, wc, snippet in remaining:
                print(f"       para {para_num}: {wc} words — \"{snippet}\"")
    else:
        print(f"  ✓  Paragraph structure OK (no paragraphs exceed {args.words_per_chunk} words)")
    # --- End audit ---

    word_count = len(text.split())
    print(f"  {word_count} words, {len(text)} characters")
    print(f"  Voice: {args.voice}")
    print(f"  Region: {args.region}")

    chunks = split_text(text, args.words_per_chunk)
    print(f"  Split into {len(chunks)} chunk(s)")

    audio_parts = []
    chunk_stats = []
    t_start = time.time()
    cache_dir = output_path + ".chunks"
    os.makedirs(cache_dir, exist_ok=True)
    for i, chunk in enumerate(chunks, 1):
        chunk_path = os.path.join(cache_dir, f"chunk_{i:03d}.mp3")
        if os.path.isfile(chunk_path):
            print(f"  Chunk {i}/{len(chunks)}: using cached {chunk_path}")
            with open(chunk_path, "rb") as cf:
                audio = cf.read()
            elapsed = 0.0
        else:
            ssml = text_to_ssml(chunk, args.voice)
            audio, elapsed = synthesize_chunk(ssml, i, len(chunks), args.key, tts_url)
            with open(chunk_path, "wb") as cf:
                cf.write(audio)
        audio_parts.append(audio)
        chunk_stats.append((len(chunk.split()), len(audio), elapsed))
    total_elapsed = time.time() - t_start

    print(f"\nWriting: {output_path}")
    with open(output_path, "wb") as f:
        for part in audio_parts:
            f.write(part)
    shutil.rmtree(cache_dir, ignore_errors=True)

    total_size = os.path.getsize(output_path)
    print(f"Done! Output: {total_size / 1024 / 1024:.1f} MB")

    _print_report(chunk_stats, args.voice, total_elapsed)


if __name__ == "__main__":
    main()
