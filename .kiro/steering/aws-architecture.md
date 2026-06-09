---
inclusion: always
description: "Patrones de arquitectura AWS: Well-Architected Framework, servicios preferidos, resiliencia y naming conventions"
---

## Principios
- Well-Architected Framework como guía base (Operational Excellence, Security, Reliability, Performance, Cost, Sustainability)
- Preferir serverless sobre instancias cuando el workload lo permita
- Infrastructure as Code (IaC) obligatorio para todos los recursos — usar CDK o Terraform

## Servicios preferidos
- Compute: Lambda (serverless), ECS Fargate (containers), EKS (k8s a escala)
- Messaging: SQS para colas, SNS para fan-out, EventBridge para event-driven
- Storage: S3 (objetos), DynamoDB (NoSQL), Aurora Serverless (relacional)
- API: API Gateway (REST/HTTP/WebSocket)
- Secrets: AWS Secrets Manager — NUNCA hardcodear credenciales

## Autenticación y autorización
- Identidad de usuarios: **AWS IAM Identity Center** (no Cognito). Permite usar
  los usuarios/grupos de la organización y federar con Active Directory u otro
  IdP SAML/OIDC externo.
- Flujo: OIDC Device Authorization Flow. El navegador NO llama a Identity Center
  directamente (no expone CORS); el Lambda API actúa de proxy (`/auth/sso/*`).
- Autorización del API: API Gateway con **AWS_IAM** + firma **SigV4** usando las
  credenciales temporales (STS) obtenidas de Identity Center.
- Nunca credenciales de larga duración en el cliente; siempre STS temporal.
- Todo corre en AWS (Amplify + API Gateway + Lambda). No hay servidores locales.

## Patrones de resiliencia
- Circuit breaker con exponential backoff en llamadas externas
- Dead letter queues (DLQ) en todas las colas SQS
- Multi-AZ por defecto en recursos stateful
- Health checks y alarmas en CloudWatch para todos los servicios críticos

## Naming conventions
- Recursos: `{proyecto}-{ambiente}-{recurso}` (ej: `myapp-prod-api`)
- Tags obligatorios: `Project`, `Environment`, `Owner`, `CostCenter`
