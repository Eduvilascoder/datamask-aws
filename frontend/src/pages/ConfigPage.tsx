import React, { useEffect, useState, useCallback } from 'react';
import {
  Container,
  Header,
  SpaceBetween,
  Button,
  Alert,
  Flashbar,
  Toggle,
  FormField,
  Box,
  Select,
  Textarea,
  Input,
  Table,
  type SelectProps,
} from '@cloudscape-design/components';
import type { FlashbarProps } from '@cloudscape-design/components';
import authApi from '../services/authApi';

/** Regla regex configurable. */
interface RegexRule {
  type: string;
  pattern: string;
  enabled: boolean;
}

/** Método de ofuscación: IA, Regex o ambos. */
type DetectionMethod = 'ai' | 'regex' | 'both';

/** Configuración avanzada de detección. */
interface DetectionConfig {
  detectionMethod: DetectionMethod;
  macieVerification: boolean;
  snsAlertsEnabled: boolean;
  bedrockModelId: string;
  bedrockTemperature: number;
  bedrockPrompt: string;
  regexRules: RegexRule[];
  ignoreEntities: string[];
}

interface BedrockModel {
  id: string;
  label: string;
  provider: string;
}

/**
 * Valida la configuración de detección.
 * Retorna un mensaje de error o null si es válida.
 */
export const validateDetectionConfig = (config: DetectionConfig): string | null => {
  const method = config.detectionMethod ?? 'both';
  if (!['ai', 'regex', 'both'].includes(method)) {
    return 'Debe seleccionar un método de ofuscación válido.';
  }
  const usesRegex = method === 'regex' || method === 'both';

  if (!config.bedrockModelId) {
    return 'Debe seleccionar un modelo de Bedrock.';
  }
  if (
    typeof config.bedrockTemperature !== 'number' ||
    Number.isNaN(config.bedrockTemperature) ||
    config.bedrockTemperature < 0 ||
    config.bedrockTemperature > 1
  ) {
    return 'La temperatura debe ser un número entre 0 y 1.';
  }
  if (!config.bedrockPrompt.trim() || !config.bedrockPrompt.includes('{text}')) {
    return 'El prompt no puede estar vacío y debe incluir el marcador {text}.';
  }
  const badRule = config.regexRules.find((r) => !r.type.trim() || !r.pattern.trim());
  if (badRule) {
    return 'Cada regla regex requiere un tipo y un patrón.';
  }
  if (usesRegex && !config.regexRules.some((r) => r.enabled)) {
    return 'El método seleccionado usa regex pero no hay ninguna regla activa.';
  }
  return null;
};

