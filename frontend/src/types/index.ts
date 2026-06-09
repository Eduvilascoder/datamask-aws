/** Tipos de datos sensibles detectables por el sistema. */
export enum SensitiveDataType {
  NOMBRE = 'NOMBRE',
  EMAIL = 'EMAIL',
  CELULAR = 'CELULAR',
  TELEFONO = 'TELEFONO',
  DIRECCION = 'DIRECCION',
  TARJETA_CREDITO = 'TARJETA_CREDITO',
  CUENTA_BANCARIA = 'CUENTA_BANCARIA',
  DNI = 'DNI',
  CUIT_CUIL = 'CUIT_CUIL',
  PASAPORTE = 'PASAPORTE',
}

/** Configuración de tipos de datos sensibles a detectar. */
export interface TypeConfig {
  nombre: boolean;
  email: boolean;
  celular: boolean;
  telefono: boolean;
  direccion: boolean;
  tarjeta_credito: boolean;
  cuenta_bancaria: boolean;
  dni: boolean;
  cuit_cuil: boolean;
  pasaporte: boolean;
}

/** Información básica de un archivo PDF encontrado. */
export interface FileInfo {
  name: string;
  size_bytes: number;
  path: string;
}

/** Respuesta de validación de carpeta. */
export interface FolderValidationResponse {
  valid: boolean;
  files: FileInfo[];
  error: string | null;
}

/** Resultado del procesamiento de un archivo PDF. */
export interface ProcessingResult {
  input_file: string;
  output_file: string;
  success: boolean;
  entities_found: number;
  entities_by_type: Record<string, number>;
  error: string | null;
  processing_time_ms: number;
}

/** Evento de progreso recibido por SSE. */
export interface ProgressEvent {
  type: 'progress' | 'complete' | 'error';
  current_file: string;
  current_index: number;
  total_files: number;
  percentage: number;
  result?: ProcessingResult;
  summary?: ProcessingSummary;
}

/** Resumen final del procesamiento. */
export interface ProcessingSummary {
  total_files: number;
  successful: number;
  failed: number;
  total_entities: number;
}

/** Estado de procesamiento de un documento en el pipeline AWS. */
export type DocumentStatus = 'UPLOADED' | 'PROCESSING' | 'COMPLETED' | 'FAILED';

/** Categoría de error para documentos FAILED. */
export type ErrorCategory =
  | 'error de lectura'
  | 'error de NER'
  | 'error de escritura'
  | 'error de servicio';

/** Registro de un documento gestionado por el pipeline. */
export interface DocumentRecord {
  documentId: string;
  fileName: string;
  fileSize: number;
  status: DocumentStatus;
  uploadedAt: string;
  completedAt: string | null;
  entitiesFound: number | null;
  entitiesByType: Record<string, number> | null;
  processingTimeMs: number | null;
  errorMessage: string | null;
  errorCategory: ErrorCategory | null;
}

/** Entrada del registro de auditoría. */
export interface AuditLogEntry {
  filename: string;
  file_size_bytes: number;
  os_user: string;
  timestamp: string;
  result: string;
  entities_detected: number;
  entities_by_type: Record<string, number>;
  error_detail: string | null;
  engine: string | null;
}

/** Respuesta paginada de logs. */
export interface LogsResponse {
  entries: AuditLogEntry[];
  total: number;
  page: number;
  page_size: number;
}
