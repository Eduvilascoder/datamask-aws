"""
Listado dinámico de modelos de Bedrock disponibles para detección.

Lista modelos de MÚLTIPLES proveedores (Anthropic, Amazon, Meta, Mistral,
Cohere, etc.) que:
- Sean ACTIVOS (excluye los marcados como LEGACY por el proveedor).
- Soporten el modo de inferencia que usa el pipeline (texto, on-demand o
  vía inference profile).

La detección invoca los modelos con la API **Converse** de Bedrock, que es
unificada para todos los proveedores, así que cualquier modelo de chat de
texto funciona sin código específico por proveedor.

Se cachea por instancia Lambda. Si la consulta falla, hay un fallback mínimo.
"""

import logging
from typing import Any

import boto3

logger = logging.getLogger(__name__)

# Modelo por defecto: inference profile de Claude Haiku 4.5 (activo, económico).
DEFAULT_BEDROCK_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

# Fallback si la API de Bedrock no responde.
_FALLBACK_MODELS: list[dict[str, str]] = [
    {
        "id": DEFAULT_BEDROCK_MODEL_ID,
        "label": "Claude Haiku 4.5",
        "provider": "Anthropic",
    },
]

# Etiquetas legibles por providerName de Bedrock.
_PROVIDER_LABELS: dict[str, str] = {
    "Anthropic": "Anthropic",
    "Amazon": "Amazon",
    "Meta": "Meta",
    "Mistral AI": "Mistral AI",
    "Cohere": "Cohere",
    "AI21 Labs": "AI21 Labs",
    "DeepSeek": "DeepSeek",
}

_bedrock_client = None
_models_cache: list[dict[str, str]] | None = None


def _get_bedrock_client():
    global _bedrock_client
    if _bedrock_client is None:
        _bedrock_client = boto3.client("bedrock")
    return _bedrock_client


def _label_from_model(model_name: str, model_id: str) -> str:
    """Etiqueta legible del modelo. Prefiere el modelName de Bedrock."""
    if model_name:
        return model_name
    # Fallback: derivar del id.
    base = model_id.split(".", 1)[-1].split("-v", 1)[0]
    return base.replace("-", " ").title()


def _collect_inference_profiles(client: Any) -> dict[str, str]:
    """Devuelve {modelArnSuffix -> inferenceProfileId} de profiles activos us.*.

    Permite mapear un foundation model a su inference profile cuando el
    modelo requiere inferencia vía profile (no on-demand directo).
    """
    profiles: dict[str, str] = {}
    next_token: str | None = None
    while True:
        kwargs: dict[str, Any] = {"maxResults": 100}
        if next_token:
            kwargs["nextToken"] = next_token
        page = client.list_inference_profiles(**kwargs)
        for p in page.get("inferenceProfileSummaries", []):
            if p.get("status") != "ACTIVE":
                continue
            pid = p.get("inferenceProfileId", "")
            if not pid.startswith("us."):
                continue
            base = pid.split(".", 1)[-1]  # quita 'us.'
            profiles[base] = pid
        next_token = page.get("nextToken")
        if not next_token:
            break
    return profiles


def list_available_models() -> list[dict[str, str]]:
    """Lista modelos ACTIVOS (no Legacy) de todos los proveedores soportados.

    Returns:
        Lista de {"id", "label", "provider"} ordenada por proveedor y label.
        El id es el modelId on-demand o el inference profile id si aplica.
    """
    global _models_cache
    if _models_cache is not None:
        return _models_cache

    try:
        client = _get_bedrock_client()
        profiles = _collect_inference_profiles(client)

        models: list[dict[str, str]] = []
        seen_ids: set[str] = set()

        fm = client.list_foundation_models(byOutputModality="TEXT")
        for m in fm.get("modelSummaries", []):
            status = (m.get("modelLifecycle", {}) or {}).get("status", "")
            if status != "ACTIVE":
                continue  # excluye LEGACY

            model_id = m.get("modelId", "")
            provider = m.get("providerName", "")
            model_name = m.get("modelName", "")

            # Solo modelos de texto que soporten ON_DEMAND o INFERENCE_PROFILE.
            inference_types = m.get("inferenceTypesSupported", []) or []
            input_mods = m.get("inputModalities", []) or []
            output_mods = m.get("outputModalities", []) or []
            if "TEXT" not in input_mods or "TEXT" not in output_mods:
                continue

            # Elegir el identificador invocable:
            #  - ON_DEMAND  -> modelId directo
            #  - si requiere INFERENCE_PROFILE -> usar el profile us.*
            invoke_id = None
            if "ON_DEMAND" in inference_types:
                invoke_id = model_id
            elif "INFERENCE_PROFILE" in inference_types and model_id in profiles:
                invoke_id = profiles[model_id]
            if not invoke_id or invoke_id in seen_ids:
                continue

            seen_ids.add(invoke_id)
            models.append({
                "id": invoke_id,
                "label": _label_from_model(model_name, model_id),
                "provider": _PROVIDER_LABELS.get(provider, provider or "Otros"),
            })

        if not models:
            logger.warning("Sin modelos activos de Bedrock — usando fallback")
            _models_cache = list(_FALLBACK_MODELS)
        else:
            models.sort(key=lambda m: (m["provider"], m["label"]))
            _models_cache = models
    except Exception:
        logger.exception("Error listando modelos de Bedrock — usando fallback")
        _models_cache = list(_FALLBACK_MODELS)

    return _models_cache


def list_providers() -> list[str]:
    """Lista los proveedores con al menos un modelo activo disponible."""
    seen: list[str] = []
    for m in list_available_models():
        if m["provider"] not in seen:
            seen.append(m["provider"])
    return sorted(seen)


def is_valid_model(model_id: str) -> bool:
    """Indica si el model_id está entre los modelos activos disponibles."""
    return any(m["id"] == model_id for m in list_available_models())
