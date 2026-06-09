# Implementation Plan: Template CloudFormation para Despliegue por Partner

## Overview

Implementación de un template CloudFormation YAML autónomo (~35 recursos), un script de empaquetado de Lambdas, y una guía de despliegue en español. El enfoque es incremental: primero la estructura base del template con parámetros y condiciones, luego los recursos de almacenamiento, compute, API, observabilidad, y finalmente el script de empaquetado y la documentación.

## Tasks

- [x] 1. Crear estructura base del template CloudFormation
  - [x] 1.1 Crear el archivo `datamask-template.yaml` con la sección de metadatos, parámetros y condiciones
    - Crear archivo `datamask-template.yaml` en la raíz del proyecto
    - Incluir `AWSTemplateFormatVersion: '2010-09-09'` y `Description` en español
    - Definir los 7 parámetros: `Environment`, `BucketName`, `AlertEmail`, `BedrockModelId`, `LambdaCodeS3Bucket`, `LambdaCodeS3Prefix`, `ProjectTag`
    - Incluir AllowedPattern con regex para `BucketName` y `AlertEmail`
    - Incluir AllowedValues para `Environment` (dev, prod)
    - Incluir valores por defecto donde corresponda
    - Incluir descripción en español para cada parámetro
    - Definir condiciones `IsProd` e `IsDev`
    - _Requisitos: 1.3, 1.4, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7_

- [x] 2. Implementar recursos de almacenamiento y cifrado
  - [x] 2.1 Crear recursos KMS Key, Alias, bucket de logs y bucket de documentos
    - Crear `AWS::KMS::Key` con rotación automática y política de administración root
    - Crear `AWS::KMS::Alias` con patrón `datamask-{Environment}-s3-key`
    - Crear `AWS::S3::Bucket` para logs con SSE-S3 y lifecycle de 90 días
    - Crear `AWS::S3::Bucket` para documentos con SSE-KMS, BlockPublicAccess, Versioning, lifecycle para `procesamiento/` (30 días)
    - Configurar Server Access Logging apuntando al bucket de logs
    - Aplicar `DeletionPolicy` condicional: Retain en prod, Delete en dev
    - Aplicar tags estándar a todos los recursos (Project, Environment, ManagedBy, Application, Stack)
    - _Requisitos: 3.1, 3.2, 3.5, 3.6, 10.3, 10.4, 10.5, 10.6, 9.1, 9.2, 9.3_

  - [x] 2.2 Crear tabla DynamoDB con GSI y cifrado
    - Crear `AWS::DynamoDB::Table` con nombre `datamask-{Environment}-documents`
    - Clave de partición `PK` (String) y ordenación `SK` (String)
    - GSI1 con `GSI1PK` y `GSI1SK`
    - Modo PAY_PER_REQUEST
    - Cifrado con AWS_OWNED
    - DeletionPolicy condicional
    - Tags estándar
    - _Requisitos: 3.3, 3.4, 10.5, 10.6, 9.1, 9.2, 9.3_

