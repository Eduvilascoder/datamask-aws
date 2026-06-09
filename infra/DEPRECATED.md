# ⚠️ CDK OBSOLETO — NO USAR PARA DESPLEGAR

Esta carpeta `infra/` contiene una definición CDK **histórica y desactualizada**
que **NO refleja la arquitectura actual** de DataMask.

## Por qué no usarla

El despliegue real y soportado usa el template **`datamask-template.yaml`** en
la raíz del repositorio (ver `docs/howtodeploydatamask.md`). El CDK de esta
carpeta quedó desincronizado tras la migración de autenticación a Amazon Cognito:

- Define los endpoints del API con `AuthorizationType.IAM` (firma SigV4), cuando
  la versión actual usa un **Cognito User Pools authorizer (JWT)**.
- **No** define los recursos de Cognito (User Pool, Client, dominio, IdP SAML).
- Aún incluye el proxy SSO `/auth/sso/*` y variables `SSO_*` que fueron
  eliminados.

Desplegar este CDK **revertiría** la migración de seguridad.

## Qué usar en su lugar

```bash
./scripts/install.sh --profile <p> --alert-email <e> --callback-url <u>
```

o seguí `docs/howtodeploydatamask.md`.

> Esta carpeta se conserva solo como referencia. Si en el futuro se retoma CDK,
> debe reescribirse para coincidir con `datamask-template.yaml`.
