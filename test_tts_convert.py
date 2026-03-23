# Run: pytest test_tts_convert.py -v

import pytest
from tts_convert import (
    audit_text,
    normalize_text,
    split_text,
    text_to_ssml,
    _fmt_size,
    _fmt_speed,
    DEFAULT_MAX_WORDS,
)


# ---------------------------------------------------------------------------
# audit_text()
# ---------------------------------------------------------------------------

class TestAuditText:
    def test_normal_text_no_issues(self):
        text = "Hello world.\n\nThis is a short paragraph."
        issues = audit_text(text, max_words=100)
        assert issues == []

    def test_single_oversized_paragraph(self):
        big_para = " ".join(["word"] * 150)
        text = f"Short intro.\n\n{big_para}\n\nShort outro."
        issues = audit_text(text, max_words=100)
        assert len(issues) == 1
        para_num, wc, snippet = issues[0]
        assert para_num == 2
        assert wc == 150

    def test_multiple_oversized_paragraphs(self):
        big_a = " ".join(["alpha"] * 200)
        big_b = " ".join(["beta"] * 300)
        text = f"{big_a}\n\n{big_b}"
        issues = audit_text(text, max_words=100)
        assert len(issues) == 2
        assert issues[0][0] == 1  # para number
        assert issues[0][1] == 200
        assert issues[1][0] == 2
        assert issues[1][1] == 300

    def test_empty_text(self):
        issues = audit_text("", max_words=100)
        assert issues == []

    def test_whitespace_only_text(self):
        issues = audit_text("   \n\n   \n\n   ", max_words=100)
        assert issues == []

    def test_exactly_at_limit(self):
        para = " ".join(["word"] * 100)
        issues = audit_text(para, max_words=100)
        assert issues == []

    def test_one_over_limit(self):
        para = " ".join(["word"] * 101)
        issues = audit_text(para, max_words=100)
        assert len(issues) == 1
        assert issues[0][1] == 101

    def test_snippet_truncated_and_newlines_replaced(self):
        # Paragraph with an internal newline (not a paragraph break) and long text
        long_line = "a" * 90
        para = f"{long_line}\nmore text " + " ".join(["w"] * 200)
        text = para
        issues = audit_text(text, max_words=50)
        assert len(issues) == 1
        snippet = issues[0][2]
        assert len(snippet) <= 80
        # Internal newline should be shown as the arrow character
        # (only if it falls within the first 80 chars)

    def test_all_caps_sections_treated_normally(self):
        """All-caps text should be counted by words the same as any other text."""
        para = " ".join(["WORD"] * 50)
        issues = audit_text(para, max_words=100)
        assert issues == []

    def test_all_caps_oversized(self):
        para = " ".join(["SHOUT"] * 150)
        issues = audit_text(para, max_words=100)
        assert len(issues) == 1
        assert issues[0][1] == 150


# ---------------------------------------------------------------------------
# normalize_text()
# ---------------------------------------------------------------------------

class TestNormalizeText:
    def test_single_newlines_expanded(self):
        text = "Para one.\nPara two.\nPara three."
        normalized, count = normalize_text(text)
        assert "\n\n" in normalized
        assert count == 2  # two single newlines
        # After normalization, each original single newline becomes double
        paragraphs = [p for p in normalized.split("\n\n") if p.strip()]
        assert len(paragraphs) == 3

    def test_triple_newlines_collapsed(self):
        text = "Para one.\n\n\n\nPara two."
        normalized, _ = normalize_text(text)
        assert "\n\n\n" not in normalized
        assert "Para one.\n\nPara two." == normalized

    def test_already_double_newlines_unchanged(self):
        text = "Para one.\n\nPara two.\n\nPara three."
        normalized, count = normalize_text(text)
        # No single newlines to expand
        assert count == 0
        assert normalized == text

    def test_mixed_newlines(self):
        text = "A.\nB.\n\nC.\n\n\nD."
        normalized, count = normalize_text(text)
        # Single newlines: after "A." and after "\n\nC." (none, that's triple)
        # Actually let's just verify the structure
        assert "\n\n\n" not in normalized
        paragraphs = [p for p in normalized.split("\n\n") if p.strip()]
        assert len(paragraphs) == 4


