import * as cdk from 'aws-cdk-lib';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as cw_actions from 'aws-cdk-lib/aws-cloudwatch-actions';
import * as logs from 'aws-cdk-lib/aws-logs';
import { Construct } from 'constructs';
import { DataMaskStackProps } from './shared-props';

/**
 * ObservabilityStack: CloudWatch Alarms, SNS Topic para alertas,
 * y retención de logs.
 *
 * Requirements:
 * - 10.6: CloudWatch alarms con notificación SNS para errores Lambda >5 en 5min,
 *         latencia P99 API Gateway >3s en 5min, errores Textract/Comprehend/Bedrock >3 en 5min
 * - 11.5: Retención de logs CloudWatch de 90 días
 *
 * Esta stack es independiente y usa convenciones de nombres para referenciar
 * las funciones Lambda y la API Gateway sin requerir props cross-stack.
 */
export class ObservabilityStack extends cdk.Stack {
  /** SNS Topic para alertas de CloudWatch */
  public readonly alertsTopic: sns.Topic;

  constructor(scope: Construct, id: string, props: DataMaskStackProps) {
    super(scope, id, props);

    const { environment, prefix } = props;

    // Nombres de funciones Lambda por convención
    const lambdaFunctionNames = [
      `${prefix}-trigger`,
      `${prefix}-detection`,
      `${prefix}-redaction`,
      `${prefix}-api`,
    ];

    // Nombre de la API Gateway REST por convención
    const apiGatewayName = `${prefix}-api`;

    // ─────────────────────────────────────────────────────────────────────────
    // SNS Topic para alertas
    // Req 10.6: SNS topic para notificaciones de alarmas
    // ─────────────────────────────────────────────────────────────────────────
    this.alertsTopic = new sns.Topic(this, 'AlertsTopic', {
      topicName: `${prefix}-alerts`,
      displayName: `DataMask ${environment} — Alertas de Observabilidad`,
    });

    // ─────────────────────────────────────────────────────────────────────────
    // CloudWatch Alarms — Errores Lambda > 5 en 5 minutos
    // Req 10.6: Lambda errors >5 in 5min
    // ─────────────────────────────────────────────────────────────────────────
    for (const functionName of lambdaFunctionNames) {
      const alarm = new cloudwatch.Alarm(this, `LambdaErrors-${functionName}`, {
        alarmName: `${prefix}-lambda-errors-${functionName}`,
        alarmDescription:
          `Alarma: errores en Lambda ${functionName} superan 5 en 5 minutos (${environment})`,
        metric: new cloudwatch.Metric({
          namespace: 'AWS/Lambda',
          metricName: 'Errors',
          dimensionsMap: {
            FunctionName: functionName,
          },
          statistic: 'Sum',
          period: cdk.Duration.minutes(5),
        }),
        threshold: 5,
        evaluationPeriods: 1,
        comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
        treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
      });

      alarm.addAlarmAction(new cw_actions.SnsAction(this.alertsTopic));
      alarm.addOkAction(new cw_actions.SnsAction(this.alertsTopic));
    }

    // ─────────────────────────────────────────────────────────────────────────
    // CloudWatch Alarm — Latencia P99 API Gateway > 3s en 5 minutos
    // Req 10.6: API Gateway P99 latency >3s in 5min
    // ─────────────────────────────────────────────────────────────────────────
    const apiLatencyAlarm = new cloudwatch.Alarm(this, 'ApiGatewayP99Latency', {
      alarmName: `${prefix}-api-p99-latency`,
      alarmDescription:
        `Alarma: latencia P99 de API Gateway supera 3 segundos en 5 minutos (${environment})`,
      metric: new cloudwatch.Metric({
        namespace: 'AWS/ApiGateway',
        metricName: 'Latency',
        dimensionsMap: {
          ApiName: apiGatewayName,
        },
        statistic: 'p99',
        period: cdk.Duration.minutes(5),
      }),
      threshold: 3000, // 3000ms = 3 segundos
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    apiLatencyAlarm.addAlarmAction(new cw_actions.SnsAction(this.alertsTopic));
    apiLatencyAlarm.addOkAction(new cw_actions.SnsAction(this.alertsTopic));

    // ─────────────────────────────────────────────────────────────────────────
    // CloudWatch Alarms — Errores de servicios AWS (Textract/Comprehend/Bedrock)
    // > 3 en 5 minutos
    // Req 10.6: Textract/Comprehend/Bedrock errors >3 in 5min
    //
    // Se usa una custom metric publicada por las Lambdas del pipeline.
    // Namespace: DataMask/{env}
    // MetricName: ServiceInvocationError
    // Dimensión: ServiceName = Textract | Comprehend | Bedrock
    // ─────────────────────────────────────────────────────────────────────────
    const awsServices = ['Textract', 'Comprehend', 'Bedrock'];

    for (const serviceName of awsServices) {
      const alarm = new cloudwatch.Alarm(this, `ServiceErrors-${serviceName}`, {
        alarmName: `${prefix}-${serviceName.toLowerCase()}-errors`,
        alarmDescription:
          `Alarma: errores de invocación a ${serviceName} superan 3 en 5 minutos (${environment})`,
        metric: new cloudwatch.Metric({
          namespace: `DataMask/${environment}`,
          metricName: 'ServiceInvocationError',
          dimensionsMap: {
            ServiceName: serviceName,
          },
          statistic: 'Sum',
          period: cdk.Duration.minutes(5),
        }),
        threshold: 3,
        evaluationPeriods: 1,
        comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
        treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
      });

      alarm.addAlarmAction(new cw_actions.SnsAction(this.alertsTopic));
      alarm.addOkAction(new cw_actions.SnsAction(this.alertsTopic));
    }

    // ─────────────────────────────────────────────────────────────────────────
    // CloudWatch Log Groups — Retención de 90 días
    // Req 11.5: CloudWatch log retention of 90 days
    //
    // Crear log groups para cada Lambda con retención explícita de 90 días.
    // Si las Lambdas crean sus propios log groups al ejecutarse, CDK los
    // adoptará gracias al nombre lógico coincidente.
    // ─────────────────────────────────────────────────────────────────────────
    for (const functionName of lambdaFunctionNames) {
      new logs.LogGroup(this, `LogGroup-${functionName}`, {
        logGroupName: `/aws/lambda/${functionName}`,
        retention: logs.RetentionDays.THREE_MONTHS, // 90 días
        removalPolicy: environment === 'prod'
          ? cdk.RemovalPolicy.RETAIN
          : cdk.RemovalPolicy.DESTROY,
      });
    }

    // Log group para API Gateway access logs
    new logs.LogGroup(this, 'ApiGatewayLogGroup', {
      logGroupName: `/aws/apigateway/${prefix}-api`,
      retention: logs.RetentionDays.THREE_MONTHS, // 90 días
      removalPolicy: environment === 'prod'
        ? cdk.RemovalPolicy.RETAIN
        : cdk.RemovalPolicy.DESTROY,
    });

    // ─────────────────────────────────────────────────────────────────────────
    // CfnOutputs — Exportar valores para referencia desde otras stacks
    // ─────────────────────────────────────────────────────────────────────────
    new cdk.CfnOutput(this, 'AlertsTopicArn', {
      value: this.alertsTopic.topicArn,
      description: 'ARN del SNS topic de alertas',
      exportName: `${prefix}-alerts-topic-arn`,
    });

    new cdk.CfnOutput(this, 'AlertsTopicName', {
      value: this.alertsTopic.topicName!,
      description: 'Nombre del SNS topic de alertas',
      exportName: `${prefix}-alerts-topic-name`,
    });
  }
}
