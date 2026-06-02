# Requirements Document

## Introduction

DataMask AWS es la reconstrucción cloud-native de la aplicación DataMask sobre servicios de AWS. La aplicación permite a usuarios autenticados subir documentos PDF a S3, procesarlos automáticamente mediante un pipeline serverless que detecta y ofusca datos personales sensibles (PII), y descargar los documentos redactados. Reemplaza los componentes locales (Ollama, spaCy, PyMuPDF) por servicios gestionados de AWS (Textract, Comprehend, Bedrock) para lograr mayor escalabilidad, rendimiento y disponibilidad.

## Glossary

- **Sistema**: El sistema DataMask AWS en su totalidad (frontend, backend, pipeline de procesamiento)
- **Pipeline_Redacción**: Conjunto de funciones Lambda orquestadas que procesan un PDF desde la extracción de texto hasta la generación del documento ofuscado
- **Usuario**: Persona autenticada mediante IAM Identity Center que interactúa con la aplicación web
- **Bucket_Documentos**: Bucket S3 con dos prefijos: `originales/` para PDFs subidos y `ofuscados/` para PDFs redactados
- **Motor_Detección**: Combinación de AWS Comprehend (detección determinista de PII) y AWS Bedrock (análisis contextual con LLM) para máxima cobertura
- **Entidad_PII**: Dato personal identificable detectado en el texto (nombre, DNI, email, teléfono, dirección, CUIT/CUIL, tarjeta de crédito, cuenta bancaria, pasaporte, fecha)
- **Documento_Ofuscado**: PDF resultante donde cada Entidad_PII ha sido reemplazada por una etiqueta descriptiva (e.g., `[NOMBRE]`, `[DNI]`)
- **Informe_Markdown**: Versión en texto plano del documento ofuscado con metadatos de las entidades detectadas
- **Frontend_Web**: Aplicación React hosteada en AWS Amplify Hosting (CDN global integrado, build automático) que permite la interacción del Usuario con el Sistema
- **API_Backend**: API REST desplegada en API Gateway + Lambda que gestiona la autenticación, uploads y consultas
- **IaC**: Infrastructure as Code implementada con AWS CDK (TypeScript) para desplegar todos los recursos

## Requirements

### Requirement 1: Autenticación con IAM Identity Center

**User Story:** As a usuario, I want to autenticarme con mis credenciales corporativas de IAM Identity Center, so that puedo acceder de forma segura a la aplicación sin gestionar credenciales adicionales.

#### Acceptance Criteria

1. WHEN el Usuario accede al Frontend_Web sin sesión activa, THE Sistema SHALL redirigir al flujo de login de IAM Identity Center preservando la URL de destino original para restaurarla tras la autenticación exitosa
2. WHEN el Usuario completa la autenticación exitosamente en IAM Identity Center, THE Sistema SHALL establecer una sesión autenticada con una duración máxima de 60 minutos y redirigir al dashboard principal (o a la URL de destino original si fue preservada)
3. WHEN el Usuario realiza una petición con una sesión expirada (más de 60 minutos desde el inicio de sesión o más de 15 minutos de inactividad), THE Sistema SHALL redirigir automáticamente al flujo de re-autenticación
4. THE Sistema SHALL almacenar tokens de sesión en el navegador utilizando cookies HttpOnly con flags Secure y SameSite=Lax
5. WHEN el Usuario selecciona "Cerrar sesión", THE Sistema SHALL invalidar la sesión en el servidor, eliminar la cookie de sesión del navegador y redirigir a la pantalla de login
6. IF la autenticación falla por credenciales inválidas, THEN THE Sistema SHALL mostrar un mensaje de error que indique el motivo general del fallo (credenciales incorrectas, cuenta bloqueada o sesión expirada) sin revelar identificadores internos, trazas de pila ni nombres de servicios
7. IF IAM Identity Center no está disponible o no responde dentro de 10 segundos, THEN THE Sistema SHALL mostrar un mensaje de error indicando que el servicio de autenticación no está disponible y ofrecer la opción de reintentar

### Requirement 2: Subida de documentos PDF a S3

