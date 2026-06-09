/**
 * CognitoAuthService - Autenticación con Amazon Cognito (Hosted UI) federado
 * con AWS IAM Identity Center vía SAML.
 *
 * Flujo (idéntico en concepto al proyecto ANSES):
 *   1. El usuario hace clic en "Iniciar sesión".
 *   2. Se redirige a la Hosted UI de Cognito (identity_provider=IAMIdentityCenter),
 *      que a su vez redirige a Identity Center para autenticar.
 *   3. Identity Center valida al usuario (puede federar con AD u otro IdP) y
 *      vuelve a Cognito; Cognito redirige a la app con un authorization code.
 *   4. La app canjea el code por tokens (id/access/refresh) vía PKCE.
 *   5. El id token (JWT) se usa como Bearer para autorizar el API Gateway
 *      (Cognito User Pools authorizer).
 *
 * No requiere proxy backend: Cognito expone CORS en su token endpoint.
 */

const STATE_KEY = 'datamask_oauth_state';
const VERIFIER_KEY = 'datamask_pkce_verifier';

export interface CognitoConfig {
  domain: string; // ej: datamask-123.auth.us-east-1.amazoncognito.com
  clientId: string;
  redirectUri: string; // debe coincidir con CallbackURLs del client
  /** Nombre del IdP SAML en Cognito (si la federación está habilitada). */
  identityProvider?: string;
}

export interface CognitoTokens {
  idToken: string;
  accessToken: string;
  refreshToken?: string;
  /** Momento de expiración (epoch ms). */
  expiresAt: number;
}

/** Claims relevantes del id token. */
export interface CognitoUser {
  username: string;
  email: string;
  sub: string;
}

/** Lee la configuración de Cognito desde variables de entorno (Vite). */
export function loadCognitoConfig(): CognitoConfig | null {
  const domain = import.meta.env.VITE_COGNITO_DOMAIN as string | undefined;
  const clientId = import.meta.env.VITE_COGNITO_CLIENT_ID as string | undefined;
  const redirectUri =
    (import.meta.env.VITE_COGNITO_REDIRECT_URI as string | undefined) ||
    window.location.origin + '/';
  const identityProvider = import.meta.env.VITE_COGNITO_IDP as string | undefined;

  if (!domain || !clientId) {
    return null;
  }
  return { domain, clientId, redirectUri, identityProvider };
}

/** Genera una cadena aleatoria URL-safe. */
function randomUrlSafe(bytes = 64): string {
  const arr = new Uint8Array(bytes);
  crypto.getRandomValues(arr);
  return base64UrlEncode(arr.buffer);
}

