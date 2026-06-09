# Documento de Requisitos — Template CloudFormation para Despliegue por Partner

## Introducción

Este documento define los requisitos para crear un template de AWS CloudFormation (YAML) autónomo que permita a un partner desplegar la aplicación DataMask AWS completa en la cuenta AWS de un cliente, sin requerir instalación de CDK, Node.js ni herramientas de desarrollo. El template incluirá parámetros configurables para adaptarse a diferentes cuentas AWS y se acompañará de documentación en español para facilitar el despliegue.

## Glosario

- **Template_CloudFormation**: Archivo YAML que define todos los recursos AWS necesarios para desplegar DataMask AWS de forma declarativa usando el servicio AWS CloudFormation
- **Partner**: Persona técnica externa que ejecuta el despliegue en la cuenta AWS del cliente usando la consola de CloudFormation o AWS CLI
- **Cuenta_Cliente**: Cuenta AWS donde se desplegará la aplicación, cuyo ID y región pueden diferir del entorno de desarrollo original
- **Stack_CloudFormation**: Conjunto de recursos AWS creados y gestionados como una unidad a partir del Template_CloudFormation
- **Parámetro_Stack**: Valor configurable que el Partner proporciona al momento de crear el Stack_CloudFormation para adaptar el despliegue a la Cuenta_Cliente
- **Guía_Despliegue**: Documento en español con instrucciones paso a paso para que el Partner despliegue la aplicación
- **Pipeline_Redacción**: Conjunto de funciones Lambda que procesan PDFs: Trigger (evento S3), Detección (Comprehend + Bedrock + Regex), Redacción (PyMuPDF)
- **Bucket_Documentos**: Bucket S3 que almacena los PDFs originales, procesados y ofuscados
- **Tabla_Estado**: Tabla DynamoDB que registra el estado de procesamiento de cada documento

## Requisitos

### Requisito 1: Template CloudFormation autónomo

**User Story:** As a partner, I want to tener un template CloudFormation YAML completo que despliegue toda la infraestructura de DataMask AWS, so that puedo desplegar la aplicación en la cuenta del cliente sin instalar CDK ni herramientas de desarrollo.

#### Criterios de Aceptación

1. THE Template_CloudFormation SHALL definir todos los recursos de infraestructura necesarios en un único archivo YAML compatible con AWS CloudFormation, incluyendo: bucket S3 con cifrado KMS, tabla DynamoDB, 4 funciones Lambda (Trigger, Detección, Redacción, API), API Gateway REST, cola SQS Dead Letter Queue, topic SNS para alertas, y alarmas CloudWatch
2. THE Template_CloudFormation SHALL ser desplegable directamente desde la consola de AWS CloudFormation o mediante el comando `aws cloudformation create-stack` sin requerir instalación previa de CDK CLI, Node.js ni compilación de TypeScript
3. THE Template_CloudFormation SHALL incluir una sección `Description` con el nombre de la aplicación, versión y propósito en español
4. THE Template_CloudFormation SHALL utilizar la versión de formato `2010-09-09` de AWS CloudFormation
5. IF el Template_CloudFormation se despliega en una región que no soporta alguno de los servicios requeridos (Textract, Comprehend, Bedrock), THEN THE Template_CloudFormation SHALL fallar la validación con un mensaje indicando qué servicio no está disponible en la región seleccionada

### Requisito 2: Parámetros configurables para la cuenta del cliente

**User Story:** As a partner, I want to proporcionar valores específicos de la cuenta del cliente como parámetros al crear el stack, so that la aplicación se adapta al entorno AWS destino sin modificar el template.

#### Criterios de Aceptación

