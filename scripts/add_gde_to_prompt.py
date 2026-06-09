"""Agrega la instrucción de EXPEDIENTE_GDE al prompt del usuario (sin pisar).

Inserta la línea de tipo EXPEDIENTE_GDE antes del bloque de INSTRUCCIONES si
aún no está presente. Preserva el resto del prompt.

Uso: python3 scripts/add_gde_to_prompt.py <username>
"""
import sys

import boto3

TABLE = "datamask-dev-documents"
PROFILE = "masterGenAI"
REGION = "us-east-1"
SK_DETECTION = "CONFIG#DETECTION"

GDE_LINE = (
    "- EXPEDIENTE_GDE: número de expediente del sistema GDE de la "
    "Administración Pública argentina, con formato "
    "TIPO-AÑO-NÚMERO-ECOSISTEMA-REPARTICIÓN#MINISTERIO "
    "(ej: EX-2025-12345678-APN-SCEYM#MEC). El TIPO puede ser EX, IF, NO, "
    "PV, RE, entre otros. Detectá el identificador completo como una sola "
    "entidad."
)

username = sys.argv[1] if len(sys.argv) > 1 else "eduvilas"

ddb = boto3.Session(profile_name=PROFILE, region_name=REGION).resource("dynamodb")
table = ddb.Table(TABLE)

resp = table.get_item(Key={"PK": f"USER#{username}", "SK": SK_DETECTION})
item = resp.get("Item")
if not item:
    print(f"El usuario '{username}' no tiene CONFIG#DETECTION.")
    sys.exit(1)

prompt = item.get("bedrockPrompt", "")
if "EXPEDIENTE_GDE" in prompt:
    print(f"'{username}' ya menciona EXPEDIENTE_GDE en el prompt. Sin cambios.")
    sys.exit(0)

marker = "INSTRUCCIONES:"
if marker in prompt:
    new_prompt = prompt.replace(marker, f"{GDE_LINE}\n\n{marker}", 1)
else:
    new_prompt = f"{prompt}\n\n{GDE_LINE}"

table.update_item(
    Key={"PK": f"USER#{username}", "SK": SK_DETECTION},
    UpdateExpression="SET bedrockPrompt = :p",
    ExpressionAttributeValues={":p": new_prompt},
)
print(f"Instrucción EXPEDIENTE_GDE agregada al prompt de '{username}'.")
