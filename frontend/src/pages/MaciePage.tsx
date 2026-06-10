import React, { useEffect, useState, useCallback } from 'react';
import {
  Container,
  Header,
  SpaceBetween,
  Table,
  Button,
  Box,
  Badge,
  Alert,
  Flashbar,
  StatusIndicator,
  type FlashbarProps,
} from '@cloudscape-design/components';
import authApi from '../services/authApi';

/* ─── Tipos ─────────────────────────────────────────────────────────── */

interface MacieFinding {
  id: string;
  scope: string;
  type: string;
  title: string;
  severity: string;
  count: number;
  s3Key: string;
  updatedAt: string;
}

interface MacieResponse {
  macieEnabled: boolean;
  findings: MacieFinding[];
  message: string;
}

/* ─── Utilidades ────────────────────────────────────────────────────── */

function formatDate(dateStr: string): string {
  if (!dateStr) return '—';
  const d = new Date(dateStr);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleString('es-AR', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function severityColor(sev: string): 'red' | 'blue' | 'grey' {
  const s = sev.toLowerCase();
  if (s === 'alta' || s === 'high') return 'red';
  if (s === 'media' || s === 'medium') return 'blue';
  return 'grey';
}

/** Color del badge según el alcance del hallazgo. */
function scopeColor(scope: string): 'red' | 'green' | 'grey' {
  if (scope === 'ORIGINALES') return 'red';
  if (scope === 'OFUSCADOS') return 'green';
  return 'grey';
}

/** Etiqueta legible del alcance. */
function scopeLabel(scope: string): string {
  if (scope === 'ORIGINALES') return 'Originales';
  if (scope === 'OFUSCADOS') return 'Ofuscados';
  return scope || '—';
}

/** Muestra solo el nombre de archivo de la key S3. */
function fileFromKey(key: string): string {
  if (!key) return '—';
  const parts = key.split('/');
  return parts[parts.length - 1] || key;
}

/* ─── Componente ────────────────────────────────────────────────────── */

const MaciePage: React.FC = () => {
  const [data, setData] = useState<MacieResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [flashMessages, setFlashMessages] = useState<FlashbarProps.MessageDefinition[]>([]);

  const fetchFindings = useCallback(async () => {
    setLoading(true);
    try {
      const response = await authApi.get('/macie/findings');
      setData(response.data as MacieResponse);
    } catch {
      setFlashMessages([
        {
          type: 'error',
          content: 'No se pudieron obtener los hallazgos de Macie.',
          id: `macie-err-${Date.now()}`,
          dismissible: true,
          onDismiss: () => setFlashMessages([]),
        },
      ]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchFindings();
  }, [fetchFindings]);

  const findings = data?.findings ?? [];

  return (
    <SpaceBetween size="l">
      {flashMessages.length > 0 && <Flashbar items={flashMessages} />}

      <Container
        header={
          <Header
            variant="h1"
            description="Hallazgos de Amazon Macie sobre los documentos ofuscados — segunda capa de verificación de PII residual."
            actions={
              <Button iconName="refresh" onClick={fetchFindings} loading={loading}>
                Actualizar
              </Button>
            }
          >
            Monitoreo PII — Macie
          </Header>
        }
      >
        <SpaceBetween size="m">
          {data && !data.macieEnabled && (
            <Alert type="info" header="Amazon Macie no está habilitado">
              {data.message ||
                'Habilite Amazon Macie en la cuenta y active la verificación en Configuración para ver hallazgos.'}
            </Alert>
          )}

          {data && data.macieEnabled && (
            <Box>
              <SpaceBetween size="xs" direction="horizontal">
                <StatusIndicator type="success">Macie habilitado</StatusIndicator>
                {findings.length > 0 ? (
                  <Badge color="red">
                    {findings.length} hallazgo(s) de PII residual
                  </Badge>
                ) : (
                  <Badge color="green">Sin hallazgos</Badge>
                )}
              </SpaceBetween>
            </Box>
          )}

          <Box variant="small" color="text-body-secondary">
            La verificación de Macie es asíncrona: los hallazgos pueden tardar
            unos minutos en aparecer tras procesar un documento. Solo se muestran
            los hallazgos de sus propios documentos.
          </Box>
        </SpaceBetween>
      </Container>

      <Table
        loading={loading}
        loadingText="Cargando hallazgos..."
        trackBy="id"
        empty={
          <Box textAlign="center" color="inherit" padding="l">
            <b>Sin hallazgos</b>
            <Box variant="p" color="inherit">
              {data?.macieEnabled
                ? 'Macie no detectó PII residual en los documentos ofuscados.'
                : 'Active Macie para ver resultados de verificación.'}
            </Box>
          </Box>
        }
        header={<Header counter={`(${findings.length})`}>Hallazgos</Header>}
        columnDefinitions={[
          {
            id: 'scope',
            header: 'Alcance',
            width: 130,
            cell: (item: MacieFinding) => (
              <Badge color={scopeColor(item.scope)}>{scopeLabel(item.scope)}</Badge>
            ),
          },
          {
            id: 'severity',
            header: 'Severidad',
            width: 120,
            cell: (item: MacieFinding) => (
              <Badge color={severityColor(item.severity)}>{item.severity}</Badge>
            ),
          },
          {
            id: 'title',
            header: 'Hallazgo',
            minWidth: 240,
            cell: (item: MacieFinding) => item.title || item.type,
          },
          {
            id: 'count',
            header: 'Ocurrencias',
            width: 130,
            cell: (item: MacieFinding) => item.count ?? '—',
          },
          {
            id: 's3Key',
            header: 'Documento',
            cell: (item: MacieFinding) => fileFromKey(item.s3Key),
          },
          {
            id: 'updatedAt',
            header: 'Detectado',
            cell: (item: MacieFinding) => formatDate(item.updatedAt),
          },
        ]}
        items={findings}
        stripedRows
      />
    </SpaceBetween>
  );
};

export default MaciePage;