- [ ] 3. Implementar recursos de compute (Lambda, Layer, DLQ, Roles IAM)
  - [x] 3.1 Crear la DLQ SQS y el Lambda Layer PyMuPDF
    - Crear `AWS::SQS::Queue` con nombre `datamask-{Environment}-dlq`, retención 14 días, cifrado SSE-SQS
    - Crear `AWS::Lambda::LayerVersion` para PyMuPDF con runtime Python 3.12 y código desde S3
    - Tags estándar
    - _Requisitos: 6.1, 4.5, 9.1_

  - [x] 3.2 Crear los 4 roles IAM con mínimo privilegio
    - Crear `TriggerRole` con permisos específicos: S3 (GetObject originales/, PutObject procesamiento/, PutObject errores/, DeleteObject originales/), DynamoDB (GetItem, PutItem, UpdateItem, Query), Textract, Lambda:InvokeFunction (Detection), KMS, SQS:SendMessage (DLQ), CloudWatch Logs
    - Crear `DetectionRole` con permisos: S3 (GetObject procesamiento/, PutObject procesamiento/), DynamoDB, Comprehend:DetectPiiEntities, Bedrock:InvokeModel, Lambda:InvokeFunction (Redaction), KMS, SQS, CloudWatch Logs
    - Crear `RedactionRole` con permisos: S3 (GetObject originales/ y procesamiento/, PutObject ofuscados/), DynamoDB, KMS, SQS, CloudWatch Logs
    - Crear `ApiHandlerRole` con permisos: S3 (PutObject originales/, GetObject ofuscados/), DynamoDB, KMS, SecretsManager:GetSecretValue, CloudWatch Logs
    - NO usar wildcards (`*`) en acciones IAM
    - Tags estándar en cada rol
    - _Requisitos: 4.6, 10.2, 9.1, 9.3_

  - [x] 3.3 Crear las 4 funciones Lambda con configuración completa
    - Crear `TriggerFunction`: Python 3.12, 512 MB, timeout 300s, código desde S3, variables de entorno (DOCUMENTS_BUCKET, DOCUMENTS_TABLE, ENVIRONMENT, DLQ_URL, DETECTION_FUNCTION_NAME), DLQ configurada
    - Crear `DetectionFunction`: Python 3.12, 1024 MB, timeout 120s, código desde S3, variables de entorno (DOCUMENTS_BUCKET, DOCUMENTS_TABLE, ENVIRONMENT, DLQ_URL, BEDROCK_MODEL_ID, REDACTION_FUNCTION_NAME), DLQ configurada
    - Crear `RedactionFunction`: Python 3.12, 1024 MB, timeout 300s, código desde S3, layer PyMuPDF asociada, variables de entorno (DOCUMENTS_BUCKET, DOCUMENTS_TABLE, ENVIRONMENT, DLQ_URL), DLQ configurada
    - Crear `ApiHandlerFunction`: Python 3.12, 256 MB, timeout 30s, código desde S3, variables de entorno (DOCUMENTS_BUCKET, DOCUMENTS_TABLE, ENVIRONMENT)
    - Configurar `ReservedConcurrentExecutions` solo cuando `IsProd` (10 Trigger, 5 Detection, 5 Redaction)
    - Tags estándar
    - _Requisitos: 4.1, 4.2, 4.3, 4.4, 4.8, 6.2, 9.1, 9.3_

  - [x] 3.4 Crear Lambda Permission y configurar S3 Event Notification
    - Crear `AWS::Lambda::Permission` para permitir a S3 invocar la función Trigger
    - Configurar `NotificationConfiguration` en el bucket de documentos con filtro prefijo `originales/` y evento `s3:ObjectCreated:*`
    - Usar `DependsOn: TriggerLambdaPermission` para evitar dependencia circular
    - _Requisitos: 4.7_

- [x] 4. Checkpoint — Validar template parcial
  - Ejecutar `cfn-lint datamask-template.yaml` para verificar errores de sintaxis y mejores prácticas hasta este punto. Preguntar al usuario si hay dudas.

- [x] 5. Implementar API Gateway REST
  - [x] 5.1 Crear el API Gateway REST con recursos, métodos e integración Lambda Proxy
    - Crear `AWS::ApiGateway::RestApi` con nombre `datamask-{Environment}-api`, endpoint REGIONAL
    - Definir los recursos: `/auth/login`, `/auth/callback`, `/auth/logout`, `/upload/presign`, `/documents`, `/documents/{id}`, `/documents/{id}/download/{type}`, `/config`
    - Crear métodos HTTP para cada recurso: POST `/auth/login`, POST `/auth/callback`, POST `/auth/logout`, POST `/upload/presign`, POST `/documents`, GET `/documents`, GET `/documents/{id}`, GET `/documents/{id}/download/{type}`, GET `/config`, PUT `/config`
    - Configurar integración Lambda Proxy con ApiHandlerFunction en todos los métodos
    - Crear métodos OPTIONS con Mock integration para CORS en cada recurso
    - _Requisitos: 5.1, 5.2, 5.3_

  - [x] 5.2 Configurar CORS, GatewayResponses, Deployment y Stage
    - Configurar headers CORS: AllowOrigin `*`, AllowMethods (GET, POST, PUT, OPTIONS), AllowHeaders (Content-Type, Authorization, X-Amz-Date, X-Api-Key, X-Amz-Security-Token)
    - Crear `AWS::ApiGateway::GatewayResponse` para ACCESS_DENIED → 403 con body `{"message": "Forbidden"}`
    - Crear `AWS::ApiGateway::GatewayResponse` para UNAUTHORIZED → 403 con body `{"message": "Forbidden"}`
    - Crear `AWS::ApiGateway::Deployment` y `AWS::ApiGateway::Stage` con nombre del ambiente
    - Tags estándar
    - _Requisitos: 5.4, 5.5, 9.1_

