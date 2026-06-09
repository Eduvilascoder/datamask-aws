import * as cdk from 'aws-cdk-lib';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as kms from 'aws-cdk-lib/aws-kms';

/**
 * Propiedades compartidas por todas las stacks de DataMask.
 */
export interface DataMaskStackProps extends cdk.StackProps {
  /** Ambiente de despliegue: 'dev' o 'prod' */
  readonly environment: string;
  /** Prefijo para nombres de recursos: 'datamask-{environment}' */
  readonly prefix: string;
}

/**
 * Propiedades para ComputeStack que incluyen referencias cross-stack
 * desde StorageStack como strings para evitar dependencias circulares
 * causadas por S3 event notifications (Bucket.addEventNotification crea
 * un custom resource que necesita referenciar el Lambda ARN de vuelta
 * en el template del bucket).
 */
export interface ComputeStackProps extends DataMaskStackProps {
  /** ARN del bucket S3 de documentos */
  readonly documentsBucketArn: string;
  /** Nombre del bucket S3 de documentos */
  readonly documentsBucketName: string;
  /** ARN de la tabla DynamoDB de documentos */
  readonly documentsTableArn: string;
  /** Nombre de la tabla DynamoDB de documentos */
  readonly documentsTableName: string;
  /** ARN de la KMS key para cifrado S3 */
  readonly s3KmsKeyArn: string;
}

/**
 * Propiedades para ApiStack que incluyen referencias a recursos de Storage.
 * ApiStack no configura S3 event notifications, por lo que puede recibir
 * L2 constructs directamente sin causar ciclos.
 */
export interface ApiStackProps extends DataMaskStackProps {
  /** Bucket S3 de documentos (desde StorageStack) */
  readonly documentsBucket: s3.IBucket;
  /** Tabla DynamoDB de documentos (desde StorageStack) */
  readonly documentsTable: dynamodb.ITable;
  /** KMS key para cifrado S3 (desde StorageStack) */
  readonly s3KmsKey: kms.IKey;
  /** URL del portal de IAM Identity Center (AWS access portal) */
  readonly ssoStartUrl?: string;
  /** Región donde está habilitado IAM Identity Center */
  readonly ssoRegion?: string;
  /** Nombre del cliente OIDC a registrar en Identity Center */
  readonly ssoClientName?: string;
}
