# Flujo de la Aplicación — DataMask AWS

## Resumen

DataMask AWS procesa documentos PDF para detectar y ofuscar datos personales sensibles (PII). El flujo es completamente serverless y event-driven: el usuario sube un PDF, y el sistema lo procesa automáticamente a través de un pipeline de 3 Lambdas encadenadas.

---

## Diagrama de Flujo Completo

```mermaid
sequenceDiagram
    participant U as Usuario
    participant FE as Frontend (React)
    participant COG as Cognito Hosted UI
    participant IDC as IAM Identity Center
    participant API as API Gateway
    participant LApi as Lambda API
    participant S3 as S3 Bucket
    participant LTrig as Lambda Trigger
    participant TX as Amazon Textract
    participant LDet as Lambda Detection
    participant BR as Amazon Bedrock
    participant LRed as Lambda Redaction
    participant DDB as DynamoDB

    Note over U,DDB: 1. AUTENTICACIÓN (Cognito federado con IAM Identity Center · SAML)
    U->>FE: Click "Iniciar sesión"
    FE->>COG: Redirect /oauth2/authorize (code + PKCE, identity_provider)
    COG->>IDC: Redirección SAML al IdP
    U->>IDC: Autentica (AD federado / usuario IDC)
    IDC-->>COG: Aserción SAML
    COG-->>FE: Redirect con authorization code
    FE->>COG: POST /oauth2/token (code + code_verifier)
    COG-->>FE: id_token + access_token + refresh_token
    Note over FE,API: Las llamadas siguientes llevan el id_token (JWT) en Authorization

    Note over U,DDB: 2. UPLOAD DE PDF
    U->>FE: Seleccionar PDF(s)
    FE->>API: POST /upload/presign
    API->>LApi: Generar presigned URL
    LApi-->>FE: Presigned URL (expiración 300s)
    FE->>S3: PUT directo con presigned URL<br/>(prefijo: originales/)
    FE->>API: POST /documents
    API->>LApi: Registrar documento
    LApi->>DDB: PutItem (status: UPLOADED)
    LApi-->>FE: Confirmación

    Note over U,DDB: 3. PIPELINE AUTOMÁTICO (event-driven)

    Note over S3,LTrig: 3a. TRIGGER
    S3->>LTrig: S3 Event: ObjectCreated (originales/)
    LTrig->>DDB: UpdateItem (status: PROCESSING)
    LTrig->>LTrig: Validar PDF (firma %PDF, tamaño <500MB)
    LTrig->>TX: DetectDocumentText (síncrono <15 pág)<br/>StartDocumentTextDetection (asíncrono ≥15 pág)
    TX-->>LTrig: Texto extraído + bounding boxes
    LTrig->>S3: Guardar textract_output.json<br/>(procesamiento/{id}/)
    LTrig->>LDet: Invoke (asíncrono)

    Note over LDet,BR: 3b. DETECTION
    LDet->>S3: Leer textract_output.json
    LDet->>LDet: Procesar el texto página por página
    LDet->>BR: Converse (detección contextual de PII por página)
    BR-->>LDet: Entidades PII (JSON)
    LDet->>LDet: Aplicar reglas regex configurables<br/>(DNI, CUIT/CUIL, email, etc.)
    LDet->>LDet: Fusionar + resolver solapamientos
    LDet->>S3: Guardar entities.json<br/>(procesamiento/{id}/)
    LDet->>DDB: UpdateItem (entitiesFound)
    LDet->>LRed: Invoke (asíncrono)

    Note over LRed,DDB: 3c. REDACTION
    LRed->>S3: Descargar PDF original
    LRed->>S3: Leer entities.json
    LRed->>DDB: Leer config PII del usuario
    LRed->>LRed: Filtrar entidades según config
    LRed->>LRed: Aplicar redacciones con PyMuPDF<br/>(reemplazar PII por [TIPO])
    LRed->>S3: Subir PDF ofuscado<br/>(ofuscados/{userId}/{docId}/)
    LRed->>S3: Subir informe Markdown
    LRed->>DDB: UpdateItem (status: COMPLETED,<br/>estadísticas)

    Note over U,DDB: 4. CONSULTA Y DESCARGA
    U->>FE: Ver documentos procesados
    FE->>API: GET /documents
    API->>LApi: Listar documentos
    LApi->>DDB: Query (usuario, ordenado por fecha)
    LApi-->>FE: Lista con estados

    U->>FE: Descargar PDF ofuscado
    FE->>API: GET /documents/{id}/download/redacted
    API->>LApi: Generar presigned URL descarga
    LApi-->>FE: Presigned URL (expiración 5min)
    FE->>S3: GET con presigned URL
    S3-->>U: PDF ofuscado
```

