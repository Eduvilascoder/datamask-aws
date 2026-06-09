import React, { useState } from 'react';
import { BrowserRouter, Routes, Route, useNavigate, useLocation } from 'react-router-dom';
import {
  AppLayout,
  SideNavigation,
  type SideNavigationProps,
  Box,
  SpaceBetween,
  Spinner,
  Button,
  ContentLayout,
  Header,
  Container,
} from '@cloudscape-design/components';
import UploadPage from './pages/UploadPage';
import S3BrowserPage from './pages/S3BrowserPage';
import ConfigPage from './pages/ConfigPage';
import OutputPage from './pages/OutputPage';
import HelpPage from './pages/HelpPage';
import DocsPage from './pages/DocsPage';
import AuditPage from './pages/AuditPage';
import LoginPage from './pages/LoginPage';
import { ProcessingProvider } from './context/ProcessingContext';
import { AuthProvider, useAuth } from './context/AuthContext';
import es from './i18n/es';

const NAV_ITEMS: SideNavigationProps.Item[] = [
  { type: 'link', text: 'Documentos a enmascarar', href: '/' },
  { type: 'link', text: 'Archivos ofuscados', href: '/output' },
  { type: 'link', text: es.nav.logs, href: '/audit' },
  { type: 'divider' },
  { type: 'link', text: es.nav.config, href: '/config' },
  { type: 'link', text: 'Documentación', href: '/docs' },
  { type: 'link', text: 'Ayuda', href: '/help' },
];

/**
 * Contenido principal de la aplicacion con layout Cloudscape.
 */
const AppContent: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const [navOpen, setNavOpen] = useState(true);
  const { state, logout } = useAuth();

  const handleNavFollow = (event: CustomEvent<SideNavigationProps.FollowDetail>) => {
    event.preventDefault();
    navigate(event.detail.href);
  };

  return (
    <AppLayout
      navigation={
        <>
          <Box padding={{ horizontal: 'l', top: 'l', bottom: 's' }}>
            <SpaceBetween size="xxxs">
              <Box variant="h2" color="text-status-info">DataMask AWS</Box>
              <Box variant="small" color="text-body-secondary">Enmascarar datos sensibles en PDFs</Box>
              <Box fontSize="body-s" color="text-status-inactive">v1.0.0</Box>
            </SpaceBetween>
          </Box>
          <SideNavigation
            items={NAV_ITEMS}
            activeHref={location.pathname}
            onFollow={handleNavFollow}
          />
          <Box padding={{ horizontal: 'l', top: 'l' }}>
            <SpaceBetween size="xs">
              {state.user && (
                <Box variant="small" color="text-body-secondary">
                  {state.user.username || state.user.email}
                </Box>
              )}
              <Button variant="link" onClick={logout}>
                Cerrar sesion
              </Button>
            </SpaceBetween>
          </Box>
        </>
      }
      navigationOpen={navOpen}
      onNavigationChange={({ detail }) => setNavOpen(detail.open)}
      content={
        <Routes>
          <Route path="/" element={<S3BrowserPage />} />
          <Route path="/upload" element={<UploadPage />} />
          <Route path="/config" element={<ConfigPage />} />
          <Route path="/output" element={<OutputPage />} />
          <Route path="/audit" element={<AuditPage />} />
          <Route path="/docs" element={<DocsPage />} />
          <Route path="/help" element={<HelpPage />} />
        </Routes>
      }
      toolsHide
    />
  );
};

/**
 * Componente raiz que maneja la autenticacion con IAM Identity Center:
 * - Loading: muestra spinner mientras restaura la sesion almacenada
 * - No autenticado: muestra LoginPage (flujo device authorization)
 * - Autenticado: muestra el dashboard
 */
const AuthenticatedApp: React.FC = () => {
  const { state } = useAuth();

  if (state.isLoading) {
    return (
      <AppLayout
        content={
          <ContentLayout header={<Header variant="h1">DataMask AWS</Header>}>
            <Container>
              <Box textAlign="center" padding="xxl">
                <SpaceBetween size="m" direction="vertical" alignItems="center">
                  <Spinner size="large" />
                  <Box variant="p" color="text-body-secondary">
                    Verificando sesion...
                  </Box>
                </SpaceBetween>
              </Box>
            </Container>
          </ContentLayout>
        }
        navigationHide
        toolsHide
      />
    );
  }

  if (!state.isAuthenticated) {
    return (
      <AppLayout content={<LoginPage />} navigationHide toolsHide />
    );
  }

  return (
    <ProcessingProvider>
      <AppContent />
    </ProcessingProvider>
  );
};

const App: React.FC = () => {
  return (
    <BrowserRouter>
      <AuthProvider>
        <AuthenticatedApp />
      </AuthProvider>
    </BrowserRouter>
  );
};

export default App;
