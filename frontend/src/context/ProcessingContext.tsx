/**
 * ProcessingContext - Estado global del procesamiento (ofuscación).
 *
 * Vive por encima de las páginas para que el seguimiento del procesamiento
 * (barra de progreso, cronómetro, estado por documento) PERSISTA al navegar
 * entre secciones de la app y sobreviva a un refresh (se guarda en
 * localStorage). El polling de estado corre acá, no en la página.
 */

import React, {
  createContext,
  useContext,
  useState,
  useCallback,
  useRef,
  useEffect,
} from 'react';
import authApi from '../services/authApi';

/* ─── Tipos ─────────────────────────────────────────────────────────── */

export type ProcessingDocStatus = 'PENDING' | 'PROCESSING' | 'COMPLETED' | 'FAILED';

/** Estado de creación del documento ofuscado en el bucket destino. */
export type RedactedStatus = 'PENDING' | 'CREATING' | 'CREATED' | 'NONE';

export interface ProcessingDoc {
  documentId: string;
  fileName: string;
  status: ProcessingDocStatus;
  processingTimeMs?: number;
  entitiesFound?: number;
  errorMessage?: string;
  /** Estado de creación del PDF ofuscado en S3. */
  redactedStatus: RedactedStatus;
}

interface ApiDocument {
  documentId: string;
  fileName: string;
  status: string;
  processingTimeMs?: number;
  entitiesFound?: number;
  errorMessage?: string;
  s3KeyRedacted?: string | null;
}

interface ProcessingContextType {
  docs: ProcessingDoc[];
  isProcessing: boolean;
  startedAt: number | null;
  elapsedMs: number;
  /** Inicia el seguimiento de un conjunto de documentos encolados. */
  startTracking: (docs: { documentId: string; fileName: string }[]) => void;
  /** Limpia el panel de progreso. */
  clear: () => void;
}

/* ─── Constantes ────────────────────────────────────────────────────── */

const POLL_INTERVAL_MS = 3000;
const TIMER_TICK_MS = 1000;
const PROCESSING_TIMEOUT_MS = 10 * 60 * 1000;
const STORAGE_KEY = 'datamask_processing_state';
const TERMINAL: ReadonlySet<ProcessingDocStatus> = new Set(['COMPLETED', 'FAILED']);

const ProcessingContext = createContext<ProcessingContextType | null>(null);

/* ─── Utilidades ────────────────────────────────────────────────────── */

function normalizeStatus(raw: string): ProcessingDocStatus {
  const s = (raw || '').toUpperCase();
  if (s === 'COMPLETED') return 'COMPLETED';
  if (s === 'FAILED' || s === 'ERROR') return 'FAILED';
  if (s === 'PROCESSING') return 'PROCESSING';
  return 'PENDING';
}

/** Deriva el estado del doc ofuscado a partir del estado y la key S3. */
function deriveRedactedStatus(
  status: ProcessingDocStatus,
  s3KeyRedacted?: string | null,
  entitiesFound?: number
): RedactedStatus {
  if (status === 'COMPLETED') {
    if (s3KeyRedacted) return 'CREATED';
    // Completado sin archivo: no se generó (0 entidades).
    if (entitiesFound === 0) return 'NONE';
    return 'CREATED';
  }
  if (status === 'FAILED') return 'NONE';
  // En cola o procesando: el ofuscado aún se está creando.
  return status === 'PROCESSING' ? 'CREATING' : 'PENDING';
}

interface PersistedState {
  docs: ProcessingDoc[];
  isProcessing: boolean;
  startedAt: number | null;
}

function loadPersisted(): PersistedState | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as PersistedState) : null;
  } catch {
    return null;
  }
}

function persist(state: PersistedState): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    /* noop */
  }
}

/* ─── Provider ──────────────────────────────────────────────────────── */

