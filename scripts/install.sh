#!/usr/bin/env bash
#
# install.sh — Instalador one-command de DataMask AWS para partners/clientes.
#
# Despliega TODA la infraestructura en la cuenta AWS configurada:
#   1. Crea el bucket de artefactos (si no existe)
#   2. Empaqueta y sube las Lambdas
#   3. Sube el template y crea/actualiza el stack CloudFormation
#   4. Imprime los Outputs (incluida la config de Cognito para el frontend)
#
# Uso:
#   ./scripts/install.sh \
#     --profile <AWS_PROFILE> \
#     --region <REGION> \
#     --environment <dev|prod> \
#     --alert-email <email> \
#     --callback-url <https://tu-app/> \
#     [--artifacts-bucket <bucket>] \
#     [--cognito-domain-prefix <prefijo-unico>]
#
# Requisitos: aws cli v2, python3, zip. (Docker opcional para el layer PyMuPDF.)
set -euo pipefail

# ─── Valores por defecto ─────────────────────────────────────────────────────
PROFILE=""
REGION="us-east-1"
ENVIRONMENT="dev"
ALERT_EMAIL=""
CALLBACK_URL=""
ARTIFACTS_BUCKET=""
COGNITO_DOMAIN_PREFIX=""
BEDROCK_MODEL_ID="anthropic.claude-3-haiku-20240307-v1:0"
STACK_NAME=""

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ─── Parseo de argumentos ────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile) PROFILE="$2"; shift 2;;
    --region) REGION="$2"; shift 2;;
    --environment) ENVIRONMENT="$2"; shift 2;;
    --alert-email) ALERT_EMAIL="$2"; shift 2;;
    --callback-url) CALLBACK_URL="$2"; shift 2;;
    --artifacts-bucket) ARTIFACTS_BUCKET="$2"; shift 2;;
    --cognito-domain-prefix) COGNITO_DOMAIN_PREFIX="$2"; shift 2;;
    --bedrock-model) BEDROCK_MODEL_ID="$2"; shift 2;;
    --stack-name) STACK_NAME="$2"; shift 2;;
    *) echo "Argumento desconocido: $1"; exit 1;;
  esac
done

# ─── Validaciones ────────────────────────────────────────────────────────────
if [[ -z "$PROFILE" || -z "$ALERT_EMAIL" || -z "$CALLBACK_URL" ]]; then
  echo "ERROR: --profile, --alert-email y --callback-url son obligatorios."
  echo "Ejecute: $0 --help para ver el uso."
  exit 1
fi

ACCOUNT_ID="$(aws sts get-caller-identity --profile "$PROFILE" --query Account --output text)"
[[ -z "$STACK_NAME" ]] && STACK_NAME="datamask-${ENVIRONMENT}"
[[ -z "$ARTIFACTS_BUCKET" ]] && ARTIFACTS_BUCKET="datamask-artifacts-${ACCOUNT_ID}"
[[ -z "$COGNITO_DOMAIN_PREFIX" ]] && COGNITO_DOMAIN_PREFIX="datamask-${ACCOUNT_ID}"

echo "═══════════════════════════════════════════════════════════════"
echo "  DataMask AWS — Instalación"
echo "═══════════════════════════════════════════════════════════════"
echo "  Cuenta:            $ACCOUNT_ID"
echo "  Región:            $REGION"
echo "  Ambiente:          $ENVIRONMENT"
echo "  Stack:             $STACK_NAME"
echo "  Bucket artefactos: $ARTIFACTS_BUCKET"
echo "  Cognito prefix:    $COGNITO_DOMAIN_PREFIX"
echo "  Callback URL:      $CALLBACK_URL"
echo "═══════════════════════════════════════════════════════════════"

# ─── 1. Bucket de artefactos ─────────────────────────────────────────────────
echo "→ [1/5] Verificando bucket de artefactos..."
if ! aws s3api head-bucket --bucket "$ARTIFACTS_BUCKET" --profile "$PROFILE" 2>/dev/null; then
  echo "  Creando bucket $ARTIFACTS_BUCKET..."
  if [[ "$REGION" == "us-east-1" ]]; then
    aws s3api create-bucket --bucket "$ARTIFACTS_BUCKET" --profile "$PROFILE" --region "$REGION"
  else
    aws s3api create-bucket --bucket "$ARTIFACTS_BUCKET" --profile "$PROFILE" --region "$REGION" \
      --create-bucket-configuration "LocationConstraint=$REGION"
  fi
fi