---

## Detalle de Cada Etapa

### 1. Autenticación

La autenticación se realiza con **Amazon Cognito** (User Pool) como Service
Provider SAML, **federado con AWS IAM Identity Center** como IdP. El usuario
inicia sesión en la Hosted UI de Cognito, que redirige a Identity Center; si la
organización federa con Active Directory u otro IdP externo, ese mecanismo se
usa de forma transparente. El **id token (JWT)** autoriza el API mediante un
**Cognito User Pools authorizer** en API Gateway.

| Componente | Detalle |
|-----------|---------|
| Proveedor de identidad de la app | Amazon Cognito User Pool |
| IdP federado | AWS IAM Identity Center (SAML 2.0) |
| Federación | Soporta AD corporativo u otro IdP externo configurado en Identity Center |
| Flujo | OAuth2 Authorization Code + PKCE (cliente público, sin secret) |
| Autorización del API | API Gateway + Cognito User Pools authorizer (valida el JWT) |
| Tokens | id/access token (~60 min), refresh token (~30 días) |
| Renovación | Automática con el refresh token, 5 min antes de expirar |
| Persistencia | `localStorage` (sobrevive al cierre de la pestaña) |
| Identidad backend | Derivada del claim `email` del JWT (ej: eduvilas@org.com → eduvilas) |

> Detalle completo en [`docs/autenticacion.md`](autenticacion.md).

### 2. Upload

| Componente | Detalle |
|-----------|---------|
| Validación frontend | Extensión .pdf, MIME application/pdf, <50MB, máx 30 archivos |
| Presigned URL | Expiración 300s, método PUT, prefijo `originales/` |
| Retry | 3 intentos con backoff exponencial (1s, 2s, 4s) |
| Destino S3 | `s3://{bucket}/originales/{userId}/{filename}` (estructura plana) |
| Estado DynamoDB | `UPLOADED` |

### 3a. Trigger (Lambda)

| Componente | Detalle |
|-----------|---------|
| Evento | `s3:ObjectCreated:*` en prefijo `originales/` |
| Validación | Firma `%PDF`, tamaño <500MB |
| Textract síncrono | PDFs < 15 páginas (DetectDocumentText) |
| Textract asíncrono | PDFs ≥ 15 páginas (StartDocumentTextDetection + polling 5s) |
| Timeout | 300s |
| Salida | `procesamiento/{docId}/textract_output.json` |
| Estado DynamoDB | `PROCESSING` → error: `FAILED` |
| Errores | Archivo movido a `errores/`, estado FAILED en DDB, mensaje a DLQ |

### 3b. Detection (Lambda)

| Componente | Detalle |
|-----------|---------|
| Estrategia | Detección **página por página** (cada página se analiza por separado) |
| Bedrock (IA) | API Converse, modelo configurable, timeout 30s, texto truncado a 10000 chars/página |
| Localización | El texto de cada entidad de IA se ubica en el documento (no se confía en el offset del modelo); las alucinaciones se descartan |
| Regex configurables | Reglas `{type, pattern, enabled}` editables desde la UI (DNI, CUIT/CUIL, email, teléfonos, tarjetas, cuentas, etc.) |
| Fusión | Resolución de solapamientos por cobertura → confianza → prioridad de motor (regex > IA) |
| Salida | `procesamiento/{docId}/entities.json` |
| Fallback | Si Bedrock falla, continúa solo con Regex |

> El prompt, el modelo, la temperatura y las reglas regex provienen
> **exclusivamente de la Configuración** del usuario (DynamoDB). No hay prompt
> ni reglas hardcodeadas en el pipeline. El campo **`detectionMethod`**
> (`ai` | `regex` | `both`) define qué motores se aplican. Ver
> [`algoritmos-ofuscacion.md`](algoritmos-ofuscacion.md).

### 3c. Redaction (Lambda)

| Componente | Detalle |
|-----------|---------|
| PyMuPDF | Redacción visual con color de fondo por tipo de PII |
| Etiquetas | `[NOMBRE]`, `[EMAIL]`, `[DNI]`, `[CUIT_CUIL]`, etc. |
| Redacción | Solo ofusca entidades detectadas (regex configurable + IA) |
| Salida PDF | `ofuscados/{userId}/{docId}/{stem}_ofuscado.pdf` |
| Salida MD | `ofuscados/{userId}/{docId}/{stem}_informe.md` |
| Verificación Macie | Opcional (config `macieVerification`): tras confirmar el PDF ofuscado en S3, crea un classification job ONE_TIME de Macie acotado al prefijo del documento. Asíncrono y resiliente; estado en `macie_status` (`DISABLED`/`REQUESTED`/`UNAVAILABLE`). No bloquea el pipeline |
| Estado final | `COMPLETED` con estadísticas (entitiesFound, entitiesByType, processingTimeMs, macieStatus) |
| Sin entidades | Si no se detectan entidades, no genera archivo ofuscado |

