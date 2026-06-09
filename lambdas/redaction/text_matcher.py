"""
Localización tolerante de texto dentro de una página PDF (PyMuPDF).

El texto que detecta el motor de PII puede diferir del texto real del PDF en
espacios, guiones, tildes o saltos de línea (p.ej. el modelo devuelve
"SANTIAGO ADRIAN PLOHN" pero el PDF tiene "SANTIAGO ADRIÁN PLOHN", o
"+54 911 61158667" frente a "+54 9 11 61158667"). La búsqueda exacta de
PyMuPDF (`page.search_for`) falla en esos casos y la entidad no se ofusca.

Este módulo localiza la entidad de forma robusta:
  1. Intenta la búsqueda exacta (rápida y precisa).
  2. Si falla, normaliza (minúsculas, sin tildes, sin separadores) y busca la
     secuencia de palabras del PDF cuya concatenación normalizada contiene el
     texto buscado, devolviendo el rectángulo que las envuelve.
"""

import unicodedata
import unicodedata as _ud
from typing import Any

# Caracteres separadores que se ignoran al comparar (espacios, guiones, puntos,
# paréntesis, etc.). Solo se conservan letras y dígitos.
_SEP_CHARS = set(" \t\n\r-–—.,;:()[]{}/\\|+*°º#'\"")


def _normalize(text: str) -> str:
    """Normaliza texto para comparación tolerante.

    Pasa a minúsculas, elimina tildes/diacríticos y descarta todo separador
    (espacios, guiones, puntuación). Así "ADRIÁN", "Adrian" y "adrian" — o
    "+54 9 11" y "+54 911" — comparan igual.

    Args:
        text: Texto a normalizar.

    Returns:
        Texto normalizado (solo letras y dígitos en minúscula, sin tildes).
    """
    decomposed = unicodedata.normalize("NFKD", text)
    chars: list[str] = []
    for ch in decomposed:
        if _ud.combining(ch):
            continue  # descartar marca diacrítica
        if ch in _SEP_CHARS:
            continue
        chars.append(ch.lower())
    return "".join(chars)


def find_text_instances(page: Any, entity_text: str) -> list[Any]:
    """Localiza un texto en una página, tolerando diferencias de formato.

    Args:
        page: Página PyMuPDF.
        entity_text: Texto de la entidad a localizar.

    Returns:
        Lista de rectángulos (fitz.Rect) que cubren las ocurrencias. Vacía si
        no se encuentra.
    """
    if not entity_text or not entity_text.strip():
        return []

    # 1) Búsqueda exacta: precisa y barata.
    exact = page.search_for(entity_text)
    if exact:
        return exact

    # 2) Búsqueda tolerante por palabras normalizadas.
    return _find_normalized(page, entity_text)


def _find_normalized(page: Any, entity_text: str) -> list[Any]:
    """Busca el texto comparando palabras normalizadas del PDF.

    Reconstruye la secuencia de palabras de la página (con su geometría) y
    encuentra el rango contiguo cuya concatenación normalizada coincide con
    el objetivo normalizado. Devuelve el rectángulo envolvente por cada
    ocurrencia.

    Args:
        page: Página PyMuPDF.
        entity_text: Texto objetivo.

    Returns:
        Lista de rectángulos envolventes (uno por ocurrencia).
    """
    target = _normalize(entity_text)
    if not target:
        return []

    # words: (x0, y0, x1, y1, "palabra", block, line, word_no)
    words = page.get_text("words")
    if not words:
        return []

    norm_words = [_normalize(w[4]) for w in words]
    rects: list[Any] = []
    n = len(words)

    i = 0
    while i < n:
        if not norm_words[i]:
            i += 1
            continue

        acc = ""
        j = i
        matched_end = -1
        while j < n:
            acc += norm_words[j]
            if len(acc) >= len(target):
                # El objetivo debe ser prefijo de lo acumulado (las palabras
                # del PDF pueden incluir caracteres extra al final).
                if acc.startswith(target):
                    matched_end = j
                break
            if not target.startswith(acc):
                break  # divergió: este punto de inicio no sirve
            j += 1

        if matched_end >= 0:
            rects.extend(_rects_per_line(words, i, matched_end))
            i = matched_end + 1
        else:
            i += 1

    return rects


