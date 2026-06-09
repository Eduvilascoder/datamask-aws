# Implementation Plan: DataMask AWS — PDF Redaction Pipeline

## Overview

Implementación completa de DataMask AWS como solución cloud-native serverless. El plan sigue el flujo de dependencias: infraestructura CDK → backend Lambda (API + pipeline) → frontend React → integración y validación final. Las Lambdas se implementan en Python 3.12, la IaC en CDK TypeScript, y el frontend en React TypeScript.

## Tasks

- [x] 1. Configurar proyecto CDK e infraestructura base
  - [x] 1.1 Inicializar proyecto CDK TypeScript con estructura de stacks
    - Crear directorio `infra/` con `cdk.json`, `tsconfig.json`, `package.json`
    - Configurar cuenta 339712829454, región us-east-1, perfil masterGenAI en `cdk.json`
    - Crear `bin/datamask.ts` con instanciación de stacks y validación de parámetro `environment` (dev|prod)
    - Crear script de bootstrap que verifique perfil masterGenAI y cuenta asociada
    - _Requirements: 10.1, 13.1, 13.2, 13.3, 13.5, 13.6_

  - [x] 1.2 Implementar StorageStack (S3 + DynamoDB + KMS)
    - Crear S3 bucket `datamask-{env}-documents` con prefijos (originales/, ofuscados/, procesamiento/, errores/)
    - Configurar Block Public Access, SSE-KMS, S3 Access Logging, lifecycle policies
    - Crear DynamoDB table `datamask-{env}-documents` con PK/SK y GSI1 (single-table design)
    - Crear KMS keys para cifrado
    - Aplicar tags obligatorios (Project, Environment, Owner, CostCenter) via CDK Aspects
    - Configurar RemovalPolicy según ambiente (DESTROY dev, RETAIN prod)
    - _Requirements: 10.1, 10.3, 10.4, 10.5, 11.2, 11.3, 13.5_

  - [x] 1.3 Implementar ComputeStack (Lambdas + Layers + DLQ)
    - Crear Lambda functions: Trigger (512MB, 300s), Detección (1024MB, 120s), Redacción (1024MB, 300s)
    - Crear Lambda Layer para PyMuPDF compilado para Amazon Linux 2023
    - Crear SQS Dead Letter Queue `datamask-{env}-dlq` con retención 14 días
    - Configurar IAM roles con mínimo privilegio para cada Lambda
    - Configurar S3 Event Notification (s3:ObjectCreated:* en prefijo originales/) → Lambda Trigger
    - _Requirements: 10.1, 10.2, 10.5, 3.1, 3.5_

  - [x] 1.4 Implementar ApiStack (API Gateway + Lambda API + Auth)
    - Crear API Gateway REST con endpoints definidos en el diseño
    - Crear Lambda API Handler (Python 3.12) con role IAM
    - Configurar integración con IAM Identity Center (OIDC)
    - Configurar TLS 1.2 mínimo en API Gateway
    - _Requirements: 10.1, 10.2, 10.5, 11.1, 11.6_

  - [x] 1.5 Implementar FrontendStack (Amplify + CloudFront)
    - Crear Amplify App conectada al repositorio GitHub
    - Crear CloudFront Distribution con Amplify como origin
    - Configurar security headers (CSP, HSTS), TLS policy (TLSv1.2_2021)
    - Configurar WAF (opcional) y cache policies
    - _Requirements: 10.1, 10.5, 11.1_

  - [x] 1.6 Implementar ObservabilityStack (CloudWatch + SNS)
    - Crear SNS topic para alertas
    - Configurar CloudWatch alarms: errores Lambda >5 en 5min, latencia P99 API >3s, errores Textract/Comprehend/Bedrock >3 en 5min
    - Configurar retención de logs CloudWatch a 90 días
    - _Requirements: 10.6, 11.5_

  - [ ]* 1.7 Escribir tests de CDK (assertions + snapshots)
    - Verificar que no hay wildcards en IAM policies
    - Verificar tags obligatorios en todos los recursos
    - Verificar configuración de cifrado en S3 y DynamoDB
    - _Requirements: 10.2, 10.3, 10.4_

- [x] 2. Checkpoint - Validar infraestructura CDK
  - Ensure all tests pass, ask the user if questions arise.
  - Ejecutar `cdk synth` para validar síntesis sin errores