**User Story:** As a usuario autenticado, I want to explorar mis archivos locales y subir documentos PDF al bucket S3, so that puedan ser procesados por el pipeline de redacción.

#### Acceptance Criteria

1. WHEN el Usuario accede a la pantalla de upload, THE Frontend_Web SHALL mostrar un componente de selección de archivos que acepte únicamente archivos con extensión `.pdf` y tipo MIME `application/pdf`
2. WHEN el Usuario selecciona uno o más archivos PDF, THE Frontend_Web SHALL validar que cada archivo tiene extensión `.pdf`, tipo MIME `application/pdf` y tamaño menor a 50 MB, permitiendo un máximo de 10 archivos por operación de subida
3. WHEN el Usuario confirma la subida, THE API_Backend SHALL generar URLs pre-firmadas de S3 con una expiración de 300 segundos para cada archivo y retornarlas al Frontend_Web
4. IF la generación de URLs pre-firmadas falla en el API_Backend, THEN THE API_Backend SHALL retornar un mensaje de error indicando que no se pudo preparar la subida y el Frontend_Web SHALL mostrar dicho error al Usuario sin iniciar la transferencia
5. WHEN el Frontend_Web recibe las URLs pre-firmadas, THE Frontend_Web SHALL subir cada archivo directamente al prefijo `originales/` del Bucket_Documentos usando la URL pre-firmada
6. WHILE la subida está en progreso, THE Frontend_Web SHALL mostrar una barra de progreso con el porcentaje de carga para cada archivo
7. IF la subida falla por error de red o timeout, THEN THE Frontend_Web SHALL reintentar automáticamente hasta 3 veces con backoff exponencial comenzando en 1 segundo (1s, 2s, 4s) y, si persiste el error tras los 3 reintentos, mostrar un mensaje de error indicando el archivo afectado y la causa del fallo
8. WHEN la subida se completa exitosamente, THE Frontend_Web SHALL actualizar la lista de archivos mostrando el estado "Subido — pendiente de procesamiento"

### Requirement 3: Pipeline de procesamiento activado por evento S3

**User Story:** As a sistema, I want to iniciar automáticamente el pipeline de redacción cuando un PDF se sube al prefijo `originales/`, so that el procesamiento sea event-driven sin intervención manual.

#### Acceptance Criteria

1. WHEN un archivo PDF se crea en el prefijo `originales/` del Bucket_Documentos, THE Pipeline_Redacción SHALL iniciar automáticamente el procesamiento del archivo dentro de los 5 segundos posteriores al evento
2. THE Pipeline_Redacción SHALL procesar un solo archivo por ejecución para garantizar aislamiento de errores, encolando los archivos adicionales recibidos durante una ejecución activa para procesamiento secuencial
3. IF el archivo subido no es un PDF válido (no contiene la firma de encabezado `%PDF` o no es parseable como documento PDF), THEN THE Pipeline_Redacción SHALL registrar el error en CloudWatch y mover el archivo al prefijo `errores/` con los siguientes metadatos: motivo del error, timestamp del intento de procesamiento y nombre original del archivo
4. WHEN el Pipeline_Redacción inicia, THE Sistema SHALL registrar un evento de inicio en DynamoDB con timestamp, nombre del archivo y estado "PROCESSING"
5. IF el Pipeline_Redacción falla en cualquier paso, THEN THE Sistema SHALL actualizar el estado a "FAILED" en DynamoDB indicando el paso donde ocurrió la falla y la descripción del error, y enviar el evento a una Dead Letter Queue
6. WHEN el Pipeline_Redacción completa exitosamente todos los pasos de procesamiento, THE Sistema SHALL actualizar el estado a "COMPLETED" en DynamoDB con el timestamp de finalización y mover el archivo resultante al prefijo de salida correspondiente
7. IF el archivo subido excede 500 MB de tamaño, THEN THE Pipeline_Redacción SHALL rechazar el procesamiento, registrar el evento en CloudWatch y mover el archivo al prefijo `errores/` con metadato indicando que excede el tamaño máximo permitido

### Requirement 4: Extracción de texto con AWS Textract

**User Story:** As a pipeline de procesamiento, I want to extraer el texto completo de cada página del PDF usando Textract, so that puedo analizar el contenido textual para detectar PII.

