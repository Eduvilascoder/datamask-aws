# DataMask AWS

**Detección y ofuscación de datos sensibles en documentos PDF — 100% serverless en AWS**

![Version](https://img.shields.io/badge/version-2.0-blue)
![Python](https://img.shields.io/badge/python-3.12-green)
![AWS](https://img.shields.io/badge/AWS-CloudFormation-orange)
![Serverless](https://img.shields.io/badge/architecture-serverless-purple)

DataMask AWS detecta y enmascara automáticamente datos personales sensibles (PII) en documentos PDF usando inteligencia artificial. Se despliega como una solución serverless en la cuenta AWS del cliente mediante un único template CloudFormation.

## Características

- 🚀 **100% Serverless** — sin servidores que administrar, escala automáticamente
- 🤖 **Doble motor de detección** — Amazon Bedrock (IA, multi-proveedor) + patrones regex configurables
- 🇦🇷 **Formatos argentinos** — DNI, CUIT/CUIL, pasaportes, teléfonos +54
- 📄 **Redacción visual** — reemplaza PII por etiquetas `[TIPO]` conservando formato
- ⚙️ **Detección configurable** — método (IA, regex o ambos), modelo de IA, temperatura, prompt y reglas regex editables desde la UI
- 🔐 **Login federado** — Amazon Cognito federado con AWS IAM Identity Center (SAML)
- 📊 **Observabilidad** — CloudWatch Alarms + SNS para alertas en tiempo real
- 🔒 **Seguro por diseño** — cifrado KMS, IAM mínimo privilegio, TLS 1.2+
- 📦 **Despliegue simple** — instalador one-command (`scripts/install.sh`)

## Datos que detecta

| Tipo | Etiqueta | Motor | Ejemplo |
|------|----------|-------|---------|
| Nombre y Apellido | `[NOMBRE]` | IA (Bedrock) | Juan Pérez |
| Email | `[EMAIL]` | IA + Regex | usuario@ejemplo.com |
| Teléfono | `[TELEFONO]` | IA + Regex | +54 11 4567 8901 |
| Dirección | `[DIRECCION]` | IA (Bedrock) | Av. Corrientes 1234 |
| DNI | `[DNI]` | Regex | 32.456.789 |
| CUIT/CUIL | `[CUIT_CUIL]` | Regex | 20-32456789-4 |
| Tarjeta de Crédito | `[TARJETA_CREDITO]` | IA + Regex | 4532-1234-5678-9012 |
| Cuenta Bancaria | `[CUENTA_BANCARIA]` | Regex | CBU 22 dígitos |
| Pasaporte | `[PASAPORTE]` | Regex | AAB123456 |

> Cada etiqueta lleva un sufijo que indica el motor que ganó la detección:
> `-IA` (Bedrock) o `-R` (regex). Ej: `[DNI-R]`, `[NOMBRE-IA]`.

## Arquitectura

```
┌─────────────────┐     ┌──────────────┐     ┌──────────────────┐
│  Frontend React │────▶│ API Gateway  │────▶│  Lambda API      │
│  (Amplify)      │     │  REST        │     │  Handler         │
└─────────────────┘     └──────────────┘     └──────────────────┘
                                                      │
                                                      ▼
┌─────────────────────────────────────────────────────────────────┐
│                        S3 Bucket                                 │
│  originales/ ──▶ procesamiento/ ──▶ ofuscados/                  │
└─────────────────────────────────────────────────────────────────┘
        │                                        ▲
        ▼                                        │
┌──────────────┐    ┌──────────────┐    ┌──────────────────┐
│ Lambda       │───▶│ Lambda       │───▶│ Lambda           │
│ Trigger      │    │ Detection    │    │ Redaction        │
│ + Textract   │    │ + Bedrock    │    │ + PyMuPDF        │
│              │    │ + Regex      │    │                  │
└──────────────┘    └──────────────┘    └──────────────────┘
```

## Despliegue Rápido

### Opción A — Instalador one-command (recomendado)

```bash
git clone https://github.com/Eduvilascoder/DataMask-AWS.git
cd DataMask-AWS

./scripts/install.sh \
  --profile <AWS_PROFILE> \
  --region us-east-1 \
  --environment dev \
  --alert-email tu-email@dominio.com \
  --callback-url https://tu-app.amplifyapp.com/
```

El script crea el bucket de artefactos, empaqueta y sube las Lambdas, despliega
el stack CloudFormation (incluido Cognito) e imprime los Outputs. Después
configurá el frontend y, opcionalmente, la federación SAML.

### Opción B — Paso a paso

Ver la guía completa: [`docs/howtodeploydatamask.md`](docs/howtodeploydatamask.md)

> 📖 La guía cubre prerrequisitos, artefactos, stack, frontend, federación SAML
> con IAM Identity Center, verificación y ciclo de actualización.

## Documentación

| Documento | Descripción |
|-----------|-------------|
| [Cómo desplegar](docs/howtodeploydatamask.md) | Guía completa de despliegue (automático + manual) |
| [Autenticación](docs/autenticacion.md) | Login con Cognito federado con IAM Identity Center (SAML) |
| [Flujo de la Aplicación](docs/flujo-aplicacion-aws.md) | Diagrama de secuencia y detalle de cada etapa |
| [Algoritmos de ofuscación](docs/algoritmos-ofuscacion.md) | Cómo detecta PII la IA (Bedrock) y los regex |
| [Análisis de costos](docs/pricing-estimado.md) | Estimación de costos mensual con supuestos |
| [Evaluación de seguridad](docs/security-assessment.md) | Security assessment del sistema |

## Autenticación

El acceso usa **Amazon Cognito** federado con **AWS IAM Identity Center** vía
**SAML 2.0**. El usuario inicia sesión en la Hosted UI de Cognito, que redirige
a Identity Center; si la organización federa con **Active Directory** u otro IdP
externo, se usan las credenciales corporativas.

- Flujo **OAuth2 Authorization Code + PKCE** (cliente público, sin secret).
- El API se autoriza con el **id token (JWT)** mediante un **Cognito User Pools
  authorizer** en API Gateway.
- El backend deriva el usuario del claim `email` (ej: `eduvilas@org.com` →
  `eduvilas`); cada usuario solo accede a sus propios documentos.
- No se gestionan contraseñas ni se almacenan credenciales de larga duración.

Detalle en [`docs/autenticacion.md`](docs/autenticacion.md).

## Estructura del Proyecto

```
DataMask-AWS/
├── datamask-template.yaml    # Template CloudFormation (~40 recursos)
├── lambdas/
│   ├── api/                  # Lambda API Handler (uploads, documentos, config)
│   ├── trigger/              # Lambda Trigger (S3 events → Textract)
│   ├── detection/            # Lambda Detection (Bedrock IA + regex)
│   ├── redaction/            # Lambda Redaction (PyMuPDF)
│   └── layers/pymupdf/      # Lambda Layer PyMuPDF
├── frontend/                 # React + TypeScript + Cloudscape
├── scripts/
│   ├── install.sh           # Instalador one-command (backend completo)
│   ├── package-lambdas.sh   # Empaquetado de Lambdas en ZIPs
│   ├── package-release.sh   # Genera el tarball entregable
│   └── amplify_deploy.py    # Deploy manual del frontend a Amplify
├── docs/                     # Documentación para partners
└── amplify.yml               # Configuración de build para Amplify
```

## Servicios AWS Utilizados

| Servicio | Propósito | Costo dominante |
|----------|-----------|-----------------|
| Lambda | Compute serverless (4 funciones) | Por invocación |
| S3 | Almacenamiento de documentos | Por GB almacenado |
| DynamoDB | Estado y metadatos | Por request (on-demand) |
| API Gateway | API REST para frontend | Por request |
| Textract | Extracción de texto de PDFs | Por página |
| Bedrock | Detección de PII con IA (Claude, multi-proveedor) | Por token |
| KMS | Cifrado at-rest | Por request |
| SQS | Dead Letter Queue | Por mensaje |
| SNS | Alertas por email | Por notificación |
| CloudWatch | Logs y alarmas | Por GB ingestado |

> Análisis de costos detallado en [`docs/pricing-estimado.md`](docs/pricing-estimado.md).

## Seguridad

- **Autenticación**: Amazon Cognito federado con IAM Identity Center (SAML), federable con AD
- **Autorización API**: API Gateway con Cognito User Pools authorizer (id token JWT)
- **Cifrado**: KMS (at-rest) + TLS 1.2+ (in-transit, bucket policy deny no-TLS)
- **IAM**: Mínimo privilegio por función, sin wildcards en acciones
- **S3**: Block Public Access en todos los buckets
- **Secrets**: AWS Secrets Manager (nunca hardcodeados en código)
- **API**: HTTP 403 genérico sin revelar existencia de recursos
- **Logs**: No se registran datos PII en CloudWatch
- **DLQ**: Errores aislados con retención de 14 días

> Evaluación de seguridad completa en [`docs/security-assessment.md`](docs/security-assessment.md).

## Requisitos

- Cuenta AWS con acceso a Bedrock (modelo Claude habilitado)
- AWS CLI v2 configurado
- Python 3.12 + pip (para empaquetar Lambdas)
- Node.js 18+ (para build del frontend)

## Costos Estimados

Para ~100 PDFs/mes (5 páginas promedio): **~$10/mes**.

El costo está dominado por Textract (~$7.50) y Bedrock (~$1-2). Ver el desglose
y los supuestos en [`docs/pricing-estimado.md`](docs/pricing-estimado.md).

## Algoritmos de detección

DataMask combina dos motores complementarios: **IA (Bedrock)** para detección
contextual y **regex** para formatos deterministas argentinos. El detalle de
ambos algoritmos, la fusión y la resolución de solapamientos está en
[`docs/algoritmos-ofuscacion.md`](docs/algoritmos-ofuscacion.md).

---

**DataMask AWS v2.0** — by [EduTheCoder](https://github.com/Eduvilascoder)
