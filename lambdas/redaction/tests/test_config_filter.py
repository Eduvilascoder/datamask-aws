"""Tests para el filtrado de entidades por configuración (config_filter)."""

from config_filter import filter_entities_by_config


# Tipos estándar activos de ejemplo (subconjunto).
ACTIVE = {"NOMBRE", "DNI", "EMAIL"}


def _ent(t: str) -> dict:
    return {"type": t, "text": "x"}


class TestFilterEntitiesByConfig:
    """Comportamiento del filtrado por tipos activos."""

    def test_keeps_active_standard_type(self):
        result = filter_entities_by_config([_ent("NOMBRE")], ACTIVE)
        assert len(result) == 1

    def test_drops_inactive_standard_type(self):
        # TELEFONO es estándar y no está en ACTIVE → se descarta.
        result = filter_entities_by_config([_ent("TELEFONO")], ACTIVE)
        assert result == []

    def test_keeps_custom_type_always(self):
        # EXPEDIENTE_GDE no es un tipo estándar → se conserva siempre.
        result = filter_entities_by_config([_ent("EXPEDIENTE_GDE")], ACTIVE)
        assert len(result) == 1

    def test_keeps_other_custom_types(self):
        ents = [_ent("TOKEN_GITHUB"), _ent("CODIGO_TRAMITE")]
        result = filter_entities_by_config(ents, ACTIVE)
        assert len(result) == 2

    def test_mixed(self):
        ents = [
            _ent("NOMBRE"),         # estándar activo → keep
            _ent("TELEFONO"),       # estándar inactivo → drop
            _ent("EXPEDIENTE_GDE"),  # custom → keep
        ]
        result = filter_entities_by_config(ents, ACTIVE)
        types = {e["type"] for e in result}
        assert types == {"NOMBRE", "EXPEDIENTE_GDE"}
