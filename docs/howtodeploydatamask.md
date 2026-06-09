# Cómo desplegar DataMask AWS — Guía completa

Esta guía cubre **todos** los pasos para desplegar DataMask desde cero en una
cuenta AWS: pasos automáticos (CLI/scripts) y pasos manuales (consola). La
aplicación es 100% serverless y corre íntegramente en AWS.

> **Valores de referencia de la cuenta actual** (`masterGenAI`,
> `339712829454`, `us-east-1`). Reemplazá según tu cuenta/región.

---

## ⚡ Instalación rápida (one-command)

Si recibiste el entregable (tarball), la forma más rápida de instalar el backend:

```bash
tar -xzf datamask-aws-*.tar.gz
cd DataMask-AWS

# Despliega bucket de artefactos + Lambdas + stack CloudFormation completo
./scripts/install.sh \
  --profile <AWS_PROFILE> \
  --region us-east-1 \
  --environment dev \
  --alert-email tu-email@dominio.com \
  --callback-url https://tu-app.amplifyapp.com/
```

Al terminar, el script imprime los Outputs (incluida la config de Cognito).
Luego configurá el frontend (sección 6-7) y, opcionalmente, la federación SAML
(sección 8). Los pasos manuales detallados están abajo.

---

## 0. Arquitectura desplegada

| Capa | Servicio |
|------|----------|
| Frontend | React + Cloudscape en **AWS Amplify Hosting** |
| Autenticación | **Amazon Cognito** federado con **IAM Identity Center** (SAML) |
| API | **API Gateway REST** + **Lambda** (autorizador Cognito JWT) |
| Pipeline | Lambda **trigger → detection → redaction** (event-driven por S3) |
| IA | Amazon **Textract** (OCR) + **Bedrock** (Claude, multi-proveedor) |
| Datos | **S3** (documentos, SSE-KMS) + **DynamoDB** (metadatos/auditoría) |
| Resiliencia | **SQS DLQ** + **CloudWatch Alarms** + **SNS** |

---

## 1. Prerrequisitos

- AWS CLI v2 configurado con un perfil con permisos de administrador.
  ```bash
  aws configure --profile masterGenAI
  ```
- Node.js 18+ y npm (para el frontend y el build).
- Python 3.12 (para empaquetar las Lambdas).
- Acceso habilitado al modelo de Bedrock en la región
  (consola Bedrock → Model access → habilitar Claude).
- **IAM Identity Center habilitado** en la cuenta/organización (paso manual si
  no existe: consola → IAM Identity Center → Enable).

---

## 2. Bucket de artefactos (automático)

Las Lambdas y el template se suben a un bucket S3 de artefactos.

```bash
aws s3 mb s3://datamask-artifacts-339712829454 --profile masterGenAI --region us-east-1
```

---

## 3. Secret de GitHub para Amplify (manual, una vez)

Amplify necesita un token de GitHub para conectar el repo. Crealo en Secrets
Manager (no se commitea nunca):

```bash
aws secretsmanager create-secret \
  --name datamask-dev-github-token \
  --secret-string '<TU_GITHUB_TOKEN>' \
  --profile masterGenAI --region us-east-1
```

> Solo es necesario si usás el auto-build de Amplify conectado a GitHub. El
> despliegue manual del frontend (paso 7) no lo requiere.

---

## 4. Empaquetar y subir las Lambdas (automático)

```bash
# Empaqueta trigger, detection, redaction, api y el layer de PyMuPDF
bash scripts/package-lambdas.sh --output-dir "$(pwd)/build/lambdas"

# Sube los ZIP al bucket de artefactos
aws s3 sync build/lambdas/ s3://datamask-artifacts-339712829454/datamask/lambdas/ \
  --profile masterGenAI --region us-east-1
```

> En despliegues posteriores, si el layer de PyMuPDF no cambió, agregá
> `--skip-layer` al script y `--exclude "pymupdf-layer.zip"` al sync para
> acelerar.

---

## 5. Desplegar el stack de infraestructura (automático)

El template `datamask-template.yaml` crea TODA la infra (S3, DynamoDB, KMS,
Lambdas, API Gateway, Cognito, SQS, SNS, alarmas).

