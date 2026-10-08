import * as pulumi from "@pulumi/pulumi";
import * as aws from "@pulumi/aws";

export interface IAMArgs {
  environment: string;
  bucketArn: pulumi.Output<string>;
  isProd: boolean;
}

export class IAMComponent extends pulumi.ComponentResource {
  public readonly userName: pulumi.Output<string>;
  public readonly accessKeyId: pulumi.Output<string>;
  public readonly roleArn: pulumi.Output<string>;

  constructor(name: string, args: IAMArgs, opts?: pulumi.ComponentResourceOptions) {
    super("iac-cd-bench:IAM", name, args, opts);

    const user = new aws.iam.User(`${name}-user`, {
      path: "/",
      tags: {
        Name: `${name}-user`,
        Environment: args.environment,
      },
    });

    const accessKey = new aws.iam.AccessKey(`${name}-key`, {
      user: user.name,
    }, { dependsOn: [user] });

    const role = new aws.iam.Role(`${name}-role`, {
      assumeRolePolicy: JSON.stringify({
        Version: "2012-10-17",
        Statement: [{
          Effect: "Allow",
          Principal: {
            AWS: args.isProd
              ? `arn:aws:iam::123456789012:role/${name}-assumed-role`
              : `arn:aws:iam::123456789012:root`,
          },
          Action: "sts:AssumeRole",
        }],
      }),
      path: "/",
      maxSessionDuration: 3600,
      tags: {
        Name: `${name}-role`,
      },
    });

    const policy = new aws.iam.Policy(`${name}-policy`, {
      policy: JSON.stringify({
        Version: "2012-10-17",
        Statement: [{
          Effect: "Allow",
          Action: args.isProd
            ? ["s3:GetObject", "s3:PutObject"]
            : ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
          Resource: pulumi.interpolate`${args.bucketArn}/*`,
        }],
      }),
      name: `${name}-policy`,
    });

    const attachment = new aws.iam.UserPolicyAttachment(`${name}-attach`, {
      policyArn: policy.arn,
      user: user.name,
    }, { dependsOn: [user, policy] });

    this.userName = user.name;
    this.accessKeyId = accessKey.urn;
    this.roleArn = role.arn;
  }
}