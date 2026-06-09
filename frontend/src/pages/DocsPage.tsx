import React, { useState } from 'react';
import {
  Container,
  Header,
  SpaceBetween,
  ExpandableSection,
  Box,
  Table,
  Badge,
  ColumnLayout,
  Button,
  Modal,
} from '@cloudscape-design/components';
import { REPO_DOCS, type RepoDoc } from '../content/docsContent';
import MarkdownViewer from '../components/MarkdownViewer';

/**
 * Documentación estática del sistema DataMask.
 * No consume ningún endpoint: el contenido es informativo y se mantiene
 * en sincronía con la arquitectura del proyecto.
 */
const DocsPage: React.FC = () => {
  const [previewDoc, setPreviewDoc] = useState<RepoDoc | null>(null);

  return (
    <SpaceBetween size="l">
      <Container
        header={
          <Header
            variant="h1"
            description="Documentación técnica y funcional de la plataforma DataMask AWS."
          >
            Documentación
          </Header>
        }
      >
        <SpaceBetween size="l">
          <ExpandableSection headerText="Documentación del repositorio (docs/)" defaultExpanded>
            <SpaceBetween size="s">
              <Box variant="p">
                Documentos técnicos y funcionales incluidos en el proyecto. Haga
                clic en <strong>Ver</strong> para previsualizar el contenido
                completo sin salir de la aplicación.
              </Box>
              <Table
                columnDefinitions={[
                  {
                    id: 'file',
                    header: 'Archivo',
                    cell: (item) => <code>{item.file}</code>,
                  },
                  { id: 'desc', header: 'Descripción', cell: (item) => item.desc },
                  {
                    id: 'actions',
                    header: '',
                    width: 110,
                    cell: (item) => (
                      <Button
                        variant="inline-link"
                        iconName="file"
                        onClick={() => setPreviewDoc(item)}
                      >
                        Ver
                      </Button>
                    ),
                  },
                ]}
                items={REPO_DOCS}
                variant="embedded"
                stripedRows
              />
            </SpaceBetween>
          </ExpandableSection>

          <ExpandableSection headerText="¿Qué es DataMask?" defaultExpanded>
            <SpaceBetween size="s">
              <Box variant="p">
                DataMask es una solución serverless en AWS que detecta y ofusca
                datos personales sensibles (PII) en documentos PDF. Combina
                detección determinista mediante reglas regex configurables con
                análisis contextual mediante modelos de Amazon Bedrock.
              </Box>
              <Box variant="p">
                Todo el procesamiento ocurre dentro de la cuenta AWS de su
                organización; los documentos no se envían a terceros.
              </Box>
            </SpaceBetween>
          </ExpandableSection>

          <ExpandableSection headerText="Arquitectura" defaultExpanded>
            <SpaceBetween size="m">
              <Box variant="p">
                El sistema sigue una arquitectura event-driven serverless con un
                pipeline de funciones Lambda encadenadas:
              </Box>
              <ColumnLayout columns={2} variant="text-grid">
                <div>
                  <Box variant="awsui-key-label">Frontend</Box>
                  <Box variant="p">
                    React + Cloudscape, desplegado en AWS Amplify Hosting.
                  </Box>
                </div>
                <div>
                  <Box variant="awsui-key-label">Autenticación</Box>
                  <Box variant="p">
                    Amazon Cognito federado con AWS IAM Identity Center (SAML).
                    El API se autoriza con el id token (JWT) de Cognito.
                  </Box>
                </div>
                <div>
                  <Box variant="awsui-key-label">API</Box>
                  <Box variant="p">
                    Amazon API Gateway (autorización AWS_IAM) + Lambda handler.
                  </Box>
                </div>
                <div>
                  <Box variant="awsui-key-label">Almacenamiento</Box>
                  <Box variant="p">
                    Amazon S3 (documentos, cifrado at-rest) y Amazon DynamoDB
                    (metadatos y auditoría).
                  </Box>
                </div>
              </ColumnLayout>
            </SpaceBetween>
          </ExpandableSection>

          <ExpandableSection headerText="Pipeline de procesamiento">
            <Table
              columnDefinitions={[
                { id: 'step', header: 'Etapa', cell: (item) => <strong>{item.step}</strong> },
                { id: 'service', header: 'Servicio', cell: (item) => <code>{item.service}</code> },
                { id: 'desc', header: 'Descripción', cell: (item) => item.desc },
              ]}
              items={[
                {
                  step: '1. Carga',
                  service: 'S3 + API',
                  desc: 'El documento se sube directamente a S3 mediante una URL prefirmada en originales/{usuario}/{archivo}.',
                },
                {
                  step: '2. Trigger',
                  service: 'Lambda trigger',
                  desc: 'El evento S3 dispara el pipeline y registra el documento en DynamoDB con estado PROCESSING.',
                },
                {
                  step: '3. Extracción',
                  service: 'Amazon Textract',
                  desc: 'Extrae el texto del PDF, incluyendo posiciones de cada palabra.',
                },
                {
                  step: '4. Detección',
                  service: 'IA + Regex',
                  desc: 'Según el método configurado: IA (Bedrock), Regex o ambos. Detecta PII página por página.',
                },
                {
                  step: '5. Redacción',
                  service: 'Lambda redaction',
                  desc: 'Genera el PDF ofuscado conservando el formato original y un informe Markdown.',
                },
                {
                  step: '6. Auditoría',
                  service: 'DynamoDB',
                  desc: 'Registra motor, tiempo, entidades detectadas y resultado (éxito/error).',
                },
              ]}
              variant="embedded"
              stripedRows
            />
          </ExpandableSection>

          <ExpandableSection headerText="Secciones de la aplicación">
            <Table
              columnDefinitions={[
                { id: 'section', header: 'Sección', cell: (item) => <strong>{item.section}</strong> },
                { id: 'desc', header: 'Para qué sirve', cell: (item) => item.desc },
              ]}
              items={[
                { section: 'Documentos a enmascarar', desc: 'Subir documentos y lanzar el procesamiento.' },
                { section: 'Archivos ofuscados', desc: 'Descargar los PDF ofuscados y los informes Markdown generados.' },
                { section: 'Registros de Auditoría', desc: 'Historial de procesamientos con motor, tiempo, entidades y resultado.' },
                { section: 'Configuración', desc: 'Método de ofuscación (IA/Regex/Ambos), modelo de IA, prompt y reglas regex deterministas.' },
                { section: 'Ayuda', desc: 'Guía de uso, formatos soportados y consideraciones de seguridad.' },
              ]}
              variant="embedded"
              stripedRows
            />
          </ExpandableSection>

          <ExpandableSection headerText="Configuración de detección">
            <SpaceBetween size="s">
              <Box variant="p">
                Desde la sección <strong>Configuración</strong> se ajusta cómo el
                sistema detecta datos sensibles:
              </Box>
              <Box variant="p">
                • <strong>Método de ofuscación:</strong> elija qué algoritmos se
                aplican — <strong>IA</strong> (solo Bedrock), <strong>Regex</strong>{' '}
                (solo reglas deterministas) o <strong>Ambos</strong> (combinados,
                recomendado).
              </Box>
              <Box variant="p">
                • <strong>Modelo de IA:</strong> elija el modelo de Amazon Bedrock
                (ej: Claude Haiku 4.5, Claude Sonnet) para el análisis contextual.
              </Box>
              <Box variant="p">
                • <strong>Temperatura:</strong> controla la aleatoriedad del modelo
                (0 = determinista y preciso, recomendado para PII; rango 0 a 1).
              </Box>
              <Box variant="p">
                • <strong>Prompt del modelo:</strong> editable; debe incluir el
                marcador <code>{'{text}'}</code> donde se inserta el contenido del documento.
              </Box>
              <Box variant="p">
                • <strong>Reglas regex deterministas:</strong> patrones configurables
                (DNI, CUIT/CUIL, email, teléfonos, tarjetas, cuentas bancarias, etc.).
                Cada regla puede activarse o desactivarse individualmente.
              </Box>
              <Box variant="p">
                • <strong>Entidades a ignorar:</strong> valores literales que nunca
                deben ofuscarse aunque coincidan con una regla.
              </Box>
            </SpaceBetween>
          </ExpandableSection>

          <ExpandableSection headerText="Seguridad">
            <SpaceBetween size="s">
              <Box variant="p">
                • Autenticación con <strong>IAM Identity Center</strong>; soporta
                federación con Active Directory u otros IdP SAML/OIDC.
              </Box>
              <Box variant="p">
                • Credenciales temporales (STS), nunca claves de larga duración en el
                cliente.
              </Box>
              <Box variant="p">
                • Cifrado at-rest en S3 y in-transit con TLS 1.2+.
              </Box>
              <Box variant="p">
                • Cada usuario solo accede a sus propios documentos.
              </Box>
            </SpaceBetween>
          </ExpandableSection>

          <ExpandableSection headerText="Versión">
            <Box variant="p">
              <Badge color="blue">DataMask AWS v1.1</Badge> · Desarrollado por EduTheCoder.
            </Box>
          </ExpandableSection>
        </SpaceBetween>
      </Container>

      <Modal
        visible={previewDoc !== null}
        onDismiss={() => setPreviewDoc(null)}
        size="max"
        header={previewDoc?.title ?? 'Documento'}
        footer={
          <Box float="right">
            <Button variant="primary" onClick={() => setPreviewDoc(null)}>
              Cerrar
            </Button>
          </Box>
        }
      >
        {previewDoc && (
          <SpaceBetween size="s">
            <Box variant="small" color="text-body-secondary">
              <code>{previewDoc.file}</code>
            </Box>
            <MarkdownViewer content={previewDoc.content} />
          </SpaceBetween>
        )}
      </Modal>
    </SpaceBetween>
  );
};

export default DocsPage;