```bash
# Subir el template a S3 (excede el límite inline)
aws s3 cp datamask-template.yaml \
  s3://datamask-artifacts-339712829454/datamask/datamask-template.yaml \
  --profile masterGenAI --region us-east-1

# Crear el stack (primer despliegue). Para actualizar, usar update-stack.
aws cloudformation create-stack \
  --stack-name datamask-dev \
  --template-url https://datamask-artifacts-339712829454.s3.amazonaws.com/datamask/datamask-template.yaml \
  --capabilities CAPABILITY_NAMED_IAM CAPABILITY_AUTO_EXPAND \
  --profile masterGenAI --region us-east-1 \
  --parameters \
    ParameterKey=Environment,ParameterValue=dev \
    ParameterKey=AlertEmail,ParameterValue=tu-email@dominio.com \
    ParameterKey=LambdaCodeS3Bucket,ParameterValue=datamask-artifacts-339712829454 \
    ParameterKey=LambdaCodeS3Prefix,ParameterValue=datamask/lambdas/ \
    ParameterKey=BedrockModelId,ParameterValue=us.anthropic.claude-haiku-4-5-20251001-v1:0 \
    ParameterKey=CognitoDomainPrefix,ParameterValue=datamask-339712829454 \
    ParameterKey=AppCallbackUrl,ParameterValue=https://main.d3nxh77gcdsvjs.amplifyapp.com/ \
    ParameterKey=SamlMetadataUrl,ParameterValue= \
    ParameterKey=SsoStartUrl,ParameterValue=https://d-9067e44cd6.awsapps.com/start \
    ParameterKey=SsoRegion,ParameterValue=us-east-1

aws cloudformation wait stack-create-complete --stack-name datamask-dev \
  --profile masterGenAI --region us-east-1
```

> **Nota:** en el primer despliegue dejá `SamlMetadataUrl` vacío. La federación
> SAML se habilita en el paso 8 (requiere crear la app en Identity Center
> primero). Mientras tanto el login funciona con usuarios nativos de Cognito.

### Obtener los Outputs (se usan en el frontend)

```bash
aws cloudformation describe-stacks --stack-name datamask-dev \
  --profile masterGenAI --region us-east-1 \
  --query "Stacks[0].Outputs" --output table
```

Outputs clave:
- `ApiUrl` → `VITE_API_ENDPOINT`
- `CognitoDomain` → `VITE_COGNITO_DOMAIN`
- `CognitoUserPoolClientId` → `VITE_COGNITO_CLIENT_ID`
- `CognitoUserPoolId` → para crear usuarios / federación
- `CognitoSamlAcsUrl` y `CognitoSamlAudienceUri` → para la app SAML (paso 8)

---

## 6. Configurar variables de entorno del frontend (manual)

Editar `frontend/.env.production` con los Outputs del paso 5:

```dotenv
VITE_API_ENDPOINT=https://<api-id>.execute-api.us-east-1.amazonaws.com/dev
VITE_AWS_REGION=us-east-1
VITE_COGNITO_DOMAIN=datamask-339712829454.auth.us-east-1.amazoncognito.com
VITE_COGNITO_CLIENT_ID=<client-id>
VITE_COGNITO_REDIRECT_URI=https://main.d3nxh77gcdsvjs.amplifyapp.com/
# Activar SOLO después del paso 8 (federación SAML):
# VITE_COGNITO_IDP=IAMIdentityCenter
```

---

## 7. Build y despliegue del frontend (automático)

```bash
cd frontend
npm ci
npm run build
cd ..

# Despliegue manual a Amplify Hosting
python3 scripts/amplify_deploy.py
```

El script empaqueta `frontend/build/`, crea un deployment en Amplify y lo
publica. Verificá el estado:

```bash
aws amplify get-job --app-id d3nxh77gcdsvjs --branch-name main \
  --job-id <JOB_ID> --profile masterGenAI --region us-east-1 \
  --query 'job.summary.status' --output text
```

URL de la app: `https://main.d3nxh77gcdsvjs.amplifyapp.com/`

---

## 8. Federación con IAM Identity Center (MANUAL + automático)

Este es el único paso que requiere la consola (intercambio de metadata SAML).

### 8.1 Crear la aplicación SAML (consola — MANUAL)