1. THE Template_CloudFormation SHALL definir un parámetro `Environment` de tipo String con valores permitidos `dev` y `prod`, valor por defecto `prod`, y descripción en español que explique el uso de cada ambiente
2. THE Template_CloudFormation SHALL definir un parámetro `BucketName` de tipo String con una expresión regular de validación que permita únicamente nombres válidos de bucket S3 (3-63 caracteres, minúsculas, números y guiones), y descripción en español que indique el propósito del bucket
3. THE Template_CloudFormation SHALL definir un parámetro `AlertEmail` de tipo String con validación de formato de email, y descripción en español indicando que se usará para recibir notificaciones de alarmas del sistema
4. THE Template_CloudFormation SHALL definir un parámetro `BedrockModelId` de tipo String con valor por defecto `anthropic.claude-3-haiku-20240307-v1:0` y descripción en español que explique su uso para el análisis contextual de PII
5. THE Template_CloudFormation SHALL definir un parámetro `LambdaCodeS3Bucket` de tipo String con descripción en español indicando el bucket S3 donde el Partner ha subido los artefactos ZIP de las funciones Lambda
6. THE Template_CloudFormation SHALL definir un parámetro `LambdaCodeS3Prefix` de tipo String con valor por defecto `datamask/lambdas/` y descripción en español indicando el prefijo dentro del bucket de artefactos donde se encuentran los ZIPs de las funciones Lambda
7. THE Template_CloudFormation SHALL definir un parámetro `ProjectTag` de tipo String con valor por defecto `DataMask` para el tag de proyecto aplicado a todos los recursos

### Requisito 3: Recursos de almacenamiento con cifrado

**User Story:** As a partner, I want to que el template cree los recursos de almacenamiento con cifrado y políticas de seguridad apropiadas, so that los datos sensibles del cliente están protegidos desde el despliegue.

#### Criterios de Aceptación

1. THE Template_CloudFormation SHALL crear un bucket S3 con el nombre proporcionado en el parámetro `BucketName`, cifrado SSE-KMS usando una clave KMS creada en el mismo template, y BlockPublicAccess habilitado en las cuatro opciones (BlockPublicAcls, IgnorePublicAcls, BlockPublicPolicy, RestrictPublicBuckets)
2. THE Template_CloudFormation SHALL crear una clave KMS con alias `datamask-{Environment}-s3-key`, rotación automática habilitada, y política que permita administración por el root de la cuenta
3. THE Template_CloudFormation SHALL crear una tabla DynamoDB con nombre `datamask-{Environment}-documents`, clave de partición `PK` (String), clave de ordenación `SK` (String), un índice GSI con clave de partición `GSI1PK` y clave de ordenación `GSI1SK`, y modo de capacidad PAY_PER_REQUEST
4. THE Template_CloudFormation SHALL configurar la tabla DynamoDB con cifrado at-rest usando la clave KMS gestionada por AWS (AWS_OWNED)
5. THE Template_CloudFormation SHALL crear un bucket de logs con nombre `{BucketName}-access-logs`, cifrado SSE-S3, y política de ciclo de vida que elimine objetos después de 90 días
6. THE Template_CloudFormation SHALL configurar Server Access Logging en el Bucket_Documentos apuntando al bucket de logs creado

### Requisito 4: Funciones Lambda y capa PyMuPDF

**User Story:** As a partner, I want to que el template despliegue las 4 funciones Lambda con sus roles IAM de mínimo privilegio y la capa PyMuPDF, so that el pipeline de procesamiento funcione correctamente.

#### Criterios de Aceptación

1. THE Template_CloudFormation SHALL crear una función Lambda `datamask-{Environment}-trigger` con runtime Python 3.12, timeout de 300 segundos, memoria de 512 MB, código referenciado desde `s3://{LambdaCodeS3Bucket}/{LambdaCodeS3Prefix}trigger.zip`, y variables de entorno que apunten al bucket de documentos y tabla DynamoDB
2. THE Template_CloudFormation SHALL crear una función Lambda `datamask-{Environment}-detection` con runtime Python 3.12, timeout de 120 segundos, memoria de 1024 MB, código referenciado desde `s3://{LambdaCodeS3Bucket}/{LambdaCodeS3Prefix}detection.zip`, y variables de entorno incluyendo BEDROCK_MODEL_ID con el valor del parámetro correspondiente
3. THE Template_CloudFormation SHALL crear una función Lambda `datamask-{Environment}-redaction` con runtime Python 3.12, timeout de 300 segundos, memoria de 1024 MB, código referenciado desde `s3://{LambdaCodeS3Bucket}/{LambdaCodeS3Prefix}redaction.zip`, con la capa PyMuPDF asociada
4. THE Template_CloudFormation SHALL crear una función Lambda `datamask-{Environment}-api-handler` con runtime Python 3.12, timeout de 30 segundos, memoria de 256 MB, código referenciado desde `s3://{LambdaCodeS3Bucket}/{LambdaCodeS3Prefix}api.zip`
5. THE Template_CloudFormation SHALL crear una Lambda Layer `datamask-{Environment}-pymupdf` con el runtime Python 3.12 y código referenciado desde `s3://{LambdaCodeS3Bucket}/{LambdaCodeS3Prefix}pymupdf-layer.zip`
6. THE Template_CloudFormation SHALL crear un rol IAM por cada función Lambda aplicando el principio de mínimo privilegio, otorgando únicamente permisos a los recursos y acciones específicas que cada función necesita (S3, DynamoDB, Comprehend, Bedrock, Textract, KMS, CloudWatch Logs)
7. THE Template_CloudFormation SHALL configurar una notificación de evento S3 en el Bucket_Documentos que invoque la función Lambda Trigger cuando se cree un objeto con prefijo `originales/`
8. IF el ambiente es `prod`, THEN THE Template_CloudFormation SHALL configurar reservedConcurrentExecutions en las funciones Lambda para evitar throttling inesperado