#### Acceptance Criteria

1. IF el PDF tiene 15 páginas o más, THEN THE Sistema SHALL invocar AWS Textract con la operación asíncrona `StartDocumentTextDetection` y esperar la finalización del job mediante notificación SNS o polling con intervalo de 5 segundos
2. WHEN Textract completa la extracción, THE Sistema SHALL reconstruir el texto preservando el orden de páginas y la estructura de bloques (líneas y palabras) con sus coordenadas de bounding box (Left, Top, Width, Height normalizadas entre 0 y 1)
3. THE Sistema SHALL almacenar el resultado de Textract en formato JSON en el prefijo `procesamiento/{archivo_id}/textract_output.json` del Bucket_Documentos
4. IF Textract no puede procesar el PDF (cifrado, corrupto, o formato no soportado), THEN THE Sistema SHALL marcar el archivo como "FAILED" con motivo "TEXTRACT_ERROR" y el mensaje de error original en la tabla DynamoDB
5. IF el PDF tiene menos de 15 páginas, THEN THE Sistema SHALL usar la operación síncrona `DetectDocumentText` para reducir latencia
6. IF el job asíncrono de Textract no completa dentro de 300 segundos, THEN THE Sistema SHALL cancelar la espera, marcar el archivo como "FAILED" con motivo "TEXTRACT_TIMEOUT" y registrar el evento en CloudWatch

### Requirement 5: Detección determinista de PII con AWS Comprehend

**User Story:** As a pipeline de procesamiento, I want to detectar entidades PII usando Comprehend, so that identifico datos sensibles con alta precisión mediante reglas deterministas.

#### Acceptance Criteria

1. WHEN el texto extraído está disponible, THE Motor_Detección SHALL dividir el texto en bloques de máximo 5000 caracteres UTF-8 respetando límites de oración (sin cortar palabras) e invocar la API `DetectPiiEntities` de AWS Comprehend con el parámetro LanguageCode `es` para cada bloque
2. THE Motor_Detección SHALL mapear los tipos de Comprehend a los tipos del Sistema: NAME→NOMBRE, EMAIL_ADDRESS→EMAIL, PHONE→TELEFONO, ADDRESS→DIRECCION, CREDIT_DEBIT_NUMBER→TARJETA_CREDITO, BANK_ACCOUNT_NUMBER→CUENTA_BANCARIA, PASSPORT_NUMBER→PASAPORTE, DATE_TIME→FECHA, y descartar entidades cuyo tipo de Comprehend no figure en este mapeo
3. THE Motor_Detección SHALL conservar la posición absoluta dentro del texto completo del documento (offset inicio/fin recalculado según la posición del bloque), tipo mapeado al Sistema, y score de confianza de cada Entidad_PII detectada por Comprehend
4. IF una Entidad_PII detectada por Comprehend tiene un score de confianza menor a 0.75, THEN THE Motor_Detección SHALL descartarla de la lista de resultados
5. WHEN Comprehend detecta una entidad tipo "OTHER" con score mayor a 0.85, THE Motor_Detección SHALL evaluar el texto de la entidad contra los patrones regex argentinos de DNI (7-8 dígitos con o sin puntos separadores) y CUIT_CUIL (prefijo 20|23|24|27|30|33|34 seguido de 8 dígitos y 1 dígito verificador), y si ningún patrón coincide, descartar la entidad
6. IF la invocación a la API de Comprehend falla por error de servicio o throttling, THEN THE Motor_Detección SHALL reintentar la solicitud hasta 3 veces con backoff exponencial (base 1 segundo) y, si persiste el error, registrar el fallo en CloudWatch y propagar el error al Pipeline_Redacción para marcar el archivo como FAILED

### Requirement 6: Análisis contextual con AWS Bedrock

**User Story:** As a pipeline de procesamiento, I want to usar un modelo LLM en Bedrock para análisis contextual del texto, so that detecto PII que los métodos deterministas no identifican (nombres compuestos, direcciones ambiguas, datos en contexto argentino).

#### Acceptance Criteria

