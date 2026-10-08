import * as pulumi from "@pulumi/pulumi";
import * as aws from "@pulumi/aws";

export interface BucketArgs {
  versioning: boolean;
  encryption: boolean;
  replicationTarget?: string;
}

export class S3BucketComponent extends pulumi.ComponentResource {
  public readonly arn: pulumi.Output<string>;
  public readonly id: pulumi.Output<string>;

  constructor(name: string, args: BucketArgs, opts?: pulumi.ComponentResourceOptions) {
    super("iac-cd-bench:S3Bucket", name, args, opts);

    const bucket = new aws.s3.Bucket(`${name}-bucket`, {
      versioning: args.versioning ? { enabled: true } : undefined,
      serverSideEncryptionConfiguration: args.encryption ? {
        rule: {
          applyServerSideEncryptionByDefault: {
            sseAlgorithm: "AES256",
          },
        },
      } : undefined,
    });

    if (args.encryption) {
      new aws.s3.BucketPublicAccessBlock(`${name}-public`, {
        bucket: bucket.id,
        blockPublicAcls: true,
        blockPublicPolicy: true,
        ignorePublicAcls: true,
        restrictPublicBuckets: true,
      }, { dependsOn: [bucket] });
    }

    if (args.replicationTarget) {
      new aws.s3.BucketReplicationConfig(`${name}-replication`, {
        bucket: bucket.id,
        role: "arn:aws:iam::123456789012:role/s3-replication",
        rules: [{
          id: "replication",
          status: "Enabled",
          destination: {
            bucket: `arn:aws:s3:::${args.replicationTarget}`,
          },
        }],
      }, { dependsOn: [bucket] });
    }

    this.arn = bucket.arn;
    this.id = bucket.id;
  }
}