### Requisito 5: API Gateway REST

**User Story:** As a partner, I want to que el template cree un API Gateway REST conectado a la Lambda API Handler, so that el frontend pueda comunicarse con el backend.

#### Criterios de Aceptación

1. THE Template_CloudFormation SHALL crear un API Gateway REST con nombre `datamask-{Environment}-api`, tipo de endpoint REGIONAL, y stage de despliegue con el nombre del ambiente
2. THE Template_CloudFormation SHALL definir los recursos y métodos del API: POST `/auth/login`, POST `/auth/callback`, POST `/auth/logout`, POST `/upload/presign`, POST `/documents`, GET `/documents`, GET `/documents/{id}`, GET `/documents/{id}/download/{type}`, GET `/config`, PUT `/config`
3. THE Template_CloudFormation SHALL configurar integración Lambda Proxy entre cada método del API Gateway y la función Lambda API Handler
4. THE Template_CloudFormation SHALL configurar CORS con AllowOrigin `*`, métodos permitidos (GET, POST, PUT, OPTIONS), y headers permitidos (Content-Type, Authorization, X-Amz-Date, X-Api-Key, X-Amz-Security-Token)
5. THE Template_CloudFormation SHALL configurar respuestas Gateway por defecto para ACCESS_DENIED y UNAUTHORIZED retornando HTTP 403 con body genérico `{"message": "Forbidden"}` sin revelar la existencia del recurso

### Requisito 6: Cola Dead Letter Queue y topic SNS

**User Story:** As a partner, I want to que el template cree la infraestructura de manejo de errores y alertas, so that los fallos del pipeline queden registrados y el equipo sea notificado.

#### Criterios de Aceptación

1. THE Template_CloudFormation SHALL crear una cola SQS Dead Letter Queue con nombre `datamask-{Environment}-dlq`, retención de mensajes de 14 días, y cifrado SSE-SQS
2. THE Template_CloudFormation SHALL configurar cada función Lambda del pipeline (Trigger, Detección, Redacción) con la DLQ como destino para invocaciones fallidas con maxReceiveCount de 3
3. THE Template_CloudFormation SHALL crear un topic SNS con nombre `datamask-{Environment}-alerts` y suscribir el email proporcionado en el parámetro `AlertEmail`
4. THE Template_CloudFormation SHALL crear alarmas CloudWatch para: errores de Lambda mayores a 5 en 5 minutos (una alarma por función), latencia P99 del API Gateway mayor a 3 segundos en 5 minutos, y mensajes visibles en la DLQ mayor a 0
5. THE Template_CloudFormation SHALL asociar todas las alarmas CloudWatch al topic SNS de alertas para enviar notificaciones

### Requisito 7: Outputs del stack

**User Story:** As a partner, I want to que el template exporte los valores clave del despliegue como Outputs, so that puedo configurar el frontend y verificar el despliegue exitoso.

#### Criterios de Aceptación

1. THE Template_CloudFormation SHALL exportar como Outputs: URL del API Gateway, nombre del bucket de documentos, nombre de la tabla DynamoDB, ARN del topic SNS de alertas, y ARN de la clave KMS
2. THE Template_CloudFormation SHALL incluir una descripción en español para cada Output que explique su propósito y cómo utilizarlo
3. THE Template_CloudFormation SHALL usar nombres de exportación con el patrón `datamask-{Environment}-{recurso}` para evitar conflictos entre stacks del mismo u otros proyectos

