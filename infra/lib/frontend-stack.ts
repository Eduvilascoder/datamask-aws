import * as cdk from 'aws-cdk-lib';
import * as amplify from 'aws-cdk-lib/aws-amplify';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';
import { DataMaskStackProps } from './shared-props';

/**
 * FrontendStack: Amplify App conectada a GitHub, CloudFront Distribution con
 * security headers (CSP, HSTS, X-Frame-Options), TLS 1.2+ (TLSv1.2_2021),
 * cache policies optimizadas, y WAF opcional.
 *
 * Requirements: 10.1, 10.5, 11.1
 */
export class FrontendStack extends cdk.Stack {
  /** CloudFront Distribution */
  public readonly distribution: cloudfront.Distribution;

  constructor(scope: Construct, id: string, props: DataMaskStackProps) {
    super(scope, id, props);

    const { environment, prefix } = props;

    // ─────────────────────────────────────────────────────────────────────────
    // GitHub Token de Secrets Manager (placeholder reference)
    // Seguridad: El token de GitHub nunca se hardcodea en el código fuente.
    // Se debe crear manualmente en Secrets Manager con el nombre indicado.
    // ─────────────────────────────────────────────────────────────────────────
    const githubTokenSecret = secretsmanager.Secret.fromSecretNameV2(
      this,
      'GitHubToken',
      `${prefix}-github-token`
    );

    // ─────────────────────────────────────────────────────────────────────────
    // IAM Role para Amplify (mínimo privilegio)
    // ─────────────────────────────────────────────────────────────────────────
    const amplifyRole = new iam.Role(this, 'AmplifyServiceRole', {
      roleName: `${prefix}-amplify-service-role`,
      assumedBy: new iam.ServicePrincipal('amplify.amazonaws.com'),
      description: 'Role de servicio para AWS Amplify App con permisos mínimos',
    });

    // Amplify necesita permisos básicos para build y deploy
    amplifyRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'AmplifyBuildPermissions',
        effect: iam.Effect.ALLOW,
        actions: [
          'amplify:GetApp',
          'amplify:GetBranch',
          'amplify:UpdateApp',
          'amplify:UpdateBranch',
          'amplify:CreateDeployment',
          'amplify:StartDeployment',
        ],
        resources: [
          `arn:aws:amplify:${cdk.Aws.REGION}:${cdk.Aws.ACCOUNT_ID}:apps/*`,
        ],
      })
    );

    // ─────────────────────────────────────────────────────────────────────────
    // Amplify App — conectada al repositorio GitHub
    // Req 10.1: Amplify App definida en CDK
    // CI/CD: Build automático en push a main
    // ─────────────────────────────────────────────────────────────────────────
    const amplifyApp = new amplify.CfnApp(this, 'AmplifyApp', {
      name: `${prefix}-frontend`,
      description: `DataMask AWS Frontend (${environment})`,
      repository: 'https://github.com/Eduvilascoder/DataMask-AWS',
      accessToken: githubTokenSecret.secretValue.unsafeUnwrap(),
      iamServiceRole: amplifyRole.roleArn,
      platform: 'WEB_COMPUTE',
      buildSpec: [
        'version: 1',
        'applications:',
        '  - frontend:',
        '      phases:',
        '        preBuild:',
        '          commands:',
        '            - cd frontend',
        '            - npm ci',
        '        build:',
        '          commands:',
        '            - npm run build',
        '      artifacts:',
        '        baseDirectory: frontend/build',
        '        files:',
        '          - "**/*"',
        '      cache:',
        '        paths:',
        '          - frontend/node_modules/**/*',
        '    appRoot: .',
      ].join('\n'),
      environmentVariables: [
        {
          name: 'AMPLIFY_MONOREPO_APP_ROOT',
          value: '.',
        },
        {
          name: 'VITE_ENVIRONMENT',
          value: environment,
        },
      ],
      customRules: [
        // SPA: redirigir todas las rutas al index.html para React Router
        {
          source: '</^[^.]+$|\\.(?!(css|gif|ico|jpg|js|png|txt|svg|woff|woff2|ttf|map|json)$)([^.]+$)/>',
          target: '/index.html',
          status: '200',
        },
      ],
    });

    // ─────────────────────────────────────────────────────────────────────────
    // Amplify Branch — main (auto-build en push)
    // ─────────────────────────────────────────────────────────────────────────
    const amplifyBranch = new amplify.CfnBranch(this, 'AmplifyBranchMain', {
      appId: amplifyApp.attrAppId,
      branchName: 'main',
      description: `Branch principal para ambiente ${environment}`,
      enableAutoBuild: true,
      stage: environment === 'prod' ? 'PRODUCTION' : 'DEVELOPMENT',
      environmentVariables: [
        {
          name: 'VITE_API_ENDPOINT',
          value: `https://api.datamask-${environment}.example.com`,
        },
      ],
    });

    // ─────────────────────────────────────────────────────────────────────────
    // CloudFront Response Headers Policy — Security Headers
    // Req 11.1: Security headers (CSP, HSTS, X-Frame-Options, etc.)
    // ─────────────────────────────────────────────────────────────────────────
    const securityHeadersPolicy = new cloudfront.ResponseHeadersPolicy(
      this,
      'SecurityHeadersPolicy',
      {
        responseHeadersPolicyName: `${prefix}-security-headers`,
        comment: `Security headers policy para DataMask ${environment}`,
        securityHeadersBehavior: {
          contentSecurityPolicy: {
            contentSecurityPolicy: [
              "default-src 'self'",
              "script-src 'self' 'unsafe-inline'",
              "style-src 'self' 'unsafe-inline'",
              "img-src 'self' data: https:",
              "font-src 'self' data:",
              "connect-src 'self' https://*.amazonaws.com https://*.execute-api.us-east-1.amazonaws.com",
              "frame-ancestors 'none'",
              "base-uri 'self'",
              "form-action 'self'",
            ].join('; '),
            override: true,
          },
          strictTransportSecurity: {
            accessControlMaxAge: cdk.Duration.days(365),
            includeSubdomains: true,
            preload: true,
            override: true,
          },
          frameOptions: {
            frameOption: cloudfront.HeadersFrameOption.DENY,
            override: true,
          },
          contentTypeOptions: {
            override: true,
          },
          referrerPolicy: {
            referrerPolicy: cloudfront.HeadersReferrerPolicy.STRICT_ORIGIN_WHEN_CROSS_ORIGIN,
            override: true,
          },
          xssProtection: {
            protection: true,
            modeBlock: true,
            override: true,
          },
        },
      }
    );

    // ─────────────────────────────────────────────────────────────────────────
    // CloudFront Cache Policy — optimizada para SPA (React + Vite)
    // ─────────────────────────────────────────────────────────────────────────
    const spaCachePolicy = new cloudfront.CachePolicy(this, 'SpaCachePolicy', {
      cachePolicyName: `${prefix}-spa-cache-policy`,
      comment: `Cache policy para frontend SPA DataMask ${environment}`,
      defaultTtl: cdk.Duration.days(1),
      maxTtl: cdk.Duration.days(365),
      minTtl: cdk.Duration.seconds(0),
      enableAcceptEncodingGzip: true,
      enableAcceptEncodingBrotli: true,
      headerBehavior: cloudfront.CacheHeaderBehavior.none(),
      queryStringBehavior: cloudfront.CacheQueryStringBehavior.none(),
      cookieBehavior: cloudfront.CacheCookieBehavior.none(),
    });

    // ─────────────────────────────────────────────────────────────────────────
    // CloudFront Distribution
    // Req 11.1: TLS 1.2+ mínimo (TLSv1.2_2021 security policy)
    // Amplify como origin, security headers, cache policies
    // ─────────────────────────────────────────────────────────────────────────
    this.distribution = new cloudfront.Distribution(this, 'Distribution', {
      comment: `DataMask Frontend Distribution (${environment})`,
      enabled: true,
      defaultRootObject: 'index.html',
      minimumProtocolVersion: cloudfront.SecurityPolicyProtocol.TLS_V1_2_2021,
      httpVersion: cloudfront.HttpVersion.HTTP2_AND_3,
      priceClass: cloudfront.PriceClass.PRICE_CLASS_100,
      defaultBehavior: {
        origin: new origins.HttpOrigin(
          `main.${amplifyApp.attrDefaultDomain}`,
          {
            protocolPolicy: cloudfront.OriginProtocolPolicy.HTTPS_ONLY,
            httpsPort: 443,
          }
        ),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        cachePolicy: spaCachePolicy,
        responseHeadersPolicy: securityHeadersPolicy,
        allowedMethods: cloudfront.AllowedMethods.ALLOW_GET_HEAD_OPTIONS,
        compress: true,
      },
      // Error responses: redirigir errores 403/404 a index.html para SPA routing
      errorResponses: [
        {
          httpStatus: 403,
          responseHttpStatus: 200,
          responsePagePath: '/index.html',
          ttl: cdk.Duration.seconds(0),
        },
        {
          httpStatus: 404,
          responseHttpStatus: 200,
          responsePagePath: '/index.html',
          ttl: cdk.Duration.seconds(0),
        },
      ],
    });

    // ─────────────────────────────────────────────────────────────────────────
    // CfnOutputs — Exportar valores para cross-stack reference
    // Req 10.5: Exportar valores necesarios entre stacks
    // ─────────────────────────────────────────────────────────────────────────
    new cdk.CfnOutput(this, 'AmplifyAppId', {
      value: amplifyApp.attrAppId,
      description: 'ID de la aplicación Amplify',
      exportName: `${prefix}-amplify-app-id`,
    });

    new cdk.CfnOutput(this, 'AmplifyDefaultDomain', {
      value: amplifyApp.attrDefaultDomain,
      description: 'Dominio por defecto de Amplify',
      exportName: `${prefix}-amplify-default-domain`,
    });

    new cdk.CfnOutput(this, 'CloudFrontDistributionId', {
      value: this.distribution.distributionId,
      description: 'ID de la distribución CloudFront',
      exportName: `${prefix}-cloudfront-distribution-id`,
    });

    new cdk.CfnOutput(this, 'CloudFrontDomainName', {
      value: this.distribution.distributionDomainName,
      description: 'Domain name de la distribución CloudFront',
      exportName: `${prefix}-cloudfront-domain-name`,
    });

    new cdk.CfnOutput(this, 'FrontendUrl', {
      value: `https://${this.distribution.distributionDomainName}`,
      description: 'URL del frontend (CloudFront)',
      exportName: `${prefix}-frontend-url`,
    });
  }
}
