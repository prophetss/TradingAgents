from __future__ import annotations

from ntfy_bridge.scrub import scrub


def test_exact_secrets_are_removed_even_without_a_pattern():
    text = "token is hunter2 and topic ta_cmd_x"
    assert "hunter2" not in scrub(text, ["hunter2"])


def test_common_credential_shapes_are_masked():
    text = (
        "Authorization: Bearer abcdefghijklmnop\n"
        "key sk-1234567890abcdef trailing\n"
        "url https://x.test/?token=supersecretvalue&x=1\n"
        "NTFY token tk_abcdefghijklmnop"
    )
    cleaned = scrub(text)
    for leaked in ("abcdefghijklmnop", "sk-1234567890abcdef", "supersecretvalue", "tk_abcdefghijklmnop"):
        assert leaked not in cleaned


def test_chinese_report_text_survives_untouched():
    text = "贵州茅台收盘 1435.26 元，评级 Hold。"
    assert scrub(text, ["s3cret"]) == text