- [x] 3. Implementar Lambda API Handler (Backend REST)
  - [x] 3.1 Crear estructura base del Lambda API Handler
    - Crear directorio `lambdas/api/` con `handler.py`, `requirements.txt`
    - Implementar handler principal con routing por método/path
    - Implementar middleware de autenticación (validar token OIDC)
    - Implementar respuestas de error genéricas (HTTP 403 sin revelar existencia de recursos)
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 11.6_

  - [x] 3.2 Implementar endpoints de autenticación
    - POST `/auth/login` — iniciar flujo OIDC con IAM Identity Center
    - POST `/auth/callback` — intercambiar code por token, establecer sesión (60min máx, 15min inactividad)
    - POST `/auth/logout` — invalidar sesión, eliminar cookie
    - Configurar cookies HttpOnly, Secure, SameSite=Lax
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7_

  - [x] 3.3 Implementar endpoints de upload y documentos
    - POST `/upload/presign` — generar presigned URLs (expiración 300s) para prefijo originales/
    - POST `/documents` — registrar documento en DynamoDB (status=UPLOADED)
    - GET `/documents` — listar documentos del usuario paginados, ordenados por fecha desc
    - GET `/documents/{id}` — detalle de un documento
    - GET `/documents/{id}/download/{type}` — generar presigned URL descarga (expiración 5min)
    - _Requirements: 2.3, 2.4, 9.1, 9.2, 9.3, 9.4, 9.5, 11.2_

  - [x] 3.4 Implementar endpoints de configuración PII
    - GET `/config` — obtener configuración de tipos PII del usuario (default: todos activos)
    - PUT `/config` — actualizar configuración, rechazar si todos los toggles están en false
    - Persistir en DynamoDB bajo PK=USER#{userId}, SK=CONFIG#PII_TYPES
    - _Requirements: 12.1, 12.2, 12.3, 12.5, 12.6_

  - [ ]* 3.5 Escribir tests unitarios para Lambda API Handler
    - Tests para validación de autenticación y sesión
    - Tests para generación de presigned URLs
    - Tests para CRUD de documentos y configuración
    - Tests para respuestas de error (403 genérico)
    - _Requirements: 1.6, 2.4, 9.4, 11.6, 12.3_

  - [ ]* 3.6 Escribir property test — Authorization Response Opacity
    - **Property 17: Authorization Response Opacity**
    - **Validates: Requirements 11.6**

  - [ ]* 3.7 Escribir property test — PII Configuration Validation
    - **Property 15: PII Configuration Validation**
    - **Validates: Requirements 12.3**

- [x] 4. Implementar Lambda Trigger (S3 Event Handler)
  - [x] 4.1 Crear estructura de Lambda Trigger
    - Crear directorio `lambdas/trigger/` con `handler.py`, `requirements.txt`
    - Implementar handler para eventos S3 (PutObject en originales/)
    - Implementar validación de PDF (firma `%PDF` y parseabilidad)
    - Implementar validación de tamaño (<500MB)
    - Registrar estado PROCESSING en DynamoDB
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.7_

  - [x] 4.2 Implementar invocación a Textract
    - Invocar Textract síncrono (DetectDocumentText) para PDFs <15 páginas
    - Invocar Textract asíncrono (StartDocumentTextDetection) para PDFs ≥15 páginas con polling 5s
    - Reconstruir texto preservando orden de páginas, bloques y bounding boxes
    - Almacenar resultado en S3 procesamiento/{id}/textract_output.json
    - Manejar errores: TEXTRACT_ERROR, TEXTRACT_TIMEOUT (300s)
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6_

  - [x] 4.3 Implementar manejo de errores y DLQ
    - Mover archivos inválidos/excedentes a prefijo errores/ con metadatos
    - Actualizar estado FAILED en DynamoDB con paso y descripción del error
    - Configurar envío a Dead Letter Queue en caso de fallo no manejado
    - Registrar eventos en CloudWatch
    - _Requirements: 3.3, 3.5, 3.7_

  - [ ]* 4.4 Escribir property test — PDF Validity Detection
    - **Property 2: PDF Validity Detection**
    - **Validates: Requirements 3.3**

  - [ ]* 4.5 Escribir property test — Textract Output Reconstruction
    - **Property 3: Textract Output Reconstruction**
    - **Validates: Requirements 4.2**

  - [ ]* 4.6 Escribir tests unitarios para Lambda Trigger
    - Tests para parsing de eventos S3
    - Tests para validación de PDF (válido, inválido, >500MB)
    - Tests para invocación Textract (síncrono y asíncrono)
    - _Requirements: 3.3, 3.7, 4.4, 4.6_