1. WHEN la detección con Comprehend finaliza, THE Motor_Detección SHALL invocar AWS Bedrock con el modelo configurado en la variable de entorno BEDROCK_MODEL_ID (por defecto anthropic.claude-3-haiku-20240307-v1:0) enviando el texto extraído truncado a un máximo de 10000 caracteres con un prompt que incluya la lista de tipos PII a detectar e instrucciones para detección en español argentino
2. THE Motor_Detección SHALL enviar en el prompt la lista de tipos PII a detectar: NOMBRE, EMAIL, TELEFONO, CELULAR, DIRECCION, DNI, CUIT_CUIL, TARJETA_CREDITO, CUENTA_BANCARIA, PASAPORTE, FECHA, junto con instrucciones de respuesta en formato JSON array donde cada elemento contenga los campos "text", "type" y "start_offset"
3. WHEN el Motor_Detección recibe la respuesta de Bedrock, THE Motor_Detección SHALL parsear la respuesta como JSON array y descartar toda entidad que no incluya los campos obligatorios "text" (string no vacío), "type" (uno de los tipos PII válidos) y "start_offset" (entero >= 0 que coincida con la posición del texto en el documento original con una tolerancia de ±5 caracteres)
4. IF la invocación a Bedrock falla, excede el timeout de 30 segundos, o la respuesta no es un JSON array válido, THEN THE Motor_Detección SHALL continuar el procesamiento usando únicamente los resultados de Comprehend y registrar en CloudWatch una advertencia que incluya el identificador del documento, el tipo de error y el timestamp
5. THE Motor_Detección SHALL fusionar los resultados de Comprehend y Bedrock aplicando las siguientes reglas: cuando ambos detectan una entidad del mismo tipo cuyas posiciones se solapan en al menos 1 carácter, conservar la entidad de Comprehend; cuando las posiciones se solapan pero los tipos difieren, conservar ambas; cuando no hay solapamiento, incluir ambas entidades en el resultado final
6. IF Bedrock detecta una entidad cuya posición se solapa con una entidad de Comprehend de distinto tipo y la cobertura de texto de la entidad de Bedrock es mayor (más caracteres), THEN THE Motor_Detección SHALL conservar la entidad de Bedrock y descartar la de Comprehend para esa posición

### Requirement 7: Combinación y deduplicación de resultados de detección

**User Story:** As a pipeline de procesamiento, I want to combinar y deduplicar los resultados de Comprehend y Bedrock, so that genero una lista unificada de entidades PII sin duplicados para aplicar la redacción.

#### Acceptance Criteria