1. Consola → **IAM Identity Center** → **Applications** → **Add application**.
2. **I have an application I want to set up** → **SAML 2.0** → **Next**.
3. **Display name:** `DataMask`.
4. En **IAM Identity Center metadata**, copiá la **URL del SAML metadata file**
   (o derivala del entityID; ver nota abajo).
5. En **Application metadata** → **Manually type your metadata values**:
   - **Application ACS URL:** valor del Output `CognitoSamlAcsUrl`
     (ej: `https://datamask-339712829454.auth.us-east-1.amazoncognito.com/saml2/idpresponse`)
   - **Application SAML audience:** valor del Output `CognitoSamlAudienceUri`
     (ej: `urn:amazon:cognito:sp:us-east-1_ntBXg0EdP`)
6. **Submit**.

### 8.2 Mapeo de atributos (consola — MANUAL)

App creada → **Actions → Edit attribute mappings**:

| Atributo en la aplicación | Valor / atributo de usuario | Formato |
|---|---|---|
| `Subject` | `${user:email}` | `emailAddress` |
| `http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress` | `${user:email}` | `unspecified` |

### 8.3 Asignar usuarios/grupos (consola — MANUAL)

App → **Assigned users** → **Assign users** → agregá tu usuario o grupo.

### 8.4 Habilitar la federación en el stack (automático)

La URL de metadata tiene este formato (derivada del entityID de la app):
`https://portal.sso.<region>.amazonaws.com/saml/metadata/<ID>`

```bash
aws cloudformation update-stack \
  --stack-name datamask-dev \
  --template-url https://datamask-artifacts-339712829454.s3.amazonaws.com/datamask/datamask-template.yaml \
  --capabilities CAPABILITY_NAMED_IAM CAPABILITY_AUTO_EXPAND \
  --profile masterGenAI --region us-east-1 \
  --parameters \
    ParameterKey=SamlMetadataUrl,ParameterValue=https://portal.sso.us-east-1.amazonaws.com/saml/metadata/<ID> \
    ParameterKey=Environment,UsePreviousValue=true \
    ParameterKey=AlertEmail,UsePreviousValue=true \
    ParameterKey=LambdaCodeS3Bucket,UsePreviousValue=true \
    ParameterKey=LambdaCodeS3Prefix,UsePreviousValue=true \
    ParameterKey=BedrockModelId,UsePreviousValue=true \
    ParameterKey=CognitoDomainPrefix,UsePreviousValue=true \
    ParameterKey=AppCallbackUrl,UsePreviousValue=true \
    ParameterKey=SsoStartUrl,UsePreviousValue=true \
    ParameterKey=SsoRegion,UsePreviousValue=true \
    ParameterKey=SsoClientName,UsePreviousValue=true

aws cloudformation wait stack-update-complete --stack-name datamask-dev \
  --profile masterGenAI --region us-east-1
```

Esto crea el IdP `IAMIdentityCenter` en el User Pool de Cognito.

### 8.5 Activar el IdP en el frontend (automático)

```bash
# En frontend/.env.production agregar:
echo 'VITE_COGNITO_IDP=IAMIdentityCenter' >> frontend/.env.production

cd frontend && npm run build && cd ..
python3 scripts/amplify_deploy.py
```

A partir de aquí, el botón de login salta **directo a Identity Center** y usa
los usuarios corporativos (incluida federación con Active Directory si Identity
Center lo tiene configurado).

---

## 9. Usuario de prueba sin federación (opcional, automático)

Para probar antes de federar, crear un usuario nativo de Cognito:

```bash
aws cognito-idp admin-create-user \
  --user-pool-id <CognitoUserPoolId> \
  --username usuario@dominio.com \
  --user-attributes Name=email,Value=usuario@dominio.com Name=email_verified,Value=true \
  --message-action SUPPRESS \
  --profile masterGenAI --region us-east-1

aws cognito-idp admin-set-user-password \
  --user-pool-id <CognitoUserPoolId> \
  --username usuario@dominio.com \
  --password '<password-seguro>' --permanent \
  --profile masterGenAI --region us-east-1
```

---

## 10. Verificación post-despliegue (automático)

