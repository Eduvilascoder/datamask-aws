/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Endpoint del API Gateway. */
  readonly VITE_API_ENDPOINT: string;
  /** Región AWS para los clientes firmados con credenciales STS. */
  readonly VITE_AWS_REGION: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
