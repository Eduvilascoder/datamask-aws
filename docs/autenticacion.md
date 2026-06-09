# Autenticación — DataMask AWS

## Resumen

DataMask usa **Amazon Cognito User Pool** como proveedor de identidad de la
aplicación, **federado con AWS IAM Identity Center vía SAML 2.0**. Este es el
mismo modelo que el proyecto ANSES: Cognito actúa como Service Provider (SP) y
IAM Identity Center como Identity Provider (IdP). Identity Center puede a su vez
federar con Active Directory u otro IdP corporativo (SAML/OIDC).

El usuario hace login en la Hosted UI de Cognito → se redirige a Identity
Center → se autentica → vuelve **directo a la app** (sin pantalla de
confirmación de código). El API se autoriza con el **id token (JWT)** de
Cognito mediante un *Cognito User Pools Authorizer* en API Gateway.

## Arquitectura

```
┌─────────┐    ┌──────────────────┐    ┌──────────────────┐    ┌────────────┐
│ Usuario │──▶ │ Cognito Hosted   │──▶ │ IAM Identity     │──▶ │  DataMask  │
│         │◀── │ UI (SP SAML)     │◀── │ Center (IdP SAML)│◀── │  (React)   │
└─────────┘    └──────────────────┘    └──────────────────┘    └────────────┘
                       │                                              │
                       │  authorization code + PKCE → id/access token │
                       └──────────────────────────────────────────────┘
                                          │
                                          ▼
                         API Gateway (Cognito User Pools Authorizer)
                                          │
                                          ▼
                                   Lambda API Handler
                         (deriva el usuario del claim email)
```

## Componentes (stack `datamask-dev`)

| Componente | Valor |
|------------|-------|
| User Pool ID | `us-east-1_ntBXg0EdP` |
| App Client ID | `6vn9isga4bn6dpl8mslabnrtup` |
| Dominio Hosted UI | `datamask-339712829454.auth.us-east-1.amazoncognito.com` |
| ACS URL (SAML) | `https://datamask-339712829454.auth.us-east-1.amazoncognito.com/saml2/idpresponse` |
| Audience URI (SP Entity ID) | `urn:amazon:cognito:sp:us-east-1_ntBXg0EdP` |
| Callback URL | `https://main.d3nxh77gcdsvjs.amplifyapp.com/` |

> Estos valores se exportan como Outputs del stack: `CognitoUserPoolId`,
> `CognitoUserPoolClientId`, `CognitoDomain`, `CognitoSamlAcsUrl`,
> `CognitoSamlAudienceUri`.

## Flujo de autenticación (Authorization Code + PKCE)

1. El usuario hace clic en "Iniciar sesión". El frontend genera `code_verifier`
   + `code_challenge` (PKCE) y redirige a la Hosted UI de Cognito
   (`/oauth2/authorize`). Si `VITE_COGNITO_IDP` está definido, salta directo a
   Identity Center.
2. Cognito redirige a Identity Center (SAML). El usuario se autentica.
3. Identity Center devuelve la aserción SAML a la ACS URL de Cognito.
4. Cognito redirige a la app con un `code`.
5. La app canjea el `code` por tokens en `/oauth2/token` usando el
   `code_verifier` (sin client secret — cliente público).
6. El `id_token` se guarda en `localStorage` y se envía como header
   `Authorization` en cada llamada al API. Se renueva con el `refresh_token`.

## Autorización del API

- API Gateway usa un **Authorizer COGNITO_USER_POOLS** (`ApiCognitoAuthorizer`)
  que valida la firma y expiración del JWT antes de invocar la Lambda.
- `lambdas/api/middleware.py` lee los claims desde
  `requestContext.authorizer.claims` y deriva el `userId` legible a partir del
  email (`eduvilas@org.com` → `eduvilas`). Ese `userId` es la partición en
  DynamoDB y el prefijo S3 `originales/{userId}/`.

## Variables de entorno del frontend

```
VITE_COGNITO_DOMAIN=datamask-339712829454.auth.us-east-1.amazoncognito.com
VITE_COGNITO_CLIENT_ID=6vn9isga4bn6dpl8mslabnrtup
VITE_COGNITO_REDIRECT_URI=https://main.d3nxh77gcdsvjs.amplifyapp.com/
# VITE_COGNITO_IDP=IAMIdentityCenter   # activar tras federar
```

## Federación con IAM Identity Center (paso manual)

La creación de la aplicación SAML en Identity Center y el intercambio de
metadata es un paso manual (chicken-and-egg de ACS/metadata). Mientras tanto,
el login funciona con **usuarios nativos de Cognito**.

### Pasos para habilitar la federación

1. **Consola de IAM Identity Center → Applications → Add application →
   "I have an application I want to set up" → SAML 2.0.**
2. En **Application metadata**, completar manualmente:
   - **Application ACS URL:**
     `https://datamask-339712829454.auth.us-east-1.amazoncognito.com/saml2/idpresponse`
   - **Application SAML audience:**
     `urn:amazon:cognito:sp:us-east-1_ntBXg0EdP`
3. Guardar y **copiar la "IAM Identity Center SAML metadata file" URL**
   (metadata del IdP).
4. **Attribute mappings:** mapear `Subject` → `${user:email}` (formato
   emailAddress) y agregar el atributo
   `http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress`
   → `${user:email}`.
5. **Asignar usuarios/grupos** a la aplicación (ej: `eduvilas`).
6. **Redesplegar el stack** pasando el parámetro `SamlMetadataUrl` con la URL
   del paso 3:

   ```bash
   aws cloudformation update-stack --stack-name datamask-dev \
     --template-url https://datamask-artifacts-339712829454.s3.amazonaws.com/datamask/datamask-template.yaml \
     --capabilities CAPABILITY_NAMED_IAM \
     --parameters \
       ParameterKey=SamlMetadataUrl,ParameterValue=<METADATA_URL> \
       ParameterKey=CognitoDomainPrefix,UsePreviousValue=true \
       ParameterKey=AppCallbackUrl,UsePreviousValue=true \
       ... (resto con UsePreviousValue=true) \
     --profile masterGenAI --region us-east-1
   ```

   Esto crea el `CognitoSamlProvider` (IdP `IAMIdentityCenter` en Cognito) y lo
   agrega a los `SupportedIdentityProviders` del App Client.
7. En el frontend, definir `VITE_COGNITO_IDP=IAMIdentityCenter`, hacer build y
   redeploy. A partir de ahí el botón de login salta directo a Identity Center.

## Usuario de prueba (sin federación)

Mientras la federación no esté activa, hay un usuario nativo de Cognito:

- **Usuario:** `eduvilas@amazon.com`
- **Password:** definido vía `admin-set-user-password` (rotar tras la prueba).

## Cierre de sesión

`logout()` limpia el `localStorage` y redirige a `/logout` de la Hosted UI de
Cognito para cerrar la sesión del lado del IdP.

## Seguridad

- Cliente público con **PKCE** (sin client secret en el navegador).
- Tokens de corta duración (id/access: 60 min) + refresh (30 días).
- API Gateway valida el JWT antes de ejecutar cualquier Lambda.
- Cada usuario solo accede a sus propios documentos (partición por `userId`).
- Tokens en `localStorage`; se borran al cerrar sesión o ante 401/403.