export const ProcessingProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const persisted = loadPersisted();
  const [docs, setDocs] = useState<ProcessingDoc[]>(persisted?.docs ?? []);
  const [isProcessing, setIsProcessing] = useState<boolean>(persisted?.isProcessing ?? false);
  const [startedAt, setStartedAt] = useState<number | null>(persisted?.startedAt ?? null);
  const [elapsedMs, setElapsedMs] = useState(0);

  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const tickTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const trackedIds = useRef<Set<string>>(
    new Set((persisted?.docs ?? []).map((d) => d.documentId))
  );

  const stopTimers = useCallback(() => {
    if (pollTimer.current) {
      clearInterval(pollTimer.current);
      pollTimer.current = null;
    }
    if (tickTimer.current) {
      clearInterval(tickTimer.current);
      tickTimer.current = null;
    }
  }, []);

  const poll = useCallback(async () => {
    if (startedAt !== null && Date.now() - startedAt > PROCESSING_TIMEOUT_MS) {
      stopTimers();
      setIsProcessing(false);
      return;
    }
    try {
      const response = await authApi.get('/documents', { params: { limit: 100 } });
      const apiDocs: ApiDocument[] = response.data.documents || [];
      const byId = new Map(apiDocs.map((d) => [d.documentId, d]));

      setDocs((prev) =>
        prev.map((pd) => {
          const fresh = byId.get(pd.documentId);
          if (!fresh) return pd;
          const status = normalizeStatus(fresh.status);
          return {
            ...pd,
            status,
            processingTimeMs: fresh.processingTimeMs,
            entitiesFound: fresh.entitiesFound,
            errorMessage: fresh.errorMessage,
            redactedStatus: deriveRedactedStatus(
              status,
              fresh.s3KeyRedacted,
              fresh.entitiesFound
            ),
          };
        })
      );

      const allTerminal = Array.from(trackedIds.current).every((id) => {
        const fresh = byId.get(id);
        return fresh && TERMINAL.has(normalizeStatus(fresh.status));
      });
      if (allTerminal) {
        stopTimers();
        setIsProcessing(false);
      }
    } catch {
      /* error transitorio: reintenta en el próximo tick */
    }
  }, [startedAt, stopTimers]);

  // Mantener una ref al poll más reciente para los intervalos.
  const pollRef = useRef(poll);
  useEffect(() => {
    pollRef.current = poll;
  }, [poll]);

  /** Arranca/reanuda los timers de polling y cronómetro. */
  const runTimers = useCallback(() => {
    stopTimers();
    pollRef.current();
    pollTimer.current = setInterval(() => pollRef.current(), POLL_INTERVAL_MS);
    tickTimer.current = setInterval(() => {
      setStartedAt((s) => {
        if (s !== null) setElapsedMs(Date.now() - s);
        return s;
      });
    }, TIMER_TICK_MS);
  }, [stopTimers]);

  const startTracking = useCallback(
    (newDocs: { documentId: string; fileName: string }[]) => {
      const tracked: ProcessingDoc[] = newDocs.map((d) => ({
        documentId: d.documentId,
        fileName: d.fileName,
        status: 'PENDING',
        redactedStatus: 'PENDING',
      }));
      trackedIds.current = new Set(tracked.map((t) => t.documentId));
      const now = Date.now();
      setDocs(tracked);
      setIsProcessing(true);
      setStartedAt(now);
      setElapsedMs(0);
      runTimers();
    },
    [runTimers]
  );

  const clear = useCallback(() => {
    stopTimers();
    setDocs([]);
    setIsProcessing(false);
    setStartedAt(null);
    setElapsedMs(0);
    trackedIds.current = new Set();
    try {
      localStorage.removeItem(STORAGE_KEY);
    } catch {
      /* noop */
    }
  }, [stopTimers]);

  // Al montar: si había un procesamiento en curso (persistido), reanudar el polling.
  useEffect(() => {
    if (isProcessing && docs.length > 0) {
      runTimers();
    }
    return stopTimers;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Persistir el estado en cada cambio relevante.
  useEffect(() => {
    persist({ docs, isProcessing, startedAt });
  }, [docs, isProcessing, startedAt]);

  return (
    <ProcessingContext.Provider
      value={{ docs, isProcessing, startedAt, elapsedMs, startTracking, clear }}
    >
      {children}
    </ProcessingContext.Provider>
  );
};

export const useProcessing = (): ProcessingContextType => {
  const context = useContext(ProcessingContext);
  if (!context) throw new Error('useProcessing must be used within ProcessingProvider');
  return context;
};
