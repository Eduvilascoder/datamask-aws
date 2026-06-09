/**
 * Persistencia de la sesión de Cognito en localStorage.
 *
 * Se usa localStorage para que la sesión sobreviva al cierre de la pestaña.
 * Los tokens se renuevan automáticamente con el refresh token mientras éste
 * siga vigente.
 */

import type { CognitoTokens, CognitoUser } from './cognitoAuth';

const STORAGE_KEY = 'datamask_cognito_session';

export interface CognitoSession {
  tokens: CognitoTokens;
  user: CognitoUser;
}

/** Guarda la sesión completa. */
export function saveSession(session: CognitoSession): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(session));
  } catch (error) {
    console.error('[DataMask Auth] No se pudo guardar la sesión:', error);
  }
}

/** Lee la sesión almacenada. */
export function loadSession(): CognitoSession | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    return JSON.parse(raw) as CognitoSession;
  } catch (error) {
    console.error('[DataMask Auth] No se pudo leer la sesión:', error);
    clearSession();
    return null;
  }
}

/** Actualiza solo los tokens (tras un refresh) preservando el usuario. */
export function updateStoredTokens(tokens: CognitoTokens): void {
  const session = loadSession();
  if (!session) return;
  saveSession({ ...session, tokens });
}

/** Borra la sesión. */
export function clearSession(): void {
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch (error) {
    console.error('[DataMask Auth] No se pudo borrar la sesión:', error);
  }
}

/** Indica si los tokens siguen vigentes (con margen de 60s). */
export function areTokensValid(tokens: CognitoTokens): boolean {
  return tokens.expiresAt - 60_000 > Date.now();
}
