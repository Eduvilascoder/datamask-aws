import * as cdk from 'aws-cdk-lib';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as kms from 'aws-cdk-lib/aws-kms';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';
import { DataMaskStackProps } from './shared-props';

/**
 * StorageStack: S3 bucket para documentos, DynamoDB table para estado,
 * KMS keys para cifrado, y bucket de logs para S3 Access Logging.
 *
 * Requirements: 10.1, 10.3, 10.4, 10.5, 11.2, 11.3, 13.5
 */
export class StorageStack extends cdk.Stack {
  /** Bucket principal de documentos */
  public readonly documentsBucket: s3.Bucket;
  /** Bucket de logs de acceso S3 */
  public readonly logsBucket: s3.Bucket;
  /** Tabla DynamoDB para estado de documentos */
  public readonly documentsTable: dynamodb.Table;
  /** KMS key para cifrado de S3 */
  public readonly s3KmsKey: kms.Key;

  constructor(scope: Construct, id: string, props: DataMaskStackProps) {
    super(scope, id, props);

    const { environment, prefix } = props;
    const isProd = environment === 'prod';
    const removalPolicy = isProd ? cdk.RemovalPolicy.RETAIN : cdk.RemovalPolicy.DESTROY;

    // ─────────────────────────────────────────────────────────────────────────
    // KMS Key para cifrado S3 (SSE-KMS con clave gestionada por el cliente)
    // Req 10.3: Encryption at-rest with SSE-KMS (customer managed key) for S3
    // ─────────────────────────────────────────────────────────────────────────
    this.s3KmsKey = new kms.Key(this, 'S3EncryptionKey', {
      alias: `${prefix}-s3-key`,
      description: `KMS key para cifrado SSE-KMS del bucket de documentos DataMask (${environment})`,
      enableKeyRotation: true,
      removalPolicy,
      policy: new iam.PolicyDocument({
        statements: [
          // Permite al account root administrar la key
          new iam.PolicyStatement({
            sid: 'AllowRootAccountAdmin',
            effect: iam.Effect.ALLOW,
            principals: [new iam.AccountRootPrincipal()],
            actions: ['kms:*'],
            resources: ['*'],
          }),
          // Permite a S3 usar la key para cifrado
          new iam.PolicyStatement({
            sid: 'AllowS3ServiceUse',
            effect: iam.Effect.ALLOW,
            principals: [new iam.ServicePrincipal('s3.amazonaws.com')],
            actions: [
              'kms:Decrypt',
              'kms:GenerateDataKey',
            ],
            resources: ['*'],
          }),
        ],
      }),
    });

    // ─────────────────────────────────────────────────────────────────────────
    // S3 Logs Bucket — bucket dedicado para S3 Access Logging
    // Req 11.3: S3 Access Logging habilitado, logs en bucket dedicado,
    //           retención 90 días, cifrado SSE-S3
    // ─────────────────────────────────────────────────────────────────────────
    this.logsBucket = new s3.Bucket(this, 'LogsBucket', {
      bucketName: `${prefix}-documents-${this.account}-access-logs`,
      removalPolicy,
      autoDeleteObjects: !isProd,
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      objectOwnership: s3.ObjectOwnership.BUCKET_OWNER_PREFERRED,
      lifecycleRules: [
        {
          id: 'DeleteLogsAfter90Days',
          enabled: true,
          expiration: cdk.Duration.days(90),
        },
      ],
    });

    // ─────────────────────────────────────────────────────────────────────────
    // S3 Documents Bucket — bucket principal de documentos
    // Req 11.2: Block Public Access (4 opciones), acceso solo via presigned URLs o IAM roles
    // Req 10.3: SSE-KMS con customer managed key
    // Req 13.5: Naming pattern datamask-{environment}-{resource}
    // ─────────────────────────────────────────────────────────────────────────
    this.documentsBucket = new s3.Bucket(this, 'DocumentsBucket', {
      bucketName: `${prefix}-documents-${this.account}`,
      removalPolicy,
      autoDeleteObjects: !isProd,
      encryption: s3.BucketEncryption.KMS,
      encryptionKey: this.s3KmsKey,
      bucketKeyEnabled: true,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      versioned: isProd,
      serverAccessLogsBucket: this.logsBucket,
      serverAccessLogsPrefix: 'documents-access-logs/',
      lifecycleRules: [
        // Transition procesamiento/ prefix to Glacier after 90 days, delete after 365
        {
          id: 'TransitionProcesamientoToGlacier',
          enabled: true,
          prefix: 'procesamiento/',
          transitions: [
            {
              storageClass: s3.StorageClass.GLACIER,
              transitionAfter: cdk.Duration.days(90),
            },
          ],
          expiration: cdk.Duration.days(365),
        },
        // Auto-delete errores/ after 30 days
        {
          id: 'DeleteErrorsAfter30Days',
          enabled: true,
          prefix: 'errores/',
          expiration: cdk.Duration.days(30),
        },
      ],
      cors: [
        {
          allowedMethods: [s3.HttpMethods.PUT, s3.HttpMethods.GET],
          allowedOrigins: ['*'], // Se restringirá en producción con el dominio específico
          allowedHeaders: ['*'],
          maxAge: 3600,
        },
      ],
    });

    // ─────────────────────────────────────────────────────────────────────────
    // DynamoDB Table — Single Table Design
    // Req 10.3: Cifrado at-rest con clave KMS gestionada por AWS
    // ─────────────────────────────────────────────────────────────────────────
    this.documentsTable = new dynamodb.Table(this, 'DocumentsTable', {
      tableName: `${prefix}-documents`,
      partitionKey: {
        name: 'PK',
        type: dynamodb.AttributeType.STRING,
      },
      sortKey: {
        name: 'SK',
        type: dynamodb.AttributeType.STRING,
      },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
      removalPolicy,
      pointInTimeRecovery: isProd,
      timeToLiveAttribute: 'ttl',
    });

    // GSI1 para consultas por usuario+estado
    this.documentsTable.addGlobalSecondaryIndex({
      indexName: 'GSI1',
      partitionKey: {
        name: 'GSI1PK',
        type: dynamodb.AttributeType.STRING,
      },
      sortKey: {
        name: 'GSI1SK',
        type: dynamodb.AttributeType.STRING,
      },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    // ─────────────────────────────────────────────────────────────────────────
    // CfnOutputs — Exportar valores para cross-stack reference
    // Req 10.5: Exportar valores necesarios entre stacks mediante CfnOutput
    // ─────────────────────────────────────────────────────────────────────────
    new cdk.CfnOutput(this, 'DocumentsBucketArn', {
      value: this.documentsBucket.bucketArn,
      description: 'ARN del bucket de documentos',
      exportName: `${prefix}-documents-bucket-arn`,
    });

    new cdk.CfnOutput(this, 'DocumentsBucketName', {
      value: this.documentsBucket.bucketName,
      description: 'Nombre del bucket de documentos',
      exportName: `${prefix}-documents-bucket-name`,
    });

    new cdk.CfnOutput(this, 'LogsBucketArn', {
      value: this.logsBucket.bucketArn,
      description: 'ARN del bucket de logs de acceso',
      exportName: `${prefix}-logs-bucket-arn`,
    });

    new cdk.CfnOutput(this, 'DocumentsTableName', {
      value: this.documentsTable.tableName,
      description: 'Nombre de la tabla DynamoDB de documentos',
      exportName: `${prefix}-documents-table-name`,
    });

    new cdk.CfnOutput(this, 'DocumentsTableArn', {
      value: this.documentsTable.tableArn,
      description: 'ARN de la tabla DynamoDB de documentos',
      exportName: `${prefix}-documents-table-arn`,
    });

    new cdk.CfnOutput(this, 'S3KmsKeyArn', {
      value: this.s3KmsKey.keyArn,
      description: 'ARN de la KMS key para cifrado S3',
      exportName: `${prefix}-s3-kms-key-arn`,
    });

    // ─────────────────────────────────────────────────────────────────────────
    // CDK Aspects — Validar tags obligatorios durante síntesis
    // Req 10.4: Tags obligatorios validados via CDK Aspects
    // ─────────────────────────────────────────────────────────────────────────
    cdk.Aspects.of(this).add(new MandatoryTagsAspect());
  }
}

/**
 * CDK Aspect que valida la presencia de tags obligatorios
 * en todos los recursos taggables durante la síntesis.
 *
 * Tags requeridos: Project, Environment, Owner, CostCenter
 * Req 10.4: Usar CDK Aspects para validar la presencia de tags durante la síntesis.
 *
 * Nota: Los tags se aplican via cdk.Tags.of() en datamask.ts antes de la síntesis.
 * Este Aspect verifica que el TagManager de cada recurso tiene los tags requeridos.
 */
export class MandatoryTagsAspect implements cdk.IAspect {
  private static readonly REQUIRED_TAGS = ['Project', 'Environment', 'Owner', 'CostCenter'];

  public visit(node: Construct): void {
    if (cdk.TagManager.isTaggable(node)) {
      const renderedTags = node.tags.renderTags();
      // renderTags() devuelve un array de {Key, Value} para la mayoría de recursos
      if (Array.isArray(renderedTags)) {
        const tagKeys = renderedTags.map(
          (t: { Key?: string; key?: string }) => t.Key || t.key || ''
        );
        for (const requiredTag of MandatoryTagsAspect.REQUIRED_TAGS) {
          if (!tagKeys.includes(requiredTag)) {
            cdk.Annotations.of(node as unknown as Construct).addWarning(
              `Tag obligatorio '${requiredTag}' no encontrado en recurso. ` +
              `Tags requeridos: ${MandatoryTagsAspect.REQUIRED_TAGS.join(', ')}.`
            );
          }
        }
      }
    }
  }
}
