import * as pulumi from "@pulumi/pulumi";
import { S3BucketComponent } from "./components/bucket";
import { RDSComponent } from "./components/rds";
import { IAMComponent } from "./components/iam";

// Stack configuration
// `new pulumi.Config()` namespaces to the *project*, so `require("app:env")`
// looked up `<project>:app:env` and could never resolve — Pulumi.dev.yaml
// declares namespace `app`, key `env`. A namespaced Config is the correct
// reader for `app:` and `aws:` keys.
const appConfig = new pulumi.Config("app");
const awsConfig = new pulumi.Config("aws");
const env = appConfig.require("env");
const awsRegion = awsConfig.require("region");
const instanceClass = appConfig.require("instanceClass");
const instanceCount = appConfig.requireNumber("instanceCount");
const dbPasswordSecret = appConfig.requireSecret("dbPassword");
const dbUser = appConfig.require("dbUser");
const dbName = appConfig.require("dbName");
const bucketName = appConfig.require("bucketName");

// Export configuration
export const environment = env;
export const region = awsRegion;

// Infrastructure components
const bucket = new S3BucketComponent(bucketName, {
  versioning: true,
  encryption: true,
  replicationTarget: appConfig.get("replicationTargetBucket"),
});

const db = new RDSComponent(`${bucketName}-db`, {
  instanceClass,
  dbUser,
  dbName,
  dbPassword: dbPasswordSecret,
  multiAz: env === "prod",
  deletionProtection: true,
  backupRetention: 7,
  publiclyAccessible: false,
  storageEncrypted: true,
});

const iam = new IAMComponent(`${bucketName}-iam`, {
  environment: env,
  bucketArn: bucket.arn,
  isProd: env === "prod",
});

// Export outputs
export const bucketArn = bucket.arn;
export const dbEndpoint = db.endpoint;
export const dbOutputPassword = db.password;
export const iamUserName = iam.userName;
export const iamAccessKeyId = iam.accessKeyId;
export const iamRoleArn = iam.roleArn;