### Requisito 8: Documentación de despliegue para el partner

**User Story:** As a partner, I want to tener una guía en español con instrucciones paso a paso para desplegar la aplicación, so that puedo ejecutar el despliegue sin conocimiento previo de la arquitectura interna.

#### Criterios de Aceptación

1. THE Guía_Despliegue SHALL incluir una sección de prerrequisitos con: cuenta AWS activa con permisos de administrador, AWS CLI instalado y configurado, bucket S3 creado para los artefactos Lambda, y acceso al modelo Bedrock solicitado
2. THE Guía_Despliegue SHALL incluir instrucciones para preparar los artefactos Lambda: cómo empaquetar cada función en un ZIP y subir los 5 archivos (trigger.zip, detection.zip, redaction.zip, api.zip, pymupdf-layer.zip) al bucket de artefactos
3. THE Guía_Despliegue SHALL incluir el comando completo de AWS CLI para crear el stack con todos los parámetros, con valores de ejemplo para cada parámetro
4. THE Guía_Despliegue SHALL incluir instrucciones para verificar el despliegue exitoso consultando el estado del stack y los Outputs generados
5. THE Guía_Despliegue SHALL incluir una sección de troubleshooting con los errores más comunes (permisos insuficientes, bucket inexistente, modelo Bedrock no habilitado, región no soportada) y sus soluciones
6. THE Guía_Despliegue SHALL incluir instrucciones para eliminar el stack de forma limpia cuando ya no se necesite, advirtiendo sobre la retención de datos en producción
7. THE Guía_Despliegue SHALL estar escrita completamente en español e incluir un diagrama de arquitectura simplificado en formato texto (ASCII o Mermaid)

### Requisito 9: Tags y convenciones de nomenclatura

**User Story:** As a administrador de la cuenta del cliente, I want to que todos los recursos creados tengan tags estándar y nombres consistentes, so that puedo identificar y gestionar los costos y recursos de DataMask.

#### Criterios de Aceptación

1. THE Template_CloudFormation SHALL aplicar los siguientes tags a todos los recursos que soporten tagging: `Project` (valor del parámetro ProjectTag), `Environment` (valor del parámetro Environment), `ManagedBy` con valor `CloudFormation`, y `Application` con valor `DataMask`
2. THE Template_CloudFormation SHALL usar el patrón de nomenclatura `datamask-{Environment}-{recurso}` para todos los recursos que requieran un nombre explícito
3. THE Template_CloudFormation SHALL incluir un tag `Stack` con el valor del nombre del stack de CloudFormation (usando `AWS::StackName`) en todos los recursos

### Requisito 10: Seguridad del template

**User Story:** As a administrador de seguridad, I want to que el template aplique las mejores prácticas de seguridad de AWS, so that la infraestructura desplegada cumple los estándares de protección de datos.

#### Criterios de Aceptación

1. THE Template_CloudFormation SHALL configurar todas las funciones Lambda sin acceso a Internet VPC por defecto, ejecutándose en el contexto del servicio Lambda con acceso a servicios AWS mediante endpoints públicos
2. THE Template_CloudFormation SHALL prohibir el uso de wildcards (`*`) en acciones IAM en todos los roles creados, otorgando únicamente acciones específicas y documentadas por recurso
3. THE Template_CloudFormation SHALL configurar el bucket S3 con versionado habilitado para permitir recuperación ante eliminación accidental de documentos
4. THE Template_CloudFormation SHALL configurar una Lifecycle Rule en el bucket de documentos que mueva objetos del prefijo `procesamiento/` a eliminación después de 30 días para controlar costos de almacenamiento temporal
5. IF el parámetro Environment es `prod`, THEN THE Template_CloudFormation SHALL configurar DeletionPolicy como `Retain` en el bucket S3, la tabla DynamoDB y la clave KMS para prevenir eliminación accidental de datos
6. IF el parámetro Environment es `dev`, THEN THE Template_CloudFormation SHALL configurar DeletionPolicy como `Delete` en todos los recursos para facilitar la limpieza del entorno de desarrollo
