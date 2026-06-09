import React from 'react';
import {
  Container,
  Header,
  SpaceBetween,
  ExpandableSection,
  Box,
  Table,
} from '@cloudscape-design/components';

const HelpPage: React.FC = () => {
  return (
    <SpaceBetween size="l">
      <Container
        header={
          <Header variant="h1" description="Guía de uso y referencia de DataMask">
            Ayuda
          </Header>
        }
      >
        <SpaceBetween size="l">
          <ExpandableSection
            headerText="Cómo usar DataMask"
            defaultExpanded
          >
            <SpaceBetween size="s">
              <Box variant="p">
                Siga estos pasos para procesar sus documentos:
              </Box>
              <Box variant="p">
                <strong>1.</strong> Inicie sesión con IAM Identity Center desde la pantalla de login (vía Amazon Cognito).
              </Box>
              <Box variant="p">
                <strong>2.</strong> Vaya a la sección “Documentos a enmascarar” en el menú lateral.
              </Box>
              <Box variant="p">
                <strong>3.</strong> Suba uno o varios documentos. Se cargan directamente a S3 mediante URLs prefirmadas.
              </Box>
              <Box variant="p">
                <strong>4.</strong> Seleccione los documentos en la carpeta “originales/” y presione procesar. El pipeline serverless los procesa automáticamente (Textract → IA Bedrock + reglas regex → redacción).
              </Box>
              <Box variant="p">
                <strong>5.</strong> Siga el estado de cada documento (UPLOADED → PROCESSING → COMPLETED) en la lista.
              </Box>
              <Box variant="p">
                <strong>6.</strong> Descargue los documentos ofuscados desde la sección “Archivos ofuscados”.
              </Box>
              <Box variant="p">
                <strong>7.</strong> Revise el historial en “Registros de Auditoría”. En “Configuración” ajuste el modelo de IA, la temperatura, el prompt y las reglas regex.
              </Box>
            </SpaceBetween>
          </ExpandableSection>

          <ExpandableSection headerText="Formatos soportados">
            <Table
              columnDefinitions={[
                {
                  id: 'format',
                  header: 'Formato',
                  cell: (item) => <strong>{item.format}</strong>,
                },
                {
                  id: 'extension',
                  header: 'Extensión',
                  cell: (item) => <code>{item.extension}</code>,
                },
                {
                  id: 'description',
                  header: 'Descripción',
                  cell: (item) => item.description,
                },
              ]}
              items={[
                {
                  format: 'PDF',
                  extension: '.pdf',
                  description: 'Documentos PDF. Se aplican redacciones visuales preservando el formato original.',
                },
              ]}
              variant="embedded"
              stripedRows
            />
          </ExpandableSection>

          <ExpandableSection headerText="Tipos de datos detectados">
            <Table
              columnDefinitions={[
                {
                  id: 'type',
                  header: 'Tipo',
                  cell: (item) => <strong>{item.type}</strong>,
                },
                {
                  id: 'label',
                  header: 'Etiqueta',
                  cell: (item) => <code>[{item.label}]</code>,
                },
                {
                  id: 'example',
                  header: 'Ejemplo',
                  cell: (item) => item.example,
                },
              ]}
              items={[
                { type: 'Nombre y Apellido', label: 'NOMBRE', example: 'Juan Pérez, María García López' },
                { type: 'Correo Electrónico', label: 'EMAIL', example: 'usuario@ejemplo.com' },
                { type: 'Celular', label: 'CELULAR', example: '+54 9 11 1234-5678' },
                { type: 'Teléfono', label: 'TELEFONO', example: '+54 11 4567-8901' },
                { type: 'Dirección', label: 'DIRECCION', example: 'Av. Corrientes 1234, CABA' },
                { type: 'Tarjeta de Crédito', label: 'TARJETA_CREDITO', example: '4532 1234 5678 9012' },
                { type: 'Cuenta Bancaria (CBU)', label: 'CUENTA_BANCARIA', example: '0110012230001234567890' },
                { type: 'DNI', label: 'DNI', example: '32.456.789 o 32456789' },
                { type: 'CUIT/CUIL', label: 'CUIT_CUIL', example: '20-32456789-4' },
                { type: 'Pasaporte', label: 'PASAPORTE', example: 'AAB123456' },
              ]}
              variant="embedded"
              stripedRows
            />
          </ExpandableSection>

          <ExpandableSection headerText="Restricciones y limitaciones">
            <SpaceBetween size="s">
              <Box variant="p">
                • El procesamiento corre en la nube AWS de su organización (Textract, Bedrock); los documentos no salen de su cuenta.
              </Box>
              <Box variant="p">
                • Los PDFs protegidos con contraseña se omiten.
              </Box>
              <Box variant="p">
                • La detección de PII puede no capturar todos los datos sensibles (precisión ~85-95%).
              </Box>
              <Box variant="p">
                • Archivos muy grandes (&gt;50MB) pueden tardar varios minutos en procesarse.
              </Box>
            </SpaceBetween>
          </ExpandableSection>

          <ExpandableSection headerText="Seguridad y riesgos">
            <SpaceBetween size="s">
              <Box variant="p">
                <strong>Autenticación:</strong> el acceso se realiza con Amazon Cognito federado con AWS IAM Identity Center (SAML). Si su organización federa con Active Directory, se usan sus credenciales corporativas. Cada llamada al API se autoriza con el id token (JWT) de Cognito.
              </Box>
              <Box variant="h4">Riesgos conocidos</Box>
              <Box variant="p">
                • <strong>Logs con nombres de archivo:</strong> el registro de auditoría almacena los nombres de los archivos procesados. Si los nombres contienen información sensible (ej: “CV-Juan-Perez.pdf”), queda registrada en el log.
              </Box>
              <Box variant="p">
                • <strong>Documentos ofuscados retienen contenido parcial:</strong> solo se reemplazan los datos sensibles detectados; el resto del texto se conserva. La precisión es del 85-95%, por lo que algún dato podría no detectarse.
              </Box>
              <Box variant="h4">Mitigaciones</Box>
              <Box variant="p">
                • Cifrado at-rest con KMS y in-transit con TLS 1.2+.
              </Box>
              <Box variant="p">
                • El API solo acepta requests con un id token (JWT) válido de Cognito; cada usuario solo ve sus propios documentos.
              </Box>
              <Box variant="p">
                • Los buckets S3 tienen Block Public Access; los originales nunca se modifican.
              </Box>
              <Box variant="h4">Recomendaciones</Box>
              <Box variant="p">
                • Revise los documentos ofuscados antes de compartirlos para verificar que todos los datos sensibles fueron detectados.
              </Box>
              <Box variant="p">
                • Cierre sesión al terminar, especialmente en equipos compartidos.
              </Box>
            </SpaceBetween>
          </ExpandableSection>

          <ExpandableSection headerText="Acerca de">
            <SpaceBetween size="s">
              <Box variant="p">
                <strong>DataMask AWS v1.1</strong>
              </Box>
              <Box variant="p">
                Solución serverless para enmascarar datos sensibles (PII) en documentos.
                Combina Amazon Textract, Amazon Bedrock (IA) y patrones
                regex para detectar y ofuscar información personal identificable.
              </Box>
              <Box variant="p">
                Desarrollado por <strong>EduTheCoder</strong>.
              </Box>
            </SpaceBetween>
          </ExpandableSection>
        </SpaceBetween>
      </Container>
    </SpaceBetween>
  );
};

export default HelpPage;
