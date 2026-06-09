import React, { useState, useEffect, useRef, useCallback } from 'react';
import axios, { type AxiosProgressEvent } from 'axios';
import {
  Container,
  Header,
  SpaceBetween,
  Button,
  Table,
  Box,
  Flashbar,
  StatusIndicator,
  Icon,
  ProgressBar,
  ColumnLayout,
  Badge,
  Pagination,
  TextFilter,
  BreadcrumbGroup,
  Modal,
  type BreadcrumbGroupProps,
} from '@cloudscape-design/components';
import authApi from '../services/authApi';
import { useProcessing, type ProcessingDoc } from '../context/ProcessingContext';

/* ─── Tipos ─────────────────────────────────────────────────────────── */

interface S3Object {
  key: string;
  name: string;
  size: number;
  lastModified: string;
  isFolder: boolean;
}

interface S3ListResponse {
  bucket: string;
  prefix: string;
  objects: S3Object[];
  folders: string[];
}

/** Item de respuesta de /upload/presign. */
interface PresignUpload {
  uploadUrl: string;
  documentId: string;
  fileName: string;
  s3Key: string;
}

type UploadStatus = 'pending' | 'uploading' | 'success' | 'error';

interface FileUploadState {
  file: File;
  status: UploadStatus;
  progress: number;
  errorMessage?: string;
}

/* ─── Constantes ────────────────────────────────────────────────────── */

const MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024; // 50 MB
const ALLOWED_EXTENSION = '.pdf';
const ALLOWED_MIME = 'application/pdf';
const PAGE_SIZE = 20;

/* ─── Utilidades ────────────────────────────────────────────────────── */