# ─── 2. Empaquetar y subir Lambdas ───────────────────────────────────────────
echo "→ [2/5] Empaquetando Lambdas..."
bash "$ROOT_DIR/scripts/package-lambdas.sh" --output-dir "$ROOT_DIR/build/lambdas"
echo "  Subiendo Lambdas a S3..."
aws s3 sync "$ROOT_DIR/build/lambdas/" "s3://$ARTIFACTS_BUCKET/datamask/lambdas/" \
  --profile "$PROFILE" --region "$REGION"

# ─── 3. Subir template ───────────────────────────────────────────────────────
echo "→ [3/5] Subiendo template..."
aws s3 cp "$ROOT_DIR/datamask-template.yaml" \
  "s3://$ARTIFACTS_BUCKET/datamask/datamask-template.yaml" \
  --profile "$PROFILE" --region "$REGION"
TEMPLATE_URL="https://$ARTIFACTS_BUCKET.s3.amazonaws.com/datamask/datamask-template.yaml"

# ─── 4. Crear o actualizar el stack ──────────────────────────────────────────
echo "→ [4/5] Desplegando stack CloudFormation..."
PARAMS=(
  "ParameterKey=Environment,ParameterValue=$ENVIRONMENT"
  "ParameterKey=AlertEmail,ParameterValue=$ALERT_EMAIL"
  "ParameterKey=LambdaCodeS3Bucket,ParameterValue=$ARTIFACTS_BUCKET"
  "ParameterKey=LambdaCodeS3Prefix,ParameterValue=datamask/lambdas/"
  "ParameterKey=BedrockModelId,ParameterValue=$BEDROCK_MODEL_ID"
  "ParameterKey=CognitoDomainPrefix,ParameterValue=$COGNITO_DOMAIN_PREFIX"
  "ParameterKey=AppCallbackUrl,ParameterValue=$CALLBACK_URL"
  "ParameterKey=SamlMetadataUrl,ParameterValue="
)

if aws cloudformation describe-stacks --stack-name "$STACK_NAME" --profile "$PROFILE" --region "$REGION" >/dev/null 2>&1; then
  echo "  Stack existe → update-stack"
  aws cloudformation update-stack --stack-name "$STACK_NAME" \
    --template-url "$TEMPLATE_URL" \
    --capabilities CAPABILITY_NAMED_IAM CAPABILITY_AUTO_EXPAND \
    --parameters "${PARAMS[@]}" --profile "$PROFILE" --region "$REGION"
  aws cloudformation wait stack-update-complete --stack-name "$STACK_NAME" --profile "$PROFILE" --region "$REGION"
else
  echo "  Stack nuevo → create-stack"
  aws cloudformation create-stack --stack-name "$STACK_NAME" \
    --template-url "$TEMPLATE_URL" \
    --capabilities CAPABILITY_NAMED_IAM CAPABILITY_AUTO_EXPAND \
    --parameters "${PARAMS[@]}" --profile "$PROFILE" --region "$REGION"
  aws cloudformation wait stack-create-complete --stack-name "$STACK_NAME" --profile "$PROFILE" --region "$REGION"
fi

# Forzar redeploy del stage del API (por si cambiaron métodos/authorizer).
API_ID="$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --profile "$PROFILE" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='ApiGatewayId'].OutputValue" --output text)"
if [[ -n "$API_ID" && "$API_ID" != "None" ]]; then
  aws apigateway create-deployment --rest-api-id "$API_ID" --stage-name "$ENVIRONMENT" \
    --profile "$PROFILE" --region "$REGION" >/dev/null 2>&1 || true
fi

# ─── 5. Outputs ──────────────────────────────────────────────────────────────
echo "→ [5/5] Listo. Outputs del stack:"
echo "═══════════════════════════════════════════════════════════════"
aws cloudformation describe-stacks --stack-name "$STACK_NAME" --profile "$PROFILE" --region "$REGION" \
  --query "Stacks[0].Outputs[].{Clave:OutputKey,Valor:OutputValue}" --output table

cat <<EOF

═══════════════════════════════════════════════════════════════
  PRÓXIMOS PASOS
═══════════════════════════════════════════════════════════════
  1. Confirme la suscripción SNS en su email ($ALERT_EMAIL).
  2. Configure frontend/.env.production con los Outputs Cognito:
       VITE_API_ENDPOINT      = (Output ApiUrl)
       VITE_COGNITO_DOMAIN    = (Output CognitoDomain)
       VITE_COGNITO_CLIENT_ID = (Output CognitoUserPoolClientId)
       VITE_COGNITO_REDIRECT_URI = $CALLBACK_URL
  3. Build y deploy del frontend (ver docs/howtodeploydatamask.md sección 7).
  4. (Opcional) Federación SAML con IAM Identity Center:
     ver docs/howtodeploydatamask.md sección 8.
═══════════════════════════════════════════════════════════════
EOF
