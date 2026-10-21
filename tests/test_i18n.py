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
