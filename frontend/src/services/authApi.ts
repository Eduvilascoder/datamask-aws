import axios from 'axios';
import {
  loadSession,
  clearSession,
  areTokensValid,
  updateStoredTokens,
} from './cognitoSessionStorage';
import { CognitoAuthService, loadCognitoConfig } from './cognitoAuth';

/**
 * Axios instance para el API Backend de DataMask AWS.
 *
 * El API Gateway usa un authorizer de Cognito User Pools, por lo que cada
 * request se autoriza con el id token (JWT) en el header Authorization.
 */

const API_BASE_URL = import.meta.env.VITE_API_ENDPOINT || '/api';
const AUTH_TIMEOUT_MS = 15_000;

const cognitoConfig = loadCognitoConfig();
const cognitoService = cognitoConfig ? new CognitoAuthService(cognitoConfig) : null;

const authApi = axios.create({
  baseURL: API_BASE_URL,
  timeout: AUTH_TIMEOUT_MS,
  headers: {
    'Content-Type': 'application/json',
  },
});

/** Obtiene un id token válido, renovándolo con el refresh token si expiró. */
async function getValidIdToken(): Promise<string | null> {
  const session = loadSession();
  if (!session) return null;

  if (areTokensValid(session.tokens)) {
    return session.tokens.idToken;
  }
  if (session.tokens.refreshToken && cognitoService) {
    try {
      const refreshed = await cognitoService.refreshTokens(session.tokens.refreshToken);
      updateStoredTokens(refreshed);
      return refreshed.idToken;
    } catch {
      return null;
    }
  }
  return null;
}

/**
 * Request interceptor: adjunta el id token como Bearer en Authorization.
 */
authApi.interceptors.request.use(
  async (config) => {
    const idToken = await getValidIdToken();
    if (idToken) {
      config.headers.set('Authorization', idToken);
    }
    return config;
  },
  (error) => Promise.reject(error)
);

/**
 * Response interceptor: maneja errores 401/403 limpiando la sesión.
 */
authApi.interceptors.response.use(
  (response) => response,
  (error) => {
    if (axios.isAxiosError(error) && error.response) {
      const status = error.response.status;
      if (status === 401 || status === 403) {
        clearSession();
        window.location.href = '/';
        return new Promise(() => {});
      }
    }
    return Promise.reject(error);
  }
);

export default authApi;
