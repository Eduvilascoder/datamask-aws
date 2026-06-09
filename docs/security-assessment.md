# Evaluación de seguridad — DataMask AWS

Este documento es un **security assessment** del sistema DataMask AWS. Revisa la
arquitectura contra el pilar de Seguridad del AWS Well-Architected Framework y
las normas internas del proyecto (mínimo privilegio, cifrado, manejo de
secrets). Clasifica los hallazgos por severidad y propone remediaciones.

> Alcance: infraestructura (`datamask-template.yaml`), código de las Lambdas,
> frontend y dependencias. Fecha de la evaluación: ver historial de Git.
> Este assessment es de revisión estática; **no reemplaza** un pentest ni una
> auditoría formal con herramientas de runtime.

---

## Resumen ejecutivo

| Dimensión | Estado |
|-----------|--------|
| Gestión de identidades y accesos (IAM) | ✅ Sólido — mínimo privilegio por función |
| Autenticación / autorización | ✅ Sólido — Cognito + IdC (SAML), JWT en API |
| Protección de datos (at-rest / in-transit) | ✅ Sólido — KMS + TLS forzado |
| Detección de amenazas / logging | 🟡 Mejorable — falta CloudTrail/GuardDuty explícito |
| Gestión de dependencias | 🟡 Mejorable — rangos abiertos en requirements |
| Gestión de secrets | ✅ Sólido — Secrets Manager, sin hardcodeo |

**Conclusión:** la postura de seguridad es **buena** para una solución
serverless. No se detectaron vulnerabilidades críticas. Los hallazgos son de
severidad **media/baja** y de endurecimiento (hardening).

---

## 1. Identidad y control de accesos (IAM)

### Fortalezas

- **Mínimo privilegio por función.** Cada Lambda tiene su propio rol con
  permisos acotados al recurso y prefijo S3 que necesita:
  - *trigger*: lee `originales/*`, escribe `procesamiento/*` y `errores/*`,
    borra `originales/*`, invoca solo la Lambda *detection*.
  - *detection*: lee/escribe solo `procesamiento/*`, invoca solo *redaction*.
  - *redaction*: lee `originales/*` y `procesamiento/*`, escribe `ofuscados/*`.
  - *api*: escribe `originales/*`, lee según endpoint; sin permisos de pipeline.
- **DynamoDB** acotado a la tabla y su GSI1 (no `*`).
- **Permisos S3 segmentados por prefijo** — una Lambda no puede tocar las
  carpetas de otra etapa.

### Hallazgos

| ID | Severidad | Hallazgo | Remediación |
|----|-----------|----------|-------------|
| IAM-1 | Baja (aceptado) | `textract:*` usa `Resource: "*"` | Textract **no soporta** permisos a nivel de recurso; es obligatorio. Está documentado en el template. Sin acción. |
| IAM-2 | Baja (aceptado) | Bedrock usa `foundation-model/*` e `inference-profile/*` | Necesario para la selección **multi-proveedor** de modelos. Justificado y documentado. Opcional: restringir a una allowlist de modelos si el catálogo es fijo. |
| IAM-3 | Baja | KMS key policy concede `kms:*` al `root` de la cuenta | Patrón estándar de administración de claves (root delega vía IAM). Aceptable; revisar que no haya políticas IAM laxas que hereden este acceso. |

> Las dos excepciones de wildcard (`textract:*` y Bedrock) están **alineadas con
> la norma del proyecto**: wildcards permitidos solo con justificación
> documentada.

---

## 2. Autenticación y autorización

### Fortalezas

- **Amazon Cognito** federado con **AWS IAM Identity Center** vía **SAML 2.0**
  (federable con Active Directory u otro IdP corporativo).
- Flujo **OAuth2 Authorization Code + PKCE** con cliente público (sin client
  secret en el navegador).
- API Gateway protegido con **Cognito User Pools Authorizer** que valida la
  firma y expiración del **id token (JWT)** antes de invocar cualquier Lambda.
- **Aislamiento por usuario:** el backend deriva el `userId` del claim `email` y
  particiona DynamoDB y S3 (`originales/{userId}/`). Un usuario solo accede a sus
  propios documentos; los endpoints de borrado verifican que la key contenga el
  `userId`.
- Tokens de corta duración (id/access ~60 min) + refresh.

### Hallazgos