- [x] 6. Implementar recursos de observabilidad (SNS, CloudWatch Alarms)
  - [x] 6.1 Crear topic SNS, suscripción y alarmas CloudWatch
    - Crear `AWS::SNS::Topic` con nombre `datamask-{Environment}-alerts`
    - Crear `AWS::SNS::Subscription` tipo email con el parámetro `AlertEmail`
    - Crear alarmas CloudWatch: errores Lambda > 5 en 5 min (una por cada función del pipeline: Trigger, Detection, Redaction, ApiHandler)
    - Crear alarma de latencia P99 del API Gateway > 3s en 5 min
    - Crear alarma DLQ mensajes visibles > 0
    - Asociar todas las alarmas al topic SNS
    - Tags estándar
    - _Requisitos: 6.3, 6.4, 6.5, 9.1_

- [x] 7. Implementar Outputs del stack
  - [x] 7.1 Agregar sección Outputs con exportaciones
    - Exportar ApiUrl (URL del API Gateway stage)
    - Exportar DocumentsBucketName
    - Exportar DocumentsTableName
    - Exportar AlertsTopicArn
    - Exportar KmsKeyArn
    - Incluir descripción en español para cada Output
    - Usar Export Name con patrón `datamask-{Environment}-{recurso}`
    - _Requisitos: 7.1, 7.2, 7.3_

- [x] 8. Checkpoint — Validación completa del template
  - Ejecutar `cfn-lint datamask-template.yaml` sin errores. Verificar que el template cumple con las mejores prácticas de seguridad (sin wildcards en IAM, cifrado en todos los recursos stateful, tags completos). Preguntar al usuario si hay dudas.

- [x] 9. Crear script de empaquetado de Lambdas
  - [x] 9.1 Crear `scripts/package-lambdas.sh` con lógica de empaquetado
    - Crear shell script con opciones `--output-dir` y `--skip-layer`
    - Implementar empaquetado de las 4 funciones Lambda: crear directorio temporal, copiar código Python, instalar dependencias con `pip install -t .`, crear ZIP excluyendo `__pycache__`, `.pytest_cache`, `tests/`
    - Implementar empaquetado de la capa PyMuPDF (estructura `python/` para Lambda Layer)
    - Verificar que genera los 5 archivos: trigger.zip, detection.zip, redaction.zip, api.zip, pymupdf-layer.zip
    - Incluir manejo de errores y mensajes informativos
    - Hacer el script ejecutable (`chmod +x`)
    - _Requisitos: 8.2_

- [x] 10. Crear guía de despliegue para el partner
  - [x] 10.1 Crear `docs/guia-despliegue-partner.md` con todas las secciones
    - Escribir en español completo
    - Sección 1: Introducción con diagrama de arquitectura en Mermaid
    - Sección 2: Prerrequisitos (cuenta AWS, AWS CLI, bucket S3, acceso Bedrock, Python 3.12 + Docker)
    - Sección 3: Preparación de artefactos (clonar repo, ejecutar script, subir ZIPs, verificar)
    - Sección 4: Despliegue del stack (comando AWS CLI completo con parámetros de ejemplo, alternativa consola)
    - Sección 5: Verificación post-despliegue (estado del stack, Outputs, test API)
    - Sección 6: Configuración adicional (Bedrock, frontend, confirmar SNS)
    - Sección 7: Troubleshooting (permisos insuficientes, bucket inexistente, modelo no habilitado, región no soportada)
    - Sección 8: Actualización y eliminación (update-stack, delete-stack, advertencia retención prod)
    - Sección 9: Costos estimados
    - _Requisitos: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7_

- [x] 11. Checkpoint final — Validación integral
  - Ejecutar `cfn-lint datamask-template.yaml` sin errores. Verificar que el script de empaquetado es ejecutable y tiene la estructura correcta. Verificar que la guía de despliegue tiene todas las secciones requeridas. Preguntar al usuario si hay dudas.

## Notes

- Esta feature produce Infrastructure as Code (CloudFormation YAML), un shell script y documentación — no código de aplicación con tests de propiedades
- La validación se realiza mediante `cfn-lint` y `aws cloudformation validate-template`
- Los checkpoints permiten validar incrementalmente que el template es correcto
- Cada tarea referencia requisitos específicos para trazabilidad
- El template debe evitar wildcards (`*`) en IAM y aplicar cifrado en todos los recursos stateful
- El orden de creación de recursos en el template evita dependencias circulares (KMS → S3 → Roles → Lambdas → Permission → S3 Notification → API Gateway → Alarms)

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["2.1", "2.2"] },
    { "id": 2, "tasks": ["3.1", "3.2"] },
    { "id": 3, "tasks": ["3.3"] },
    { "id": 4, "tasks": ["3.4"] },
    { "id": 5, "tasks": ["5.1"] },
    { "id": 6, "tasks": ["5.2", "6.1"] },
    { "id": 7, "tasks": ["7.1"] },
    { "id": 8, "tasks": ["9.1", "10.1"] }
  ]
}
```
