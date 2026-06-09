"""Agrega la regla EXPEDIENTE_GDE a la config de detección de un usuario.

Preserva el resto de la configuración (modelo, temperatura, prompt, demás
reglas, ignorados). Si la regla ya existe (por tipo y patrón), no la duplica.

Uso: python3 scripts/add_expediente_gde_rule.py <username>
"""
import sys

import boto3

TABLE = "datamask-dev-documents"
PROFILE = "masterGenAI"
REGION = "us-east-1"
SK_DETECTION = "CONFIG#DETECTION"

GDE_RULE = {
    "type": "EXPEDIENTE_GDE",
    "pattern": r"\b[A-Z]{2,5}-\d{4}-\d{6,}-[A-Z]{2,}-[A-Z0-9]+#[A-Z]+\b",
    "enabled": True,
}

username = sys.argv[1] if len(sys.argv) > 1 else "eduvilas"

ddb = boto3.Session(profile_name=PROFILE, region_name=REGION).resource("dynamodb")
table = ddb.Table(TABLE)

resp = table.get_item(Key={"PK": f"USER#{username}", "SK": SK_DETECTION})
item = resp.get("Item")
if not item:
    print(f"El usuario '{username}' no tiene CONFIG#DETECTION. Use seed_config.py.")
    sys.exit(1)

rules = item.get("regexRules", [])
already = any(
    r.get("type") == GDE_RULE["type"] and r.get("pattern") == GDE_RULE["pattern"]
    for r in rules
)
if already:
    print(f"'{username}' ya tiene la regla EXPEDIENTE_GDE. Sin cambios.")
    sys.exit(0)

rules.append(dict(GDE_RULE))
table.update_item(
    Key={"PK": f"USER#{username}", "SK": SK_DETECTION},
    UpdateExpression="SET regexRules = :r",
    ExpressionAttributeValues={":r": rules},
)
print(f"Regla EXPEDIENTE_GDE agregada a '{username}'. Total reglas: {len(rules)}")
