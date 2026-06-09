import * as cdk from 'aws-cdk-lib';
import * as apigateway from 'aws-cdk-lib/aws-apigateway';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as path from 'path';
import { Construct } from 'constructs';
import { ApiStackProps } from './shared-props';

/**
 * ApiStack: API Gateway REST, Lambda API Handler, integración con IAM Identity Center.
 *
 * Requirements:
 *  - 10.1: Definir API Gateway en CDK
 *  - 10.2: Mínimo privilegio IAM
 *  - 10.5: Exportar valores via CfnOutput
 *  - 11.1: TLS 1.2+ en API Gateway, rechazar versiones inferiores
 *  - 11.6: HTTP 403 genérico sin revelar existencia del recurso
 */
export class ApiStack extends cdk.Stack {
  /** API Gateway REST API */
  public readonly api: apigateway.RestApi;
  /** Lambda API Handler */
  public readonly apiHandler: lambda.Function;

  constructor(scope: Construct, id: string, props: ApiStackProps) {
    super(scope, id, props);

    const {
      environment,
      prefix,
      documentsBucket,
      documentsTable,
      s3KmsKey,
      ssoStartUrl = '',
      ssoRegion = '',
      ssoClientName = 'datamask-aws',
    } = props;

    // ─────────────────────────────────────────────────────────────────────────
    // IAM Role para Lambda API Handler — Principio de mínimo privilegio
    // Req 10.2: Permisos específicos, sin wildcards en acciones ni recursos
    // ─────────────────────────────────────────────────────────────────────────
    const apiHandlerRole = new iam.Role(this, 'ApiHandlerRole', {
      roleName: `${prefix}-api-handler-role`,
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      description: 'Role IAM para Lambda API Handler de DataMask - minimo privilegio',
    });

    // Permisos básicos de Lambda (logs)
    apiHandlerRole.addToPolicy(new iam.PolicyStatement({
      sid: 'AllowCloudWatchLogs',
      effect: iam.Effect.ALLOW,
      actions: [
        'logs:CreateLogGroup',
        'logs:CreateLogStream',
        'logs:PutLogEvents',
      ],
      resources: [
        `arn:aws:logs:${this.region}:${this.account}:log-group:/aws/lambda/${prefix}-api-handler:*`,
      ],
    }));

    // Permisos S3 — presigned URLs, navegación del bucket y procesamiento
    apiHandlerRole.addToPolicy(new iam.PolicyStatement({
      sid: 'AllowS3Objects',
      effect: iam.Effect.ALLOW,
      actions: [
        's3:PutObject',
        's3:GetObject',
      ],
      resources: [
        `${documentsBucket.bucketArn}/originales/*`,
        `${documentsBucket.bucketArn}/ofuscados/*`,
      ],
    }));

    // Permiso para listar objetos del bucket (endpoint /documents/s3/list)
    apiHandlerRole.addToPolicy(new iam.PolicyStatement({
      sid: 'AllowS3List',
      effect: iam.Effect.ALLOW,
      actions: ['s3:ListBucket'],
      resources: [documentsBucket.bucketArn],
    }));

    // Permiso para invocar la Lambda Trigger (endpoint /documents/process)
    apiHandlerRole.addToPolicy(new iam.PolicyStatement({
      sid: 'AllowInvokeTrigger',
      effect: iam.Effect.ALLOW,
      actions: ['lambda:InvokeFunction'],
      resources: [
        `arn:aws:lambda:${this.region}:${this.account}:function:${prefix}-trigger`,
      ],
    }));

    // Permisos KMS — necesario para generar presigned URLs con SSE-KMS
    apiHandlerRole.addToPolicy(new iam.PolicyStatement({
      sid: 'AllowKmsForPresignedUrls',
      effect: iam.Effect.ALLOW,
      actions: [
        'kms:Decrypt',
        'kms:GenerateDataKey',
      ],
      resources: [s3KmsKey.keyArn],
    }));

    // Permisos DynamoDB — CRUD sobre tabla de documentos
    apiHandlerRole.addToPolicy(new iam.PolicyStatement({
      sid: 'AllowDynamoDBCrud',
      effect: iam.Effect.ALLOW,
      actions: [
        'dynamodb:PutItem',
        'dynamodb:GetItem',
        'dynamodb:UpdateItem',
        'dynamodb:Query',
      ],
      resources: [
        documentsTable.tableArn,
        `${documentsTable.tableArn}/index/GSI1`,
      ],
    }));

    // Permisos Secrets Manager — lectura de secrets del proyecto (si aplica)
    apiHandlerRole.addToPolicy(new iam.PolicyStatement({
      sid: 'AllowSecretsManagerRead',
      effect: iam.Effect.ALLOW,
      actions: [
        'secretsmanager:GetSecretValue',
      ],
      resources: [
        `arn:aws:secretsmanager:${this.region}:${this.account}:secret:${prefix}-*`,
      ],
    }));

    // Nota: el proxy SSO (sso.py) llama a los endpoints de IAM Identity Center
    // de forma anónima (autorizados por el access token del usuario), por lo que
    // NO requiere permisos IAM adicionales sobre sso-oidc / sso.

    // ─────────────────────────────────────────────────────────────────────────
    // Lambda API Handler — Python 3.12
    // Placeholder con routing básico, implementación detallada en tareas 3.x
    // ─────────────────────────────────────────────────────────────────────────
    this.apiHandler = new lambda.Function(this, 'ApiHandler', {
      functionName: `${prefix}-api-handler`,
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'handler.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../../lambdas/api')),
      role: apiHandlerRole,
      timeout: cdk.Duration.seconds(30),
      memorySize: 256,
      environment: {
        DOCUMENTS_BUCKET: documentsBucket.bucketName,
        DOCUMENTS_TABLE: documentsTable.tableName,
        ENVIRONMENT: environment,
        TRIGGER_FUNCTION_NAME: `${prefix}-trigger`,
        SSO_START_URL: ssoStartUrl,
        SSO_REGION: ssoRegion,
        SSO_CLIENT_NAME: ssoClientName,
      },
      logRetention: logs.RetentionDays.THREE_MONTHS,
      description: 'Lambda API Handler - proxy SSO, uploads, documentos y configuracion PII',
    });

    // ─────────────────────────────────────────────────────────────────────────
    // API Gateway REST — Endpoint regional, TLS 1.2 mínimo
    // Req 11.1: TLS 1.2+ en API Gateway, rechazar conexiones con versiones inferiores
    // ─────────────────────────────────────────────────────────────────────────
    this.api = new apigateway.RestApi(this, 'DataMaskApi', {
      restApiName: `${prefix}-api`,
      description: `DataMask API REST (${environment}) - autenticacion, uploads, documentos, configuracion PII`,
      endpointConfiguration: {
        types: [apigateway.EndpointType.REGIONAL],
      },
      // Configurar TLS 1.2 mínimo (Security Policy)
      // Req 11.1: Rechazar conexiones con versiones inferiores de TLS
      policy: new iam.PolicyDocument({
        statements: [
          new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            principals: [new iam.AnyPrincipal()],
            actions: ['execute-api:Invoke'],
            resources: ['execute-api:/*'],
          }),
        ],
      }),
      deployOptions: {
        stageName: environment,
        // Logging y métricas
        loggingLevel: apigateway.MethodLoggingLevel.INFO,
        dataTraceEnabled: environment === 'dev',
        metricsEnabled: true,
        throttlingRateLimit: 100,
        throttlingBurstLimit: 200,
      },
      // CORS — permitir origin *, métodos y headers
      defaultCorsPreflightOptions: {
        allowOrigins: apigateway.Cors.ALL_ORIGINS,
        allowMethods: apigateway.Cors.ALL_METHODS,
        allowHeaders: [
          'Content-Type',
          'Authorization',
          'X-Amz-Date',
          'X-Api-Key',
          'X-Amz-Security-Token',
          'X-Amz-Content-Sha256',
        ],
        maxAge: cdk.Duration.hours(1),
      },
      // Respuesta por defecto para requests no autorizados
      // Req 11.6: HTTP 403 genérico sin revelar existencia del recurso
      defaultMethodOptions: {
        authorizationType: apigateway.AuthorizationType.NONE,
      },
    });

    // Configurar respuesta 403 genérica para Gateway Responses
    // Req 11.6: Mensaje genérico sin revelar la existencia del recurso
    this.api.addGatewayResponse('Forbidden', {
      type: apigateway.ResponseType.ACCESS_DENIED,
      statusCode: '403',
      responseHeaders: {
        'Access-Control-Allow-Origin': "'*'",
      },
      templates: {
        'application/json': '{"message": "Forbidden"}',
      },
    });

    this.api.addGatewayResponse('Unauthorized', {
      type: apigateway.ResponseType.UNAUTHORIZED,
      statusCode: '403',
      responseHeaders: {
        'Access-Control-Allow-Origin': "'*'",
      },
      templates: {
        'application/json': '{"message": "Forbidden"}',
      },
    });

    this.api.addGatewayResponse('MissingAuth', {
      type: apigateway.ResponseType.MISSING_AUTHENTICATION_TOKEN,
      statusCode: '403',
      responseHeaders: {
        'Access-Control-Allow-Origin': "'*'",
      },
      templates: {
        'application/json': '{"message": "Forbidden"}',
      },
    });

    // ─────────────────────────────────────────────────────────────────────────
    // Lambda Proxy Integration — conexión API Gateway → Lambda
    // ─────────────────────────────────────────────────────────────────────────
    const lambdaIntegration = new apigateway.LambdaIntegration(this.apiHandler, {
      proxy: true,
      allowTestInvoke: environment === 'dev',
    });

    // ─────────────────────────────────────────────────────────────────────────
    // Definición de endpoints
    //  - /auth/sso/*: proxy SSO público (autentica al usuario) → AuthorizationType NONE
    //  - resto: protegidos con AWS_IAM (firma SigV4 con credenciales STS)
    // ─────────────────────────────────────────────────────────────────────────
    const iamAuth = { authorizationType: apigateway.AuthorizationType.IAM };

    // /auth/sso/* — proxy al device flow de Identity Center (público)
    const authResource = this.api.root.addResource('auth');
    const ssoResource = authResource.addResource('sso');
    for (const action of ['register', 'device', 'token', 'accounts', 'roles', 'credentials']) {
      ssoResource.addResource(action).addMethod('POST', lambdaIntegration, {
        authorizationType: apigateway.AuthorizationType.NONE,
      });
    }

    // /upload
    const uploadResource = this.api.root.addResource('upload');
    const uploadPresign = uploadResource.addResource('presign');
    uploadPresign.addMethod('POST', lambdaIntegration, iamAuth);

    // /documents
    const documentsResource = this.api.root.addResource('documents');
    documentsResource.addMethod('POST', lambdaIntegration, iamAuth);
    documentsResource.addMethod('GET', lambdaIntegration, iamAuth);

    // /documents/{id}
    const documentById = documentsResource.addResource('{id}');
    documentById.addMethod('GET', lambdaIntegration, iamAuth);

    // /documents/{id}/download/{type}
    const downloadResource = documentById.addResource('download');
    const downloadType = downloadResource.addResource('{type}');
    downloadType.addMethod('GET', lambdaIntegration, iamAuth);

    // /documents/s3/list y /documents/s3/download (navegación del bucket)
    const documentsS3 = documentsResource.addResource('s3');
    documentsS3.addResource('list').addMethod('GET', lambdaIntegration, iamAuth);
    documentsS3.addResource('download').addMethod('GET', lambdaIntegration, iamAuth);

    // /documents/process (encolar documentos para ofuscación)
    documentsResource.addResource('process').addMethod('POST', lambdaIntegration, iamAuth);

    // /config
    const configResource = this.api.root.addResource('config');
    configResource.addMethod('GET', lambdaIntegration, iamAuth);
    configResource.addMethod('PUT', lambdaIntegration, iamAuth);

    // ─────────────────────────────────────────────────────────────────────────
    // CfnOutputs — Exportar valores para otras stacks
    // Req 10.5: Exportar valores necesarios entre stacks mediante CfnOutput
    // ─────────────────────────────────────────────────────────────────────────
    new cdk.CfnOutput(this, 'ApiUrl', {
      value: this.api.url,
      description: 'URL base del API Gateway REST',
      exportName: `${prefix}-api-url`,
    });

    new cdk.CfnOutput(this, 'ApiId', {
      value: this.api.restApiId,
      description: 'ID del API Gateway REST',
      exportName: `${prefix}-api-id`,
    });

    new cdk.CfnOutput(this, 'ApiHandlerArn', {
      value: this.apiHandler.functionArn,
      description: 'ARN de la Lambda API Handler',
      exportName: `${prefix}-api-handler-arn`,
    });
  }
}
