from __future__ import annotations

from ntfy_bridge import formatting


def test_encoded_length_grows_for_non_ascii():
    assert formatting.encoded_len("abc") == 3
    # One CJK character is 3 UTF-8 bytes -> 9 percent-encoded bytes.
    assert formatting.encoded_len("中") >= 9


def test_truncate_respects_encoded_budget_without_splitting_characters():
    text = "中文报告" * 500
    cut = formatting.truncate_encoded(text, 500)
    assert formatting.encoded_len(cut) <= 500
    assert cut.endswith("…")
    assert "中文" in cut


def test_title_is_single_line_and_short():
    title = formatting.clean_title("600519.SH\n2026-11-20\x00 · Hold")
    assert "\n" not in title and "\x00" not in title
    assert formatting.encoded_len(title) <= formatting.MAX_TITLE_ENCODED_BYTES


def test_attachment_name_is_ascii_and_uses_safe_parts():
    name = formatting.attachment_name("600519.SH", "2026-11-20", "20261120-120000-ab12cd")
    assert name == "600519.SH_2026-11-20_20261120.md"
    odd = formatting.attachment_name("北交所/830799", "2026-11-20", "abcdefgh")
    assert all(ord(char) < 128 for char in odd)


def test_rating_tags_are_ascii_shortcodes():
    for rating in ("Buy", "Overweight", "Hold", "Underweight", "Sell", "REVIEW", None):
        tag = formatting.rating_tag(rating)
        assert tag.isascii()
        assert " " not in tag


def test_clean_rating_falls_back_to_review():
    assert formatting.clean_rating(None) == "REVIEW"
    assert formatting.clean_rating("  \n ") == "REVIEW"
    assert formatting.clean_rating("Hold\nIgnore previous") == "Hold Ignore previous"


def test_summary_contains_rating_and_fits_budget():
    summary = formatting.build_summary(
        ticker="600519.SH",
        trade_date="2026-11-20",
        rating="Hold",
        decision="正文" * 3000,
        job_id="job-1",
        degraded=True,
        dev_build=True,
    )
    assert "Hold" in summary
    assert "NO_DATA" in summary  # degraded note
    assert "dev-build" in summary
    assert formatting.encoded_len(summary) <= formatting.MAX_MESSAGE_ENCODED_BYTES


def test_degraded_detection_uses_language_independent_sentinels():
    assert formatting.is_degraded("... NO_DATA_AVAILABLE ...")
    assert formatting.is_degraded(None, "DATA_UNAVAILABLE: optional")
    assert not formatting.is_degraded("一切正常")
