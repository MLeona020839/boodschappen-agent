import string

import pytest

from grocery_agent.i18n import EXAMPLES, LANGUAGES, TEXT, translator


def fields(text: str) -> set[str]:
    return {f for _, f, _, _ in string.Formatter().parse(text) if f}


@pytest.mark.parametrize("lang", list(LANGUAGES))
def test_every_language_has_every_string_with_the_same_placeholders(lang):
    assert set(TEXT[lang]) == set(TEXT["en"])
    for key, text in TEXT[lang].items():
        assert fields(text) == fields(TEXT["en"][key]), key
    assert len(EXAMPLES[lang]) == len(EXAMPLES["en"])


def test_translator_formats_and_falls_back_to_english():
    assert translator("nl")("imported", n=3) == "3 aanbiedingen geïmporteerd"
    assert translator("xx")("title") == TEXT["en"]["title"]


def test_dollar_signs_are_escaped_for_markdown():
    text = translator("en")("using_shared_key", spent=0.5, budget=1)
    assert "\\$0.50" in text and "\\$1.00" in text


def test_settings_strip_pasted_whitespace(monkeypatch):
    from grocery_agent import settings
    monkeypatch.setenv("SOME_KEY", "  sk-ant-abc\n")
    assert settings.get("SOME_KEY") == "sk-ant-abc"
    monkeypatch.setenv("SOME_KEY", "   ")
    assert settings.get("SOME_KEY", "fallback") == "fallback"
