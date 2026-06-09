# Análisis de costos estimado — DataMask AWS

Este documento estima el costo mensual de operar DataMask AWS en una cuenta del
cliente. DataMask es 100% serverless, por lo que **se paga solo por uso**: sin
servidores encendidos, sin costo base por compute. El gasto escala casi lineal
con la cantidad de páginas procesadas.

> ⚠️ Estimación orientativa. Los precios cambian y dependen de la región. Validá
> siempre con la [calculadora oficial de AWS](https://calculator.aws/) y la
> [página de precios](https://aws.amazon.com/pricing/) de cada servicio para tu
> región y volumen reales.

---

## Supuestos del modelo

Definimos un escenario base y dos de mayor volumen. Los supuestos son
explícitos para que puedas recalcular con tus números.

| Supuesto | Valor |
|----------|-------|
| Región | `us-east-1` (N. Virginia) |
| Documentos por mes (base) | 100 PDFs |
| Páginas promedio por documento | 5 → **500 páginas/mes** |
| Tamaño promedio por PDF | 1 MB |
| Modelo de IA | Claude Haiku 4.5 (Bedrock, on-demand) |
| Tokens de entrada por página | ~1.500 (texto de la página + prompt) |
| Tokens de salida por página | ~300 (JSON de entidades) |
| Invocaciones Lambda por documento | 4 (api, trigger, detection, redaction) |
| Duración media Lambda | trigger 8s, detection 12s, redaction 6s, api 0,3s |
| Almacenamiento S3 | originales + ofuscados + intermedios ≈ 3 GB acumulado |
| Retención de logs CloudWatch | 14 días |
| Usuarios concurrentes | bajos (uso interno / por lotes) |

### Precios unitarios usados (us-east-1, orientativos)

| Servicio | Precio unitario |
|----------|-----------------|
| Textract DetectDocumentText | $0,0015 por página (primeras 1M/mes) |
| Bedrock Claude Haiku 4.5 — input | $1,00 por millón de tokens |
| Bedrock Claude Haiku 4.5 — output | $5,00 por millón de tokens |
| Lambda — requests | $0,20 por millón de requests |
| Lambda — compute | $0,0000166667 por GB-segundo |
| S3 Standard — almacenamiento | $0,023 por GB-mes |
| DynamoDB on-demand — escritura | $1,25 por millón de WRU |
| DynamoDB on-demand — lectura | $0,25 por millón de RRU |
| API Gateway REST | $3,50 por millón de requests |
| KMS | $1,00 por clave-mes + $0,03 por 10.000 requests |
| SNS / SQS | prácticamente $0 a este volumen |
| CloudWatch Logs | $0,50 por GB ingestado |

> Las fuentes de precios fueron consultadas en las páginas oficiales de AWS y
> recalculadas para este documento. El contenido fue reformulado para cumplir
> con las restricciones de licencia.

---

## Escenario base — 100 PDFs/mes (500 páginas)

### 1. Amazon Textract (OCR)

500 páginas × $0,0015 = **$0,75/mes**.

### 2. Amazon Bedrock (detección IA)

La detección es **por página**. Por página: ~1.500 tokens de entrada + ~300 de
salida.

- Entrada: 500 × 1.500 = 750.000 tokens → 0,75M × $1,00 = **$0,75**
- Salida: 500 × 300 = 150.000 tokens → 0,15M × $5,00 = **$0,75**
- **Subtotal Bedrock ≈ $1,50/mes**

> Cambiar a un modelo más potente (p. ej. Claude Sonnet) multiplica este rubro
> varias veces. Es la palanca de costo más sensible y es **configurable** desde
> la UI.

### 3. AWS Lambda

| Función | Invocaciones | Memoria | Dur. media | GB-seg | Costo compute |
|---------|--------------|---------|-----------|--------|---------------|
| trigger | 100 | 512 MB | 8 s | 400 | $0,0067 |
| detection | 100 | 1024 MB | 12 s | 1.200 | $0,0200 |
| redaction | 100 | 1024 MB | 6 s | 600 | $0,0100 |
| api | ~2.000 | 256 MB | 0,3 s | 150 | $0,0025 |

- Compute total ≈ **$0,04/mes**
- Requests: ~2.300 → muy por debajo del free tier de 1M → **$0,00**
- **Subtotal Lambda ≈ $0,04/mes** (a menudo cubierto por free tier)

### 4. Amazon S3

- Almacenamiento: 3 GB × $0,023 = $0,069
- Requests (PUT/GET): miles → ~$0,02
- **Subtotal S3 ≈ $0,09/mes**

### 5. DynamoDB (on-demand)

A 100 documentos: unos pocos miles de lecturas/escrituras. **≈ $0,02/mes**.

### 6. API Gateway

~2.000 requests/mes × $3,50/M = **$0,01/mes**.

### 7. KMS

1 clave × $1,00 + requests de cifrado (~decenas de miles) ≈ $0,03.
**≈ $1,03/mes**.

### 8. CloudWatch, SNS, SQS

Logs (~1 GB) + alarmas + DLQ ≈ **$0,55/mes**.

### Total escenario base

| Servicio | Costo/mes |
|----------|-----------|
| Textract | $0,75 |
| Bedrock (IA) | $1,50 |
| Lambda | $0,04 |
| S3 | $0,09 |
| DynamoDB | $0,02 |
| API Gateway | $0,01 |
| KMS | $1,03 |
| CloudWatch + SNS + SQS | $0,55 |
| **TOTAL** | **≈ $3,99/mes** |

> A bajo volumen, el costo está dominado por **rubros fijos** (KMS, CloudWatch)
> más que por el procesamiento. La porción "por documento" (Textract + Bedrock)
> es de apenas ~$2,25 para 100 PDFs.

A esto se suma el **hosting del frontend en Amplify** (build + hosting): a este
volumen suele quedar en **$0–$5/mes** según tráfico y minutos de build.

**Estimación realista escenario base: $5–$10/mes** incluyendo Amplify.

---

## Escenarios de mayor volumen

Los rubros fijos (KMS ~$1, CloudWatch ~$0,5) casi no cambian; lo que escala es
Textract + Bedrock + (poco) Lambda/S3.

| Concepto | 100 PDFs (500 pág) | 1.000 PDFs (5.000 pág) | 10.000 PDFs (50.000 pág) |
|----------|--------------------|------------------------|--------------------------|
| Textract | $0,75 | $7,50 | $75,00 |
| Bedrock (Haiku 4.5) | $1,50 | $15,00 | $150,00 |
| Lambda | $0,04 | $0,40 | $4,00 |
| S3 | $0,09 | $0,50 | $4,00 |
| DynamoDB | $0,02 | $0,20 | $2,00 |
| API Gateway | $0,01 | $0,10 | $1,00 |
| KMS | $1,03 | $1,30 | $4,00 |
| CloudWatch/SNS/SQS | $0,55 | $2,00 | $12,00 |
| **TOTAL aprox.** | **~$4** | **~$27** | **~$252** |

> A 10.000 PDFs/mes el costo por documento baja a **~$0,025** — la arquitectura
> serverless mantiene un costo marginal muy bajo y predecible.

---

## Palancas de costo (cómo optimizar)

1. **Modelo de IA (la palanca #1).** Haiku 4.5 es económico. Modelos más
   potentes multiplican el rubro Bedrock. Es configurable desde la UI.
2. **Reducir tokens.** El texto se trunca a 10.000 caracteres por página; un
   prompt más conciso reduce tokens de entrada.
3. **Desactivar IA para documentos triviales.** Si un tipo de documento se
   resuelve solo con regex, se puede confiar en regex (Bedrock devuelve poco) —
   aunque el ahorro principal viene de elegir bien el modelo.
4. **Retención de logs.** Bajar CloudWatch a 7 días reduce el rubro de logs.
5. **Lifecycle de S3.** Mover `procesamiento/` y `errores/` a expiración (TTL)
   evita acumular artefactos intermedios. Los `errores/` ya expiran a 30 días.
6. **Free tier.** Lambda (1M requests), DynamoDB y CloudWatch suelen quedar
   dentro del free tier a bajo volumen.

---

## Qué NO está incluido

- Transferencia de datos saliente significativa (descargas masivas).
- Provisioned Throughput de Bedrock (solo conviene a volúmenes muy altos y
  sostenidos; este modelo asume **on-demand**).
- Costos de desarrollo/CI fuera de Amplify.
- Soporte AWS (plan Business/Enterprise, si aplica).

---

## Resumen ejecutivo

| Volumen mensual | Costo estimado AWS | Costo por documento |
|-----------------|--------------------|--------------------|
| 100 PDFs | ~$5–$10 (con Amplify) | ~$0,05 |
| 1.000 PDFs | ~$27–$32 | ~$0,03 |
| 10.000 PDFs | ~$252–$260 | ~$0,025 |

DataMask tiene un **costo de entrada muy bajo** y escala de forma predecible. El
gasto está dominado por Textract y Bedrock (ambos por uso), y la elección del
modelo de IA es el principal factor de variación.
