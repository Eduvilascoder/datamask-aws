import React, { useEffect, useState, useCallback } from 'react';
import {
  Container,
  Header,
  SpaceBetween,
  Table,
  Button,
  Box,
  Flashbar,
  Tabs,
  Icon,
  Badge,
  Modal,
} from '@cloudscape-design/components';
import authApi from '../services/authApi';

/* ─── Tipos ─────────────────────────────────────────────────────────── */

interface S3Object {
  key: string;
  name: string;
  size: number;
  lastModified: string;
  isFolder: boolean;
}

/* ─── Utilidades ────────────────────────────────────────────────────── */

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

function formatDate(dateStr: string): string {
  if (!dateStr) return '—';
  const d = new Date(dateStr);
  return d.toLocaleString('es-AR', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

/* ─── Componente ────────────────────────────────────────────────────── */

const OutputPage: React.FC = () => {
  const [pdfFiles, setPdfFiles] = useState<S3Object[]>([]);
  const [mdFiles, setMdFiles] = useState<S3Object[]>([]);
  const [bucketName, setBucketName] = useState<string>('');
  const [loading, setLoading] = useState(true);
  const [deleting, setDeleting] = useState(false);
  const [toDelete, setToDelete] = useState<S3Object | null>(null);
  const [purging, setPurging] = useState(false);
  const [showPurgeConfirm, setShowPurgeConfirm] = useState(false);
  const [flashMessages, setFlashMessages] = useState<
    Array<{ type: 'success' | 'error'; content: string; id: string; dismissible: boolean }>
  >([]);

  /* ─── Cargar archivos ofuscados desde S3 ────────────────────────── */

  const fetchFiles = useCallback(async () => {
    setLoading(true);
    try {
      const response = await authApi.get('/documents/s3/list', {
        params: { prefix: 'ofuscados/' },
      });
      const data = response.data;
      setBucketName(data.bucket);

      const objects: S3Object[] = data.objects || [];
      // Separar PDFs ofuscados de los informes Markdown
      setPdfFiles(objects.filter((f) => f.name.toLowerCase().endsWith('.pdf')));
      setMdFiles(objects.filter((f) => f.name.toLowerCase().endsWith('.md')));
    } catch {
      setFlashMessages([
        {
          type: 'error',
          content: 'Error al cargar los archivos ofuscados.',
          id: `load-err-${Date.now()}`,
          dismissible: true,
        },
      ]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchFiles();
  }, [fetchFiles]);

  /* ─── Descargar archivo (presigned URL) ─────────────────────────── */

  const handleDownload = async (item: S3Object) => {
    try {
      const response = await authApi.get('/documents/s3/download', {
        params: { key: item.key },
      });
      const url = response.data.url;
      window.open(url, '_blank');
    } catch {
      setFlashMessages([
        {
          type: 'error',
          content: `No se pudo descargar "${item.name}".`,
          id: `dl-err-${Date.now()}`,
          dismissible: true,
        },
      ]);
    }
  };

  /* ─── Eliminar archivo de S3 ─────────────────────────────────────── */

  const handleConfirmDelete = async () => {
    if (!toDelete) return;
    setDeleting(true);
    try {
      await authApi.delete('/documents/s3/object', {
        params: { key: toDelete.key },
      });
      setFlashMessages([
        {
          type: 'success',
          content: `Se eliminó "${toDelete.name}".`,
          id: `del-ok-${Date.now()}`,
          dismissible: true,
        },
      ]);
      setToDelete(null);
      await fetchFiles();
    } catch {
      setFlashMessages([
        {
          type: 'error',
          content: `No se pudo eliminar "${toDelete.name}".`,
          id: `del-err-${Date.now()}`,
          dismissible: true,
        },
      ]);
    } finally {
      setDeleting(false);
    }
  };

  /* ─── Borrar todos los ofuscados ─────────────────────────────────── */

  const handlePurgeObfuscated = async () => {
    setPurging(true);
    try {
      const response = await authApi.post('/documents/purge', { scope: 'obfuscated' });
      const obf = response.data.deletedObfuscated ?? 0;
      const proc = response.data.deletedProcessing ?? 0;
      setFlashMessages([
        {
          type: 'success',
          content: `Se eliminaron ${obf} archivo(s) ofuscado(s) y ${proc} intermedio(s). Los originales y la auditoría se conservan.`,
          id: `purge-ok-${Date.now()}`,
          dismissible: true,
        },
      ]);
      await fetchFiles();
    } catch {
      setFlashMessages([
        {
          type: 'error',
          content: 'No se pudieron eliminar los archivos ofuscados.',
          id: `purge-err-${Date.now()}`,
          dismissible: true,
        },
      ]);
    } finally {
      setPurging(false);
      setShowPurgeConfirm(false);
    }
  };

  /* ─── Tabla ─────────────────────────────────────────────────────── */

  const renderTable = (files: S3Object[], typeLabel: string, badgeColor: 'green' | 'blue') => (
    <Table
      loading={loading}
      loadingText="Cargando..."
      trackBy="key"
      empty={
        <Box textAlign="center" color="inherit" padding="l">
          <b>Sin archivos</b>
          <Box variant="p" color="inherit">
            No hay archivos ofuscados todavía. Procesá documentos desde “Documentos en S3”.
          </Box>
        </Box>
      }
      header={
        <Header
          counter={`(${files.length})`}
          actions={
            <Button iconName="refresh" onClick={fetchFiles} loading={loading}>
              Actualizar
            </Button>
          }
        >
          Archivos
        </Header>
      }
      columnDefinitions={[
        {
          id: 'name',
          header: 'Nombre',
          minWidth: 400,
          cell: (item: S3Object) => (
            <Button variant="link" onClick={() => handleDownload(item)}>
              <Icon name="file" /> {item.name}
            </Button>
          ),
          sortingField: 'name',
        },
        {
          id: 'type',
          header: 'Tipo',
          width: 110,
          cell: () => <Badge color={badgeColor}>{typeLabel}</Badge>,
        },
        {
          id: 'size',
          header: 'Tamaño',
          width: 120,
          cell: (item: S3Object) => formatFileSize(item.size),
        },
        {
          id: 'lastModified',
          header: 'Generado',
          cell: (item: S3Object) => formatDate(item.lastModified),
        },
        {
          id: 'actions',
          header: 'Acciones',
          width: 160,
          cell: (item: S3Object) => (
            <SpaceBetween size="xs" direction="horizontal">
              <Button variant="icon" iconName="download" onClick={() => handleDownload(item)} />
              <Button variant="icon" iconName="remove" onClick={() => setToDelete(item)} />
            </SpaceBetween>
          ),
        },
      ]}
      items={files}
      stripedRows
    />
  );

  return (
    <SpaceBetween size="l">
      {flashMessages.length > 0 && (
        <Flashbar
          items={flashMessages.map((msg) => ({
            ...msg,
            onDismiss: () => setFlashMessages((prev) => prev.filter((m) => m.id !== msg.id)),
          }))}
        />
      )}

      <Container
        header={
          <Header
            variant="h1"
            description="Explore y descargue los archivos ofuscados generados (PDF y Markdown)."
            info={bucketName ? <Badge color="blue">{bucketName}</Badge> : undefined}
            actions={
              <Button
                iconName="remove"
                onClick={() => setShowPurgeConfirm(true)}
                loading={purging}
                disabled={pdfFiles.length === 0 && mdFiles.length === 0}
              >
                Borrar todos los ofuscados
              </Button>
            }
          >
            Archivos ofuscados
          </Header>
        }
      >
        <Tabs
          tabs={[
            {
              id: 'pdf',
              label: `PDF ofuscados (${pdfFiles.length})`,
              content: renderTable(pdfFiles, 'PDF', 'green'),
            },
            {
              id: 'md',
              label: `Informes Markdown (${mdFiles.length})`,
              content: renderTable(mdFiles, 'Markdown', 'blue'),
            },
          ]}
        />
      </Container>

      <Modal
        visible={toDelete !== null}
        onDismiss={() => setToDelete(null)}
        header="Eliminar archivo"
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
            ¿Seguro que desea eliminar “{toDelete.name}”? Esta acción no se puede deshacer.
          </Box>
        )}
      </Modal>

      <Modal
        visible={showPurgeConfirm}
        onDismiss={() => setShowPurgeConfirm(false)}
        header="Borrar todos los archivos ofuscados"
        footer={
          <Box float="right">
            <SpaceBetween size="xs" direction="horizontal">
              <Button variant="link" onClick={() => setShowPurgeConfirm(false)} disabled={purging}>
                Cancelar
              </Button>
              <Button variant="primary" onClick={handlePurgeObfuscated} loading={purging}>
                Borrar todo
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="s">
          <Box>
            Se eliminarán <b>todos</b> los PDF ofuscados e informes Markdown de tu
            usuario (carpeta <code>ofuscados/</code>) y los artefactos intermedios.
          </Box>
          <Box color="text-status-info">
            Los documentos <b>originales</b> y el registro de auditoría se conservan.
            Esta acción no se puede deshacer.
          </Box>
        </SpaceBetween>
      </Modal>
    </SpaceBetween>
  );
};

export default OutputPage;