1. WHEN ambos motores (Comprehend y Bedrock) completan la detección, THE Motor_Detección SHALL combinar las listas de entidades de ambos motores en una lista unificada ordenada por posición de inicio ascendente dentro de cada página
2. IF uno de los motores (Comprehend o Bedrock) falla o no retorna resultados, THEN THE Motor_Detección SHALL combinar los resultados disponibles del motor exitoso junto con los resultados regex, y registrar en el JSON de salida cuál motor no contribuyó
3. THE Motor_Detección SHALL identificar entidades duplicadas cuando dos entidades del mismo tipo se solapan y la intersección de sus rangos de caracteres (start-end) dividida por la longitud de la entidad más corta supera el 80%
4. WHEN se detectan entidades duplicadas, THE Motor_Detección SHALL conservar la entidad con mayor span de texto (más caracteres cubiertos); IF ambas entidades tienen el mismo span, THEN THE Motor_Detección SHALL conservar la que posea mayor score de confianza
5. THE Motor_Detección SHALL detectar adicionalmente DNI (7-8 dígitos con puntos, formato X.XXX.XXX o XX.XXX.XXX), CUIT/CUIL (formato XX-XXXXXXXX-X donde el prefijo es 20, 23, 24, 27, 30, 33 o 34), y pasaportes argentinos (formato AAX###### donde X es una letra opcional y # son 6 dígitos) mediante patrones regex, asignando un score de confianza de 0.95 a cada detección regex
6. THE Motor_Detección SHALL generar un JSON de salida con la lista unificada de entidades donde cada entidad incluye: texto detectado, tipo de entidad, número de página (entero >= 1), posición inicio (entero >= 0), posición fin (entero > posición inicio), fuente (valor entre: "comprehend", "bedrock", "regex"), y score de confianza (decimal entre 0.0 y 1.0)
7. IF la lista unificada no contiene entidades después de la combinación y deduplicación, THEN THE Motor_Detección SHALL generar el JSON de salida con un arreglo vacío de entidades

### Requirement 8: Redacción del PDF y generación de salida

**User Story:** As a pipeline de procesamiento, I want to redactar el PDF original reemplazando cada PII por su etiqueta y generar un informe markdown, so that el usuario obtiene un documento seguro y un resumen de lo detectado.

#### Acceptance Criteria

1. WHEN la lista unificada de entidades está disponible, THE Pipeline_Redacción SHALL generar un nuevo PDF donde cada Entidad_PII es reemplazada por su etiqueta en formato `[TIPO]` (e.g., `[NOMBRE]`, `[DNI]`), preservando la posición original del texto y aplicando un color de fondo diferenciado por tipo de entidad
2. WHEN la lista unificada de entidades está disponible y contiene 0 entidades detectadas, THE Pipeline_Redacción SHALL omitir la generación del Documento_Ofuscado y del Informe_Markdown, y registrar el resultado con entities_found igual a 0
3. THE Pipeline_Redacción SHALL preservar la estructura del PDF original (fuentes, layout, imágenes y páginas) reemplazando únicamente el texto de las entidades detectadas, utilizando guardado incremental para no alterar los elementos no redactados
4. THE Pipeline_Redacción SHALL almacenar el Documento_Ofuscado en el prefijo `ofuscados/` del Bucket_Documentos con el nombre `{nombre_original}_ofuscado.pdf`
5. THE Pipeline_Redacción SHALL generar un Informe_Markdown que contenga: título con el nombre del archivo original, nombre del archivo original como metadato, separador visual, y el texto completo ofuscado organizado por número de página
6. THE Pipeline_Redacción SHALL almacenar el Informe_Markdown en el prefijo `ofuscados/` con el nombre `{nombre_original}_informe.md`
7. IF el PDF de entrada está protegido con contraseña o es un archivo corrupto, THEN THE Pipeline_Redacción SHALL retornar un resultado con success igual a false, un código de error específico (PASSWORD_PROTECTED o CORRUPTED), y no generar archivos de salida
8. IF ocurre un error inesperado durante la redacción del PDF, THEN THE Pipeline_Redacción SHALL retornar un resultado con success igual a false, el mensaje de error descriptivo, y el tiempo de procesamiento transcurrido en milisegundos
9. WHEN la redacción se completa exitosamente, THE Sistema SHALL actualizar el estado del archivo en DynamoDB a "COMPLETED" con las estadísticas de entidades detectadas incluyendo cantidad total y desglose por tipo (diccionario tipo:cantidad)

### Requirement 9: Consulta de estado y descarga de resultados

**User Story:** As a usuario autenticado, I want to ver el estado de procesamiento de mis documentos y descargar los resultados, so that puedo obtener los PDFs ofuscados y los informes de detección.

#### Acceptance Criteria

1. WHEN el Usuario accede a la página de procesamiento, THE Frontend_Web SHALL mostrar la lista de documentos subidos ordenados por fecha de subida descendente, indicando para cada uno su estado actual (UPLOADED, PROCESSING, COMPLETED, FAILED) y su nombre de archivo
2. WHEN un documento tiene estado COMPLETED, THE Frontend_Web SHALL mostrar botones habilitados para descargar el PDF ofuscado y el informe markdown asociado
3. WHEN el Usuario solicita descargar un archivo, THE API_Backend SHALL generar una URL pre-firmada de S3 con expiración de 5 minutos y redirigir la descarga
4. IF la URL pre-firmada ha expirado o el archivo no existe en S3, THEN THE API_Backend SHALL responder con un error indicando que el recurso no está disponible y el Frontend_Web SHALL mostrar un mensaje de error al Usuario
5. WHEN un documento tiene estado COMPLETED, THE Frontend_Web SHALL mostrar para cada documento: cantidad total de entidades detectadas (valor numérico entero), desglose por tipo de entidad con cantidad por cada tipo, y tiempo de procesamiento expresado en segundos
6. WHEN un documento tiene estado FAILED, THE Frontend_Web SHALL mostrar el motivo del error limitado a un máximo de 200 caracteres, indicando la categoría del fallo (error de lectura, error de NER, error de escritura, o error de servicio)
7. WHILE existen documentos con estado PROCESSING visibles en la página, THE Frontend_Web SHALL actualizar automáticamente el estado de los documentos mediante polling cada 10 segundos, cesando las consultas cuando ningún documento se encuentre en estado PROCESSING
8. IF el Usuario no tiene documentos subidos, THEN THE Frontend_Web SHALL mostrar un estado vacío con un mensaje indicando que no hay documentos y un enlace o botón para navegar a la funcionalidad de subida de documentos

### Requirement 10: Infraestructura como Código con AWS CDK

**User Story:** As a desarrollador, I want to definir toda la infraestructura con AWS CDK en TypeScript, so that el despliegue sea reproducible, versionable y auditado.

#### Acceptance Criteria

1. THE IaC SHALL definir todos los recursos AWS necesarios en un proyecto CDK con TypeScript incluyendo: Amplify App (frontend), S3 bucket, Lambda functions, API Gateway, DynamoDB table, IAM roles, SQS Dead Letter Queue, y SNS topic para notificaciones de alarmas
2. THE IaC SHALL aplicar el principio de mínimo privilegio en cada rol IAM, prohibiendo el uso de wildcards (*) en acciones y recursos, y otorgando únicamente permisos a los servicios y recursos específicos que cada función Lambda consume
3. THE IaC SHALL configurar cifrado at-rest con SSE-KMS (clave gestionada por el cliente) para el Bucket_Documentos y cifrado at-rest con clave KMS gestionada por AWS para la tabla DynamoDB
4. THE IaC SHALL configurar tags obligatorios en todos los recursos: Project=DataMask, Environment={env}, Owner=EduTheCoder, CostCenter={cost_center}, y usar CDK Aspects para validar la presencia de estos tags durante la síntesis
5. THE IaC SHALL separar los stacks por dominio: NetworkStack, StorageStack, ComputeStack, ApiStack, exportando los valores necesarios entre stacks mediante CfnOutput para permitir despliegues independientes
6. THE IaC SHALL configurar CloudWatch alarms con notificación a un SNS topic para: errores de Lambda > 5 en 5 minutos, latencia P99 de API Gateway > 3 segundos en 5 minutos, y errores de invocación a Textract/Comprehend/Bedrock > 3 en 5 minutos

### Requirement 11: Seguridad y cumplimiento

**User Story:** As a administrador del sistema, I want to que toda la comunicación y almacenamiento esté cifrado y los accesos sean auditados, so that cumplo con los estándares de protección de datos sensibles.

#### Acceptance Criteria

1. THE Sistema SHALL cifrar todas las comunicaciones en tránsito usando TLS 1.2 o superior, configurando políticas de seguridad mínima en CloudFront (TLSv1.2_2021) y API Gateway que rechacen conexiones con versiones inferiores de TLS
2. THE Sistema SHALL bloquear el acceso público al Bucket_Documentos mediante Block Public Access habilitado en las cuatro opciones, permitiendo acceso únicamente mediante URLs pre-firmadas con expiración máxima de 15 minutos o roles IAM autorizados con permisos específicos por prefijo (originales/, ofuscados/)
3. THE Sistema SHALL registrar todos los accesos (lectura, escritura y eliminación) al Bucket_Documentos mediante S3 Access Logging habilitado, almacenando los logs en un bucket dedicado de logs con una política de retención de 90 días y cifrado SSE-S3
4. THE Sistema SHALL almacenar los secrets (client IDs, endpoints de Identity Center) en AWS Secrets Manager con rotación automática configurada cada 90 días, nunca en variables de entorno hardcodeadas ni en código fuente
5. THE Sistema SHALL configurar una política de retención de 90 días para los logs de CloudWatch, tras los cuales los logs se eliminan automáticamente
6. IF un Usuario intenta acceder a un recurso sin autorización, THEN THE API_Backend SHALL retornar HTTP 403 con un mensaje genérico sin revelar la existencia del recurso y registrar el intento fallido en CloudWatch incluyendo timestamp, identidad del solicitante y recurso solicitado
7. IF una URL pre-firmada del Bucket_Documentos ha expirado o es inválida, THEN THE Sistema SHALL rechazar la solicitud con HTTP 403 sin revelar si el objeto existe en el bucket

### Requirement 12: Configuración de tipos de PII

**User Story:** As a usuario autenticado, I want to configurar qué tipos de datos sensibles detectar y ofuscar, so that puedo adaptar el procesamiento a mis necesidades específicas.

#### Acceptance Criteria

1. THE Frontend_Web SHALL mostrar un panel de configuración con toggles para activar/desactivar cada tipo de Entidad_PII: NOMBRE, EMAIL, TELEFONO, CELULAR, DIRECCION, DNI, CUIT_CUIL, TARJETA_CREDITO, CUENTA_BANCARIA, PASAPORTE, FECHA
2. WHEN el Usuario modifica la configuración y confirma el guardado, THE API_Backend SHALL persistir los cambios en la tabla DynamoDB asociados al Usuario autenticado y mostrar una notificación de éxito en un máximo de 3 segundos desde la confirmación
3. IF el Usuario intenta guardar la configuración con todos los tipos de PII desactivados, THEN THE Sistema SHALL rechazar la operación, mantener la configuración previa sin modificaciones y mostrar un mensaje de error indicando que al menos un tipo debe permanecer activo
4. WHEN el Pipeline_Redacción procesa un archivo, THE Motor_Detección SHALL respetar la configuración del Usuario, detectando y ofuscando únicamente los tipos activos
5. THE Sistema SHALL proveer una configuración por defecto con los 11 tipos predefinidos activos para usuarios nuevos que no posean una configuración previamente almacenada
6. IF la persistencia de la configuración falla por error de escritura o indisponibilidad del almacenamiento, THEN THE API_Backend SHALL retornar un error al Frontend_Web indicando que la configuración no pudo ser guardada y preservar la configuración anterior sin alteraciones

### Requirement 13: Despliegue en cuenta AWS específica

**User Story:** As a desarrollador, I want to desplegar la aplicación en la cuenta AWS 339712829454 usando el perfil masterGenAI, so that la infraestructura se crea en el entorno correcto.

#### Acceptance Criteria

1. THE IaC SHALL definir la cuenta AWS 339712829454 y la región us-east-1 como valores de entorno por defecto en el archivo de contexto CDK (`cdk.json`), permitiendo su sobreescritura mediante parámetros de contexto en línea de comandos
2. THE IaC SHALL usar el perfil AWS CLI `masterGenAI` como perfil de despliegue, configurado en `cdk.json` bajo la clave `profile`
3. THE IaC SHALL incluir un script de bootstrap que, antes de ejecutar `cdk deploy`, verifique que el perfil `masterGenAI` existe en la configuración de AWS CLI y que la cuenta asociada al perfil coincide con 339712829454; IF la validación falla, THEN el script SHALL terminar con código de salida distinto de cero y mostrar un mensaje de error indicando cuál verificación falló (perfil inexistente o cuenta incorrecta)
4. THE IaC SHALL incluir instrucciones en el README que cubran como mínimo: prerrequisitos (Node.js, AWS CLI, CDK CLI), configuración del perfil `masterGenAI`, ejecución del script de bootstrap, comandos para sintetizar (`cdk synth`) y desplegar (`cdk deploy`) por ambiente
5. THE IaC SHALL soportar los ambientes `dev` y `prod` mediante el parámetro de contexto CDK `-c environment=dev|prod`, diferenciando al menos el prefijo de nombres de recursos según el patrón `datamask-{environment}-{recurso}` y la política de eliminación (DESTROY en dev, RETAIN en prod)
6. IF el parámetro de contexto `environment` no es proporcionado o contiene un valor distinto de `dev` o `prod`, THEN THE IaC SHALL fallar la síntesis con un mensaje de error indicando los valores válidos de ambiente