def _rects_per_line(words: list, start: int, end: int) -> list[Any]:
    """Construye rectángulos envolventes agrupando por línea de texto.

    Evita cubrir contenido no relacionado cuando la entidad abarca varias
    líneas: genera un rectángulo por cada línea (block, line) en lugar de
    una única caja gigante que englobaría el espacio intermedio.

    Args:
        words: Lista de words de PyMuPDF (x0,y0,x1,y1,texto,block,line,word).
        start: Índice de la primera palabra del match (inclusive).
        end: Índice de la última palabra del match (inclusive).

    Returns:
        Lista de rectángulos, uno por línea cubierta.
    """
    import fitz

    rects: list[Any] = []
    current: Any = None
    current_line: tuple[int, int] | None = None

    for k in range(start, end + 1):
        w = words[k]
        line_key = (w[5], w[6])  # (block, line)
        word_rect = fitz.Rect(w[:4])
        if current_line == line_key and current is not None:
            current |= word_rect
        else:
            if current is not None:
                rects.append(current)
            current = word_rect
            current_line = line_key

    if current is not None:
        rects.append(current)

    return rects


def find_textract_instances(
    page: Any,
    entity_text: str,
    textract_pages: list,
    page_num: int,
) -> list[Any]:
    """Localiza una entidad usando la geometría de Textract (bounding boxes).

    Fallback para texto que vive en imágenes del PDF (sin capa de texto), como
    el nombre del encabezado de un CV. Textract hace OCR y entrega, por cada
    palabra, una boundingBox normalizada (0-1). Se busca la secuencia de
    palabras cuya concatenación normalizada coincide con la entidad y se
    convierten sus cajas a coordenadas de la página PDF.

    Args:
        page: Página PyMuPDF (para conocer ancho/alto reales).
        entity_text: Texto de la entidad a localizar.
        textract_pages: Lista de páginas de Textract (con blocks/words).
        page_num: Número de página (1-indexed) de la entidad.

    Returns:
        Lista de rectángulos en coordenadas del PDF. Vacía si no se encuentra.
    """
    import fitz

    target = _normalize(entity_text)
    if not target or not textract_pages:
        return []

    words = _textract_words_for_page(textract_pages, page_num)
    if not words:
        return []

    page_rect = page.rect
    pw, ph = page_rect.width, page_rect.height

    norm_words = [_normalize(w["text"]) for w in words]
    rects: list[Any] = []
    n = len(words)

    i = 0
    while i < n:
        if not norm_words[i]:
            i += 1
            continue
        acc = ""
        j = i
        matched_end = -1
        while j < n:
            acc += norm_words[j]
            if len(acc) >= len(target):
                if acc.startswith(target):
                    matched_end = j
                break
            if not target.startswith(acc):
                break
            j += 1

        if matched_end >= 0:
            box = None
            for k in range(i, matched_end + 1):
                wb = _bbox_to_rect(fitz, words[k]["bbox"], pw, ph)
                box = wb if box is None else (box | wb)
            if box is not None:
                rects.append(box)
            i = matched_end + 1
        else:
            i += 1

    return rects


def _textract_words_for_page(textract_pages: list, page_num: int) -> list:
    """Extrae las palabras (texto + bbox) de una página de Textract.

    Args:
        textract_pages: Lista de páginas de Textract.
        page_num: Número de página 1-indexed.

    Returns:
        Lista de dicts {text, bbox} en orden de lectura.
    """
    page = None
    for p in textract_pages:
        if p.get("pageNumber") == page_num:
            page = p
            break
    if page is None and 1 <= page_num <= len(textract_pages):
        page = textract_pages[page_num - 1]
    if page is None:
        return []

    words: list[dict] = []
    for block in page.get("blocks", []):
        if block.get("type") != "LINE":
            continue
        for w in block.get("words", []):
            bbox = w.get("boundingBox")
            text = w.get("text", "")
            if bbox and text:
                words.append({"text": text, "bbox": bbox})
    return words


def _bbox_to_rect(fitz: Any, bbox: dict, page_w: float, page_h: float) -> Any:
    """Convierte una boundingBox normalizada de Textract a fitz.Rect en puntos.

    Args:
        fitz: Módulo PyMuPDF.
        bbox: Dict {left, top, width, height} normalizado (0-1).
        page_w: Ancho de la página PDF en puntos.
        page_h: Alto de la página PDF en puntos.

    Returns:
        fitz.Rect en coordenadas absolutas de la página.
    """
    x0 = bbox["left"] * page_w
    y0 = bbox["top"] * page_h
    x1 = (bbox["left"] + bbox["width"]) * page_w
    y1 = (bbox["top"] + bbox["height"]) * page_h
    return fitz.Rect(x0, y0, x1, y1)
