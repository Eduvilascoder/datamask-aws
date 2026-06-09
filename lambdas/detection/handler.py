"""
Lambda Detección — PII Detection Engine.

Detecta datos sensibles (PII) usando múltiples motores:
1. AWS Comprehend — detección determinista
2. AWS Bedrock (Claude) — análisis contextual
3. Patrones regex — formatos argentinos

Responsabilidades:
- Dividir texto en bloques de ≤5000 bytes UTF-8 (sin cortar oraciones)
- Invocar Comprehend DetectPiiEntities por bloque
- Invocar Bedrock con prompt para PII argentino
- Aplicar patrones regex
- Fusionar, deduplicar y resolver conflictos
- Persistir JSON de entidades unificadas en S3

Runtime: Python 3.12
Timeout: 120s
Memoria: 1024MB
"""

import json
import logging
import os
from typing import Any

import boto3

from bedrock_client import invoke_bedrock
from entity_merger import (
    generate_output_json,
    merge_entities,
    store_entities_result,
)
from regex_detector import detect_with_rules
from user_config import get_detection_config

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Variables de entorno
DOCUMENTS_BUCKET = os.environ.get("DOCUMENTS_BUCKET", "")
DOCUMENTS_TABLE = os.environ.get("DOCUMENTS_TABLE", "")
DLQ_URL = os.environ.get("DLQ_URL", "")
BEDROCK_MODEL_ID = os.environ.get(
    "BEDROCK_MODEL_ID", "anthropic.claude-3-haiku-20240307-v1:0"
)
ENVIRONMENT = os.environ.get("ENVIRONMENT", "dev")
REDACTION_FUNCTION_NAME = os.environ.get("REDACTION_FUNCTION_NAME", "")


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Handler principal para detección de PII.

    Orquesta la detección con los 3 motores, fusiona resultados,
    y persiste el JSON final en S3.

    Args:
        event: Evento con texto extraído y metadata del documento.
               Esperado: {document_id, user_id, text_content, page_texts}
        context: Contexto de ejecución Lambda.

    Returns:
        Diccionario con entidades detectadas y metadata.
    """
    logger.info(
        "Evento de detección recibido: %s", json.dumps(event, default=str)
    )

    try:
        # Contrato de invocación desde la Lambda Trigger (camelCase)
        bucket = event.get("bucket", "") or DOCUMENTS_BUCKET
        document_id = event.get("documentId", "") or event.get("document_id", "")
        user_id = event.get("userId", "") or event.get("user_id", "")
        file_name = event.get("fileName", "") or event.get("file_name", "")
        s3_key_original = event.get("s3KeyOriginal", "") or event.get(
            "s3_key_original", ""
        )
        textract_output_key = event.get("textractOutputKey", "") or (
            f"procesamiento/{user_id}/{document_id}/textract_output.json"
        )

        # Leer el output de Textract desde S3 y reconstruir el texto
        text_content, page_texts = _load_textract_text(
            bucket, textract_output_key
        )

        logger.info(
            "Iniciando detección PII: document_id=%s, text_length=%d",
            document_id,
            len(text_content),
        )

        # Cargar configuración de detección del usuario (regex, modelo,
        # prompt, entidades a ignorar). Fail-open a defaults si no existe.
        det_config = get_detection_config(user_id, DOCUMENTS_TABLE)

        # Detección PÁGINA POR PÁGINA: se ejecuta IA + regex sobre el texto
        # de cada página por separado. Así cada entidad nace con su número de
        # página correcto (clave para que la redacción la encuentre en todas
        # las páginas) y se evita truncar el documento completo.
        final_entities = _detect_all_pages(
            text_content=text_content,
            page_texts=page_texts,
            document_id=document_id,
            det_config=det_config,
        )

        # Excluir entidades que el usuario marcó para ignorar (por valor).
        final_entities = _apply_ignore_list(
            final_entities, det_config["ignoreEntities"]
        )

        # Resumen del motor de detección (para auditoría).
        engine_label = _build_engine_label(
            final_entities, det_config["bedrockModelId"]
        )

        logger.info(
            "Entidades finales: %d en %d página(s)",
            len(final_entities),
            len(page_texts) or 1,
        )

        # 7. Generar JSON de salida
        output_json = generate_output_json(
            entities=final_entities,
            document_id=document_id,
        )

        # 8. Almacenar en S3
        stored = _store_results(output_json, user_id, document_id)
        s3_key_entities = f"procesamiento/{user_id}/{document_id}/entities.json"

        # 9. Invocar Lambda Redacción para generar el PDF ofuscado
        _invoke_redaction_lambda(
            document_id=document_id,
            user_id=user_id,
            s3_key_original=s3_key_original,
            s3_key_entities=s3_key_entities,
            entities_found=len(final_entities),
            original_filename=file_name,
            engine=engine_label,
        )

        return {
            "statusCode": 200,
            "body": json.dumps({
                "document_id": document_id,
                "entities_found": len(final_entities),
                "entities": [e.to_dict() for e in final_entities],
                "stored_in_s3": stored,
                "message": "Detección PII completada",
            }),
        }

    except Exception as e:
        logger.error("Error en detección PII: %s", str(e), exc_info=True)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)}),
        }


def _detect_all_pages(
    text_content: str,
    page_texts: list[dict],
    document_id: str,
    det_config: dict,
) -> list:
    """Detecta PII página por página (IA + regex) y fusiona el resultado.

    Ejecutar la detección sobre el texto de cada página por separado garantiza
    que cada entidad tenga su número de página correcto y que las páginas
    posteriores no se pierdan por el truncado del texto completo.

    Aplica el método de ofuscación configurado por el usuario
    (`detectionMethod`): "ai" usa solo Bedrock, "regex" solo reglas regex,
    y "both" combina ambos motores (default).

    Si no hay información de páginas (page_texts vacío), procesa el documento
    completo como una sola página.

    Args:
        text_content: Texto completo del documento (fallback sin páginas).
        page_texts: Lista de páginas {page_number, text}.
        document_id: ID del documento para logging.
        det_config: Configuración de detección del usuario.

    Returns:
        Lista final de entidades (fusionadas y sin solapamientos) con páginas.
    """
    pages = page_texts or [{"page_number": 1, "text": text_content}]
    all_entities: list = []

    method = det_config.get("detectionMethod", "both")
    use_ai = method in ("ai", "both")
    use_regex = method in ("regex", "both")

    for page in pages:
        page_number = page.get("page_number", 1)
        page_text = page.get("text", "")
        if not page_text.strip():
            continue

        bedrock_entities = (
            _run_bedrock(
                page_text,
                document_id,
                model_id=det_config["bedrockModelId"],
                prompt_template=det_config["bedrockPrompt"],
                temperature=det_config.get("bedrockTemperature", 0.0),
            )
            if use_ai
            else []
        )
        regex_entities = (
            _run_regex(page_text, det_config["regexRules"]) if use_regex else []
        )

        # Asignar el número de página a las entidades de esta página.
        for entity in bedrock_entities:
            entity.page = page_number
        for entity in regex_entities:
            entity.page = page_number

        # Fusionar y resolver solapamientos dentro de la página.
        page_entities = merge_entities(
            bedrock=bedrock_entities,
            regex=regex_entities,
        )
        all_entities.extend(page_entities)

    return all_entities


def _run_bedrock(
    text_content: str,
    document_id: str,
    model_id: str | None = None,
    prompt_template: str | None = None,
    temperature: float = 0.0,
) -> list:
    """Ejecuta detección con Bedrock (modo resiliente).

    Si Bedrock falla, retorna lista vacía y el pipeline continúa.

    Args:
        text_content: Texto completo del documento.
        document_id: ID del documento para logging.
        model_id: Modelo Bedrock configurado por el usuario.
        prompt_template: Prompt configurado por el usuario (con {text}).
        temperature: Temperatura del modelo configurada por el usuario.

    Returns:
        Lista de DetectedEntity de Bedrock, o vacía si falla.
    """
    return invoke_bedrock(
        text=text_content,
        document_id=document_id,
        model_id=model_id or BEDROCK_MODEL_ID,
        prompt_template=prompt_template,
        temperature=temperature,
    )


def _run_regex(text_content: str, rules: list[dict]) -> list:
    """Ejecuta detección con las reglas regex configuradas por el usuario.

    Args:
        text_content: Texto completo del documento.
        rules: Reglas regex {type, pattern, enabled} desde la config.

    Returns:
        Lista de DetectedEntity detectadas por las reglas activas.
    """
    return detect_with_rules(text=text_content, rules=rules)


def _apply_ignore_list(entities: list, ignore_values: list[str]) -> list:
    """Excluye entidades cuyo texto coincide con la lista de ignorados.

    La comparación es case-insensitive y por valor exacto (tras strip).

    Args:
        entities: Entidades finales detectadas.
        ignore_values: Valores literales que NO deben ofuscarse.

    Returns:
        Lista de entidades sin las que están en la lista de ignorados.
    """
    if not ignore_values:
        return entities

    normalized = {v.strip().casefold() for v in ignore_values if v and v.strip()}
    if not normalized:
        return entities

    return [
        e for e in entities
        if e.text.strip().casefold() not in normalized
    ]


def _store_results(
    output_json: dict,
    user_id: str,
    document_id: str,
) -> bool:
    """Almacena resultados en S3 si el bucket está configurado.

    Args:
        output_json: JSON de salida a almacenar.
        user_id: ID del usuario.
        document_id: ID del documento.

    Returns:
        True si se almacenó exitosamente, False en caso contrario.
    """
    if not DOCUMENTS_BUCKET:
        logger.warning(
            "DOCUMENTS_BUCKET no configurado — resultados no almacenados"
        )
        return False

    s3_client = boto3.client("s3")
    return store_entities_result(
        output=output_json,
        bucket=DOCUMENTS_BUCKET,
        user_id=user_id,
        document_id=document_id,
        s3_client=s3_client,
    )


def _load_textract_text(
    bucket: str, textract_output_key: str
) -> tuple[str, list[dict[str, Any]]]:
    """Lee el output de Textract desde S3 y reconstruye el texto.

    Concatena el texto de todos los bloques LINE preservando el orden
    de páginas. Devuelve el texto completo y la lista de textos por página
    (para asignación de páginas a las entidades).

    Args:
        bucket: Bucket S3 donde está el output de Textract.
        textract_output_key: Key del archivo textract_output.json.

    Returns:
        Tupla (text_content, page_texts) donde page_texts es una lista de
        dicts {pageNumber, text}.
    """
    s3_client = boto3.client("s3")
    try:
        response = s3_client.get_object(Bucket=bucket, Key=textract_output_key)
        data = json.loads(response["Body"].read())
    except Exception as e:
        logger.error(
            "Error leyendo Textract output %s: %s", textract_output_key, str(e)
        )
        return "", []

    page_texts: list[dict[str, Any]] = []
    full_parts: list[str] = []
    cursor = 0

    for page in data.get("pages", []):
        page_number = page.get("pageNumber", 1)
        line_texts = [
            block.get("text", "")
            for block in page.get("blocks", [])
            if block.get("type") == "LINE"
        ]
        page_text = "\n".join(line_texts)
        start_offset = cursor
        end_offset = cursor + len(page_text)
        page_texts.append({
            "page_number": page_number,
            "text": page_text,
            "start_offset": start_offset,
            "end_offset": end_offset,
        })
        full_parts.append(page_text)
        # +1 por el "\n" que une las páginas en el texto completo.
        cursor = end_offset + 1

    return "\n".join(full_parts), page_texts


def _build_engine_label(entities: list, model_id: str) -> str:
    """Construye una etiqueta legible del motor de detección usado.

    Combina los motores que efectivamente aportaron entidades. Si Bedrock
    participó, incluye el nombre del modelo (p.ej. "Bedrock (Claude 3.5 Haiku)").

    Args:
        entities: Entidades finales (cada una con .source).
        model_id: ID del modelo Bedrock configurado.

    Returns:
        Etiqueta como "Bedrock (Claude 3 Haiku) + Comprehend + Regex".
    """
    sources = {getattr(e, "source", "") for e in entities}
    parts: list[str] = []
    if "bedrock" in sources:
        parts.append(f"IA ({_model_label(model_id)})")
    if "regex" in sources:
        parts.append("Regex")
    if not parts:
        # Sin entidades: reportar el motor IA configurado como referencia.
        return f"IA ({_model_label(model_id)})"
    return " + ".join(parts)


def _model_label(model_id: str) -> str:
    """Convierte un modelId/inference profile de Bedrock en etiqueta legible."""
    # Derivar de forma genérica: us.anthropic.claude-haiku-4-5-... -> Claude Haiku 4.5
    name = model_id.split("anthropic.claude-", 1)[-1].split("-v1", 1)[0]
    if name == model_id:
        return model_id  # no es un modelo Claude conocido; usar id tal cual
    tokens = [p for p in name.split("-") if not (p.isdigit() and len(p) == 8)]
    words: list[str] = []
    for tok in tokens:
        if tok.isdigit() and words and words[-1].replace(".", "").isdigit():
            words[-1] = f"{words[-1]}.{tok}"
        elif tok.isdigit():
            words.append(tok)
        else:
            words.append(tok.capitalize())
    return f"Claude {' '.join(words)}".strip()


def _invoke_redaction_lambda(
    document_id: str,
    user_id: str,
    s3_key_original: str,
    s3_key_entities: str,
    entities_found: int,
    original_filename: str,
    engine: str = "",
) -> None:
    """Invoca la Lambda de Redacción de forma asíncrona.

    Args:
        document_id: ID del documento.
        user_id: ID del usuario.
        s3_key_original: Key S3 del PDF original.
        s3_key_entities: Key S3 del JSON de entidades.
        entities_found: Cantidad de entidades detectadas.
        original_filename: Nombre del archivo original.
    """
    if not REDACTION_FUNCTION_NAME:
        logger.warning(
            "REDACTION_FUNCTION_NAME no configurado — redacción no invocada"
        )
        return

    payload = {
        "document_id": document_id,
        "user_id": user_id,
        "s3_key_original": s3_key_original,
        "s3_key_entities": s3_key_entities,
        "entities_found": entities_found,
        "original_filename": original_filename,
        "engine": engine,
    }

    try:
        lambda_client = boto3.client("lambda")
        lambda_client.invoke(
            FunctionName=REDACTION_FUNCTION_NAME,
            InvocationType="Event",  # Asíncrono
            Payload=json.dumps(payload).encode("utf-8"),
        )
        logger.info(
            "Lambda Redacción invocada para documento %s: %s",
            document_id,
            REDACTION_FUNCTION_NAME,
        )
    except Exception as e:
        logger.error(
            "Error invocando Lambda Redacción para %s: %s",
            document_id,
            str(e),
            exc_info=True,
        )


def _calculate_block_offsets(blocks: list[str]) -> list[int]:
    """(Obsoleto) Se conservaba para Comprehend. Sin uso tras quitar Comprehend."""
    offsets: list[int] = []
    current_offset = 0
    for block in blocks:
        offsets.append(current_offset)
        current_offset += len(block)
    return offsets