- [x] 5. Implementar Lambda Detección (PII Detection)
  - [x] 5.1 Implementar text splitter y motor Comprehend
    - Crear directorio `lambdas/detection/` con `handler.py`, `requirements.txt`
    - Implementar text splitter: bloques ≤5000 bytes UTF-8, sin cortar oraciones, concatenación reproduce original
    - Implementar invocación a Comprehend DetectPiiEntities (LanguageCode=es) por bloque
    - Implementar mapeo de tipos Comprehend → Sistema (NAME→NOMBRE, EMAIL_ADDRESS→EMAIL, etc.)
    - Filtrar entidades con confianza <0.75
    - Recalcular offsets absolutos según posición del bloque
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.6_

  - [x] 5.2 Implementar patrones regex argentinos
    - DNI: 7-8 dígitos con puntos (X.XXX.XXX o XX.XXX.XXX)
    - CUIT/CUIL: prefijo (20|23|24|27|30|33|34) + separador + 8 dígitos + separador + 1 dígito
    - Pasaporte argentino: AA + letra opcional + 6 dígitos
    - Asignar confianza 0.95 a detecciones regex
    - Evaluar entidades Comprehend tipo "OTHER" (score >0.85) contra patrones
    - _Requirements: 5.5, 7.5_

  - [x] 5.3 Implementar invocación a Bedrock
    - Invocar Bedrock con modelo configurado (BEDROCK_MODEL_ID, default claude-3-haiku)
    - Construir prompt con lista de tipos PII e instrucciones para español argentino
    - Truncar texto a máximo 10000 caracteres
    - Parsear respuesta JSON array, validar campos obligatorios (text, type, start_offset)
    - Descartar entidades con campos faltantes o valores inválidos (tolerancia ±5 chars para offset)
    - Continuar solo con Comprehend+Regex si Bedrock falla/timeout (30s)
    - _Requirements: 6.1, 6.2, 6.3, 6.4_

  - [x] 5.4 Implementar fusión, deduplicación y salida
    - Fusionar entidades Comprehend + Bedrock: mismo tipo y solapamiento → Comprehend; distinto tipo y solapamiento con mayor cobertura Bedrock → Bedrock; sin solapamiento → ambas
    - Deduplicar: mismo tipo, intersección/longitud_menor > 80% → mantener mayor span (o mayor confianza si iguales)
    - Ordenar por página ascendente, luego por start_offset ascendente
    - Generar JSON de salida con todos los campos requeridos (text, type, page, startOffset, endOffset, source, confidence)
    - Almacenar en S3 procesamiento/{id}/entities.json
    - _Requirements: 6.5, 6.6, 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7_

  - [ ]* 5.5 Escribir property test — Text Block Splitting Round-Trip
    - **Property 4: Text Block Splitting Round-Trip**
    - **Validates: Requirements 5.1, 5.3**

  - [ ]* 5.6 Escribir property test — Comprehend Type Mapping
    - **Property 5: Comprehend Type Mapping**
    - **Validates: Requirements 5.2**

  - [ ]* 5.7 Escribir property test — Argentine Regex Pattern Detection
    - **Property 6: Argentine Regex Pattern Detection**
    - **Validates: Requirements 5.5, 7.5**

  - [ ]* 5.8 Escribir property test — Bedrock Response Parsing
    - **Property 7: Bedrock Response Parsing**
    - **Validates: Requirements 6.3**

  - [ ]* 5.9 Escribir property test — Entity Merge Conflict Resolution
    - **Property 8: Entity Merge Conflict Resolution**
    - **Validates: Requirements 6.5, 6.6**

  - [ ]* 5.10 Escribir property test — Entity Deduplication
    - **Property 9: Entity Deduplication**
    - **Validates: Requirements 7.3, 7.4**

  - [ ]* 5.11 Escribir property test — Entity List Ordering
    - **Property 10: Entity List Ordering**
    - **Validates: Requirements 7.1**

  - [ ]* 5.12 Escribir property test — Entity Output JSON Completeness
    - **Property 11: Entity Output JSON Completeness**
    - **Validates: Requirements 7.6**

  - [ ]* 5.13 Escribir tests unitarios para Lambda Detección
    - Tests para text splitter con textos de diversas longitudes
    - Tests para mapeo de tipos Comprehend
    - Tests para patrones regex con ejemplos válidos e inválidos
    - Tests para fusión y deduplicación con casos edge
    - _Requirements: 5.1, 5.2, 5.5, 6.3, 6.5, 7.3_

- [x] 6. Checkpoint - Validar pipeline backend
  - Ensure all tests pass, ask the user if questions arise.
  - Verificar que las 3 Lambdas del pipeline se invocan correctamente en cadena

