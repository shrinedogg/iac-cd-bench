/**
 * prod environment — application logs bucket + scoped reader identity.
 *
 * Same pattern as `assets`/`reader` in `./main.ts`, for a second, independent
 * bucket. See `../../dev/infra/logs.ts`.
 */

import { ReaderIam, SecureBucket } from "../../../composites/index.js";

const ENV = "prod";
const REGION = "us-east-1";
const APP_NAMESPACE = "app";
const SERVICE_ACCOUNT = "myapp";

export const logs = SecureBucket({
  name: "myapp-logs-prod",
  env: ENV,
  region: REGION,
  component: "logs",
});

// Scoped to myapp-logs-prod only — additionalActions is an enumerated list,
// not a wildcard, so this reader cannot reach the assets bucket.
export const logsReader = ReaderIam({
  name: "myapp-logs-prod",
  env: ENV,
  bucketName: "myapp-logs-prod",
  trust: {
    mode: "oidc",
    providerARN: "arn:aws:iam::123456789012:oidc-provider/oidc.eks.us-east-1.amazonaws.com/id/EXAMPLEPROD",
    issuerHost: "oidc.eks.us-east-1.amazonaws.com/id/EXAMPLEPROD",
    serviceAccountNamespace: APP_NAMESPACE,
    serviceAccountName: SERVICE_ACCOUNT,
  },
  additionalActions: ["s3:PutObject"],
  component: "logs",
});