const ConfigPage: React.FC = () => {
  const [detection, setDetection] = useState<DetectionConfig | null>(null);
  const [models, setModels] = useState<BedrockModel[]>([]);
  const [selectedProvider, setSelectedProvider] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [creatingJobs, setCreatingJobs] = useState(false);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [flashMessages, setFlashMessages] = useState<FlashbarProps.MessageDefinition[]>([]);

  const fetchConfig = useCallback(async () => {
    setLoading(true);
    try {
      const detRes = await authApi.get('/config/detection');
      const cfg = detRes.data.config as DetectionConfig;
      const availableModels = (detRes.data.availableModels as BedrockModel[]) || [];
      setDetection(cfg);
      setModels(availableModels);
      // Derivar el proveedor a partir del modelo configurado.
      const current = availableModels.find((m) => m.id === cfg.bedrockModelId);
      setSelectedProvider(current?.provider ?? availableModels[0]?.provider ?? null);
    } catch {
      setDetection(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchConfig();
  }, [fetchConfig]);

  /* ─── Helpers de detección ──────────────────────────────────────── */

  const updateDetection = (patch: Partial<DetectionConfig>) => {
    setDetection((prev) => (prev ? { ...prev, ...patch } : prev));
  };

  const updateRule = (index: number, patch: Partial<RegexRule>) => {
    setDetection((prev) => {
      if (!prev) return prev;
      const rules = prev.regexRules.map((r, i) => (i === index ? { ...r, ...patch } : r));
      return { ...prev, regexRules: rules };
    });
  };

  const addRule = () => {
    setDetection((prev) =>
      prev
        ? { ...prev, regexRules: [...prev.regexRules, { type: '', pattern: '', enabled: true }] }
        : prev
    );
  };

  const removeRule = (index: number) => {
    setDetection((prev) =>
      prev ? { ...prev, regexRules: prev.regexRules.filter((_, i) => i !== index) } : prev
    );
  };

  const flash = (type: 'success' | 'error', content: string) => {
    setFlashMessages([
      {
        type,
        content,
        id: `${type}-${Date.now()}`,
        dismissible: true,
        onDismiss: () => setFlashMessages([]),
      },
    ]);
  };

  const handleCreateMacieJobs = async () => {
    setCreatingJobs(true);
    setFlashMessages([]);
    try {
      const res = await authApi.post('/macie/jobs', {});
      const data = res.data as {
        macieEnabled: boolean;
        jobs: Array<{ scope: string; status: string }>;
        message: string;
      };
      if (!data.macieEnabled) {
        flash('error', data.message || 'Amazon Macie no está habilitado en la cuenta.');
      } else {
        flash('success', data.message || 'Jobs de Macie creados.');
      }
    } catch {
      flash('error', 'No se pudieron crear los jobs de Macie.');
    } finally {
      setCreatingJobs(false);
    }
  };

  const handleSave = async () => {
    if (!detection) {
      return;
    }

    const error = validateDetectionConfig(detection);
    if (error) {
      setValidationError(error);
      return;
    }

    setSaving(true);
    setValidationError(null);
    setFlashMessages([]);

    try {
      await authApi.put('/config/detection', detection);
      flash('success', 'Configuración guardada correctamente.');
    } catch (err) {
      const msg =
        (err as { response?: { data?: { message?: string } } })?.response?.data?.message ||
        'La configuración no pudo ser guardada.';
      flash('error', msg);
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <Container>
        <Box textAlign="center" padding="l">
          <Alert type="info">Cargando configuración...</Alert>
        </Box>
      </Container>
    );
  }

  // Proveedores disponibles (derivados de los modelos).
  const providerOptions: SelectProps.Option[] = Array.from(
    new Set(models.map((m) => m.provider))
  )
    .sort()
    .map((p) => ({ value: p, label: p }));
  const selectedProviderOption =
    providerOptions.find((o) => o.value === selectedProvider) ?? null;

  // Modelos del proveedor seleccionado.
  const modelOptions: SelectProps.Option[] = models
    .filter((m) => m.provider === selectedProvider)
    .map((m) => ({ value: m.id, label: m.label }));
  const selectedModel =
    modelOptions.find((o) => o.value === detection?.bedrockModelId) ?? null;

  // Opciones de método de ofuscación.
  const methodOptions: SelectProps.Option[] = [
    { value: 'both', label: 'Ambos (IA + Regex)', description: 'Combina IA y reglas regex (recomendado)' },
    { value: 'ai', label: 'Solo IA', description: 'Detección contextual con Amazon Bedrock' },
    { value: 'regex', label: 'Solo Regex', description: 'Reglas deterministas configurables' },
  ];
  const method: DetectionMethod = detection?.detectionMethod ?? 'both';
  const selectedMethodOption = methodOptions.find((o) => o.value === method) ?? methodOptions[0];
  const showAi = method === 'ai' || method === 'both';
  const showRegex = method === 'regex' || method === 'both';

  return (
    <SpaceBetween size="l">
      {flashMessages.length > 0 && <Flashbar items={flashMessages} />}

      {validationError && (
        <Alert type="error" dismissible onDismiss={() => setValidationError(null)}>
          {validationError}
        </Alert>
      )}

      <Container
        header={
          <Header
            variant="h1"
            description="Configure el motor de IA, el prompt y las reglas deterministas usadas para detectar y ofuscar datos sensibles."
            actions={
              <Button variant="primary" onClick={handleSave} loading={saving} disabled={loading || !detection}>
                Guardar configuración
              </Button>
            }
          >
            Configuración
          </Header>
        }
      >
        <Box color="text-body-secondary">
          Los cambios se aplican a los próximos documentos procesados.
        </Box>
      </Container>

      {detection && (
        <>
          <Container header={<Header variant="h2" description="Elija qué algoritmos se aplican para detectar y ofuscar datos sensibles.">Método de ofuscación</Header>}>
            <SpaceBetween size="l">
              <FormField
                label="Método"
                description="IA usa Amazon Bedrock (contextual); Regex usa reglas deterministas; Ambos combina los dos motores."
              >
                <Select
                  selectedOption={selectedMethodOption}
                  options={methodOptions}
                  onChange={({ detail }) =>
                    updateDetection({
                      detectionMethod: (detail.selectedOption.value as DetectionMethod) ?? 'both',
                    })
                  }
                />
              </FormField>

              <FormField
                label="Verificación con Amazon Macie (segunda capa)"
                description="Tras ofuscar, Macie escanea el documento resultante para detectar PII residual como medida de protección adicional. Requiere Amazon Macie habilitado en la cuenta. Puede generar costos adicionales por GB analizado."
              >
                <Toggle
                  checked={detection.macieVerification}
                  onChange={({ detail }) =>
                    updateDetection({ macieVerification: detail.checked })
                  }
                >
                  {detection.macieVerification
                    ? 'Verificación Macie habilitada'
                    : 'Verificación Macie deshabilitada'}
                </Toggle>
              </FormField>

              <FormField
                label="Alertas y mensajes por Amazon SNS"
                description="Habilita el envío de notificaciones por email (vía SNS) ante eventos de la aplicación, como la creación de jobs de Macie o fallos de procesamiento."
              >
                <Toggle
                  checked={detection.snsAlertsEnabled}
                  onChange={({ detail }) =>
                    updateDetection({ snsAlertsEnabled: detail.checked })
                  }
                >
                  {detection.snsAlertsEnabled
                    ? 'Alertas SNS habilitadas'
                    : 'Alertas SNS deshabilitadas'}
                </Toggle>
              </FormField>
            </SpaceBetween>
          </Container>

          <Container
            header={
              <Header
                variant="h2"
                description="Crea dos jobs de Amazon Macie para escanear PII en sus documentos: uno sobre los originales y otro sobre los ofuscados. Los hallazgos se diferencian por alcance y se ven en Monitoreo PII — Macie."
                actions={
                  <Button
                    iconName="search"
                    onClick={handleCreateMacieJobs}
                    loading={creatingJobs}
                  >
                    Crear jobs de Macie
                  </Button>
                }
              >
                Escaneo de buckets con Macie
              </Header>
            }
          >
            <SpaceBetween size="s">
              <Box variant="p" color="text-body-secondary">
                Se crearán dos jobs ONE_TIME de Macie acotados a sus carpetas:
              </Box>
              <Box variant="p">
                • <strong>Originales</strong> — escanea <code>originales/</code> (datos sin ofuscar).
              </Box>
              <Box variant="p">
                • <strong>Ofuscados</strong> — escanea <code>ofuscados/</code> (resultado ofuscado, verificación de PII residual).
              </Box>
              <Box variant="small" color="text-status-info">
                Requiere Amazon Macie habilitado en la cuenta. Cada job lleva un
                tag <code>DataMaskScope</code> (ORIGINALES / OFUSCADOS) para
                diferenciar los hallazgos en la consola de Macie.
              </Box>
            </SpaceBetween>
          </Container>

          {showAi && (
          <>
          <Container header={<Header variant="h2" description="Modelo de Amazon Bedrock usado para el análisis contextual.">Modelo de IA</Header>}>
            <SpaceBetween size="l">
              <FormField
                label="Proveedor"
                description="Elija el proveedor del modelo de IA (Bedrock)."
              >
                <Select
                  selectedOption={selectedProviderOption}
                  options={providerOptions}
                  onChange={({ detail }) => {
                    const provider = detail.selectedOption.value ?? '';
                    setSelectedProvider(provider);
                    // Auto-seleccionar el primer modelo del proveedor.
                    const first = models.find((m) => m.provider === provider);
                    if (first) updateDetection({ bedrockModelId: first.id });
                  }}
                  placeholder="Seleccione un proveedor"
                />
              </FormField>

              <FormField label="Modelo">
                <Select
                  selectedOption={selectedModel}
                  options={modelOptions}
                  onChange={({ detail }) =>
                    updateDetection({ bedrockModelId: detail.selectedOption.value ?? '' })
                  }
                  placeholder="Seleccione un modelo"
                  empty="No hay modelos para este proveedor"
                />
              </FormField>

              <FormField
                label="Temperatura del modelo"
                description="Controla la aleatoriedad de la respuesta. 0 = determinista y preciso (recomendado para detección de PII); valores más altos generan respuestas más variadas. Rango: 0 a 1."
                constraintText="Valor entre 0 y 1 (ej: 0, 0.2, 0.5)."
              >
                <Input
                  type="number"
                  value={String(detection.bedrockTemperature)}
                  step={0.1}
                  inputMode="decimal"
                  onChange={({ detail }) => {
                    const parsed = Number(detail.value);
                    updateDetection({
                      bedrockTemperature: Number.isNaN(parsed) ? 0 : parsed,
                    });
                  }}
                  placeholder="0.0"
                />
              </FormField>
            </SpaceBetween>
          </Container>

          <Container header={<Header variant="h2" description="Instrucciones que se envían al modelo. Debe incluir el marcador {text} donde se inserta el documento.">Prompt del modelo</Header>}>
            <FormField label="Prompt" stretch>
              <Textarea
                value={detection.bedrockPrompt}
                onChange={({ detail }) => updateDetection({ bedrockPrompt: detail.value })}
                rows={14}
                placeholder="Instrucciones para el modelo... incluya {text}"
              />
            </FormField>
          </Container>
          </>
          )}

          {showRegex && (
          <Container
            header={
              <Header
                variant="h2"
                description="Reglas regex deterministas. Cada coincidencia se ofusca con la etiqueta del tipo indicado."
                actions={<Button iconName="add-plus" onClick={addRule}>Agregar regla</Button>}
              >
                Reglas regex deterministas
              </Header>
            }
          >
            <Table
              items={detection.regexRules}
              variant="embedded"
              empty={<Box textAlign="center" color="text-body-secondary">Sin reglas regex</Box>}
              columnDefinitions={[
                {
                  id: 'enabled',
                  header: 'Activa',
                  width: 90,
                  cell: (item) => {
                    const idx = detection.regexRules.indexOf(item);
                    return (
                      <Toggle
                        checked={item.enabled}
                        onChange={({ detail }) => updateRule(idx, { enabled: detail.checked })}
                      />
                    );
                  },
                },
                {
                  id: 'type',
                  header: 'Tipo (etiqueta)',
                  width: 200,
                  cell: (item) => {
                    const idx = detection.regexRules.indexOf(item);
                    return (
                      <Input
                        value={item.type}
                        onChange={({ detail }) => updateRule(idx, { type: detail.value.toUpperCase() })}
                        placeholder="Ej: DNI"
                      />
                    );
                  },
                },
                {
                  id: 'pattern',
                  header: 'Patrón regex',
                  cell: (item) => {
                    const idx = detection.regexRules.indexOf(item);
                    return (
                      <Input
                        value={item.pattern}
                        onChange={({ detail }) => updateRule(idx, { pattern: detail.value })}
                        placeholder="Expresión regular"
                      />
                    );
                  },
                },
                {
                  id: 'actions',
                  header: '',
                  width: 100,
                  cell: (item) => {
                    const idx = detection.regexRules.indexOf(item);
                    return (
                      <Button variant="inline-link" iconName="remove" onClick={() => removeRule(idx)}>
                        Quitar
                      </Button>
                    );
                  },
                },
              ]}
            />
          </Container>
          )}

          <Container header={<Header variant="h2" description="Valores literales que el sistema NO debe ofuscar aunque se detecten (uno por línea).">Entidades a ignorar</Header>}>
            <FormField label="Valores a ignorar" stretch>
              <Textarea
                value={detection.ignoreEntities.join('\n')}
                onChange={({ detail }) =>
                  updateDetection({
                    ignoreEntities: detail.value
                      .split('\n')
                      .map((v) => v.trim())
                      .filter((v) => v.length > 0),
                  })
                }
                rows={6}
                placeholder="Ej: Banco Nación&#10;Ministerio de Economía"
              />
            </FormField>
          </Container>
        </>
      )}
    </SpaceBetween>
  );
};

export default ConfigPage;