- [x] 7. Implementar Lambda Redacción (PDF Redaction)
  - [x] 7.1 Implementar redacción de PDF con PyMuPDF
    - Crear directorio `lambdas/redaction/` con `handler.py`, `requirements.txt`
    - Descargar PDF original desde S3
    - Aplicar redacciones: reemplazar texto PII por etiquetas `[TIPO]` con color de fondo diferenciado por tipo
    - Preservar estructura del PDF (fuentes, layout, imágenes) usando guardado incremental
    - Manejar PDFs protegidos con contraseña (retornar PASSWORD_PROTECTED)
    - Manejar PDFs corruptos (retornar CORRUPTED)
    - Omitir generación si entities_found=0
    - _Requirements: 8.1, 8.2, 8.3, 8.7, 8.8_

  - [x] 7.2 Implementar generación de salida y almacenamiento
    - Generar nombre de archivo: `{stem}_ofuscado.pdf` y `{stem}_informe.md`
    - Subir PDF ofuscado a S3 ofuscados/{userId}/{docId}/
    - Generar informe Markdown: título, metadatos, texto ofuscado por página
    - Subir informe a S3 ofuscados/{userId}/{docId}/
    - Actualizar DynamoDB: status=COMPLETED, estadísticas (entitiesFound, entitiesByType, processingTimeMs)
    - _Requirements: 8.4, 8.5, 8.6, 8.9_

  - [x] 7.3 Implementar filtrado por configuración de usuario
    - Leer configuración PII del usuario desde DynamoDB
    - Filtrar entidades detectadas: incluir solo tipos activos en la configuración
    - _Requirements: 12.4_

  - [ ]* 7.4 Escribir property test — Output Filename Generation
    - **Property 12: Output Filename Generation**
    - **Validates: Requirements 8.4, 8.6**

  - [ ]* 7.5 Escribir property test — Markdown Report Structure
    - **Property 13: Markdown Report Structure**
    - **Validates: Requirements 8.5**

  - [ ]* 7.6 Escribir property test — Error Message Truncation
    - **Property 14: Error Message Truncation**
    - **Validates: Requirements 9.6**

  - [ ]* 7.7 Escribir property test — Entity Type Filtering by Configuration
    - **Property 16: Entity Type Filtering by Configuration**
    - **Validates: Requirements 12.4**

  - [ ]* 7.8 Escribir tests unitarios para Lambda Redacción
    - Tests para redacción de texto con PyMuPDF
    - Tests para generación de markdown
    - Tests para manejo de PDFs protegidos/corruptos
    - Tests para filtrado por configuración
    - _Requirements: 8.1, 8.5, 8.7, 12.4_

- [x] 8. Checkpoint - Validar pipeline completo de procesamiento
  - Ensure all tests pass, ask the user if questions arise.
  - Verificar flujo completo: S3 event → Trigger → Detección → Redacción → DynamoDB COMPLETED

- [x] 9. Implementar Frontend React
  - [x] 9.1 Configurar proyecto React con Vite y dependencias
    - Configurar Vite + React 18 + TypeScript en directorio `frontend/`
    - Instalar dependencias: Cloudscape Design System, React Router, Axios
    - Configurar ESLint + Prettier, tsconfig con strict: true
    - Configurar Vitest para tests
    - _Requirements: 10.1_

  - [x] 9.2 Implementar AuthProvider y flujo de autenticación
    - Crear componente AuthProvider con gestión de tokens OIDC
    - Implementar refresh automático de token y detección de sesión expirada
    - Implementar redirect a login con URL de destino preservada
    - Implementar logout (invalidar sesión, limpiar cookies)
    - Mostrar mensajes de error descriptivos sin revelar detalles internos
    - _Requirements: 1.1, 1.2, 1.3, 1.5, 1.6, 1.7_

  - [x] 9.3 Implementar UploadPage con validación y subida directa a S3
    - Crear componente de selección de archivos (solo .pdf, MIME application/pdf)
    - Validar: extensión, MIME type, tamaño <50MB, máximo 10 archivos por operación
    - Solicitar presigned URLs al backend y subir directamente a S3
    - Mostrar barra de progreso por archivo
    - Implementar retry automático 3x con backoff exponencial (1s, 2s, 4s)
    - Actualizar lista con estado "Subido — pendiente de procesamiento"
    - _Requirements: 2.1, 2.2, 2.5, 2.6, 2.7, 2.8_

  - [x] 9.4 Implementar ProcessingPage con polling y descarga
    - Listar documentos ordenados por fecha desc con estado (UPLOADED, PROCESSING, COMPLETED, FAILED)
    - Mostrar estadísticas para COMPLETED: entidades totales, desglose por tipo, tiempo en segundos
    - Mostrar botones de descarga (PDF ofuscado + informe markdown) para COMPLETED
    - Mostrar motivo de error (máx 200 chars, con categoría) para FAILED
    - Implementar polling cada 10s mientras existan documentos en PROCESSING
    - Mostrar estado vacío con enlace a upload si no hay documentos
    - _Requirements: 9.1, 9.2, 9.3, 9.5, 9.6, 9.7, 9.8_

  - [x] 9.5 Implementar ConfigPage con toggles PII
    - Mostrar toggles para 11 tipos de PII
    - Validar que al menos 1 tipo quede activo (rechazar si todos false)
    - Persistir vía API y mostrar notificación de éxito (<3s)
    - Cargar configuración existente o defaults (todos activos)
    - _Requirements: 12.1, 12.2, 12.3, 12.5, 12.6_

  - [ ]* 9.6 Escribir property test — File Upload Validation
    - **Property 1: File Upload Validation**
    - **Validates: Requirements 2.2**

  - [ ]* 9.7 Escribir tests unitarios frontend (Vitest)
    - Tests para AuthProvider (login, logout, sesión expirada)
    - Tests para UploadPage (validación de archivos, progreso)
    - Tests para ProcessingPage (estados, polling, descarga)
    - Tests para ConfigPage (toggles, validación)
    - _Requirements: 1.1, 2.2, 9.1, 12.1_

