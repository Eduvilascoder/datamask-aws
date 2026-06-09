/**
 * AuthContext - Autenticación con Amazon Cognito federado con AWS IAM
 * Identity Center (SAML).
 *
 * Cognito actúa como Service Provider SAML; Identity Center es el IdP (que
 * puede federar con Active Directory u otro IdP corporativo). El usuario hace
 * login en la Hosted UI de Cognito → Identity Center → vuelve directo a la app
 * (sin pantalla de confirmación de código). El id token (JWT) autoriza el API.
 *
 * Máquina de estados:
 *   idle → redirecting → (callback) → exchanging → authenticated | error
 */

import React, {
  createContext,
  useContext,
  useState,
  useCallback,
  useEffect,
  useRef,
} from 'react';
import {
  CognitoAuthService,
  loadCognitoConfig,
  type CognitoTokens,
  type CognitoUser,
} from '../services/cognitoAuth';
import {
  loadSession,
  saveSession,
  clearSession,
  updateStoredTokens,
  areTokensValid,
} from '../services/cognitoSessionStorage';

export interface AuthUser {
  username: string;
  email: string;
}

export interface AuthState {
  isAuthenticated: boolean;
  user: AuthUser | null;
  isLoading: boolean;
  error: string | null;
}

/** Estados del flujo de login con Cognito. */
export type AuthFlowState =
  | 'idle'
  | 'redirecting'
  | 'exchanging'
  | 'authenticated'
  | 'error';

export interface AuthContextType {
  state: AuthState;
  flowState: AuthFlowState;
  /** Indica si falta configuración de Cognito (despliegue incompleto). */
  configMissing: boolean;
  login: () => Promise<void>;
  logout: () => void;
  clearError: () => void;
  /** Devuelve un id token válido (renovándolo si hace falta). */
  getIdToken: () => Promise<string | null>;
}

const AuthContext = createContext<AuthContextType | null>(null);

/** Umbral para renovar el token antes de expirar (5 minutos). */
const REFRESH_THRESHOLD_MS = 5 * 60 * 1000;
/** Frecuencia de chequeo de expiración. */
const REFRESH_CHECK_INTERVAL_MS = 60 * 1000;

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const config = loadCognitoConfig();
  const serviceRef = useRef<CognitoAuthService | null>(
    config ? new CognitoAuthService(config) : null
  );

  const [state, setState] = useState<AuthState>({
    isAuthenticated: false,
    user: null,
    isLoading: true,
    error: null,
  });
  const [flowState, setFlowState] = useState<AuthFlowState>('idle');

  const tokensRef = useRef<CognitoTokens | null>(null);

  const setError = useCallback((message: string) => {
    setFlowState('error');
    setState((prev) => ({ ...prev, isLoading: false, error: message }));
  }, []);

  const finalizeLogin = useCallback((tokens: CognitoTokens, user: CognitoUser) => {
    tokensRef.current = tokens;
    saveSession({ tokens, user });
    setFlowState('authenticated');
    setState({
      isAuthenticated: true,
      user: { username: user.username, email: user.email },
      isLoading: false,
      error: null,
    });
  }, []);

  const login = useCallback(async () => {
    const service = serviceRef.current;
    if (!service) {
      setError('La autenticación no está configurada (falta Cognito).');
      return;
    }
    setState((prev) => ({ ...prev, error: null, isLoading: true }));
    setFlowState('redirecting');
    try {
      await service.login();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'No se pudo iniciar sesión');
    }
  }, [setError]);

  const logout = useCallback(() => {
    clearSession();
    tokensRef.current = null;
    setFlowState('idle');
    setState({ isAuthenticated: false, user: null, isLoading: false, error: null });
    const service = serviceRef.current;
    if (service) {
      service.logout(); // redirige a la Hosted UI de logout de Cognito
    }
  }, []);

  const clearError = useCallback(() => {
    setState((prev) => ({ ...prev, error: null }));
    if (flowState === 'error') setFlowState('idle');
  }, [flowState]);

  const getIdToken = useCallback(async (): Promise<string | null> => {
    const service = serviceRef.current;
    const tokens = tokensRef.current;
    if (!service || !tokens) return null;

    if (areTokensValid(tokens)) {
      return tokens.idToken;
    }
    // Intentar renovar con el refresh token.
    if (tokens.refreshToken) {
      try {
        const refreshed = await service.refreshTokens(tokens.refreshToken);
        tokensRef.current = refreshed;
        updateStoredTokens(refreshed);
        return refreshed.idToken;
      } catch {
        return null;
      }
    }
    return null;
  }, []);

  // Al montar: procesar callback de Cognito o restaurar sesión.
  useEffect(() => {
    const service = serviceRef.current;
    if (!service) {
      // Sin configuración de Cognito: no se puede autenticar.
      setState((prev) => ({ ...prev, isLoading: false }));
      return;
    }

    // 1. ¿Volvimos del login de Cognito?
    if (service.hasAuthCallback()) {
      setFlowState('exchanging');
      setState((prev) => ({ ...prev, isLoading: true }));
      (async () => {
        try {
          const tokens = await service.completeLogin();
          if (!tokens) {
            setState((prev) => ({ ...prev, isLoading: false }));
            setFlowState('idle');
            return;
          }
          const user = service.getUser(tokens);
          finalizeLogin(tokens, user);
        } catch (err) {
          setError(err instanceof Error ? err.message : 'Error al completar el login');
        }
      })();
      return;
    }

    // 2. Restaurar sesión almacenada.
    const session = loadSession();
    if (!session) {
      setState((prev) => ({ ...prev, isLoading: false }));
      return;
    }

    if (areTokensValid(session.tokens)) {
      tokensRef.current = session.tokens;
      setFlowState('authenticated');
      setState({
        isAuthenticated: true,
        user: { username: session.user.username, email: session.user.email },
        isLoading: false,
        error: null,
      });
      return;
    }

    // Tokens expirados: intentar renovar en silencio.
    if (session.tokens.refreshToken) {
      (async () => {
        try {
          const refreshed = await service.refreshTokens(session.tokens.refreshToken!);
          tokensRef.current = refreshed;
          updateStoredTokens(refreshed);
          setFlowState('authenticated');
          setState({
            isAuthenticated: true,
            user: { username: session.user.username, email: session.user.email },
            isLoading: false,
            error: null,
          });
        } catch {
          clearSession();
          setState((prev) => ({ ...prev, isLoading: false }));
        }
      })();
      return;
    }

    clearSession();
    setState((prev) => ({ ...prev, isLoading: false }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Renovación automática del token antes de expirar.
  useEffect(() => {
    if (!state.isAuthenticated) return;

    const intervalId = setInterval(async () => {
      const tokens = tokensRef.current;
      const service = serviceRef.current;
      if (!tokens || !service) return;

      const timeLeft = tokens.expiresAt - Date.now();
      if (timeLeft > REFRESH_THRESHOLD_MS) return;

      if (!tokens.refreshToken) {
        logout();
        return;
      }
      try {
        const refreshed = await service.refreshTokens(tokens.refreshToken);
        tokensRef.current = refreshed;
        updateStoredTokens(refreshed);
      } catch {
        logout();
      }
    }, REFRESH_CHECK_INTERVAL_MS);

    return () => clearInterval(intervalId);
  }, [state.isAuthenticated, logout]);

  return (
    <AuthContext.Provider
      value={{
        state,
        flowState,
        configMissing: serviceRef.current === null,
        login,
        logout,
        clearError,
        getIdToken,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = (): AuthContextType => {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};
