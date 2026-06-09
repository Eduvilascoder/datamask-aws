# Algoritmos de detección y ofuscación — DataMask AWS

Este documento explica cómo DataMask detecta datos personales (PII) y los
ofusca. La detección combina **dos motores complementarios**: un motor de
**Inteligencia Artificial (Amazon Bedrock)** para detección contextual y un
motor de **expresiones regulares (regex)** para formatos deterministas. Luego
un proceso de **fusión** unifica ambos resultados, y la **redacción** aplica las
etiquetas sobre el PDF.

> Importante: el prompt de IA, el modelo, la temperatura y las reglas regex
> provienen **exclusivamente de la Configuración** del usuario (persistida en
> DynamoDB). No existe prompt ni reglas hardcodeadas en el pipeline en runtime.

## Método de ofuscación (configurable)

Desde **Configuración** el usuario elige qué motores se aplican mediante el
campo `detectionMethod`:

| Método | Valor | Comportamiento |
|--------|-------|----------------|
| Ambos (recomendado) | `both` | Ejecuta IA + Regex y fusiona los resultados (default) |
| Solo IA | `ai` | Ejecuta únicamente Bedrock; ignora las reglas regex |
| Solo Regex | `regex` | Ejecuta únicamente las reglas regex; no invoca Bedrock |

El handler de detección (`_detect_all_pages`) lee `detectionMethod` y activa los
motores correspondientes por página. Con `regex`, la configuración debe tener al
menos una regla activa (se valida en el API). Con `ai`, no se requieren reglas
activas.

> Qué motores se ejecutan también es configurable mediante `detectionMethod`:
> `ai` (solo Bedrock), `regex` (solo reglas regex) o `both` (ambos, fusionados;
> valor por defecto). Cuando se elige un único motor, el otro no se invoca.

---

## Visión general del pipeline de detección

```
textract_output.json (texto por página)
        │
        ▼
  Para CADA página (según detectionMethod):
        ├─▶ Motor IA (Bedrock Converse)  ──▶ entidades contextuales   [si ai|both]
        └─▶ Motor Regex (reglas config)  ──▶ entidades deterministas  [si regex|both]
        │
        ▼
   Fusión + resolución de solapamientos (por página)
        │
        ▼
   entities.json  ──▶  Redacción (PyMuPDF) ──▶ PDF ofuscado
```

La detección se ejecuta **página por página**. Esto garantiza que cada entidad
nazca con su número de página correcto y que documentos largos no pierdan
páginas por el truncado del texto. Según el `detectionMethod` configurado, en
cada página se invoca el motor de IA, el de regex o ambos.

---

## 1. Motor de IA (Amazon Bedrock)

### 1.1 Qué resuelve

La IA detecta PII **contextual** que los patrones fijos no capturan bien:

- Nombres y apellidos de personas (incluyendo compuestos).
- Direcciones físicas con estructura variable ("Av. Corrientes 1234 Piso 5").
- Fechas personales en lenguaje natural.
- Datos ambiguos que requieren entender el contexto de la oración.

### 1.2 Cómo funciona

1. **Entrada por página.** Se toma el texto de cada página (truncado a 10.000
   caracteres por página como salvaguarda de costo/latencia).
2. **Construcción del prompt.** Se usa la plantilla configurada por el usuario,
   reemplazando el marcador `{text}` por el texto de la página. Si la plantilla
   no incluye `{text}`, el texto se anexa al final (fallback de formato). Si no
   hay plantilla configurada, la detección por IA falla de forma visible (no se
   inventa un prompt).
3. **Invocación con la API Converse.** Bedrock se invoca con `converse`, que es
   **unificada para todos los proveedores** (Anthropic, Amazon Nova, Meta Llama,
   Mistral, etc.). La temperatura es configurable (0.0 = determinista,
   recomendado para extracción precisa). Si un modelo no soporta `temperature`,
   se reintenta sin ese parámetro.
4. **Parseo robusto de la respuesta.** Se espera un JSON array de entidades
   `{text, type, start_offset}`. El parser tolera texto adicional antes/después
   del array y extrae el bloque JSON válido.
