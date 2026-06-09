#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { StorageStack } from '../lib/storage-stack';
import { ComputeStack } from '../lib/compute-stack';
import { ApiStack } from '../lib/api-stack';
import { FrontendStack } from '../lib/frontend-stack';
import { ObservabilityStack } from '../lib/observability-stack';

const app = new cdk.App();

// Validar parámetro de contexto 'environment'
const environment = app.node.tryGetContext('environment');

if (!environment || !['dev', 'prod'].includes(environment)) {
  throw new Error(
    `El parámetro de contexto 'environment' es obligatorio y debe ser 'dev' o 'prod'. ` +
    `Uso: cdk synth -c environment=dev|prod. Valor recibido: '${environment ?? ''}'`
  );
}

// Configuración de cuenta y región desde contexto o defaults en cdk.json
const account = app.node.tryGetContext('account') || '339712829454';
const region = app.node.tryGetContext('region') || 'us-east-1';

const env: cdk.Environment = { account, region };

// Tags obligatorios aplicados a todas las stacks
const tags: Record<string, string> = {
  Project: 'DataMask',
  Environment: environment,
  Owner: 'EduTheCoder',
  CostCenter: `datamask-${environment}`,
};

// Nombre base para recursos
const prefix = `datamask-${environment}`;

// ─────────────────────────────────────────────────────────────────────────
// Instanciar stacks con dependencias
// ─────────────────────────────────────────────────────────────────────────

const storageStack = new StorageStack(app, `${prefix}-storage`, {
  env,
  environment,
  prefix,
});

// ComputeStack recibe ARNs/nombres como strings para evitar dependencias
// circulares causadas por cross-stack token references con S3 event notifications.
const computeStack = new ComputeStack(app, `${prefix}-compute`, {
  env,
  environment,
  prefix,
  documentsBucketArn: storageStack.documentsBucket.bucketArn,
  documentsBucketName: storageStack.documentsBucket.bucketName,
  documentsTableArn: storageStack.documentsTable.tableArn,
  documentsTableName: storageStack.documentsTable.tableName,
  s3KmsKeyArn: storageStack.s3KmsKey.keyArn,
});
computeStack.addDependency(storageStack);

// ApiStack recibe L2 constructs directamente (no configura event notifications)
const apiStack = new ApiStack(app, `${prefix}-api`, {
  env,
  environment,
  prefix,
  documentsBucket: storageStack.documentsBucket,
  documentsTable: storageStack.documentsTable,
  s3KmsKey: storageStack.s3KmsKey,
});
apiStack.addDependency(storageStack);

const frontendStack = new FrontendStack(app, `${prefix}-frontend`, {
  env,
  environment,
  prefix,
});

const observabilityStack = new ObservabilityStack(app, `${prefix}-observability`, {
  env,
  environment,
  prefix,
});

// Aplicar tags obligatorios a todas las stacks
const allStacks = [storageStack, computeStack, apiStack, frontendStack, observabilityStack];
for (const stack of allStacks) {
  for (const [key, value] of Object.entries(tags)) {
    cdk.Tags.of(stack).add(key, value);
  }
}
