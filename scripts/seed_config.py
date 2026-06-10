"""Siembra la config de detección de un usuario con las reglas default.

Uso: python3 scripts/seed_config.py <username>
Sobrescribe CONFIG#DETECTION del usuario en DynamoDB con los defaults del
código (modelo, temperatura, prompt y todas las reglas regex).
"""
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lambdas" / "api"))

import boto3
from detection_config import default_detection_config, SK_DETECTION

TABLE = "datamask-dev-documents"
PROFILE = "masterGenAI"
REGION = "us-east-1"

username = sys.argv[1] if len(sys.argv) > 1 else "eduvilas"

cfg = default_detection_config()
ddb = boto3.Session(profile_name=PROFILE, region_name=REGION).resource("dynamodb")
table = ddb.Table(TABLE)

item = {
    "PK": f"USER#{username}",
    "SK": SK_DETECTION,
    "userId": username,
    "detectionMethod": cfg["detectionMethod"],
    "macieVerification": cfg["macieVerification"],
    "snsAlertsEnabled": cfg["snsAlertsEnabled"],
    "bedrockModelId": cfg["bedrockModelId"],
    "bedrockTemperature": Decimal(str(cfg["bedrockTemperature"])),
    "bedrockPrompt": cfg["bedrockPrompt"],
    "regexRules": cfg["regexRules"],
    "ignoreEntities": cfg["ignoreEntities"],
}
table.put_item(Item=item)
print(f"Config sembrada para '{username}': {len(cfg['regexRules'])} reglas regex")
for r in cfg["regexRules"]:
    print(f"  - {r['type']} (enabled={r['enabled']})")