5. **Validación por localización real (clave).** Los LLM cuentan offsets de
   caracteres de forma poco confiable. Por eso **no se confía en el
   `start_offset`** que reporta el modelo: se busca el texto literal de la
   entidad dentro del documento y se calcula el offset verdadero.
   - Si hay varias ocurrencias, se elige la más cercana al offset sugerido.
   - Si el texto **no aparece** en el documento, la entidad se descarta (es una
     alucinación del modelo).
   - El tipo debe pertenecer al conjunto válido (`NOMBRE`, `EMAIL`, `TELEFONO`,
     `CELULAR`, `DIRECCION`, `DNI`, `CUIT_CUIL`, `TARJETA_CREDITO`,
     `CUENTA_BANCARIA`, `PASAPORTE`, `FECHA`).

### 1.3 Resiliencia

Si Bedrock falla, hace timeout (30s) o responde con formato inválido, el motor
de IA devuelve una lista vacía y el pipeline continúa **solo con regex**. El
fallo se registra en CloudWatch sin volcar el contenido del documento.

### 1.4 Características de la detección por IA

| Propiedad | Valor |
|-----------|-------|
| API | Bedrock **Converse** (multi-proveedor) |
| Confianza asignada | 0.85 |
| `source` | `bedrock` (sufijo de etiqueta `-IA`) |
| Timeout | 30 s |
| Truncado | 10.000 caracteres por página |
| Offset | Recalculado por localización real, no el del modelo |

---

## 2. Motor de Regex

### 2.1 Qué resuelve

Los regex detectan **formatos deterministas** de alta precisión, sobre todo
identificadores argentinos donde la estructura es estable:

- DNI (`32.456.789`), CUIT/CUIL (`20-32456789-4`), pasaporte (`AAB123456`).
- Email, teléfonos fijos y celulares (incluido el +54).
- Tarjetas de crédito, cuentas bancarias (CBU/CVU 22 dígitos, IBAN).
- Tipos personalizados: teléfono Chile, expediente, expediente GDE (Gestión
  Documental Electrónica), número de serie, token de GitHub, código de trámite,
  etc.

### 2.2 Cómo funciona

1. **Reglas configurables.** Cada regla es un objeto
   `{type, pattern, enabled}` que viene de la Configuración del usuario. No hay
   patrones hardcodeados en el detector.
2. **Aplicación.** Para cada regla habilitada se compila el patrón y se buscan
   **todas** las ocurrencias en el texto de la página (`finditer`). Cada
   coincidencia produce una entidad con su offset exacto.
3. **Robustez.** Una regla deshabilitada, sin tipo/patrón, o con un patrón regex
   inválido se **omite** sin abortar el resto (se registra un warning).

### 2.3 Reglas por defecto (editables)

Las reglas de **alta precisión** vienen activadas; las **ruidosas** (NOMBRE,
DIRECCION, FECHA, CELULAR) vienen **desactivadas** por defecto porque por regex
generan falsos positivos — esos tipos los cubre mejor la IA. El usuario puede
activarlas o ajustar el patrón desde la UI.

| Tipo | Activa por defecto | Motivo |
|------|--------------------|--------|
| DNI, CUIT_CUIL, PASAPORTE | ✅ | Formato estable, alta precisión |
| EMAIL | ✅ | Formato estándar |
| TELEFONO | ✅ | Formato estable |
| TARJETA_CREDITO, CUENTA_BANCARIA | ✅ | Formato numérico estable |
| TOKEN_GITHUB | ✅ | Prefijo + longitud fija |
| EXPEDIENTE_GDE | ✅ | Formato GDE estable (`LETRA-AAAA-NNNNNNNN-...`) |
| NOMBRE, DIRECCION, FECHA, CELULAR | ❌ | Ruidoso por regex; lo cubre la IA |
| EXPEDIENTE_GDE | ✅ | Expediente GDE de APN (formato fijo, alta precisión) |
| TELEFONO_CHILE, EXPEDIENTE, NUMERO_SERIE, CODIGO_TRAMITE | ❌ | Tipos personalizados opcionales |

