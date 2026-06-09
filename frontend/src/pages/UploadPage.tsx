import React, { useState, useRef } from 'react';
import axios, { type AxiosProgressEvent } from 'axios';
import {
  Container,
  Header,
  SpaceBetween,
  Button,
  Alert,
  ProgressBar,
  Table,
  StatusIndicator,
  Box,
  Flashbar,
  FormField,
} from '@cloudscape-design/components';
import authApi from '../services/authApi';

/* ─── Constantes de validación ──────────────────────────────────────── */

const MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024; // 50 MB
const MAX_FILES_PER_UPLOAD = 10;
const ALLOWED_EXTENSION = '.pdf';
const ALLOWED_MIME = 'application/pdf';

/* ─── Constantes de retry ───────────────────────────────────────────── */

const MAX_RETRIES = 3;
const INITIAL_BACKOFF_MS = 1000; // 1s, 2s, 4s

/* ─── Tipos ─────────────────────────────────────────────────────────── */

interface ValidationError {
  fileName: string;
  reason: string;
}

interface PresignedUpload {
  fileName: string;
  documentId: string;
  uploadUrl: string;
  s3Key: string;
}

type UploadStatus = 'pending' | 'uploading' | 'success' | 'error';

interface FileUploadState {
  file: File;
  status: UploadStatus;
  progress: number;
  errorMessage?: string;
  documentId?: string;
  s3Key?: string;
  retryCount: number;
}

/* ─── Utilidades ────────────────────────────────────────────────────── */

/**
 * Valida una lista de archivos contra las reglas de negocio.
 * Retorna un arreglo de errores; vacío si todo es válido.
 */
function validateFiles(files: File[]): ValidationError[] {
  const errors: ValidationError[] = [];

  if (files.length > MAX_FILES_PER_UPLOAD) {
    errors.push({
      fileName: '(selección)',
      reason: `Se permite un máximo de ${MAX_FILES_PER_UPLOAD} archivos por operación. Seleccionó ${files.length}.`,
    });
    return errors;
  }

  for (const file of files) {
    const extension = file.name.toLowerCase().slice(file.name.lastIndexOf('.'));
    if (extension !== ALLOWED_EXTENSION) {
      errors.push({
        fileName: file.name,
        reason: `Extensión no válida: "${extension}". Solo se permite .pdf`,
      });
      continue;
    }

    if (file.type !== ALLOWED_MIME) {
      errors.push({
        fileName: file.name,
        reason: `Tipo MIME no válido: "${file.type || 'desconocido'}". Se requiere application/pdf`,
      });
      continue;
    }

    if (file.size >= MAX_FILE_SIZE_BYTES) {
      const sizeMB = (file.size / (1024 * 1024)).toFixed(1);
      errors.push({
        fileName: file.name,
        reason: `Archivo demasiado grande: ${sizeMB} MB. El máximo es 50 MB.`,
      });
    }
  }

  return errors;
}

/**
 * Espera un tiempo determinado (para backoff exponencial).
 */
function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Determina si un error es de red (retryable).
 */
function isNetworkError(error: unknown): boolean {
  if (!axios.isAxiosError(error)) return false;
  // Sin respuesta del servidor = error de red/timeout
  if (!error.response) return true;
  // Errores de servidor (5xx) son retryable
  if (error.response.status >= 500) return true;
  return false;
}

/* ─── Componente ────────────────────────────────────────────────────── */

