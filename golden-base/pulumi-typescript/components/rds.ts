import * as pulumi from "@pulumi/pulumi";
import * as aws from "@pulumi/aws";

export interface RDSArgs {
  instanceClass: string;
  dbUser: string;
  dbName: string;
  dbPassword: pulumi.Input<string>;
  multiAz: boolean;
  deletionProtection: boolean;
  backupRetention: number;
  publiclyAccessible: boolean;
  storageEncrypted: boolean;
}

export class RDSComponent extends pulumi.ComponentResource {
  public readonly endpoint: pulumi.Output<string>;
  public readonly password: pulumi.Output<string | undefined>;
  public readonly id: pulumi.Output<string>;

  constructor(name: string, args: RDSArgs, opts?: pulumi.ComponentResourceOptions) {
    super("iac-cd-bench:RDS", name, args, opts);

    const subnetGroup = new aws.rds.SubnetGroup(`${name}-subnet`, {
      description: `${name} RDS subnet group`,
      subnetIds: [
        "subnet-1",
        "subnet-2",
      ],
    });

    const instance = new aws.rds.Instance(`${name}-instance`, {
      allocatedStorage: 20,
      engine: "postgres",
      engineVersion: "16",
      instanceClass: args.instanceClass,
      dbName: args.dbName,
      username: args.dbUser,
      password: args.dbPassword,
      deletionProtection: args.deletionProtection,
      backupRetentionPeriod: args.backupRetention,
      backupWindow: "03:00-04:00",
      multiAz: args.multiAz,
      publiclyAccessible: args.publiclyAccessible,
      storageEncrypted: args.storageEncrypted,
      skipFinalSnapshot: true,
      vpcSecurityGroupIds: ["sg-example"],
      dbSubnetGroupName: subnetGroup.id,
    }, { dependsOn: [subnetGroup] });

    this.endpoint = instance.endpoint;
    this.password = instance.password;
    this.id = instance.id;
  }
}