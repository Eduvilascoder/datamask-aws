"""Tests para la localización tolerante de texto (text_matcher).

Verifican que una entidad detectada se localiza en el PDF aunque difiera en
tildes, espacios o guiones respecto del texto real (causa por la que antes no
se ofuscaban nombre, teléfono ni dirección).
"""

from text_matcher import (
    _normalize,
    find_text_instances,
    find_textract_instances,
)


class _FakePage:
    """Página simulada: search_for exacto + get_text('words')."""

    def __init__(self, words, exact_hits=None):
        self._words = words
        self._exact_hits = exact_hits or {}

    def search_for(self, text):
        return self._exact_hits.get(text, [])

    def get_text(self, kind):
        assert kind == "words"
        return self._words


def _w(x0, text, block=0, line=0, word=0):
    """Crea una tupla word de PyMuPDF en una línea horizontal simple."""
    return (x0, 0, x0 + 9, 5, text, block, line, word)


class TestNormalize:
    def test_strips_accents(self):
        assert _normalize("ADRIÁN") == _normalize("ADRIAN")

    def test_ignores_separators(self):
        assert _normalize("+54 9 11 61158667") == _normalize("+54 911 61158667")

    def test_ignores_hyphens(self):
        assert _normalize("Buenos - Aires") == _normalize("Buenos Aires")

    def test_lowercases(self):
        assert _normalize("Plohn") == "plohn"


class TestFindTextInstances:
    def test_exact_match_short_circuits(self):
        page = _FakePage(words=[], exact_hits={"DNI 35.123.456": ["RECT"]})
        assert find_text_instances(page, "DNI 35.123.456") == ["RECT"]

    def test_matches_name_with_accent_difference(self):
        # Detectado sin tilde; el PDF tiene "ADRIÁN" con tilde.
        words = [_w(0, "SANTIAGO", word=0), _w(10, "ADRIÁN", word=1),
                 _w(20, "PLOHN", word=2)]
        page = _FakePage(words)
        rects = find_text_instances(page, "SANTIAGO ADRIAN PLOHN")
        assert len(rects) == 1
        assert (rects[0].x0, rects[0].x1) == (0, 29)

    def test_matches_phone_with_space_difference(self):
        words = [_w(0, "+54"), _w(10, "9", word=1), _w(20, "11", word=2),
                 _w(30, "61158667", word=3)]
        page = _FakePage(words)
        rects = find_text_instances(page, "+54 911 61158667")
        assert len(rects) == 1

    def test_matches_address_with_hyphens(self):
        words = [
            _w(0, "Olazábal", word=0), _w(10, "1471", word=1),
            _w(20, "-", word=2), _w(30, "Ituzaingó", word=3),
        ]
        page = _FakePage(words)
        rects = find_text_instances(page, "Olazabal 1471 Ituzaingo")
        assert len(rects) == 1

    def test_multiline_entity_returns_rect_per_line(self):
        words = [
            _w(0, "Buenos", line=0, word=0), _w(10, "Aires", line=0, word=1),
            _w(0, "Argentina", line=1, word=0),
        ]
        page = _FakePage(words)
        rects = find_text_instances(page, "Buenos Aires Argentina")
        assert len(rects) == 2

    def test_returns_empty_when_not_found(self):
        words = [_w(0, "Hola"), _w(10, "mundo", word=1)]
        page = _FakePage(words)
        assert find_text_instances(page, "Juan Pérez") == []

    def test_empty_entity_text_returns_empty(self):
        page = _FakePage(words=[])
        assert find_text_instances(page, "") == []
        assert find_text_instances(page, "   ") == []


class _FakePageRect:
    """Página simulada con dimensiones (para fallback de Textract)."""

    class _Rect:
        def __init__(self, w, h):
            self.width = w
            self.height = h

    def __init__(self, width=600.0, height=800.0):
        self.rect = self._Rect(width, height)


def _bbox(left, top, width, height):
    return {"left": left, "top": top, "width": width, "height": height}


def _textract_page(page_number, words):
    """Crea una página Textract con una sola LINE que contiene words."""
    return {
        "pageNumber": page_number,
        "blocks": [
            {
                "type": "LINE",
                "words": [
                    {"text": t, "boundingBox": bb} for t, bb in words
                ],
            }
        ],
    }


class TestFindTextractInstances:
    """Fallback usando geometría de Textract (texto en imágenes)."""

    def test_locates_name_in_image_header(self):
        # El nombre del encabezado vive en una imagen: solo Textract lo ve.
        pages = [
            _textract_page(
                1,
                [
                    ("SANTIAGO", _bbox(0.18, 0.005, 0.26, 0.026)),
                    ("ADRIAN", _bbox(0.45, 0.0035, 0.18, 0.027)),
                    ("PLOHN", _bbox(0.65, 0.005, 0.16, 0.025)),
                ],
            )
        ]
        page = _FakePageRect(width=600.0, height=800.0)
        rects = find_textract_instances(
            page, "SANTIAGO ADRIAN PLOHN", pages, 1
        )
        assert len(rects) == 1
        # Cubre desde la primera a la última palabra, escalado a puntos.
        assert rects[0].x0 == 0.18 * 600.0
        assert rects[0].x1 == (0.65 + 0.16) * 600.0

    def test_returns_empty_without_textract(self):
        page = _FakePageRect()
        assert find_textract_instances(page, "Juan", [], 1) == []

    def test_returns_empty_when_text_absent(self):
        pages = [_textract_page(1, [("Hola", _bbox(0.1, 0.1, 0.1, 0.02))])]
        page = _FakePageRect()
        assert find_textract_instances(page, "Juan Pérez", pages, 1) == []
