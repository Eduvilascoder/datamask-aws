import * as cdk from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as sqs from 'aws-cdk-lib/aws-sqs';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3n from 'aws-cdk-lib/aws-s3-notifications';
import * as path from 'path';
import { Construct } from 'constructs';
import { ComputeStackProps } from './shared-props';

/**
 * ComputeStack: Lambda functions (Trigger, Detección, Redacción),
 * Lambda Layer (PyMuPDF), SQS Dead Letter Queue, IAM roles con mínimo privilegio,
 * y S3 Event Notification para activar el pipeline.
 *
 * Requirements: 10.1, 10.2, 10.5, 3.1, 3.5
 */
export class ComputeStack extends cdk.Stack {
  /** Lambda Trigger — activada por S3 PutObject en originales/ */
  public readonly triggerFunction: lambda.Function;
  /** Lambda Detección — procesa PII con Comprehend + Bedrock */
  public readonly detectionFunction: lambda.Function;
  /** Lambda Redacción — aplica redacciones con PyMuPDF */
  public readonly redactionFunction: lambda.Function;
  /** Lambda Layer con PyMuPDF compilado para Amazon Linux 2023 */
  public readonly pymupdfLayer: lambda.LayerVersion;
  /** SQS Dead Letter Queue para eventos fallidos */
  public readonly deadLetterQueue: sqs.Queue;

