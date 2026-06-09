#!/usr/bin/env bash
#
# Bootstrap script para DataMask AWS
# Verifica que el perfil masterGenAI existe y la cuenta asociada es 339712829454.
#
set -euo pipefail

EXPECTED_PROFILE="masterGenAI"
EXPECTED_ACCOUNT="339712829454"

echo "============================================"
echo " DataMask AWS — Bootstrap Verification"
echo "============================================"
echo ""

# 1. Verificar que AWS CLI está instalado
if ! command -v aws &> /dev/null; then
  echo "ERROR: AWS CLI no está instalado."
  echo "  Instálalo desde: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"
  exit 1
fi

echo "✓ AWS CLI encontrado: $(aws --version)"
echo ""

# 2. Verificar que el perfil masterGenAI existe en la configuración de AWS CLI
echo "Verificando perfil '$EXPECTED_PROFILE'..."

if ! aws configure list --profile "$EXPECTED_PROFILE" &> /dev/null; then
  echo ""
  echo "ERROR: El perfil '$EXPECTED_PROFILE' no existe en la configuración de AWS CLI."
  echo ""
  echo "  Para configurarlo:"
  echo "    aws configure --profile $EXPECTED_PROFILE"
  echo ""
  echo "  O agrega la siguiente entrada en ~/.aws/config:"
  echo "    [profile $EXPECTED_PROFILE]"
  echo "    region = us-east-1"
  echo "    output = json"
  echo ""
  exit 1
fi

echo "✓ Perfil '$EXPECTED_PROFILE' encontrado."
echo ""

# 3. Verificar que la cuenta asociada al perfil es la correcta
echo "Verificando cuenta AWS asociada al perfil..."

ACTUAL_ACCOUNT=$(aws sts get-caller-identity --profile "$EXPECTED_PROFILE" --query "Account" --output text 2>/dev/null) || {
  echo ""
  echo "ERROR: No se pudo obtener la identidad del perfil '$EXPECTED_PROFILE'."
  echo "  Verifica que las credenciales del perfil son válidas y tienen permisos para sts:GetCallerIdentity."
  echo ""
  exit 1
}

if [ "$ACTUAL_ACCOUNT" != "$EXPECTED_ACCOUNT" ]; then
  echo ""
  echo "ERROR: La cuenta asociada al perfil '$EXPECTED_PROFILE' no coincide."
  echo "  Cuenta esperada: $EXPECTED_ACCOUNT"
  echo "  Cuenta obtenida: $ACTUAL_ACCOUNT"
  echo ""
  echo "  Verifica la configuración del perfil en ~/.aws/credentials o ~/.aws/config."
  echo ""
  exit 1
fi

echo "✓ Cuenta verificada: $ACTUAL_ACCOUNT"
echo ""

# 4. Verificar que Node.js está instalado (necesario para CDK)
if ! command -v node &> /dev/null; then
  echo "ERROR: Node.js no está instalado."
  echo "  Instálalo desde: https://nodejs.org/"
  exit 1
fi

echo "✓ Node.js encontrado: $(node --version)"

# 5. Verificar que CDK CLI está instalado
if ! command -v cdk &> /dev/null; then
  echo ""
  echo "ADVERTENCIA: CDK CLI no está instalado globalmente."
  echo "  Puedes instalarlo con: npm install -g aws-cdk"
  echo "  O usar npx cdk desde el directorio infra/."
  echo ""
else
  echo "✓ CDK CLI encontrado: $(cdk --version)"
fi

echo ""
echo "============================================"
echo " ✓ Todas las verificaciones pasaron."
echo "   Puedes ejecutar 'cdk bootstrap' y luego 'cdk deploy'."
echo "============================================"
echo ""

exit 0
