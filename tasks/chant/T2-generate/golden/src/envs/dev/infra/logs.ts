/**
 * dev environment — application logs bucket + scoped reader identity.
 *
 * Same pattern as `assets`/`reader` in `./main.ts`, for a second, independent
 * bucket. Discovered and built alongside main.ts by `chant build
 * src/envs/dev/infra` — every .ts file under this directory is part of the
 * same build root.
 */

import { ReaderIam, SecureBucket } from "../../../composites/index.js";

const ENV = "dev";
const REGION = "us-east-1";

export const logs = SecureBucket({
  name: "myapp-logs-dev",
  env: ENV,
  region: REGION,
  component: "logs",
});

// Scoped to myapp-logs-dev only — additionalActions is an enumerated list,
// not a wildcard, so this reader cannot reach the assets bucket.
export const logsReader = ReaderIam({
  name: "myapp-logs-dev",
  env: ENV,
  bucketName: "myapp-logs-dev",
  trust: { mode: "account", accountID: "123456789012" },
  additionalActions: ["s3:PutObject"],
  component: "logs",
});