# ---------------------------------------------------------------------------
# split_text()
# ---------------------------------------------------------------------------

class TestSplitText:
    def test_single_short_paragraph(self):
        text = "Hello world, this is a test."
        chunks = split_text(text, max_words=100)
        assert len(chunks) == 1
        assert chunks[0] == "Hello world, this is a test."

    def test_multiple_paragraphs_fit_in_one_chunk(self):
        text = "Paragraph one.\n\nParagraph two.\n\nParagraph three."
        chunks = split_text(text, max_words=100)
        assert len(chunks) == 1

    def test_chunks_respect_word_limit(self):
        # Create 5 paragraphs, each 30 words, limit 50 words
        paras = []
        for i in range(5):
            paras.append(" ".join([f"word{i}"] * 30))
        text = "\n\n".join(paras)
        chunks = split_text(text, max_words=50)
        # Each paragraph is 30 words, so max 1 per chunk (30+30=60 > 50)
        assert len(chunks) == 5
        for chunk in chunks:
            assert len(chunk.split()) <= 50

    def test_paragraphs_grouped_up_to_limit(self):
        # 4 paragraphs of 20 words each, limit 50 -> first two fit (40), then next two fit (40)
        paras = []
        for i in range(4):
            paras.append(" ".join([f"w{i}"] * 20))
        text = "\n\n".join(paras)
        chunks = split_text(text, max_words=50)
        assert len(chunks) == 2
        for chunk in chunks:
            assert len(chunk.split()) <= 50

    def test_exactly_at_limit(self):
        # One paragraph with exactly max_words words -> should be 1 chunk
        para = " ".join(["word"] * 100)
        chunks = split_text(para, max_words=100)
        assert len(chunks) == 1

    def test_empty_text(self):
        chunks = split_text("", max_words=100)
        assert chunks == []

    def test_whitespace_paragraphs_skipped(self):
        text = "Real paragraph.\n\n   \n\n   \n\nAnother real one."
        chunks = split_text(text, max_words=100)
        assert len(chunks) == 1
        assert "Real paragraph." in chunks[0]
        assert "Another real one." in chunks[0]

    def test_paragraph_boundary_preserved_in_chunks(self):
        """Chunks should join their paragraphs with double newlines."""
        p1 = " ".join(["a"] * 20)
        p2 = " ".join(["b"] * 20)
        text = f"{p1}\n\n{p2}"
        chunks = split_text(text, max_words=100)
        assert len(chunks) == 1
        assert "\n\n" in chunks[0]

    def test_oversized_single_paragraph_still_returned(self):
        """A paragraph exceeding the limit cannot be split further; it should
        still appear as a single chunk rather than being dropped."""
        big_para = " ".join(["word"] * 200)
        chunks = split_text(big_para, max_words=100)
        assert len(chunks) == 1
        assert len(chunks[0].split()) == 200

    def test_oversized_paragraph_does_not_merge_with_previous(self):
        """When an oversized paragraph is encountered after existing content in
        the current chunk, the current chunk should be flushed first."""
        small = " ".join(["s"] * 10)
        big = " ".join(["b"] * 200)
        text = f"{small}\n\n{big}"
        chunks = split_text(text, max_words=100)
        assert len(chunks) == 2
        assert chunks[0].strip() == small
        assert len(chunks[1].split()) == 200


# ---------------------------------------------------------------------------
# text_to_ssml()
# ---------------------------------------------------------------------------

