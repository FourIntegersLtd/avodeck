"""Slice A increment 11d: pages not in the pack's language (F37, architecture 10.3).

`language.detect(text, expected)` names the language of a page's words: the pack's language when the
page is plausibly in it, another language only when it is clearly that one, and nothing for a page
with too few words to tell. The same text always gives the same answer. Nothing is translated.
The text is made up.
"""

from __future__ import annotations

from app.features.pipeline import language

ENGLISH = (
    "Synthetic patient seen today with a cough for three days. No fever and eating well. "
    "Chest clear on examination. Advised fluids and rest, and to come back if it gets worse "
    "or if there is any shortness of breath."
)
WELSH = (
    "Annwyl Feddyg, diolch am eich llythyr ynglŷn â'r claf. Rydym wedi trefnu apwyntiad yn y clinig "
    "ddydd Llun nesaf. Os oes gennych unrhyw gwestiynau, cysylltwch â'r swyddfa os gwelwch yn dda. "
    "Yn gywir, yr adran gofal sylfaenol."
)
FRENCH = (
    "Patient vu aujourd'hui pour une toux depuis trois jours. Pas de fièvre et il mange bien. "
    "Examen pulmonaire normal. Nous avons conseillé du repos et de boire beaucoup, et de revenir "
    "si les symptômes s'aggravent."
)


def test_a_page_in_the_packs_language_is_that_language() -> None:
    assert language.detect(ENGLISH, "en") == "en"
    assert language.detect(FRENCH, "fr") == "fr"


def test_a_page_clearly_in_another_language_is_named() -> None:
    assert language.detect(WELSH, "en") == "cy"
    assert language.detect(FRENCH, "en") == "fr"
    assert language.detect(ENGLISH, "fr") == "en"


def test_too_few_words_tells_nothing() -> None:
    assert language.detect("BP 120/80 reviewed", "en") is None
    assert language.detect("", "en") is None
    assert language.detect("12/03/2024 4.5 mmol/L 120 80 7.2", "en") is None


def test_the_same_page_always_gives_the_same_answer() -> None:
    mixed = ENGLISH[:60] + " " + FRENCH[:60]
    assert len({language.detect(mixed, "en") for _ in range(20)}) == 1