```bash
# Authorizer Cognito activo en API Gateway
aws apigateway get-authorizers --rest-api-id <api-id> \
  --profile masterGenAI --region us-east-1 \
  --query 'items[].{name:name,type:type}'

# IdP SAML en Cognito (tras paso 8)
aws cognito-idp list-identity-providers --user-pool-id <CognitoUserPoolId> \
  --profile masterGenAI --region us-east-1 \
  --query 'Providers[].{name:ProviderName,type:ProviderType}'

# Hosted UI responde (302 = OK, redirige al login)
curl -s -o /dev/null -w "%{http_code}\n" \
  "https://<CognitoDomain>/oauth2/authorize?response_type=code&client_id=<client-id>&redirect_uri=https://main.d3nxh77gcdsvjs.amplifyapp.com/&scope=openid+email+profile"
```

Abrí `https://main.d3nxh77gcdsvjs.amplifyapp.com/` (Cmd+Shift+R) y probá el login.

---

## 11. Ciclo de actualización (resumen)

| Cambio | Comandos |
|--------|----------|
| **Solo código Lambda** | `package-lambdas.sh` → `s3 sync` → `aws lambda update-function-code` por función |
| **Infra (template)** | `s3 cp template` → `cloudformation update-stack` |
| **Frontend** | `npm run build` → `python3 scripts/amplify_deploy.py` |
| **CORS / métodos API** | tras update-stack: `aws apigateway create-deployment --rest-api-id <id> --stage-name dev` |

### Nombres de las funciones Lambda
`datamask-dev-api-handler`, `datamask-dev-detection`,
`datamask-dev-redaction`, `datamask-dev-trigger`.

---

## 12. Parámetros del template (referencia)

| Parámetro | Descripción |
|-----------|-------------|
| `Environment` | `dev` o `prod` (prod retiene datos al borrar el stack) |
| `AlertEmail` | Email para alarmas de CloudWatch (SNS) |
| `LambdaCodeS3Bucket` / `LambdaCodeS3Prefix` | Ubicación de los ZIP de Lambda |
| `BedrockModelId` | Modelo Bedrock por defecto |
| `CognitoDomainPrefix` | Prefijo del dominio Hosted UI (único global) |
| `AppCallbackUrl` | URL de callback OAuth (debe terminar en `/`) |
| `SamlMetadataUrl` | URL de metadata SAML de Identity Center (vacío = sin federación) |
| `SsoStartUrl` / `SsoRegion` | Datos del portal de Identity Center (heredados) |

---

## 13. Configuración funcional (desde la app)

En **Configuración** (UI), sin redesplegar:
- **Método de detección:** qué motores se aplican — `ai` (solo IA), `regex`
  (solo reglas regex) o `both` (ambos, fusionados). Por defecto `both`.
- **Verificación con Amazon Macie (2da capa):** opcional, desactivada por
  defecto. Si se activa, tras ofuscar se lanza un escaneo de Macie sobre el
  documento ofuscado para detectar PII residual. Requiere Macie habilitado en la
  cuenta; si no lo está, la verificación se omite sin romper el pipeline.
- **Verificación con Macie:** segunda capa opcional que, tras ofuscar, escanea
  el documento con Amazon Macie para detectar PII residual. Desactivada por
  defecto (su activación añade costo). Si está habilitada, la Lambda de
  redacción crea un classification job ONE_TIME de Macie acotado al prefijo del
  documento ofuscado tras subirlo a S3. El job es asíncrono y resiliente: no
  bloquea ni rompe el pipeline (si Macie no está habilitado o falla, el estado
  queda en `UNAVAILABLE`). El estado de la verificación (`DISABLED` |
  `REQUESTED` | `UNAVAILABLE`) se persiste en DynamoDB (`macie_status`); los
  hallazgos se consultan luego vía la consola de Macie / EventBridge.
- **Modelo de IA:** modelo de Bedrock a usar.
- **Temperatura:** aleatoriedad del modelo (0 = determinista; rango 0–1).
- **Prompt:** editable; debe incluir el marcador `{text}`.
- **Reglas regex deterministas:** DNI, CUIT/CUIL, email, teléfonos, tarjetas,
  cuentas bancarias (AR/US), etc. Activables individualmente.
- **Entidades a ignorar:** valores literales que nunca se ofuscan.
