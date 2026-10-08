## Task: Review PR diff for security issues

**Stack:** bare (plain hand-authored Kubernetes manifests, `kubectl apply -f`, no delivery tooling — no rendering pipeline, no policy engine, no CI gate beyond whatever the reviewer catches by eye)

You are given a PR diff against `prod/` in a bare golden repo. The diff
touches `prod/db-secret.yaml`, `prod/s3-bucket.yaml`, `prod/rds.yaml`, and
`prod/app.yaml`.

### PR Diff Preview

```diff
--- a/prod/db-secret.yaml
+++ b/prod/db-secret.yaml
 stringData:
-  password: "REPLACE_OUT_OF_BAND_NEVER_COMMIT_REAL_VALUE"
+  password: "Summer2026!Prod#db"

--- a/prod/s3-bucket.yaml
+++ b/prod/s3-bucket.yaml
 spec:
   name: myapp-assets-prod
   versioning:
     status: Enabled
-  publicAccessBlock:
-    blockPublicACLs: true
-    blockPublicPolicy: true
-    ignorePublicACLs: true
-    restrictPublicBuckets: true
+  policy: |
+    {
+      "Version": "2012-10-17",
+      "Statement": [{
+        "Effect": "Allow",
+        "Principal": "*",
+        "Action": "s3:GetObject",
+        "Resource": "arn:aws:s3:::myapp-assets-prod/*"
+      }]
+    }

--- a/prod/rds.yaml
+++ b/prod/rds.yaml
 spec:
   multiAZ: true
   storageEncrypted: true
-  publiclyAccessible: false
-  deletionProtection: true
+  publiclyAccessible: true
+  deletionProtection: false

--- a/prod/app.yaml
+++ b/prod/app.yaml
     spec:
       serviceAccountName: myapp-prod
       containers:
         - name: myapp
           image: docker.io/myapp/myapp:latest
           ports:
             - containerPort: 8080
-          resources:
-            requests:
-              cpu: 100m
-              memory: 128Mi
-            limits:
-              cpu: 500m
-              memory: 256Mi
```

### Your Task

Review this diff and identify security issues. For each one:
1. What is the issue, and which file/field introduces it?
2. Is it CRITICAL, HIGH, MEDIUM, or LOW severity?
3. What should be changed before this PR merges?

Then rank all the issues you found by severity, worst first.

### Context Files

{{scenario_spec}}