- [x] 10. Checkpoint - Validar frontend
  - Ensure all tests pass, ask the user if questions arise.
  - Verificar que el frontend compila sin errores y los tests de Vitest pasan

- [x] 11. Integración y wiring final
  - [x] 11.1 Conectar frontend con backend y configurar despliegue
    - Configurar API client en frontend con base URL del API Gateway
    - Configurar Amplify build settings (amplify.yml) con build de Vite
    - Verificar flujo completo: login → upload → procesamiento → descarga
    - Verificar configuración PII persiste y se aplica al pipeline
    - _Requirements: 1.1, 2.5, 9.3, 12.4_

  - [x] 11.2 Configurar seguridad end-to-end
    - Verificar TLS 1.2+ en CloudFront y API Gateway
    - Verificar Block Public Access en S3
    - Verificar secrets en Secrets Manager (no hardcodeados)
    - Verificar respuestas 403 genéricas sin revelar existencia de recursos
    - _Requirements: 11.1, 11.2, 11.4, 11.6, 11.7_

  - [ ]* 11.3 Escribir tests de integración
    - Test flujo upload → S3 event → pipeline completo con mocks
    - Test transiciones de estado DynamoDB (UPLOADED → PROCESSING → COMPLETED/FAILED)
    - Test auth flow con mock OIDC provider
    - _Requirements: 3.1, 3.4, 3.6, 1.2_

- [x] 12. Final checkpoint - Validar sistema completo
  - Ensure all tests pass, ask the user if questions arise.
  - Ejecutar `cdk synth` exitoso
  - Todos los property tests pasan (Hypothesis, mínimo 100 iteraciones)
  - Todos los unit tests pasan (pytest + Vitest)

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties (17 properties defined in design)
- Unit tests validate specific examples and edge cases
- Python backend uses pytest + Hypothesis for property-based testing
- Frontend uses Vitest + Testing Library
- CDK tests use Jest + CDK assertions
- Lambda runtime: Python 3.12 for all backend functions
- Frontend: React 18 + TypeScript + Vite + Cloudscape Design System

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "9.1"] },
    { "id": 2, "tasks": ["1.3", "1.4", "1.5", "1.6"] },
    { "id": 3, "tasks": ["1.7", "3.1", "4.1", "5.1"] },
    { "id": 4, "tasks": ["3.2", "4.2", "5.2", "5.3"] },
    { "id": 5, "tasks": ["3.3", "3.4", "4.3", "5.4"] },
    { "id": 6, "tasks": ["3.5", "3.6", "3.7", "4.4", "4.5", "4.6", "5.5", "5.6", "5.7", "5.8"] },
    { "id": 7, "tasks": ["5.9", "5.10", "5.11", "5.12", "5.13", "7.1"] },
    { "id": 8, "tasks": ["7.2", "7.3"] },
    { "id": 9, "tasks": ["7.4", "7.5", "7.6", "7.7", "7.8", "9.2"] },
    { "id": 10, "tasks": ["9.3", "9.4", "9.5"] },
    { "id": 11, "tasks": ["9.6", "9.7"] },
    { "id": 12, "tasks": ["11.1", "11.2"] },
    { "id": 13, "tasks": ["11.3"] }
  ]
}
```
