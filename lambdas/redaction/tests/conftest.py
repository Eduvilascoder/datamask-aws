"""Configuración de tests para la Lambda redaction.

Provee un stub mínimo de `fitz` (PyMuPDF) para poder testear la lógica de
localización de texto (text_matcher) sin instalar la dependencia nativa, que
en producción se provee vía Lambda Layer.
"""

import sys
import types


class _StubRect:
    """Rectángulo mínimo compatible con la API usada por text_matcher."""

    def __init__(self, *args):
        if len(args) == 1:
            self.x0, self.y0, self.x1, self.y1 = args[0]
        else:
            self.x0, self.y0, self.x1, self.y1 = args

    def __or__(self, other):
        return _StubRect(
            (
                min(self.x0, other.x0),
                min(self.y0, other.y0),
                max(self.x1, other.x1),
                max(self.y1, other.y1),
            )
        )

    def __ior__(self, other):
        self.x0 = min(self.x0, other.x0)
        self.y0 = min(self.y0, other.y0)
        self.x1 = max(self.x1, other.x1)
        self.y1 = max(self.y1, other.y1)
        return self

    def __eq__(self, other):
        return (self.x0, self.y0, self.x1, self.y1) == (
            other.x0,
            other.y0,
            other.x1,
            other.y1,
        )

    def __repr__(self):
        return f"Rect({self.x0},{self.y0},{self.x1},{self.y1})"


if "fitz" not in sys.modules:
    _stub = types.ModuleType("fitz")
    _stub.Rect = _StubRect
    sys.modules["fitz"] = _stub