const UploadPage: React.FC = () => {
  const [fileStates, setFileStates] = useState<FileUploadState[]>([]);
  const [validationErrors, setValidationErrors] = useState<ValidationError[]>([]);
  const [isUploading, setIsUploading] = useState(false);
  const [flashMessages, setFlashMessages] = useState<
    Array<{
      type: 'success' | 'error' | 'info';
      content: string;
      id: string;
      dismissible: boolean;
    }>
  >([]);

  const fileInputRef = useRef<HTMLInputElement>(null);

  /**
   * Maneja la selección de archivos desde el input nativo.
   */
  const handleFileSelection = (event: React.ChangeEvent<HTMLInputElement>) => {
    const selectedFiles = event.target.files;
    if (!selectedFiles || selectedFiles.length === 0) return;

    const filesArray = Array.from(selectedFiles);

    // Validar archivos
    const errors = validateFiles(filesArray);
    setValidationErrors(errors);

    if (errors.length > 0) {
      setFileStates([]);
      return;
    }

    // Preparar estado de subida para cada archivo
    const states: FileUploadState[] = filesArray.map((file) => ({
      file,
      status: 'pending',
      progress: 0,
      retryCount: 0,
    }));

    setFileStates(states);
    setFlashMessages([]);
  };

  /**
   * Sube un archivo individual a S3 con retry automático.
   */
  const uploadFileToS3 = async (
    file: File,
    uploadUrl: string,
    index: number
  ): Promise<void> => {
    let lastError: unknown = null;

    for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
      if (attempt > 0) {
        // Backoff exponencial: 1s, 2s, 4s
        const backoffMs = INITIAL_BACKOFF_MS * Math.pow(2, attempt - 1);
        await delay(backoffMs);

        setFileStates((prev) => {
          const updated = [...prev];
          updated[index] = {
            ...updated[index],
            retryCount: attempt,
            status: 'uploading',
            progress: 0,
          };
          return updated;
        });
      }

      try {
        await axios.put(uploadUrl, file, {
          headers: {
            'Content-Type': 'application/pdf',
            'x-amz-server-side-encryption': 'aws:kms',
          },
          onUploadProgress: (progressEvent: AxiosProgressEvent) => {
            const percent = progressEvent.total
              ? Math.round((progressEvent.loaded * 100) / progressEvent.total)
              : 0;
            setFileStates((prev) => {
              const updated = [...prev];
              updated[index] = { ...updated[index], progress: percent };
              return updated;
            });
          },
        });
        return; // Éxito
      } catch (error) {
        lastError = error;
        if (!isNetworkError(error) || attempt === MAX_RETRIES) {
          throw lastError;
        }
      }
    }

    throw lastError;
  };

  /**
   * Registra un documento subido exitosamente en el backend.
   */
  const registerDocument = async (
    documentId: string,
    fileName: string,
    fileSize: number,
    s3Key: string
  ): Promise<void> => {
    await authApi.post('/documents', {
      documentId,
      fileName,
      fileSize,
      s3Key,
    });
  };

  /**
   * Ejecuta el flujo completo de subida:
   * 1. Solicitar presigned URLs
   * 2. Subir cada archivo a S3
   * 3. Registrar cada documento en el backend
   */
  const handleUpload = async () => {
    if (fileStates.length === 0) return;

    setIsUploading(true);
    setFlashMessages([]);

    // Marcar todos como uploading
    setFileStates((prev) =>
      prev.map((fs) => ({ ...fs, status: 'uploading' as UploadStatus, progress: 0 }))
    );

    try {
      // 1. Solicitar presigned URLs al backend
      const fileMetadata = fileStates.map((fs) => ({
        fileName: fs.file.name,
        fileSize: fs.file.size,
        contentType: 'application/pdf',
      }));

      const presignResponse = await authApi.post('/upload/presign', {
        files: fileMetadata,
      });

      const uploads: PresignedUpload[] = presignResponse.data.uploads;

      // 2. Subir cada archivo directamente a S3 y registrar
      const results = await Promise.allSettled(
        uploads.map(async (upload, index) => {
          const fileState = fileStates[index];

          // Guardar documentId y s3Key en el estado
          setFileStates((prev) => {
            const updated = [...prev];
            updated[index] = {
              ...updated[index],
              documentId: upload.documentId,
              s3Key: upload.s3Key,
            };
            return updated;
          });

          // Subir a S3 (con retry)
          await uploadFileToS3(fileState.file, upload.uploadUrl, index);

          // 3. Registrar documento en el backend
          await registerDocument(
            upload.documentId,
            upload.fileName,
            fileState.file.size,
            upload.s3Key
          );

          // Marcar éxito
          setFileStates((prev) => {
            const updated = [...prev];
            updated[index] = { ...updated[index], status: 'success', progress: 100 };
            return updated;
          });
        })
      );

      // Procesar resultados
      const failures = results.filter((r) => r.status === 'rejected');

      results.forEach((result, index) => {
        if (result.status === 'rejected') {
          const errorMsg = axios.isAxiosError(result.reason)
            ? result.reason.message
            : 'Error al subir el archivo';
          setFileStates((prev) => {
            const updated = [...prev];
            updated[index] = {
              ...updated[index],
              status: 'error',
              errorMessage: errorMsg,
            };
            return updated;
          });
        }
      });

      if (failures.length === 0) {
        setFlashMessages([
          {
            type: 'success',
            content: `${fileStates.length} archivo(s) subido(s) correctamente.`,
            id: 'upload-success',
            dismissible: true,
          },
        ]);
      } else if (failures.length < fileStates.length) {
        setFlashMessages([
          {
            type: 'info',
            content: `${fileStates.length - failures.length} archivo(s) subido(s). ${failures.length} fallaron.`,
            id: 'upload-partial',
            dismissible: true,
          },
        ]);
      } else {
        setFlashMessages([
          {
            type: 'error',
            content: 'No se pudo subir ningún archivo. Verifique su conexión e intente nuevamente.',
            id: 'upload-fail',
            dismissible: true,
          },
        ]);
      }
    } catch (error) {
      // Error al solicitar presigned URLs
      const errorMsg = axios.isAxiosError(error)
        ? error.response?.data?.message || error.message
        : 'No se pudo preparar la subida. Intente nuevamente.';

      setFlashMessages([
        {
          type: 'error',
          content: errorMsg,
          id: 'presign-fail',
          dismissible: true,
        },
      ]);

      setFileStates((prev) =>
        prev.map((fs) => ({
          ...fs,
          status: 'error' as UploadStatus,
          errorMessage: 'No se pudo obtener la URL de subida',
        }))
      );
    } finally {
      setIsUploading(false);
    }
  };

  /**
   * Reinicia el formulario para una nueva selección.
   */
  const handleReset = () => {
    setFileStates([]);
    setValidationErrors([]);
    setFlashMessages([]);
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
  };

  /**
   * Formatea bytes a una representación legible.
   */
  const formatFileSize = (bytes: number): string => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  /**
   * Renderiza el indicador de estado por archivo.
   */
  const renderStatus = (item: FileUploadState) => {
    switch (item.status) {
      case 'pending':
        return <StatusIndicator type="pending">Pendiente</StatusIndicator>;
      case 'uploading':
        return <StatusIndicator type="in-progress">Subiendo...</StatusIndicator>;
      case 'success':
        return (
          <StatusIndicator type="success">
            Subido — pendiente de procesamiento
          </StatusIndicator>
        );
      case 'error':
        return (
          <StatusIndicator type="error">
            {item.errorMessage || 'Error'}
          </StatusIndicator>
        );
    }
  };

  const hasFilesReady = fileStates.length > 0 && fileStates.some((f) => f.status === 'pending');
  const allDone = fileStates.length > 0 && fileStates.every((f) => f.status === 'success' || f.status === 'error');

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

      <Container
        header={
          <Header
            variant="h1"
            description="Seleccione archivos PDF para subir al sistema de procesamiento. Los archivos serán analizados automáticamente para detectar y ofuscar datos sensibles."
            actions={
              <SpaceBetween size="xs" direction="horizontal">
                {allDone && (
                  <Button onClick={handleReset}>Nueva subida</Button>
                )}
                <Button
                  variant="primary"
                  onClick={handleUpload}
                  disabled={!hasFilesReady || isUploading}
                  loading={isUploading}
                >
                  {isUploading ? 'Subiendo...' : 'Subir archivos'}
                </Button>
              </SpaceBetween>
            }
          >
            Subir documentos PDF
          </Header>
        }
      >
        <SpaceBetween size="m">
          {/* Selector de archivos */}
          <FormField
            label="Seleccionar archivos"
            description={`Formato: PDF (.pdf) — Tamaño máximo: 50 MB por archivo — Máximo ${MAX_FILES_PER_UPLOAD} archivos por operación`}
          >
            <input
              ref={fileInputRef}
              type="file"
              accept=".pdf,application/pdf"
              multiple
              onChange={handleFileSelection}
              disabled={isUploading}
              style={{
                padding: '8px',
                border: '1px solid #aab7b8',
                borderRadius: '4px',
                width: '100%',
              }}
            />
          </FormField>

          {/* Errores de validación */}
          {validationErrors.length > 0 && (
            <Alert type="error" header="Errores de validación">
              <ul style={{ margin: 0, paddingLeft: '20px' }}>
                {validationErrors.map((err, idx) => (
                  <li key={idx}>
                    <strong>{err.fileName}</strong>: {err.reason}
                  </li>
                ))}
              </ul>
            </Alert>
          )}
        </SpaceBetween>
      </Container>

      {/* Tabla de archivos con progreso */}
      {fileStates.length > 0 && (
        <Container
          header={
            <Header variant="h2">
              Archivos seleccionados ({fileStates.length})
            </Header>
          }
        >
          <Table
            items={fileStates}
            columnDefinitions={[
              {
                id: 'name',
                header: 'Archivo',
                cell: (item) => item.file.name,
                width: 250,
              },
              {
                id: 'size',
                header: 'Tamaño',
                cell: (item) => formatFileSize(item.file.size),
                width: 100,
              },
              {
                id: 'progress',
                header: 'Progreso',
                cell: (item) => {
                  if (item.status === 'uploading') {
                    return (
                      <ProgressBar
                        value={item.progress}
                        status="in-progress"
                        additionalInfo={
                          item.retryCount > 0
                            ? `Reintento ${item.retryCount}/${MAX_RETRIES}`
                            : undefined
                        }
                      />
                    );
                  }
                  if (item.status === 'success') {
                    return <ProgressBar value={100} status="success" />;
                  }
                  if (item.status === 'error') {
                    return <ProgressBar value={item.progress} status="error" />;
                  }
                  return <Box color="text-body-secondary">—</Box>;
                },
                width: 250,
              },
              {
                id: 'status',
                header: 'Estado',
                cell: (item) => renderStatus(item),
                width: 280,
              },
            ]}
            variant="embedded"
            empty={
              <Box textAlign="center" padding="l">
                No hay archivos seleccionados
              </Box>
            }
          />
        </Container>
      )}

      {/* Información adicional */}
      {fileStates.length === 0 && validationErrors.length === 0 && (
        <Alert type="info">
          Seleccione uno o más archivos PDF para comenzar. Los archivos serán subidos de forma
          segura y procesados automáticamente para detectar datos personales sensibles.
        </Alert>
      )}
    </SpaceBetween>
  );
};

export default UploadPage;
