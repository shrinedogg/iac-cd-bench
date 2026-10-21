## Task: Review PR diff for security issues

**Stack:** krops (Flux + CAPI/CAPA + ACK, konflate PR renders)

In krops, pull requests are reviewed as the **rendered Flux diff** (konflate
shows the blast radius of what will actually be applied). You are given the
rendered diff of PR #91, "onboard eu-central-1 + production-grade eu-west-1",
against the current repo, where:

- Per-cluster AWS resources live on the **management cluster** under
  `mgmt/aws/infrastructure/workload-resources/` (ACK S3/RDS/IAM), namespace
  `ack-system`, all carrying `services.k8s.aws/adoption-policy:
  adopt-or-create`, and non-default-region clusters carrying
  `services.k8s.aws/region: <region>`.
- The repo's RDS idiom is `manageMasterUserPassword: true` — RDS generates
  the master password and stores it in Secrets Manager; **no DB password
  ever sits in Git**.
- S3 buckets are secure-by-default: all four `publicAccessBlock` flags
  true, AES256 default encryption, versioning enabled, `BucketOwnerEnforced`,
  and a bucket policy that denies any request not using TLS
  (`DenyInsecureTransport`).
- The reader roles trust exactly one principal — the account root
  `arn:aws:iam::120392301094:root` — and grant only scoped read-only
  actions (explicit `s3:Get*`-style actions, and RDS Describe actions that
  support resource-level scoping). The repo comment is explicit: the trust
  principal must NOT be wildcarded.

### Rendered diff (PR #91)

```diff
--- a/mgmt/aws/infrastructure/workload-resources/dbinstance.yaml
+++ b/mgmt/aws/infrastructure/workload-resources/dbinstance.yaml
@@ db-eu-west-1 (existing) @@
 spec:
-  dbInstanceClass: db.t4g.micro
+  dbInstanceClass: db.m6g.large
   manageMasterUserPassword: true
-  multiAZ: false
+  multiAZ: true
@@ (new doc) @@
+apiVersion: rds.services.k8s.aws/v1alpha1
+kind: DBInstance
+metadata:
+  name: db-eu-central-1
+  namespace: ack-system
+  annotations:
+    services.k8s.aws/adoption-policy: adopt-or-create
+    services.k8s.aws/region: eu-central-1
+spec:
+  dbInstanceIdentifier: krops-eu-central-1-workload-db
+  engine: postgres
+  engineVersion: "17"
+  dbInstanceClass: db.t4g.micro
+  allocatedStorage: 20
+  storageType: gp3
+  masterUsername: postgres
+  masterUserPassword:
+    namespace: ack-system
+    name: db-master-password
+    key: password
+  multiAZ: false
+  publiclyAccessible: false
+  storageEncrypted: true
```

```diff
--- a/mgmt/aws/infrastructure/workload-resources/bucket.yaml
+++ b/mgmt/aws/infrastructure/workload-resources/bucket.yaml
@@ (new doc) @@
+apiVersion: s3.services.k8s.aws/v1alpha1
+kind: Bucket
+metadata:
+  name: data-eu-central-1
+  namespace: ack-system
+spec:
+  name: krops-120392301094-eu-central-1-workload-data
+  createBucketConfiguration:
+    locationConstraint: eu-central-1
+  publicAccessBlock:
+    blockPublicACLs: true
+    blockPublicPolicy: false
+    ignorePublicACLs: true
+    restrictPublicBuckets: true
+  encryption:
+    rules:
+      - applyServerSideEncryptionByDefault:
+          sseAlgorithm: AES256
+        bucketKeyEnabled: true
+  versioning:
+    status: Enabled
+  ownershipControls:
+    rules:
+      - objectOwnership: BucketOwnerEnforced
+  policy: |
+    {
+      "Version": "2012-10-17",
+      "Statement": [
+        {
+          "Sid": "PublicRead",
+          "Effect": "Allow",
+          "Principal": { "AWS": "*" },
+          "Action": "s3:GetObject",
+          "Resource": [
+            "arn:aws:s3:::krops-120392301094-eu-central-1-workload-data/*"
+          ]
+        }
+      ]
+    }
```

```diff
--- a/mgmt/aws/infrastructure/workload-resources/role.yaml
+++ b/mgmt/aws/infrastructure/workload-resources/role.yaml
@@ (new doc) @@
+apiVersion: iam.services.k8s.aws/v1alpha1
+kind: Role
+metadata:
+  name: reader-eu-central-1
+  namespace: ack-system
+  annotations:
+    services.k8s.aws/adoption-policy: adopt-or-create
+spec:
+  name: krops-eu-central-1-workload-reader
+  description: "Read-only access to krops S3 buckets and RDS instances"
+  assumeRolePolicyDocument: |
+    {
+      "Version": "2012-10-17",
+      "Statement": [
+        {
+          "Effect": "Allow",
+          "Principal": { "AWS": "arn:aws:iam::*:root" },
+          "Action": "sts:AssumeRole"
+        }
+      ]
+    }
+  inlinePolicies:
+    krops-read-only: |
+      {
+        "Version": "2012-10-17",
+        "Statement": [
+          {
+            "Sid": "ReadKrops",
+            "Effect": "Allow",
+            "Action": ["s3:*", "rds:*"],
+            "Resource": ["arn:aws:s3:::krops-*", "arn:aws:s3:::krops-*/*",
+                         "arn:aws:rds:*:120392301094:db:krops-*"]
+          }
+        ]
+      }
```

```diff
--- /dev/null
+++ b/mgmt/aws/infrastructure/workload-resources/db-password.yaml
+apiVersion: v1
+kind: Secret
+metadata:
+  name: db-master-password
+  namespace: ack-system
+type: Opaque
+stringData:
+  password: Sup3r-S3cret!2026
```

```diff
--- a/mgmt/aws/clusters/eu-west-1/staging/cluster.yaml
+++ b/mgmt/aws/clusters/eu-west-1/staging/cluster.yaml
@@ MachinePool workload-arm (existing) @@
 spec:
-  replicas: 3
+  replicas: 5
@@ AWSManagedMachinePool workload-arm (existing) @@
 spec:
   scaling:
     minSize: 3
-    maxSize: 5
+    maxSize: 8
```

Note the new bucket doc above is exactly as it would render: check its
annotations against the repo conventions stated at the top.

### Your task

Review the diff and answer:

1. What security issues exist?
2. Which are critical (merge-blocking) vs high vs warning? Which hunks are
   NOT problems?
3. What exactly must change before this PR can merge?

### Context Files

{{scenario_spec}}