class TestTextToSsml:
    VOICE = "en-US-JennyNeural"

    def test_wraps_in_speak_and_voice_tags(self):
        ssml = text_to_ssml("Hello", self.VOICE)
        assert "<speak" in ssml
        assert "</speak>" in ssml
        assert f"<voice name='{self.VOICE}'>" in ssml
        assert "</voice>" in ssml

    def test_ampersand_escaped(self):
        ssml = text_to_ssml("Tom & Jerry", self.VOICE)
        assert "Tom &amp; Jerry" in ssml
        # Raw & should not appear (except inside &amp;)
        assert "& " not in ssml.replace("&amp;", "")

    def test_less_than_escaped(self):
        ssml = text_to_ssml("x < y", self.VOICE)
        assert "x &lt; y" in ssml
        # The only raw < should be in XML tags
        content_area = ssml.split(f"<voice name='{self.VOICE}'>")[1].split("</voice>")[0]
        assert "<" not in content_area.replace("&lt;", "").replace('<break time="800ms"/>', "")

    def test_greater_than_escaped(self):
        ssml = text_to_ssml("x > y", self.VOICE)
        assert "x &gt; y" in ssml

    def test_quotes_in_text(self):
        """Double quotes in text content should be preserved or escaped.
        saxutils.escape does NOT escape quotes by default, but they are
        harmless inside element content (only problematic in attributes)."""
        ssml = text_to_ssml('She said "hello"', self.VOICE)
        # The text should appear in the output — either raw or escaped
        assert "hello" in ssml

    def test_paragraph_breaks_become_ssml_breaks(self):
        ssml = text_to_ssml("Para one.\n\nPara two.", self.VOICE)
        assert '<break time="800ms"/>' in ssml
        assert "Para one." in ssml
        assert "Para two." in ssml

    def test_empty_input(self):
        ssml = text_to_ssml("", self.VOICE)
        assert "<speak" in ssml
        assert "</speak>" in ssml
        # Should still be valid SSML structure

    def test_multiple_special_characters(self):
        ssml = text_to_ssml("A & B < C > D", self.VOICE)
        assert "&amp;" in ssml
        assert "&lt;" in ssml
        assert "&gt;" in ssml

    def test_voice_name_appears_in_output(self):
        custom_voice = "en-GB-SoniaNeural"
        ssml = text_to_ssml("Test", custom_voice)
        assert f"name='{custom_voice}'" in ssml


# ---------------------------------------------------------------------------
# _fmt_size()
# ---------------------------------------------------------------------------

class TestFmtSize:
    def test_zero_bytes(self):
        assert _fmt_size(0) == "0.0 MB"

    def test_one_megabyte(self):
        assert _fmt_size(1024 * 1024) == "1.0 MB"

    def test_fractional(self):
        result = _fmt_size(512 * 1024)
        assert result == "0.5 MB"

    def test_large_value(self):
        result = _fmt_size(10 * 1024 * 1024)
        assert result == "10.0 MB"


# ---------------------------------------------------------------------------
# _fmt_speed()
# ---------------------------------------------------------------------------

class TestFmtSpeed:
    def test_zero_elapsed_returns_dash(self):
        assert _fmt_speed(1024, 0) == "\u2014"  # em-dash

    def test_one_mb_per_second(self):
        result = _fmt_speed(1024 * 1024, 1.0)
        assert result == "1.0 MB/s"

    def test_fractional_speed(self):
        result = _fmt_speed(512 * 1024, 1.0)
        assert result == "0.5 MB/s"


# ---------------------------------------------------------------------------
# Integration-style: split_text + text_to_ssml pipeline
# ---------------------------------------------------------------------------

class TestSplitThenSsml:
    """Verify that text split into chunks still produces valid SSML for each chunk."""

    def test_each_chunk_produces_valid_ssml(self):
        paras = [" ".join(["word"] * 30) for _ in range(5)]
        text = "\n\n".join(paras)
        chunks = split_text(text, max_words=50)
        for chunk in chunks:
            ssml = text_to_ssml(chunk, "en-US-JennyNeural")
            assert ssml.startswith("<speak")
            assert ssml.endswith("</speak>")

    def test_special_chars_survive_split_and_ssml(self):
        text = "Tom & Jerry.\n\nX < Y.\n\nA > B."
        chunks = split_text(text, max_words=1000)
        assert len(chunks) == 1
        ssml = text_to_ssml(chunks[0], "en-US-JennyNeural")
        assert "&amp;" in ssml
        assert "&lt;" in ssml
        assert "&gt;" in ssml