| ID | Severidad | Hallazgo | Remediación |
|----|-----------|----------|-------------|
| AUTH-1 | Media | Tokens persistidos en `localStorage` | Práctica común en SPAs, pero expuesta a XSS. Mitigado por la baja superficie de scripts y CSP. Considerar `httpOnly` cookies si el riesgo de XSS aumenta. |
| AUTH-2 | Baja | Derivación de identidad por `email` (subaddressing normalizado) | Correcto, pero depende de que el IdP garantice unicidad de email. Validar en el IdP corporativo. |

---

## 3. Protección de datos

### Fortalezas

- **Cifrado at-rest:**
  - Bucket de documentos: **SSE-KMS** con clave gestionada (CMK) y
    `BucketKeyEnabled` (reduce costos de KMS).
  - DynamoDB: cifrado por defecto.
  - SQS DLQ: SSE gestionado.
- **Cifrado in-transit:** la **bucket policy niega** cualquier request con
  `aws:SecureTransport=false` (fuerza HTTPS/TLS). API Gateway sirve sobre TLS.
- **Block Public Access** activo en todos los buckets (ACLs y políticas
  públicas bloqueadas).
- **No se registran datos PII** en CloudWatch (los logs usan previews acotados,
  nunca el contenido del documento).
- **Errores genéricos:** el API responde **HTTP 403 genérico** sin revelar la
  existencia de recursos.

### Hallazgos

| ID | Severidad | Hallazgo | Remediación |
|----|-----------|----------|-------------|
| DATA-1 | Baja | El bucket de **logs** usa SSE-S3 (AES256), no KMS | Aceptable para logs de acceso. Si la política exige KMS para todo, migrar a `aws:kms`. |
| DATA-2 | Media | **Point-in-Time Recovery** de DynamoDB solo en `prod` | En `dev` no hay PITR (decisión de costo). Confirmar que producción siempre despliega con `Environment=prod`. |
| DATA-3 | Baja | Artefactos intermedios en `procesamiento/` contienen texto extraído (potencial PII) | Aplicar lifecycle/TTL a `procesamiento/` para expirar artefactos (ya existe TTL en `errores/`). El borrado por usuario ya los purga. |
| DATA-4 | Baja | Presigned URLs de descarga (5 min) | Ventana corta y correcta. No reducir más para no afectar UX. |

---

## 4. Detección, logging y monitoreo

### Fortalezas

- **CloudWatch Alarms + SNS** para errores de Lambda, latencia P99 del API y
  mensajes en la DLQ.
- **Dead Letter Queue** con retención de 14 días para errores no manejados.
- Logs por función con grupo dedicado.

### Hallazgos

| ID | Severidad | Hallazgo | Remediación |
|----|-----------|----------|-------------|
| LOG-1 | Media | No hay **CloudTrail** ni **GuardDuty** explícitos en el template | Habilitarlos a nivel de cuenta/organización (suele gestionarse fuera del stack de la app). Recomendado para auditoría y detección de amenazas. |
| LOG-2 | Baja | Retención de logs no parametrizada explícitamente | Definir `RetentionInDays` en los log groups (p. ej. 14–30 días) para controlar costo y cumplir retención. |
| LOG-3 | Baja | Sin alarma de **throttling** de Bedrock/Textract | Agregar alarmas de `ThrottledRequests` si el volumen crece. |

---

## 5. Seguridad del código

### Fortalezas

- **Validación de entrada** en el API: rechazo de keys con `..` (path
  traversal), validación de ownership por `userId`, validación de la config de
  detección (modelo válido, temperatura en rango, patrones regex que compilan,
  marcador `{text}` obligatorio).
- **Sin prompt ni reglas hardcodeadas** en el pipeline: todo proviene de la
  configuración del usuario; un patrón regex inválido se omite sin abortar.
- **Resiliencia:** si Bedrock falla, el pipeline continúa con regex; los errores
  marcan el documento como `FAILED` (no quedan colgados).
- **Manejo de PDFs hostiles:** detección de PDFs protegidos/corruptos, límite de
  tamaño, validación de firma `%PDF`.

### Hallazgos

| ID | Severidad | Hallazgo | Remediación |
|----|-----------|----------|-------------|
| CODE-1 | Baja | Patrones regex definidos por el usuario podrían causar **ReDoS** | Mitigado parcialmente: se valida que compilen y se limita su longitud. Considerar un timeout de evaluación o `re2` si se permite regex arbitrario de terceros. |
| CODE-2 | Baja | El texto enviado a Bedrock se trunca a 10.000 chars/página | Es un control de costo/latencia, no de seguridad, pero acota la exposición de datos al modelo. OK. |