  constructor(scope: Construct, id: string, props: ComputeStackProps) {
    super(scope, id, props);

    const {
      environment,
      prefix,
      documentsBucketArn,
      documentsBucketName,
      documentsTableArn,
      documentsTableName,
      s3KmsKeyArn,
    } = props;

    // Importar bucket como referencia para event notification
    const documentsBucket = s3.Bucket.fromBucketAttributes(this, 'ImportedDocsBucket', {
      bucketArn: documentsBucketArn,
      bucketName: documentsBucketName,
    });

    // ─────────────────────────────────────────────────────────────────────────
    // SQS Dead Letter Queue
    // Req 3.5: Failed events go to Dead Letter Queue
    // Retención 14 días según diseño
    // ─────────────────────────────────────────────────────────────────────────
    this.deadLetterQueue = new sqs.Queue(this, 'DeadLetterQueue', {
      queueName: `${prefix}-dlq`,
      retentionPeriod: cdk.Duration.days(14),
      encryption: sqs.QueueEncryption.SQS_MANAGED,
    });

    // ─────────────────────────────────────────────────────────────────────────
    // Lambda Layer — PyMuPDF compilado para Amazon Linux 2023
    // Se usa en Lambda Redacción para procesamiento de PDF
    // La compilación real se realiza en deploy time (Docker build)
    // ─────────────────────────────────────────────────────────────────────────
    this.pymupdfLayer = new lambda.LayerVersion(this, 'PyMuPDFLayer', {
      layerVersionName: `${prefix}-pymupdf-layer`,
      description: 'PyMuPDF (fitz) compilado para Amazon Linux 2023 - Python 3.12',
      compatibleRuntimes: [lambda.Runtime.PYTHON_3_12],
      compatibleArchitectures: [lambda.Architecture.X86_64],
      code: lambda.Code.fromAsset(
        path.join(__dirname, '../../lambdas/layers/pymupdf')
      ),
    });

    // ─────────────────────────────────────────────────────────────────────────
    // IAM Roles — Mínimo privilegio por cada Lambda
    // Req 10.2: No wildcards (*) en acciones/recursos
    // ─────────────────────────────────────────────────────────────────────────

    // --- IAM Role: Lambda Trigger ---
    const triggerRole = new iam.Role(this, 'TriggerLambdaRole', {
      roleName: `${prefix}-trigger-lambda-role`,
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      description: 'Role para Lambda Trigger con permisos S3, DynamoDB, Textract, Lambda invoke',
    });

    // Permisos básicos de logging para CloudWatch
    triggerRole.addManagedPolicy(
      iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole')
    );

    // S3: Leer de originales/, escribir en procesamiento/ y errores/
    triggerRole.addToPolicy(new iam.PolicyStatement({
      sid: 'S3ReadOriginals',
      effect: iam.Effect.ALLOW,
      actions: ['s3:GetObject', 's3:HeadObject'],
      resources: [`${documentsBucketArn}/originales/*`],
    }));

    triggerRole.addToPolicy(new iam.PolicyStatement({
      sid: 'S3WriteProcesamiento',
      effect: iam.Effect.ALLOW,
      actions: ['s3:PutObject'],
      resources: [
        `${documentsBucketArn}/procesamiento/*`,
        `${documentsBucketArn}/errores/*`,
      ],
    }));

    // S3: Mover archivos inválidos (requiere delete en originales/ y put en errores/)
    triggerRole.addToPolicy(new iam.PolicyStatement({
      sid: 'S3MoveInvalidFiles',
      effect: iam.Effect.ALLOW,
      actions: ['s3:DeleteObject'],
      resources: [`${documentsBucketArn}/originales/*`],
    }));

    // DynamoDB: Leer y escribir estado del documento
    triggerRole.addToPolicy(new iam.PolicyStatement({
      sid: 'DynamoDBReadWrite',
      effect: iam.Effect.ALLOW,
      actions: [
        'dynamodb:GetItem',
        'dynamodb:PutItem',
        'dynamodb:UpdateItem',
        'dynamodb:Query',
      ],
      resources: [
        documentsTableArn,
        `${documentsTableArn}/index/GSI1`,
      ],
    }));

    // Textract: Invocar detección de texto (síncrono y asíncrono)
    triggerRole.addToPolicy(new iam.PolicyStatement({
      sid: 'TextractInvoke',
      effect: iam.Effect.ALLOW,
      actions: [
        'textract:DetectDocumentText',
        'textract:StartDocumentTextDetection',
        'textract:GetDocumentTextDetection',
      ],
      resources: ['*'], // Textract no soporta resource-level permissions
    }));

    // KMS: Descifrar objetos S3 cifrados con la KMS key
    triggerRole.addToPolicy(new iam.PolicyStatement({
      sid: 'KMSDecrypt',
      effect: iam.Effect.ALLOW,
      actions: ['kms:Decrypt', 'kms:GenerateDataKey'],
      resources: [s3KmsKeyArn],
    }));

    // SQS: Enviar mensajes al DLQ
    triggerRole.addToPolicy(new iam.PolicyStatement({
      sid: 'SQSSendToDLQ',
      effect: iam.Effect.ALLOW,
      actions: ['sqs:SendMessage'],
      resources: [this.deadLetterQueue.queueArn],
    }));

    // --- IAM Role: Lambda Detección ---
    const detectionRole = new iam.Role(this, 'DetectionLambdaRole', {
      roleName: `${prefix}-detection-lambda-role`,
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      description: 'Role para Lambda Deteccion con permisos Comprehend, Bedrock, S3, DynamoDB',
    });

    detectionRole.addManagedPolicy(
      iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole')
    );

    // S3: Leer desde procesamiento/, escribir entities.json
    detectionRole.addToPolicy(new iam.PolicyStatement({
      sid: 'S3ReadWriteProcesamiento',
      effect: iam.Effect.ALLOW,
      actions: ['s3:GetObject', 's3:PutObject'],
      resources: [`${documentsBucketArn}/procesamiento/*`],
    }));

    // DynamoDB: Actualizar estado
    detectionRole.addToPolicy(new iam.PolicyStatement({
      sid: 'DynamoDBUpdateState',
      effect: iam.Effect.ALLOW,
      actions: [
        'dynamodb:GetItem',
        'dynamodb:UpdateItem',
        'dynamodb:Query',
      ],
      resources: [
        documentsTableArn,
        `${documentsTableArn}/index/GSI1`,
      ],
    }));

    // Comprehend: Detectar PII
    detectionRole.addToPolicy(new iam.PolicyStatement({
      sid: 'ComprehendDetectPii',
      effect: iam.Effect.ALLOW,
      actions: ['comprehend:DetectPiiEntities'],
      resources: ['*'], // Comprehend no soporta resource-level permissions
    }));

    // Bedrock: Invocar modelo para análisis contextual
    detectionRole.addToPolicy(new iam.PolicyStatement({
      sid: 'BedrockInvokeModel',
      effect: iam.Effect.ALLOW,
      actions: ['bedrock:InvokeModel'],
      resources: [
        `arn:aws:bedrock:${this.region}::foundation-model/anthropic.claude-3-haiku-20240307-v1:0`,
        `arn:aws:bedrock:${this.region}::foundation-model/anthropic.claude-*`,
      ],
    }));

    // KMS: Descifrar objetos S3
    detectionRole.addToPolicy(new iam.PolicyStatement({
      sid: 'KMSDecryptDetection',
      effect: iam.Effect.ALLOW,
      actions: ['kms:Decrypt', 'kms:GenerateDataKey'],
      resources: [s3KmsKeyArn],
    }));

    // SQS: Enviar mensajes al DLQ
    detectionRole.addToPolicy(new iam.PolicyStatement({
      sid: 'SQSSendToDLQDetection',
      effect: iam.Effect.ALLOW,
      actions: ['sqs:SendMessage'],
      resources: [this.deadLetterQueue.queueArn],
    }));

    // --- IAM Role: Lambda Redacción ---
    const redactionRole = new iam.Role(this, 'RedactionLambdaRole', {
      roleName: `${prefix}-redaction-lambda-role`,
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      description: 'Role para Lambda Redaccion con permisos S3, DynamoDB, KMS',
    });

    redactionRole.addManagedPolicy(
      iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole')
    );

    // S3: Leer PDF original, leer entities, escribir resultados ofuscados
    redactionRole.addToPolicy(new iam.PolicyStatement({
      sid: 'S3ReadOriginalAndEntities',
      effect: iam.Effect.ALLOW,
      actions: ['s3:GetObject'],
      resources: [
        `${documentsBucketArn}/originales/*`,
        `${documentsBucketArn}/procesamiento/*`,
      ],
    }));

    redactionRole.addToPolicy(new iam.PolicyStatement({
      sid: 'S3WriteOfuscados',
      effect: iam.Effect.ALLOW,
      actions: ['s3:PutObject'],
      resources: [`${documentsBucketArn}/ofuscados/*`],
    }));

    // DynamoDB: Leer config usuario, actualizar estado COMPLETED
    redactionRole.addToPolicy(new iam.PolicyStatement({
      sid: 'DynamoDBReadWriteState',
      effect: iam.Effect.ALLOW,
      actions: [
        'dynamodb:GetItem',
        'dynamodb:UpdateItem',
        'dynamodb:Query',
      ],
      resources: [
        documentsTableArn,
        `${documentsTableArn}/index/GSI1`,
      ],
    }));

    // KMS: Descifrar y cifrar objetos S3
    redactionRole.addToPolicy(new iam.PolicyStatement({
      sid: 'KMSDecryptEncryptRedaction',
      effect: iam.Effect.ALLOW,
      actions: ['kms:Decrypt', 'kms:GenerateDataKey'],
      resources: [s3KmsKeyArn],
    }));

    // SQS: Enviar mensajes al DLQ
    redactionRole.addToPolicy(new iam.PolicyStatement({
      sid: 'SQSSendToDLQRedaction',
      effect: iam.Effect.ALLOW,
      actions: ['sqs:SendMessage'],
      resources: [this.deadLetterQueue.queueArn],
    }));

    // ─────────────────────────────────────────────────────────────────────────
    // Lambda Functions
    // Req 10.1: Define Lambda functions in CDK
    // ─────────────────────────────────────────────────────────────────────────

    // --- Lambda Trigger: 512MB, 300s ---
    // Req 3.1: S3 event triggers pipeline within 5s of upload
    this.triggerFunction = new lambda.Function(this, 'TriggerFunction', {
      functionName: `${prefix}-trigger`,
      description: 'S3 event handler - valida PDF, invoca Textract, inicia pipeline',
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'handler.lambda_handler',
      code: lambda.Code.fromAsset(
        path.join(__dirname, '../../lambdas/trigger')
      ),
      memorySize: 512,
      timeout: cdk.Duration.seconds(300),
      role: triggerRole,
      architecture: lambda.Architecture.X86_64,
      environment: {
        DOCUMENTS_BUCKET: documentsBucketName,
        DOCUMENTS_TABLE: documentsTableName,
        DLQ_URL: this.deadLetterQueue.queueUrl,
        ENVIRONMENT: environment,
        DETECTION_FUNCTION_NAME: `${prefix}-detection`,
      },
      deadLetterQueue: this.deadLetterQueue,
      retryAttempts: 2,
    });

    // --- Lambda Detección: 1024MB, 120s ---
    this.detectionFunction = new lambda.Function(this, 'DetectionFunction', {
      functionName: `${prefix}-detection`,
      description: 'Deteccion de PII con Comprehend + Bedrock + regex argentinos',
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'handler.lambda_handler',
      code: lambda.Code.fromAsset(
        path.join(__dirname, '../../lambdas/detection')
      ),
      memorySize: 1024,
      timeout: cdk.Duration.seconds(120),
      role: detectionRole,
      architecture: lambda.Architecture.X86_64,
      environment: {
        DOCUMENTS_BUCKET: documentsBucketName,
        DOCUMENTS_TABLE: documentsTableName,
        DLQ_URL: this.deadLetterQueue.queueUrl,
        BEDROCK_MODEL_ID: 'anthropic.claude-3-haiku-20240307-v1:0',
        ENVIRONMENT: environment,
      },
      deadLetterQueue: this.deadLetterQueue,
      retryAttempts: 2,
    });

    // --- Lambda Redacción: 1024MB, 300s ---
    this.redactionFunction = new lambda.Function(this, 'RedactionFunction', {
      functionName: `${prefix}-redaction`,
      description: 'Redaccion de PDF con PyMuPDF - genera documento ofuscado e informe markdown',
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'handler.lambda_handler',
      code: lambda.Code.fromAsset(
        path.join(__dirname, '../../lambdas/redaction')
      ),
      memorySize: 1024,
      timeout: cdk.Duration.seconds(300),
      role: redactionRole,
      architecture: lambda.Architecture.X86_64,
      layers: [this.pymupdfLayer],
      environment: {
        DOCUMENTS_BUCKET: documentsBucketName,
        DOCUMENTS_TABLE: documentsTableName,
        DLQ_URL: this.deadLetterQueue.queueUrl,
        ENVIRONMENT: environment,
      },
      deadLetterQueue: this.deadLetterQueue,
      retryAttempts: 2,
    });

    // Permiso para que Lambda Trigger pueda invocar Lambda Detección
    triggerRole.addToPolicy(new iam.PolicyStatement({
      sid: 'InvokeDetectionLambda',
      effect: iam.Effect.ALLOW,
      actions: ['lambda:InvokeFunction'],
      resources: [this.detectionFunction.functionArn],
    }));

    // Permiso para que Lambda Detección pueda invocar Lambda Redacción
    detectionRole.addToPolicy(new iam.PolicyStatement({
      sid: 'InvokeRedactionLambda',
      effect: iam.Effect.ALLOW,
      actions: ['lambda:InvokeFunction'],
      resources: [this.redactionFunction.functionArn],
    }));

    // ─────────────────────────────────────────────────────────────────────────
    // S3 Event Notification — PutObject en originales/ → Lambda Trigger
    // Req 3.1: S3 event triggers pipeline within 5s of upload
    // Se usa addEventNotification sobre el bucket importado, que crea un
    // custom resource dentro de ComputeStack para configurar la notificación.
    // addPermission se agrega automáticamente por CDK.
    // ─────────────────────────────────────────────────────────────────────────
    documentsBucket.addEventNotification(
      s3.EventType.OBJECT_CREATED,
      new s3n.LambdaDestination(this.triggerFunction),
      { prefix: 'originales/', suffix: '.pdf' }
    );

    // ─────────────────────────────────────────────────────────────────────────
    // CfnOutputs — Exportar valores para cross-stack reference
    // Req 10.5: Export values via CfnOutput between stacks
    // ─────────────────────────────────────────────────────────────────────────
    new cdk.CfnOutput(this, 'TriggerFunctionArn', {
      value: this.triggerFunction.functionArn,
      description: 'ARN de la Lambda Trigger',
      exportName: `${prefix}-trigger-function-arn`,
    });

    new cdk.CfnOutput(this, 'DetectionFunctionArn', {
      value: this.detectionFunction.functionArn,
      description: 'ARN de la Lambda Detección',
      exportName: `${prefix}-detection-function-arn`,
    });

    new cdk.CfnOutput(this, 'RedactionFunctionArn', {
      value: this.redactionFunction.functionArn,
      description: 'ARN de la Lambda Redacción',
      exportName: `${prefix}-redaction-function-arn`,
    });

    new cdk.CfnOutput(this, 'DlqUrl', {
      value: this.deadLetterQueue.queueUrl,
      description: 'URL de la Dead Letter Queue',
      exportName: `${prefix}-dlq-url`,
    });

    new cdk.CfnOutput(this, 'DlqArn', {
      value: this.deadLetterQueue.queueArn,
      description: 'ARN de la Dead Letter Queue',
      exportName: `${prefix}-dlq-arn`,
    });

    new cdk.CfnOutput(this, 'PyMuPDFLayerArn', {
      value: this.pymupdfLayer.layerVersionArn,
      description: 'ARN del Lambda Layer PyMuPDF',
      exportName: `${prefix}-pymupdf-layer-arn`,
    });
  }
}
