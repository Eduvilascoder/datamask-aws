import React, { useEffect, useState, useCallback } from 'react';
import {
  Container,
  Header,
  SpaceBetween,
  Table,
  Button,
  Box,
  Badge,
  Flashbar,
  StatusIndicator,
  Modal,
  type FlashbarProps,
} from '@cloudscape-design/components';
import authApi from '../services/authApi';
import { useAuth } from '../context/AuthContext';

/* ─── Tipos ─────────────────────────────────────────────────────────── */

interface AuditDocument {
  documentId: string;
  fileName: string;
  fileSize?: number;
  status: string;
  uploadedAt?: string;
  completedAt?: string;
  entitiesFound?: number;
  entitiesByType?: Record<string, number>;
  processingTimeMs?: number;
  errorMessage?: string;
  errorStep?: string;
  engine?: string;
  /** Derivado del prefijo S3 / sesión. */
  user?: string;
}

/* ─── Utilidades ────────────────────────────────────────────────────── */

function formatFileSize(bytes?: number): string {
  if (bytes === undefined || bytes === null) return '—';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

function formatDate(dateStr?: string): string {
  if (!dateStr) return '—';
  const d = new Date(dateStr);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleString('es-AR', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

function formatDuration(ms?: number): string {
  if (ms === undefined || ms === null) return '—';
  if (ms < 1000) return `${ms} ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  const minutes = Math.floor(seconds / 60);
  const rem = Math.round(seconds % 60);
  return `${minutes}m ${rem}s`;
}

/** Devuelve el color de badge según el motor usado. */
function engineBadgeColor(engine?: string): 'blue' | 'green' | 'grey' {
  if (!engine) return 'grey';
  if (engine.toLowerCase().includes('bedrock')) return 'blue';
  if (engine.toLowerCase().includes('comprehend')) return 'green';
  return 'grey';
}

/* ─── Componente ────────────────────────────────────────────────────── */

const AuditPage: React.FC = () => {
  const { state } = useAuth();
  const currentUser = state.user?.username || state.user?.email || '—';
  const [documents, setDocuments] = useState<AuditDocument[]>([]);
  const [loading, setLoading] = useState(true);
  const [deleting, setDeleting] = useState(false);
  const [toDelete, setToDelete] = useState<AuditDocument | null>(null);
  const [flashMessages, setFlashMessages] = useState<FlashbarProps.MessageDefinition[]>([]);
  const [errorDoc, setErrorDoc] = useState<AuditDocument | null>(null);
  const [purging, setPurging] = useState(false);
  const [showPurgeConfirm, setShowPurgeConfirm] = useState(false);

  const fetchDocuments = useCallback(async () => {
    setLoading(true);
    try {
      const response = await authApi.get('/documents', { params: { limit: 100 } });
      const docs: AuditDocument[] = response.data.documents || [];
      setDocuments(docs);
    } catch {
      setFlashMessages([
        {
          type: 'error',
          content: 'No se pudieron cargar los registros de auditoría.',
          id: `load-err-${Date.now()}`,
          dismissible: true,
          onDismiss: () => setFlashMessages([]),
        },
      ]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchDocuments();
  }, [fetchDocuments]);

  const handleConfirmDelete = async () => {
    if (!toDelete) return;
    setDeleting(true);
    try {
      await authApi.delete(`/documents/${encodeURIComponent(toDelete.documentId)}`);
      setFlashMessages([
        {
          type: 'success',
          content: `Se eliminó el registro de "${toDelete.fileName}".`,
          id: `del-ok-${Date.now()}`,
          dismissible: true,
          onDismiss: () => setFlashMessages([]),
        },
      ]);
      setToDelete(null);
      await fetchDocuments();
    } catch {
      setFlashMessages([
        {
          type: 'error',
          content: `No se pudo eliminar el registro de "${toDelete.fileName}".`,
          id: `del-err-${Date.now()}`,
          dismissible: true,
          onDismiss: () => setFlashMessages([]),
        },
      ]);
    } finally {
      setDeleting(false);
    }
  };

  const handlePurgeAudit = async () => {
    setPurging(true);
    try {
      const response = await authApi.delete('/documents');
      const deleted = response.data.deletedRecords ?? 0;
      setFlashMessages([
        {
          type: 'success',
          content: `Se eliminaron ${deleted} registro(s) de auditoría. Los archivos en S3 no se modificaron.`,
          id: `purge-ok-${Date.now()}`,
          dismissible: true,
          onDismiss: () => setFlashMessages([]),
        },
      ]);
      await fetchDocuments();
    } catch {
      setFlashMessages([
        {
          type: 'error',
          content: 'No se pudo eliminar el registro de auditoría.',
          id: `purge-err-${Date.now()}`,
          dismissible: true,
          onDismiss: () => setFlashMessages([]),
        },
      ]);
    } finally {
      setPurging(false);
      setShowPurgeConfirm(false);
    }
  };

  const renderResult = (item: AuditDocument) => {
    const status = (item.status || '').toUpperCase();
    if (status === 'COMPLETED') {
      return <StatusIndicator type="success">Éxito</StatusIndicator>;
    }
    if (status === 'FAILED' || status === 'ERROR') {
      return (
        <SpaceBetween size="xs" direction="horizontal" alignItems="center">
          <StatusIndicator type="error">Error</StatusIndicator>
          <Button variant="inline-link" onClick={() => setErrorDoc(item)}>
            Ver error
          </Button>
        </SpaceBetween>
      );
    }
    if (status === 'PROCESSING' || status === 'UPLOADED') {
      return <StatusIndicator type="in-progress">En proceso</StatusIndicator>;
    }
    return <StatusIndicator type="stopped">{status || '—'}</StatusIndicator>;
  };

  return (
    <SpaceBetween size="l">
      {flashMessages.length > 0 && <Flashbar items={flashMessages} />}

      <Container
        header={
          <Header
            variant="h1"
            counter={`(${documents.length})`}
            description="Historial de documentos procesados con detalle de motor, tiempo y resultados."
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button iconName="refresh" onClick={fetchDocuments} loading={loading}>
                  Actualizar
                </Button>
                <Button
                  iconName="remove"
                  onClick={() => setShowPurgeConfirm(true)}
                  loading={purging}
                  disabled={documents.length === 0}
                >
                  Borrar registro de auditoría
                </Button>
              </SpaceBetween>
            }
          >
            Registros de Auditoría
          </Header>
        }
      >
        <Table
          loading={loading}
          loadingText="Cargando registros..."
          trackBy="documentId"
          variant="embedded"
          stripedRows
          items={documents}
          empty={
            <Box textAlign="center" color="inherit" padding="l">
              <b>Sin registros</b>
              <Box variant="p" color="inherit">
                No hay documentos procesados todavía.
              </Box>
            </Box>
          }
          columnDefinitions={[
            {
              id: 'fileName',
              header: 'Archivo',
              minWidth: 220,
              cell: (item) => item.fileName || '—',
              sortingField: 'fileName',
            },
            {
              id: 'fileSize',
              header: 'Tamaño',
              width: 110,
              cell: (item) => formatFileSize(item.fileSize),
            },
            {
              id: 'user',
              header: 'Usuario',
              width: 140,
              cell: (item) => item.user || currentUser,
            },
            {
              id: 'timestamp',
              header: 'Fecha y Hora',
              width: 200,
              cell: (item) => formatDate(item.completedAt || item.uploadedAt),
            },
            {
              id: 'engine',
              header: 'Motor',
              width: 200,
              cell: (item) =>
                item.engine ? (
                  <Badge color={engineBadgeColor(item.engine)}>{item.engine}</Badge>
                ) : (
                  '—'
                ),
            },
            {
              id: 'time',
              header: 'Tiempo',
              width: 110,
              cell: (item) => formatDuration(item.processingTimeMs),
            },
            {
              id: 'result',
              header: 'Resultado',
              width: 180,
              cell: renderResult,
            },
            {
              id: 'entities',
              header: 'Entidades',
              width: 110,
              cell: (item) =>
                item.entitiesFound !== undefined && item.entitiesFound !== null
                  ? item.entitiesFound
                  : '—',
            },
            {
              id: 'actions',
              header: 'Acciones',
              width: 100,
              cell: (item) => (
                <Button variant="icon" iconName="remove" onClick={() => setToDelete(item)} />
              ),
            },
          ]}
        />
      </Container>

      <Modal
        visible={errorDoc !== null}
        onDismiss={() => setErrorDoc(null)}
        header="Detalle del error"
        footer={
          <Box float="right">
            <Button variant="primary" onClick={() => setErrorDoc(null)}>
              Cerrar
            </Button>
          </Box>
        }
      >
        {errorDoc && (
          <SpaceBetween size="m">
            <Box>
              <Box variant="awsui-key-label">Archivo</Box>
              <Box>{errorDoc.fileName}</Box>
            </Box>
            {errorDoc.errorStep && (
              <Box>
                <Box variant="awsui-key-label">Etapa</Box>
                <Box>{errorDoc.errorStep}</Box>
              </Box>
            )}
            <Box>
              <Box variant="awsui-key-label">Mensaje</Box>
              <Box>
                <code>{errorDoc.errorMessage || 'Sin detalle disponible.'}</code>
              </Box>
            </Box>
          </SpaceBetween>
        )}
      </Modal>

      <Modal
        visible={showPurgeConfirm}
        onDismiss={() => setShowPurgeConfirm(false)}
        header="Borrar registro de auditoría"
        footer={
          <Box float="right">
            <SpaceBetween size="xs" direction="horizontal">
              <Button variant="link" onClick={() => setShowPurgeConfirm(false)} disabled={purging}>
                Cancelar
              </Button>
              <Button variant="primary" onClick={handlePurgeAudit} loading={purging}>
                Borrar todo
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="s">
          <Box>
            Se eliminarán <b>todos</b> los registros de auditoría de tu usuario
            ({documents.length} registro(s)).
          </Box>
          <Box color="text-status-info">
            Los archivos en S3 (originales y ofuscados) <b>no</b> se modifican.
            Esta acción no se puede deshacer.
          </Box>
        </SpaceBetween>
      </Modal>

      <Modal
        visible={toDelete !== null}
        onDismiss={() => setToDelete(null)}
        header="Eliminar registro"
        footer={
          <Box float="right">
            <SpaceBetween size="xs" direction="horizontal">
              <Button variant="link" onClick={() => setToDelete(null)} disabled={deleting}>
                Cancelar
              </Button>
              <Button variant="primary" onClick={handleConfirmDelete} loading={deleting}>
                Eliminar
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        {toDelete && (
          <Box>
            ¿Seguro que desea eliminar el registro de “{toDelete.fileName}”? Se borrarán también
            los archivos asociados (original y ofuscados). Esta acción no se puede deshacer.
          </Box>
        )}
      </Modal>
    </SpaceBetween>
  );
};

export default AuditPage;