/** Codifica un ArrayBuffer en base64url (sin padding). */
function base64UrlEncode(buffer: ArrayBuffer): string {
  const view = new Uint8Array(buffer);
  let str = '';
  for (const b of view) str += String.fromCharCode(b);
  return btoa(str).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

/** Calcula el code_challenge S256 a partir del verifier PKCE. */
async function pkceChallenge(verifier: string): Promise<string> {
  const data = new TextEncoder().encode(verifier);
  const digest = await crypto.subtle.digest('SHA-256', data);
  return base64UrlEncode(digest);
}

/** Decodifica (sin verificar firma) el payload de un JWT. */
function decodeJwtPayload(token: string): Record<string, unknown> {
  const parts = token.split('.');
  if (parts.length < 2) return {};
  const payload = parts[1].replace(/-/g, '+').replace(/_/g, '/');
  const json = decodeURIComponent(
    atob(payload)
      .split('')
      .map((c) => '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2))
      .join('')
  );
  return JSON.parse(json) as Record<string, unknown>;
}

export class CognitoAuthService {
  constructor(private readonly config: CognitoConfig) {}

  /** Indica si la URL actual trae un authorization code (callback). */
  hasAuthCallback(): boolean {
    const params = new URLSearchParams(window.location.search);
    return params.has('code') || params.has('error');
  }

  /**
   * Inicia el login redirigiendo a la Hosted UI de Cognito.
   * Si hay IdP SAML configurado, salta directo a Identity Center.
   */
  async login(): Promise<void> {
    const verifier = randomUrlSafe(64);
    const challenge = await pkceChallenge(verifier);
    const state = randomUrlSafe(16);

    sessionStorage.setItem(VERIFIER_KEY, verifier);
    sessionStorage.setItem(STATE_KEY, state);

    const params = new URLSearchParams({
      response_type: 'code',
      client_id: this.config.clientId,
      redirect_uri: this.config.redirectUri,
      scope: 'openid email profile',
      state,
      code_challenge: challenge,
      code_challenge_method: 'S256',
    });
    if (this.config.identityProvider) {
      params.set('identity_provider', this.config.identityProvider);
    }

    window.location.assign(
      `https://${this.config.domain}/oauth2/authorize?${params.toString()}`
    );
  }

  /**
   * Completa el login: canjea el authorization code por tokens.
   * @returns tokens si el callback es válido; null si no hay code.
   */
  async completeLogin(): Promise<CognitoTokens | null> {
    const params = new URLSearchParams(window.location.search);
    const error = params.get('error');
    if (error) {
      this.cleanUrl();
      throw new Error(params.get('error_description') || error);
    }

    const code = params.get('code');
    const returnedState = params.get('state');
    if (!code) return null;

    const verifier = sessionStorage.getItem(VERIFIER_KEY);
    const expectedState = sessionStorage.getItem(STATE_KEY);
    this.cleanUrl();

    if (!verifier) {
      throw new Error('No se encontró el verificador PKCE de la sesión');
    }
    if (returnedState && expectedState && returnedState !== expectedState) {
      throw new Error('State inválido (posible CSRF)');
    }

    const body = new URLSearchParams({
      grant_type: 'authorization_code',
      client_id: this.config.clientId,
      code,
      redirect_uri: this.config.redirectUri,
      code_verifier: verifier,
    });

    const response = await fetch(`https://${this.config.domain}/oauth2/token`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: body.toString(),
    });

    sessionStorage.removeItem(VERIFIER_KEY);
    sessionStorage.removeItem(STATE_KEY);

    if (!response.ok) {
      throw new Error(`No se pudieron obtener los tokens (HTTP ${response.status})`);
    }

    const data = (await response.json()) as {
      id_token: string;
      access_token: string;
      refresh_token?: string;
      expires_in: number;
    };

    return {
      idToken: data.id_token,
      accessToken: data.access_token,
      refreshToken: data.refresh_token,
      expiresAt: Date.now() + (data.expires_in ?? 3600) * 1000,
    };
  }

  /** Renueva los tokens usando el refresh token. */
  async refreshTokens(refreshToken: string): Promise<CognitoTokens> {
    const body = new URLSearchParams({
      grant_type: 'refresh_token',
      client_id: this.config.clientId,
      refresh_token: refreshToken,
    });

    const response = await fetch(`https://${this.config.domain}/oauth2/token`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: body.toString(),
    });

    if (!response.ok) {
      throw new Error(`No se pudo renovar la sesión (HTTP ${response.status})`);
    }

    const data = (await response.json()) as {
      id_token: string;
      access_token: string;
      expires_in: number;
    };

    return {
      idToken: data.id_token,
      accessToken: data.access_token,
      refreshToken, // Cognito no rota el refresh token en este flujo.
      expiresAt: Date.now() + (data.expires_in ?? 3600) * 1000,
    };
  }

  /** Extrae los datos del usuario desde el id token. */
  getUser(tokens: CognitoTokens): CognitoUser {
    const claims = decodeJwtPayload(tokens.idToken);
    const email = (claims.email as string) || '';
    const cognitoUsername = (claims['cognito:username'] as string) || '';
    const username = email ? email.split('@')[0] : cognitoUsername;
    return {
      username,
      email,
      sub: (claims.sub as string) || '',
    };
  }

  /** Redirige a la Hosted UI para cerrar sesión en Cognito. */
  logout(): void {
    const params = new URLSearchParams({
      client_id: this.config.clientId,
      logout_uri: this.config.redirectUri,
    });
    window.location.assign(
      `https://${this.config.domain}/logout?${params.toString()}`
    );
  }

  /** Limpia los parámetros de callback de la URL sin recargar. */
  private cleanUrl(): void {
    window.history.replaceState({}, document.title, window.location.pathname);
  }
}