---

## 6. Gestión de dependencias

### Hallazgos

| ID | Severidad | Hallazgo | Remediación |
|----|-----------|----------|-------------|
| DEP-1 | Media | `requirements.txt` usan **rangos abiertos** (`boto3>=1.34.0`, `PyMuPDF>=1.24.0`, `pypdf>=4.0.0`) | La norma del proyecto pide **versiones pinneadas**. Fijar versiones exactas (`==`) o usar un lockfile para builds reproducibles y evitar que una versión nueva introduzca regresiones/vulnerabilidades. |
| DEP-2 | Info | Frontend: `npm audit` reporta **0 vulnerabilidades** | Sin acción. Mantener el escaneo en CI. |
| DEP-3 | Media | No hay escaneo automatizado de dependencias en CI | Integrar `pip-audit` (Python) y `npm audit` (Node) en el pipeline, como pide la norma de seguridad. |

### Comandos de verificación recomendados

```bash
# Python (en cada lambda)
pip-audit -r lambdas/detection/requirements.txt
pip-audit -r lambdas/redaction/requirements.txt
pip-audit -r lambdas/api/requirements.txt
pip-audit -r lambdas/trigger/requirements.txt

# Node (frontend)
cd frontend && npm audit --omit=dev
```

---

## 7. Gestión de secrets

### Fortalezas

- **Sin credenciales hardcodeadas** en el código ni en el repositorio.
- El token de GitHub para Amplify se guarda en **AWS Secrets Manager**.
- Las Lambdas usan **roles IAM** (credenciales temporales STS), no access keys
  de larga duración.
- El frontend nunca recibe credenciales de larga duración (solo tokens OIDC de
  corta vida).

### Hallazgos

| ID | Severidad | Hallazgo | Remediación |
|----|-----------|----------|-------------|
| SEC-1 | Baja | Rotación de secrets no automatizada | La norma pide rotación cada 90 días. Configurar rotación automática en Secrets Manager para el token de GitHub. |
| SEC-2 | Baja | Sin pre-commit hook de detección de secrets verificado en este assessment | Asegurar que el hook de detección de secrets (norma del proyecto) esté activo en todos los entornos de desarrollo. |

---

## 8. Plan de remediación priorizado

| Prioridad | Acción | Hallazgos |
|-----------|--------|-----------|
| 1 (Alta) | Habilitar CloudTrail + GuardDuty a nivel de cuenta | LOG-1 |
| 2 (Media) | Pinear versiones en `requirements.txt` + escaneo en CI | DEP-1, DEP-3 |
| 3 (Media) | Confirmar `Environment=prod` (PITR) en producción | DATA-2 |
| 4 (Media) | Definir retención explícita de logs y lifecycle de `procesamiento/` | LOG-2, DATA-3 |
| 5 (Baja) | Rotación automática de secrets (90 días) | SEC-1 |
| 6 (Baja) | Evaluar `httpOnly` cookies si crece el riesgo de XSS | AUTH-1 |
| 7 (Baja) | Endurecer regex de usuario contra ReDoS (timeout/re2) | CODE-1 |

---

## 9. Checklist Well-Architected (pilar Seguridad)

- [x] Identidades gestionadas centralmente (Cognito + IAM Identity Center)
- [x] Mínimo privilegio en roles IAM
- [x] Trazabilidad de acciones de la app (CloudWatch) — ⚠️ falta CloudTrail
- [x] Protección de datos en reposo (KMS / SSE)
- [x] Protección de datos en tránsito (TLS forzado)
- [x] Datos clasificados y aislados por usuario
- [x] Secrets gestionados (Secrets Manager, sin hardcodeo)
- [x] Respuesta a incidentes básica (DLQ + alarmas SNS)
- [ ] Detección de amenazas automatizada (GuardDuty) — pendiente
- [ ] Escaneo de dependencias en CI — pendiente

**Veredicto:** sistema **apto para producción** con los controles actuales. Se
recomienda ejecutar el plan de remediación (sobre todo CloudTrail/GuardDuty y el
pinneo + escaneo de dependencias) antes de manejar datos sensibles reales a
escala.
