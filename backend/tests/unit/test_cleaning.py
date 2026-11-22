from app.ingestion.cleaning import clean_units, normalise, remove_repeated_lines
from app.ingestion.extractors.base import TextUnit


def test_normalise_joins_hyphenated_words_and_lines():
    assert (
        normalise("infor-\nmation is   here\nand there", keep_newlines=False)
        == "information is here and there"
    )


def test_normalise_keeps_table_newlines():
    table = "| a | b |\n| --- | --- |\n| 1 | 2 |"
    assert normalise(table, keep_newlines=True) == table


def test_nfkc_unifies_compatibility_characters():
    assert normalise("ﬁle Ｈello", keep_newlines=False) == "file Hello"


def test_page_number_lines_removed():
    units = [
        TextUnit("Real content here.", page=1),
        TextUnit("Page 1 of 9", page=1),
        TextUnit("12", page=1),
    ]
    assert [u.text for u in remove_repeated_lines(units, page_count=1)] == ["Real content here."]


def test_running_header_removed_only_when_repeated():
    units = [TextUnit("ACME confidential", page=p) for p in (1, 2, 3, 4)]
    units.append(TextUnit("Unique body text.", page=2))
    assert [u.text for u in remove_repeated_lines(units, page_count=4)] == ["Unique body text."]


def test_short_documents_keep_repeated_lines():
    units = [TextUnit("Same line", page=1), TextUnit("Same line", page=2)]
    assert len(remove_repeated_lines(units, page_count=2)) == 2


def test_empty_units_dropped():
    assert clean_units([TextUnit("   \n  "), TextUnit("ok")], None)[0].text == "ok"
