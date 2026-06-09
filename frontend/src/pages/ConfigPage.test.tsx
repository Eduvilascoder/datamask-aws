import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import ConfigPage, { validateDetectionConfig } from './ConfigPage';

// Mock authApi
vi.mock('../services/authApi', () => ({
  default: {
    get: vi.fn(),
    put: vi.fn(),
  },
}));

import authApi from '../services/authApi';

const mockedGet = vi.mocked(authApi.get);
const mockedPut = vi.mocked(authApi.put);

/** Respuesta por defecto del endpoint /config/detection. */
const DETECTION_RESPONSE = {
  data: {
    config: {
      detectionMethod: 'both',
      macieVerification: false,
      bedrockModelId: 'anthropic.claude-3-haiku-20240307-v1:0',
      bedrockTemperature: 0,
      bedrockPrompt: 'Detectá PII en: {text}',
      regexRules: [{ type: 'DNI', pattern: '\\d+', enabled: true }],
      ignoreEntities: [],
    },
    availableModels: [
      { id: 'anthropic.claude-3-haiku-20240307-v1:0', label: 'Claude 3 Haiku', provider: 'Anthropic' },
    ],
  },
};

/** Configura el mock de GET para devolver la config de detección. */
function mockGet() {
  mockedGet.mockImplementation((url: string) => {
    if (url === '/config/detection') {
      return Promise.resolve(DETECTION_RESPONSE);
    }
    return Promise.reject(new Error(`Unexpected URL: ${url}`));
  });
}

describe('validateDetectionConfig', () => {
  const base = {
    detectionMethod: 'both' as const,
    macieVerification: false,
    bedrockModelId: 'anthropic.claude-3-haiku-20240307-v1:0',
    bedrockTemperature: 0,
    bedrockPrompt: 'Detectá PII en: {text}',
    regexRules: [{ type: 'DNI', pattern: '\\d+', enabled: true }],
    ignoreEntities: [],
  };

  it('returns null when config is valid', () => {
    expect(validateDetectionConfig(base)).toBeNull();
  });

  it('returns error when no model is selected', () => {
    expect(validateDetectionConfig({ ...base, bedrockModelId: '' })).toMatch(/modelo/i);
  });

  it('returns error when temperature is out of range', () => {
    expect(validateDetectionConfig({ ...base, bedrockTemperature: 2 })).toMatch(
      /temperatura/i
    );
  });

  it('returns error when prompt is missing the {text} marker', () => {
    expect(validateDetectionConfig({ ...base, bedrockPrompt: 'sin marcador' })).toMatch(
      /\{text\}/
    );
  });

  it('returns error when a regex rule is incomplete', () => {
    expect(
      validateDetectionConfig({
        ...base,
        regexRules: [{ type: '', pattern: '', enabled: true }],
      })
    ).toMatch(/regla regex/i);
  });
});

describe('ConfigPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows loading state while fetching config', () => {
    mockedGet.mockReturnValue(new Promise(() => {})); // Never resolves
    render(<ConfigPage />);
    expect(screen.getByText('Cargando configuración...')).toBeInTheDocument();
  });

  it('renders detection sections after loading', async () => {
    mockGet();
    render(<ConfigPage />);

    await waitFor(() => {
      expect(screen.queryByText('Cargando configuración...')).not.toBeInTheDocument();
    });

    expect(screen.getByText('Modelo de IA')).toBeInTheDocument();
    expect(screen.getByText('Prompt del modelo')).toBeInTheDocument();
    expect(screen.getByText('Reglas regex deterministas')).toBeInTheDocument();
    expect(screen.getByText('Entidades a ignorar')).toBeInTheDocument();
  });

  it('calls GET /config/detection on mount', async () => {
    mockGet();
    render(<ConfigPage />);

    await waitFor(() => {
      expect(mockedGet).toHaveBeenCalledWith('/config/detection');
    });
  });

  it('calls PUT /config/detection and shows success notification on save', async () => {
    mockGet();
    mockedPut.mockResolvedValue({ data: {} });

    render(<ConfigPage />);

    await waitFor(() => {
      expect(screen.queryByText('Cargando configuración...')).not.toBeInTheDocument();
    });

    const saveButton = screen.getByText('Guardar configuración');
    fireEvent.click(saveButton);

    await waitFor(() => {
      expect(screen.getByText('Configuración guardada correctamente.')).toBeInTheDocument();
    });

    expect(mockedPut).toHaveBeenCalledWith(
      '/config/detection',
      expect.objectContaining({
        bedrockModelId: 'anthropic.claude-3-haiku-20240307-v1:0',
      })
    );
  });

  it('shows error notification when save fails', async () => {
    mockGet();
    mockedPut.mockRejectedValue(new Error('Server error'));

    render(<ConfigPage />);

    await waitFor(() => {
      expect(screen.queryByText('Cargando configuración...')).not.toBeInTheDocument();
    });

    const saveButton = screen.getByText('Guardar configuración');
    fireEvent.click(saveButton);

    await waitFor(() => {
      expect(screen.getByText('La configuración no pudo ser guardada.')).toBeInTheDocument();
    });
  });
});