### 2.4 Características de la detección por regex

| Propiedad | Valor |
|-----------|-------|
| Confianza asignada | 0.95 |
| `source` | `regex` (sufijo de etiqueta `-R`) |
| Offset | Exacto (posición de la coincidencia) |

---

## 3. Fusión y resolución de solapamientos

Cuando IA y regex (o dos reglas) detectan texto que se **solapa**, hay que
elegir una sola entidad por porción de texto para evitar etiquetas apiladas
(p. ej. un número de tarjeta marcado a la vez como `TARJETA_CREDITO`, `DNI` y
`CUENTA_BANCARIA`).

La fusión se hace **dentro de cada página** y aplica este criterio de desempate,
en orden:

1. **Mayor cobertura** — gana la entidad con el span (longitud) más largo.
2. **Mayor confianza** — si la cobertura es igual, gana la de mayor confianza
   (regex 0.95 > IA 0.85).
3. **Prioridad de motor** — si aún hay empate, gana regex sobre IA.

El resultado es una lista de entidades sin solapamientos, ordenada por página y
posición.

> El usuario puede definir una **lista de entidades a ignorar** (valores
> literales). Tras la fusión, cualquier entidad cuyo texto coincida (sin
> distinción de mayúsculas) con esa lista se excluye y no se ofusca.

---

## 4. Redacción (ofuscación visual)

La Lambda de redacción usa **PyMuPDF** para reemplazar cada entidad por una
etiqueta `[TIPO-MOTOR]` conservando el layout del PDF.

### 4.1 Etiquetas y sufijo de motor

Cada etiqueta indica **qué motor ganó** la detección:

| Motor | `source` | Sufijo | Ejemplo |
|-------|----------|--------|---------|
| IA (Bedrock) | `bedrock` | `-IA` | `[NOMBRE-IA]`, `[DIRECCION-IA]` |
| Regex | `regex` | `-R` | `[DNI-R]`, `[CUIT_CUIL-R]` |

Cada tipo se pinta con un color de fondo diferenciado (nombre azul, email verde,
DNI rojo, etc.).

### 4.2 Cómo aplica las redacciones

1. **Agrupa por página** las entidades detectadas.
2. Para cada entidad, **busca el texto** en su página asignada
   (`page.search_for`).
3. **Fallback multi-página:** si el texto no aparece en la página asignada
   (por desajustes de numeración), lo busca en **todas** las páginas.
4. **Evita el apilado visual:** si una zona ya fue redactada por otra entidad
   (solapamiento de área > 50%), se omite para no apilar etiquetas.
5. **Conserva el tamaño de fuente** original de la palabra ofuscada.
6. Aplica las redacciones (`apply_redactions`) en cada página afectada y guarda
   el PDF preservando su estructura.

### 4.3 Salidas

- `ofuscados/{userId}/{docId}/{stem}_ofuscado.pdf` — PDF con las etiquetas.
- `ofuscados/{userId}/{docId}/{stem}_informe.md` — informe Markdown con el
  texto ofuscado por página.

Si tras la detección y el filtrado por configuración **no quedan entidades**, no
se genera archivo ofuscado.

---

## 5. Resumen comparativo

| Aspecto | Motor IA (Bedrock) | Motor Regex |
|---------|--------------------|-------------|
| Fortaleza | Contexto, nombres, direcciones, fechas | Identificadores con formato fijo |
| Precisión de tipo | Alta en lenguaje natural | Muy alta en formatos estables |
| Falsos positivos | Bajos (validado contra el texto) | Bajos si el patrón es específico |
| Configurable | Modelo, temperatura, prompt | Patrón y activación por tipo |
| Offset | Recalculado por localización | Exacto |
| Confianza | 0.85 | 0.95 |
| Dependencia externa | Sí (Bedrock) | No |
| Fallback | Si falla, sigue regex | Siempre disponible |

La combinación de ambos motores ofrece **cobertura amplia** (IA) con
**precisión determinista** (regex), y deja al usuario el control total de la
configuración desde la UI.