function formatBytes(bytes: number): string {
  if (bytes === 0) return '—';
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return `${(bytes / Math.pow(1024, i)).toFixed(1)} ${sizes[i]}`;
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

/** Formatea una duración en milisegundos a "mm:ss" o "ss.s s". */
function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms} ms`;
  const totalSeconds = ms / 1000;
  if (totalSeconds < 60) return `${totalSeconds.toFixed(1)} s`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = Math.round(totalSeconds % 60);
  return `${minutes}m ${String(seconds).padStart(2, '0')}s`;
}

/** Deriva el nombre del documento (documentId) desde una key S3. */
function documentIdFromKey(key: string): string {
  return key.split('/').pop() || key;
}

/* ─── Componente ────────────────────────────────────────────────────── */

const S3BrowserPage: React.FC = () => {
  const [objects, setObjects] = useState<S3Object[]>([]);
  const [bucketName, setBucketName] = useState<string>('');
  const [currentPrefix, setCurrentPrefix] = useState<string>('originales/');
  const [isLoading, setIsLoading] = useState(false);
  const [filterText, setFilterText] = useState('');
  const [currentPage, setCurrentPage] = useState(1);
  const [flashMessages, setFlashMessages] = useState<
    Array<{ type: 'success' | 'error' | 'info' | 'warning'; content: string; id: string; dismissible: boolean }>
  >([]);

  // Upload state
  const [uploadStates, setUploadStates] = useState<FileUploadState[]>([]);
  const [isUploading, setIsUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Selección y procesamiento
  const [selectedItems, setSelectedItems] = useState<S3Object[]>([]);
  // Estado de procesamiento global (persiste al navegar / refrescar).
  const {
    docs: processingDocs,
    isProcessing,
    startedAt: processingStartedAt,
    elapsedMs,
    startTracking,
    clear: clearProcessing,
  } = useProcessing();

  // Purga de archivos procesados/ofuscados
  const [isPurging, setIsPurging] = useState(false);
  const [showPurgeConfirm, setShowPurgeConfirm] = useState(false);
  // Borrado individual de un documento a enmascarar
  const [toDelete, setToDelete] = useState<S3Object | null>(null);
  const [deletingOne, setDeletingOne] = useState(false);

  /* ─── Cargar objetos del bucket ─────────────────────────────────── */

  const loadObjects = useCallback(async (prefix: string) => {
    setIsLoading(true);
    try {
      const response = await authApi.get('/documents/s3/list', {
        params: { prefix },
      });
      const data: S3ListResponse = response.data;
      setBucketName(data.bucket);

      // El backend ya devuelve 'name' (nombre de archivo) y listado recursivo.
      const fileObjects: S3Object[] = data.objects.map((obj) => ({
        ...obj,
        // Mostrar la ruta relativa al prefijo para dar contexto del anidamiento
        name: obj.name || obj.key.split('/').pop() || obj.key,
        isFolder: false,
      }));

      setObjects(fileObjects);
    } catch (err) {
      setFlashMessages([
        {
          type: 'error',
          content: 'Error al cargar los documentos del bucket.',
          id: `error-${Date.now()}`,
          dismissible: true,
        },
      ]);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadObjects(currentPrefix);
  }, [currentPrefix, loadObjects]);

  /* ─── Navegación de carpetas ────────────────────────────────────── */

  const navigateToFolder = (folderKey: string) => {
    setCurrentPrefix(folderKey);
    setCurrentPage(1);
    setFilterText('');
  };

  const buildBreadcrumbs = (): BreadcrumbGroupProps.Item[] => {
    const parts = currentPrefix.split('/').filter(Boolean);
    const items: BreadcrumbGroupProps.Item[] = [
      { text: bucketName || 'Bucket', href: '#' },
    ];
    let accumulated = '';
    for (const part of parts) {
      accumulated += `${part}/`;
      items.push({ text: part, href: accumulated });
    }
    return items;
  };

  /* ─── Upload ────────────────────────────────────────────────────── */

  const handleFileSelection = (event: React.ChangeEvent<HTMLInputElement>) => {
    const selectedFiles = event.target.files;
    if (!selectedFiles || selectedFiles.length === 0) return;

    const filesArray = Array.from(selectedFiles);
    const validFiles: FileUploadState[] = [];
    const errors: string[] = [];

    for (const file of filesArray) {
      const ext = file.name.toLowerCase().slice(file.name.lastIndexOf('.'));
      if (ext !== ALLOWED_EXTENSION) {
        errors.push(`${file.name}: solo se permiten archivos .pdf`);
        continue;
      }
      if (file.type !== ALLOWED_MIME && file.type !== '') {
        errors.push(`${file.name}: tipo MIME no válido`);
        continue;
      }
      if (file.size > MAX_FILE_SIZE_BYTES) {
        errors.push(`${file.name}: excede 50 MB`);
        continue;
      }
      validFiles.push({ file, status: 'pending', progress: 0 });
    }

    if (errors.length > 0) {
      setFlashMessages(
        errors.map((e, i) => ({
          type: 'error' as const,
          content: e,
          id: `val-${Date.now()}-${i}`,
          dismissible: true,
        }))
      );
    }

    if (validFiles.length > 0) {
      setUploadStates(validFiles);
    }

    // Reset file input
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  const handleUpload = async () => {
    // Capturar la lista de archivos al momento del click (evita closures sobre
    // estado mutable durante el flujo asíncrono).
    const filesToUpload = uploadStates.map((fs) => fs.file);
    if (filesToUpload.length === 0 || isUploading) return;
    setIsUploading(true);

    setUploadStates((prev) =>
      prev.map((fs) => ({ ...fs, status: 'uploading' as UploadStatus, progress: 0 }))
    );

    // Actualiza el estado de un archivo identificándolo por su File (referencia
    // estable), no por índice.
    const updateFileState = (file: File, patch: Partial<FileUploadState>) => {
      setUploadStates((prev) =>
        prev.map((fs) => (fs.file === file ? { ...fs, ...patch } : fs))
      );
    };

    try {
      // Solicitar presigned URLs para exactamente los archivos capturados.
      const fileMetadata = filesToUpload.map((file) => ({
        fileName: file.name,
        fileSize: file.size,
        contentType: 'application/pdf',
      }));

      const presignResponse = await authApi.post('/upload/presign', { files: fileMetadata });
      const uploads: PresignUpload[] = presignResponse.data.uploads ?? [];

      if (uploads.length !== filesToUpload.length) {
        throw new Error('La cantidad de URLs no coincide con los archivos');
      }

      // El backend preserva el orden del array enviado, así que el upload[i]
      // corresponde al filesToUpload[i].
      const results = await Promise.allSettled(
        uploads.map(async (upload, index) => {
          const file = filesToUpload[index];

          await axios.put(upload.uploadUrl, file, {
            headers: {
              'Content-Type': 'application/pdf',
              'x-amz-server-side-encryption': 'aws:kms',
            },
            onUploadProgress: (progressEvent: AxiosProgressEvent) => {
              const percent = progressEvent.total
                ? Math.round((progressEvent.loaded * 100) / progressEvent.total)
                : 0;
              updateFileState(file, { progress: percent });
            },
          });

          // Registrar el documento con los metadatos reales del archivo.
          await authApi.post('/documents', {
            documentId: upload.documentId,
            fileName: upload.fileName,
            fileSize: file.size,
            s3Key: upload.s3Key,
          });

          updateFileState(file, { status: 'success', progress: 100 });
        })
      );

      const successes = results.filter((r) => r.status === 'fulfilled').length;
      const failures = results.filter((r) => r.status === 'rejected').length;

      results.forEach((result, index) => {
        if (result.status === 'rejected') {
          updateFileState(filesToUpload[index], {
            status: 'error',
            errorMessage: 'Error al subir',
          });
        }
      });

      if (successes > 0) {
        setFlashMessages((prev) => [
          ...prev,
          {
            type: 'success',
            content: `${successes} archivo(s) subido(s) correctamente.`,
            id: `success-${Date.now()}`,
            dismissible: true,
          },
        ]);
        // Refrescar lista
        setTimeout(() => loadObjects(currentPrefix), 1500);
      }

      if (failures > 0) {
        setFlashMessages((prev) => [
          ...prev,
          {
            type: 'error',
            content: `${failures} archivo(s) fallaron al subir.`,
            id: `fail-${Date.now()}`,
            dismissible: true,
          },
        ]);
      }
    } catch (err) {
      setFlashMessages([
        {
          type: 'error',
          content: 'Error al solicitar URLs de subida.',
          id: `presign-err-${Date.now()}`,
          dismissible: true,
        },
      ]);
    } finally {
      setIsUploading(false);
    }
  };

  const clearUploads = () => {
    setUploadStates([]);
  };

  /* ─── Procesar (ofuscar) documentos seleccionados ───────────────── */

  // Cuando el procesamiento global termina, refrescar el listado del bucket.
  const prevIsProcessing = useRef(isProcessing);
  useEffect(() => {
    if (prevIsProcessing.current && !isProcessing && processingDocs.length > 0) {
      loadObjects(currentPrefix);
    }
    prevIsProcessing.current = isProcessing;
  }, [isProcessing, processingDocs.length, loadObjects, currentPrefix]);

  const handleProcess = async () => {
    if (selectedItems.length === 0 || isProcessing) return;

    try {
      const keys = selectedItems.map((item) => item.key);
      const response = await authApi.post('/documents/process', { keys });

      const queued = response.data.queued?.length ?? 0;
      const errorList: Array<{ key: string; reason: string }> = response.data.errors ?? [];
      const errors = errorList.length;

      if (errors > 0) {
        const reasons = Array.from(new Set(errorList.map((e) => e.reason)));
        setFlashMessages((prev) => [
          ...prev,
          {
            type: queued === 0 ? 'error' : 'warning',
            content: `${errors} documento(s) no pudieron encolarse. ${reasons.join(' ')} Solo se procesan archivos de la carpeta "originales/" que aún no fueron procesados.`,
            id: `proc-err-${Date.now()}`,
            dismissible: true,
          },
        ]);
      }

      // Iniciar el seguimiento global SOLO de los documentos encolados.
      const queuedKeys: string[] = response.data.queued ?? [];
      if (queued > 0) {
        const tracked = queuedKeys.map((key) => ({
          documentId: documentIdFromKey(key),
          fileName: key.split('/').pop() || key,
        }));
        startTracking(tracked);
      }

      setSelectedItems([]);
    } catch {
      setFlashMessages((prev) => [
        ...prev,
        {
          type: 'error',
          content: 'Error al iniciar el procesamiento.',
          id: `proc-fail-${Date.now()}`,
          dismissible: true,
        },
      ]);
    }
  };

  /* ─── Purga de archivos procesados y ofuscados ──────────────────── */

  const handlePurgeProcessed = async () => {
    setIsPurging(true);
    try {
      const response = await authApi.post('/documents/purge', { scope: 'originals' });
      const orig = response.data.deletedOriginals ?? 0;
      setFlashMessages((prev) => [
        ...prev,
        {
          type: 'success',
          content: `Se eliminaron ${orig} documento(s) a enmascarar de la carpeta originales/. Los ofuscados y la auditoría se conservan.`,
          id: `purge-ok-${Date.now()}`,
          dismissible: true,
        },
      ]);
      loadObjects(currentPrefix);
    } catch {
      setFlashMessages((prev) => [
        ...prev,
        {
          type: 'error',
          content: 'No se pudieron eliminar los documentos a enmascarar.',
          id: `purge-err-${Date.now()}`,
          dismissible: true,
        },
      ]);
    } finally {
      setIsPurging(false);
      setShowPurgeConfirm(false);
    }
  };

  /** Abre el documento original en una pestaña nueva (presigned URL). */
  const handleView = async (item: S3Object) => {
    try {
      const response = await authApi.get('/documents/s3/download', {
        params: { key: item.key },
      });
      const url = response.data.url || response.data.downloadUrl;
      if (url) window.open(url, '_blank', 'noopener,noreferrer');
    } catch {
      setFlashMessages((prev) => [
        ...prev,
        {
          type: 'error',
          content: `No se pudo abrir "${item.name}".`,
          id: `view-err-${Date.now()}`,
          dismissible: true,
        },
      ]);
    }
  };

  /** Borra un documento original individual de S3. */
  const handleConfirmDeleteOne = async () => {
    if (!toDelete) return;
    setDeletingOne(true);
    try {
      await authApi.delete('/documents/s3/object', {
        params: { key: toDelete.key },
      });
      setFlashMessages((prev) => [
        ...prev,
        {
          type: 'success',
          content: `Se eliminó "${toDelete.name}".`,
          id: `del1-ok-${Date.now()}`,
          dismissible: true,
        },
      ]);
      setToDelete(null);
      loadObjects(currentPrefix);
    } catch {
      setFlashMessages((prev) => [
        ...prev,
        {
          type: 'error',
          content: `No se pudo eliminar "${toDelete.name}".`,
          id: `del1-err-${Date.now()}`,
          dismissible: true,
        },
      ]);
    } finally {
      setDeletingOne(false);
    }
  };

  /* ─── Filtrado y paginación ─────────────────────────────────────── */

  const filteredObjects = objects.filter((obj) =>
    obj.name.toLowerCase().includes(filterText.toLowerCase())
  );

  const totalPages = Math.ceil(filteredObjects.length / PAGE_SIZE);
  const paginatedObjects = filteredObjects.slice(
    (currentPage - 1) * PAGE_SIZE,
    currentPage * PAGE_SIZE
  );

  /* ─── Métricas de progreso del procesamiento ────────────────────── */

  const TERMINAL_LOCAL: ReadonlySet<string> = new Set(['COMPLETED', 'FAILED']);
  const finishedCount = processingDocs.filter((d) =>
    TERMINAL_LOCAL.has(d.status)
  ).length;
  const progressPercent =
    processingDocs.length > 0
      ? Math.round((finishedCount / processingDocs.length) * 100)
      : 0;

  /** Indicador de estado de detección por documento. */
  const renderDocStatus = (doc: ProcessingDoc) => {
    switch (doc.status) {
      case 'COMPLETED':
        return (
          <StatusIndicator type="success">
            Procesado
            {doc.entitiesFound !== undefined && doc.entitiesFound !== null
              ? ` — ${doc.entitiesFound} entidad(es)`
              : ''}
          </StatusIndicator>
        );
      case 'FAILED':
        return (
          <StatusIndicator type="error">
            {doc.errorMessage || 'Error'}
          </StatusIndicator>
        );
      case 'PROCESSING':
        return <StatusIndicator type="in-progress">Procesando...</StatusIndicator>;
      default:
        return <StatusIndicator type="pending">En cola</StatusIndicator>;
    }
  };

  /** Indicador del estado de creación del documento ofuscado en S3. */
  const renderRedactedStatus = (doc: ProcessingDoc) => {
    switch (doc.redactedStatus) {
      case 'CREATED':
        return <StatusIndicator type="success">Creado en S3</StatusIndicator>;
      case 'CREATING':
        return <StatusIndicator type="in-progress">Generando...</StatusIndicator>;
      case 'NONE':
        return (
          <StatusIndicator type="stopped">
            {doc.status === 'FAILED' ? 'No generado' : 'Sin entidades'}
          </StatusIndicator>
        );
      default:
        return <StatusIndicator type="pending">En espera</StatusIndicator>;
    }
  };

  /* ─── Render ────────────────────────────────────────────────────── */

  return (
    <SpaceBetween size="l">
      {flashMessages.length > 0 && (
        <Flashbar
          items={flashMessages.map((msg) => ({
            ...msg,
            onDismiss: () =>
              setFlashMessages((prev) => prev.filter((m) => m.id !== msg.id)),
          }))}
        />
      )}

      {/* Info del bucket */}
      <Container
        header={<Header variant="h2">Documentos a enmascarar</Header>}
      >
        <ColumnLayout columns={3} variant="text-grid">
          <div>
            <Box variant="awsui-key-label">Bucket</Box>
            <Box variant="p">
              {bucketName ? (
                <Badge color="blue">{bucketName}</Badge>
              ) : (
                <StatusIndicator type="loading">Cargando...</StatusIndicator>
              )}
            </Box>
          </div>
          <div>
            <Box variant="awsui-key-label">Carpeta actual</Box>
            <Box variant="p">
              <code>{currentPrefix}</code>
            </Box>
          </div>
          <div>
            <Box variant="awsui-key-label">Objetos en vista</Box>
            <Box variant="p">{objects.length}</Box>
          </div>
        </ColumnLayout>
      </Container>

      {/* Breadcrumb de navegación */}
      <BreadcrumbGroup
        items={buildBreadcrumbs()}
        onFollow={(event) => {
          event.preventDefault();
          const href = event.detail.href;
          if (href === '#') {
            setCurrentPrefix('');
          } else {
            setCurrentPrefix(href);
          }
          setCurrentPage(1);
        }}
      />

      {/* Panel de progreso del procesamiento (ofuscación) */}
      {processingDocs.length > 0 && (
        <Container
          header={
            <Header
              variant="h3"
              description="Seguimiento en tiempo real de la ofuscación. Se mantiene aunque cambies de sección."
              actions={
                <SpaceBetween direction="horizontal" size="s" alignItems="center">
                  <Box variant="awsui-value-large" textAlign="right">
                    <Icon name="status-in-progress" />{' '}
                    {formatDuration(elapsedMs)}
                  </Box>
                  {!isProcessing && (
                    <Button iconName="close" onClick={clearProcessing}>
                      Cerrar
                    </Button>
                  )}
                </SpaceBetween>
              }
            >
              {isProcessing ? 'Procesando documentos...' : 'Procesamiento finalizado'}
            </Header>
          }
        >
          <SpaceBetween size="m">
            <ProgressBar
              value={progressPercent}
              status={isProcessing ? 'in-progress' : 'success'}
              label={`${finishedCount} de ${processingDocs.length} documento(s)`}
              description={
                isProcessing
                  ? `Tiempo transcurrido: ${formatDuration(elapsedMs)}`
                  : `Tiempo total: ${formatDuration(elapsedMs)}`
              }
              additionalInfo={
                processingStartedAt
                  ? `Iniciado a las ${formatDate(new Date(processingStartedAt).toISOString())}`
                  : undefined
              }
            />

            <Table
              variant="embedded"
              trackBy="documentId"
              items={processingDocs}
              columnDefinitions={[
                {
                  id: 'fileName',
                  header: 'Documento',
                  minWidth: 300,
                  cell: (item: ProcessingDoc) => (
                    <span>
                      <Icon name="file" /> {item.fileName}
                    </span>
                  ),
                },
                {
                  id: 'time',
                  header: 'Tiempo',
                  width: 130,
                  cell: (item: ProcessingDoc) =>
                    item.processingTimeMs !== undefined &&
                    item.processingTimeMs !== null
                      ? formatDuration(item.processingTimeMs)
                      : '—',
                },
                {
                  id: 'status',
                  header: 'Estado detección',
                  minWidth: 200,
                  cell: renderDocStatus,
                },
                {
                  id: 'redacted',
                  header: 'Doc Ofuscado',
                  minWidth: 180,
                  cell: renderRedactedStatus,
                },
              ]}
            />
          </SpaceBetween>
        </Container>
      )}

      {/* Zona de upload */}
      <Container
        header={
          <Header
            variant="h3"
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button
                  iconName="upload"
                  onClick={() => fileInputRef.current?.click()}
                  disabled={isUploading}
                >
                  Seleccionar PDF
                </Button>
                {uploadStates.length > 0 && (
                  <>
                    <Button
                      variant="primary"
                      onClick={handleUpload}
                      loading={isUploading}
                      disabled={uploadStates.length === 0}
                    >
                      Subir ({uploadStates.length})
                    </Button>
                    <Button onClick={clearUploads} disabled={isUploading}>
                      Limpiar
                    </Button>
                  </>
                )}
              </SpaceBetween>
            }
          >
            Subir nuevos documentos
          </Header>
        }
      >
        <input
          ref={fileInputRef}
          type="file"
          accept=".pdf,application/pdf"
          multiple
          style={{ display: 'none' }}
          onChange={handleFileSelection}
        />

        {uploadStates.length > 0 && (
          <SpaceBetween size="s">
            {uploadStates.map((us, idx) => (
              <div key={idx}>
                <SpaceBetween size="xxs">
                  <Box variant="small">
                    {us.file.name} ({formatBytes(us.file.size)})
                    {us.status === 'success' && (
                      <StatusIndicator type="success"> Subido</StatusIndicator>
                    )}
                    {us.status === 'error' && (
                      <StatusIndicator type="error">
                        {' '}
                        {us.errorMessage || 'Error'}
                      </StatusIndicator>
                    )}
                  </Box>
                  {us.status === 'uploading' && (
                    <ProgressBar value={us.progress} label="Subiendo..." />
                  )}
                </SpaceBetween>
              </div>
            ))}
          </SpaceBetween>
        )}

        {uploadStates.length === 0 && (
          <Box variant="p" color="text-body-secondary" textAlign="center" padding="s">
            Seleccione archivos PDF para subir al bucket (máx. 50 MB por archivo)
          </Box>
        )}
      </Container>

      {/* Tabla de objetos */}
      <Table
        header={
          <Header
            variant="h3"
            counter={
              selectedItems.length > 0
                ? `(${selectedItems.length}/${filteredObjects.length})`
                : `(${filteredObjects.length})`
            }
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button
                  variant="primary"
                  iconName="gen-ai"
                  onClick={handleProcess}
                  loading={isProcessing}
                  disabled={selectedItems.length === 0}
                >
                  Procesar ({selectedItems.length})
                </Button>
                <Button iconName="refresh" onClick={() => loadObjects(currentPrefix)} loading={isLoading}>
                  Actualizar
                </Button>
                <Button
                  iconName="remove"
                  onClick={() => setShowPurgeConfirm(true)}
                  loading={isPurging}
                >
                  Borrar documentos a enmascarar
                </Button>
              </SpaceBetween>
            }
          >
            Contenido
          </Header>
        }
        items={paginatedObjects}
        selectionType="multi"
        selectedItems={selectedItems}
        onSelectionChange={({ detail }) => setSelectedItems(detail.selectedItems)}
        isItemDisabled={(item) => item.isFolder}
        ariaLabels={{
          selectionGroupLabel: 'Selección de documentos',
          allItemsSelectionLabel: () => 'Seleccionar todos',
          itemSelectionLabel: (_sel, item) => item.name,
        }}
        loading={isLoading}
        loadingText="Cargando objetos..."
        empty={
          <Box textAlign="center" padding="l">
            <Box variant="p" color="text-body-secondary">
              No hay objetos en esta ubicación. Subí documentos desde la sección
              “Subir nuevos documentos”.
            </Box>
          </Box>
        }
        filter={
          <TextFilter
            filteringText={filterText}
            filteringPlaceholder="Buscar por nombre..."
            onChange={({ detail }) => {
              setFilterText(detail.filteringText);
              setCurrentPage(1);
            }}
          />
        }
        pagination={
          totalPages > 1 ? (
            <Pagination
              currentPageIndex={currentPage}
              pagesCount={totalPages}
              onChange={({ detail }) => setCurrentPage(detail.currentPageIndex)}
            />
          ) : undefined
        }
        columnDefinitions={[
          {
            id: 'name',
            header: 'Nombre',
            minWidth: 400,
            cell: (item) =>
              item.isFolder ? (
                <Button variant="link" onClick={() => navigateToFolder(item.key)}>
                  <Icon name="folder" /> {item.name}/
                </Button>
              ) : (
                <span>
                  <Icon name="file" /> {item.name}
                </span>
              ),
            sortingField: 'name',
          },
          {
            id: 'size',
            header: 'Tamaño',
            cell: (item) => (item.isFolder ? '—' : formatBytes(item.size)),
            sortingField: 'size',
          },
          {
            id: 'lastModified',
            header: 'Última modificación',
            cell: (item) => (item.isFolder ? '—' : formatDate(item.lastModified)),
            sortingField: 'lastModified',
          },
          {
            id: 'actions',
            header: 'Acciones',
            width: 160,
            cell: (item) =>
              item.isFolder ? (
                '—'
              ) : (
                <SpaceBetween size="xs" direction="horizontal">
                  <Button
                    variant="inline-link"
                    iconName="external"
                    onClick={() => handleView(item)}
                  >
                    Ver
                  </Button>
                  <Button
                    variant="icon"
                    iconName="remove"
                    ariaLabel={`Eliminar ${item.name}`}
                    onClick={() => setToDelete(item)}
                  />
                </SpaceBetween>
              ),
          },
        ]}
        sortingDisabled={false}
        variant="container"
        trackBy="key"
      />

      <Modal
        visible={showPurgeConfirm}
        onDismiss={() => setShowPurgeConfirm(false)}
        header="Borrar documentos a enmascarar"
        footer={
          <Box float="right">
            <SpaceBetween size="xs" direction="horizontal">
              <Button variant="link" onClick={() => setShowPurgeConfirm(false)} disabled={isPurging}>
                Cancelar
              </Button>
              <Button variant="primary" onClick={handlePurgeProcessed} loading={isPurging}>
                Borrar todo
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="s">
          <Box>
            Se eliminarán <b>todos</b> los documentos originales de la carpeta{' '}
            <code>originales/</code> de tu usuario (los que subiste para enmascarar).
          </Box>
          <Box color="text-status-info">
            Los archivos <b>ofuscados</b> y el registro de auditoría se conservan.
            Esta acción no se puede deshacer.
          </Box>
        </SpaceBetween>
      </Modal>

      <Modal
        visible={toDelete !== null}
        onDismiss={() => setToDelete(null)}
        header="Eliminar documento"
        footer={
          <Box float="right">
            <SpaceBetween size="xs" direction="horizontal">
              <Button variant="link" onClick={() => setToDelete(null)} disabled={deletingOne}>
                Cancelar
              </Button>
              <Button variant="primary" onClick={handleConfirmDeleteOne} loading={deletingOne}>
                Eliminar
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        {toDelete && (
          <Box>
            ¿Seguro que desea eliminar “{toDelete.name}”? Se borrará de la carpeta
            originales/. Esta acción no se puede deshacer.
          </Box>
        )}
      </Modal>
    </SpaceBetween>
  );
};

export default S3BrowserPage;