### 4. Consulta y Descarga

| Endpoint | Método | Descripción |
|----------|--------|-------------|
| `/documents` | GET | Lista documentos paginados, ordenados por fecha desc |
| `/documents/{id}` | GET | Detalle de un documento con estadísticas |
| `/documents/{id}/download/pdf` | GET | Presigned URL para PDF ofuscado (5min) |
| `/documents/{id}/download/markdown` | GET | Presigned URL para informe MD (5min) |
| `/config/detection` | GET | Configuración de detección (método, verificación Macie, modelo, temperatura, prompt, regex, ignorar) |
| `/config/detection` | PUT | Actualizar configuración de detección |
| `/config/models` | GET | Lista de modelos de Bedrock disponibles |

---

## Manejo de Errores

### Categorías de error

| Categoría | Descripción | Acción |
|-----------|-------------|--------|
| `INVALID_PDF` | PDF corrupto o no es un PDF válido | Mover a `errores/`, estado FAILED |
| `FILE_TOO_LARGE` | Excede 500MB | Mover a `errores/`, estado FAILED |
| `PASSWORD_PROTECTED` | PDF protegido con contraseña | Estado FAILED |
| `TEXTRACT_ERROR` | Error al extraer texto | Reintentar 1x, luego FAILED + DLQ |
| `TEXTRACT_TIMEOUT` | Timeout de Textract (>300s) | Estado FAILED + DLQ |
| `DETECTION_ERROR` | Error en detección PII | Estado FAILED + DLQ |
| `REDACTION_ERROR` | Error al aplicar redacción | Estado FAILED + DLQ |

### Dead Letter Queue (DLQ)

Todos los errores no manejados se envían a la SQS Dead Letter Queue con:
- Retención: 14 días
- Alarma CloudWatch: mensajes > 0 → notificación SNS
- Cada mensaje incluye: función origen, error, timestamp, documentId

### Alarmas CloudWatch

| Alarma | Condición | Acción |
|--------|-----------|--------|
| Lambda Errors | > 5 errores en 5 min (por función) | Email SNS |
| API Latency P99 | > 3 segundos en 5 min | Email SNS |
| DLQ Messages | > 0 mensajes visibles | Email SNS |

---

## Estructura de Datos en DynamoDB

### Single-Table Design

| Access Pattern | PK | SK |
|---------------|----|----|
| Documento por ID | `USER#{userId}` | `DOC#{docId}` |
| Documentos de un usuario | `GSI1PK: USER#{userId}` | `GSI1SK: STATUS#{status}#TIMESTAMP#{ts}` |
| Configuración de detección | `USER#{userId}` | `CONFIG#DETECTION` |

### GSI1

| Access Pattern | GSI1PK | GSI1SK |
|---------------|--------|--------|
| Documentos por estado | `STATUS#{status}` | `{timestamp}` |

---

## Estructura S3

```
s3://datamask-{env}-documents-{accountId}/
├── originales/                    # PDFs subidos por el usuario
│   └── {userId}/
│       └── {filename}.pdf         # estructura plana (sin UUID intermedio)
├── procesamiento/                 # Artefactos intermedios
│   └── {userId}/{docId}/
│       ├── textract_output.json   # Texto extraído por Textract
│       └── entities.json          # Entidades PII detectadas
├── ofuscados/                     # Documentos finales
│   └── {userId}/{docId}/
│       ├── {stem}_ofuscado.pdf
│       └── {stem}_informe.md
└── errores/                       # PDFs inválidos (TTL: 30 días)
    └── {userId}/{docId}/
        └── {filename}.pdf
```

---

## Seguridad

| Aspecto | Implementación |
|---------|---------------|
| Cifrado at-rest | SSE-KMS con rotación automática de clave |
| Cifrado in-transit | TLS 1.2+ en API Gateway y CloudFront; bucket policy deny si no-TLS |
| Acceso S3 | Block Public Access en todos los buckets |
| IAM | Mínimo privilegio por función Lambda (sin wildcards en acciones) |
| Autenticación | Amazon Cognito federado con IAM Identity Center (SAML); JWT en el API |
| Secrets | AWS Secrets Manager (nunca hardcodeados) |
| Respuestas de error | HTTP 403 genérico (no revelar existencia de recursos) |
| Logs | No se registran datos PII en CloudWatch |
| DLQ | Cifrado SQS Managed SSE |
