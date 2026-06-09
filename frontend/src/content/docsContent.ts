/**
 * Contenido de la documentación del repositorio, embebido en el build.
 *
 * Los archivos Markdown viven fuera de `frontend/` (en la raíz del repo y en
 * `docs/`). Vite los importa como texto crudo (`?raw`) en tiempo de build, de
 * modo que el frontend estático (Amplify) puede mostrar un preview sin
 * depender de ningún endpoint ni del sistema de archivos en runtime.
 */

import readmeMd from '../../../README.md?raw';
import howtoMd from '../../../docs/howtodeploydatamask.md?raw';
import flujoMd from '../../../docs/flujo-aplicacion-aws.md?raw';
import autenticacionMd from '../../../docs/autenticacion.md?raw';
import algoritmosMd from '../../../docs/algoritmos-ofuscacion.md?raw';
import pricingMd from '../../../docs/pricing-estimado.md?raw';
import securityMd from '../../../docs/security-assessment.md?raw';

/** Documento del repositorio con su contenido Markdown embebido. */
export interface RepoDoc {
  /** Ruta relativa en el repositorio (para mostrar). */
  file: string;
  /** Título legible. */
  title: string;
  /** Descripción corta. */
  desc: string;
  /** Contenido Markdown completo. */
  content: string;
}

/** Listado de documentos del repositorio, en orden de relevancia. */
export const REPO_DOCS: RepoDoc[] = [
  {
    file: 'README.md',
    title: 'README',
    desc: 'Descripción general, características, arquitectura y despliegue rápido.',
    content: readmeMd,
  },
  {
    file: 'docs/howtodeploydatamask.md',
    title: 'Cómo desplegar DataMask',
    desc: 'Guía completa de despliegue (pasos automáticos y manuales).',
    content: howtoMd,
  },
  {
    file: 'docs/flujo-aplicacion-aws.md',
    title: 'Flujo de la aplicación',
    desc: 'Diagrama de secuencia y detalle de cada etapa del pipeline.',
    content: flujoMd,
  },
  {
    file: 'docs/autenticacion.md',
    title: 'Autenticación',
    desc: 'Login con Cognito federado con IAM Identity Center (SAML).',
    content: autenticacionMd,
  },
  {
    file: 'docs/algoritmos-ofuscacion.md',
    title: 'Algoritmos de ofuscación',
    desc: 'Cómo detecta PII la IA (Bedrock) y los regex, fusión y redacción.',
    content: algoritmosMd,
  },
  {
    file: 'docs/pricing-estimado.md',
    title: 'Análisis de costos',
    desc: 'Estimación de costos mensual con supuestos y escenarios.',
    content: pricingMd,
  },
  {
    file: 'docs/security-assessment.md',
    title: 'Evaluación de seguridad',
    desc: 'Security assessment con hallazgos y plan de remediación.',
    content: securityMd,
  },
];
