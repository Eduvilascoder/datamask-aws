/**
 * LoginPage - Pantalla de inicio de sesión con Amazon Cognito federado con
 * AWS IAM Identity Center.
 *
 * Al hacer clic, el navegador se redirige a la Hosted UI de Cognito, que a su
 * vez redirige a Identity Center. Tras autenticarse, el usuario vuelve directo
 * a la app (sin pantalla de confirmación de código).
 */

import React from 'react';
import {
  ContentLayout,
  Header,
  Container,
  Box,
  SpaceBetween,
  Button,
  Alert,
  Spinner,
} from '@cloudscape-design/components';
import { useAuth } from '../context/AuthContext';

const LoginPage: React.FC = () => {
  const { state, flowState, configMissing, login, clearError } = useAuth();

  const loginInProgress = flowState === 'redirecting' || flowState === 'exchanging';

  return (
    <ContentLayout
      header={
        <Header variant="h1" description="Detección y ofuscación de datos sensibles en PDF">
          DataMask AWS
        </Header>
      }
    >
      <Container header={<Header variant="h2">Iniciar sesión</Header>}>
        <SpaceBetween size="l">
          <Box variant="p" color="text-body-secondary">
            El acceso se realiza mediante AWS IAM Identity Center a través de Amazon
            Cognito. Si su organización federa con Active Directory u otro proveedor
            de identidad, se usará ese mecanismo de inicio de sesión.
          </Box>

          {configMissing && (
            <Alert type="warning" header="Configuración pendiente">
              La autenticación con Cognito aún no está configurada en este entorno.
              Defina las variables VITE_COGNITO_DOMAIN y VITE_COGNITO_CLIENT_ID.
            </Alert>
          )}

          {state.error && (
            <Alert
              type="error"
              dismissible
              onDismiss={clearError}
              header="Error de autenticación"
              action={<Button onClick={login}>Reintentar</Button>}
            >
              {state.error}
            </Alert>
          )}

          {loginInProgress ? (
            <Box textAlign="center" padding="m">
              <SpaceBetween size="s" direction="vertical" alignItems="center">
                <Spinner size="large" />
                <Box variant="p" color="text-body-secondary">
                  Conectando con IAM Identity Center...
                </Box>
              </SpaceBetween>
            </Box>
          ) : (
            <Button
              variant="primary"
              iconName="lock-private"
              onClick={login}
              disabled={configMissing}
            >
              Iniciar sesión con IAM Identity Center
            </Button>
          )}
        </SpaceBetween>
      </Container>
    </ContentLayout>
  );
};

export default LoginPage;
