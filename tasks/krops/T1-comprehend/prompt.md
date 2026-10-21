## Task: Predict delivery behavior from repo slice

**Stack:** krops (Flux + CAPI/CAPA + ACK, GitOps monorepo)

You are given a slice of a krops GitOps repository. The repo uses:
- Flux CD on a self-managed **management cluster** that reconciles everything in `mgmt/aws/`
- Cluster API (CAPI + CAPA) to provision EKS **workload clusters** (explicit `AWSManaged*` objects, no ClusterClass)
- AWS Controllers for Kubernetes (ACK) running on the **management cluster** for S3 / RDS / IAM
- A templated Flux **ResourceSet** (`fluxcd.controlplane.io/v1`) that remote-applies a per-region "bootstrap bag" (namespace, `cluster-vars` ConfigMap, pull secret, `FluxInstance`) into each workload cluster via its CAPI kubeconfig Secret
- SOPS + age for secrets (only `data`/`stringData` fields of `*.sops.yaml` files)

Workload clusters run no controllers and hold no credentials: their AWS resources
(S3, RDS, IAM) are declared under `mgmt/aws/infrastructure/workload-resources/`
and reconciled by the ACK controllers **on the management cluster**.

### Repo Structure (relevant slice)

```
mgmt/aws/
  kustomization.yaml                        # lists the four Flux Kustomization files below
  infrastructure/flux-ks.yaml               # 8 Flux Kustomizations (mgmt control plane)
  capi-providers/flux-ks.yaml               # 3 Flux Kustomizations (CAPI providers)
  addons/flux-ks.yaml                       # flux-apps Kustomization
  addons/flux-apps/flux-apps-rset.yaml      # the templated ResourceSet
  addons/flux-apps/regions/<region>/        # per-region bootstrap bags (NOT in any Flux Ks path)
  clusters/flux-ks.yaml                     # one Flux Kustomization per region
  clusters/<region>/staging/cluster.yaml    # CAPI cluster definitions
  infrastructure/workload-resources/        # ACK Bucket / DBInstance / Role CRs (ns: ack-system)
  infrastructure/aws-clusters-resources/    # AWSManagedCluster objects
```

### Context Files

`mgmt/aws/infrastructure/flux-ks.yaml` (abridged to the load-bearing docs):

```yaml
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: cert-manager
  namespace: flux-system
spec:
  interval: 5m
  prune: true
  sourceRef: {kind: GitRepository, name: flux-system}
  path: ./mgmt/aws/infrastructure/cert-manager
---
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: capi-operator
  namespace: flux-system
spec:
  interval: 5m
  prune: true
  sourceRef: {kind: GitRepository, name: flux-system}
  path: ./mgmt/aws/infrastructure/capi-operator
  dependsOn:
    - name: cert-manager
---
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: capa-identity
  namespace: flux-system
spec:
  interval: 5m
  prune: true
  sourceRef: {kind: GitRepository, name: flux-system}
  path: ./mgmt/aws/infrastructure/aws-identity
  dependsOn:
    - name: capi-operator
---
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: aws-managed-clusters
  namespace: flux-system
spec:
  interval: 5m
  prune: true
  sourceRef: {kind: GitRepository, name: flux-system}
  path: ./mgmt/aws/infrastructure/aws-clusters-resources
  dependsOn:
    - name: capa-identity
---
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: ack-controllers
  namespace: flux-system
spec:
  interval: 5m
  timeout: 10m
  prune: true
  wait: true            # workload-resources only applies once the ACK HelmReleases + CRDs are Ready
  sourceRef: {kind: GitRepository, name: flux-system}
  path: ./mgmt/aws/infrastructure/ack-controllers
---
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: workload-resources
  namespace: flux-system
spec:
  interval: 5m
  prune: true
  wait: false           # the CRs can take minutes to be Ready in AWS; ordering only needs the CRDs
  sourceRef: {kind: GitRepository, name: flux-system}
  path: ./mgmt/aws/infrastructure/workload-resources
  dependsOn:
    - name: ack-controllers
```

`mgmt/aws/addons/flux-ks.yaml`:

```yaml
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: flux-apps
  namespace: flux-system
spec:
  interval: 5m
  prune: true
  wait: false
  sourceRef: {kind: GitRepository, name: flux-system}
  path: ./mgmt/aws/addons/flux-apps
  dependsOn:
    - name: caaph-system
  decryption:
    provider: sops
    secretRef:
      name: sops-age
```

`mgmt/aws/addons/flux-apps/flux-apps-rset.yaml`:

```yaml
apiVersion: fluxcd.controlplane.io/v1
kind: ResourceSet
metadata:
  name: flux-apps
  namespace: default
spec:
  inputs:
    - region: "eu-north-1"
      cluster: "eu-north-1-workload"
    - region: "eu-west-1"
      cluster: "eu-west-1-workload"
  resourcesTemplate: |
    apiVersion: kustomize.toolkit.fluxcd.io/v1
    kind: Kustomization
    metadata:
      name: flux-apps-<< inputs.region >>
      namespace: default
    spec:
      interval: 5m
      prune: true
      wait: false
      kubeConfig:
        secretRef:
          name: << inputs.cluster >>-kubeconfig
      sourceRef:
        kind: GitRepository
        name: flux-system
        namespace: flux-system
      path: ./mgmt/aws/addons/flux-apps/regions/<< inputs.region >>
      decryption:
        provider: sops
        secretRef:
          name: sops-age
```

`mgmt/aws/addons/flux-apps/regions/eu-west-1/cluster-vars.yaml`:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: cluster-vars
  namespace: flux-system
data:
  AWS_REGION: "eu-west-1"
  CLUSTER_NAME: "eu-west-1-workload"
  AWS_ACCOUNT_ID: "120392301094"
```

`mgmt/aws/clusters/flux-ks.yaml` (one doc per region; both have
`prune: true`, `postBuild.substituteFrom: [ConfigMap krops-run (optional)]`
and `dependsOn: [capa-system]`):

```yaml
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: eu-north-1
  namespace: flux-system
spec:
  interval: 10m
  timeout: 20m
  prune: true
  wait: false
  sourceRef: {kind: GitRepository, name: flux-system}
  path: ./mgmt/aws/clusters/eu-north-1
  dependsOn:
    - name: capa-system
```

`mgmt/aws/clusters/eu-north-1/staging/cluster.yaml` (abridged — one of two
MachinePools; the kustomization applies `namePrefix: eu-north-1-`):

```yaml
apiVersion: cluster.x-k8s.io/v1beta1
kind: Cluster
metadata:
  name: workload
  namespace: default
  labels:
    fluxcd: enabled
    region: eu-north-1
spec:
  clusterNetwork:
    pods: {cidrBlocks: [192.168.0.0/16]}
    services: {cidrBlocks: [10.128.0.0/12]}
  controlPlaneRef:
    apiVersion: controlplane.cluster.x-k8s.io/v1beta2
    kind: AWSManagedControlPlane
    name: workload-control-plane
  infrastructureRef:
    apiVersion: infrastructure.cluster.x-k8s.io/v1beta2
    kind: AWSManagedCluster
    name: eu-north-1-workload
---
apiVersion: cluster.x-k8s.io/v1beta1
kind: MachinePool
metadata:
  name: workload-arm
  namespace: default
spec:
  clusterName: workload
  replicas: 3
  template:
    spec:
      clusterName: workload
      infrastructureRef:
        apiVersion: infrastructure.cluster.x-k8s.io/v1beta2
        kind: AWSManagedMachinePool
        name: workload-arm
      version: "1.34.6"
---
apiVersion: infrastructure.cluster.x-k8s.io/v1beta2
kind: AWSManagedMachinePool
metadata:
  name: workload-arm
  namespace: default
spec:
  instanceType: t4g.medium
  scaling: {minSize: 3, maxSize: 5}
  amiType: AL2023_ARM_64_STANDARD
  capacityType: onDemand
```

`mgmt/aws/infrastructure/workload-resources/dbinstance.yaml` (the eu-west-1
doc; the file holds one DBInstance per workload cluster, both in namespace
`ack-system`):

```yaml
apiVersion: rds.services.k8s.aws/v1alpha1
kind: DBInstance
metadata:
  name: db-eu-west-1
  namespace: ack-system
  annotations:
    services.k8s.aws/adoption-policy: adopt-or-create
    services.k8s.aws/region: eu-west-1
spec:
  dbInstanceIdentifier: krops-eu-west-1-workload-db
  engine: postgres
  engineVersion: "17"
  dbInstanceClass: db.t4g.micro
  allocatedStorage: 20
  storageType: gp3
  masterUsername: postgres
  manageMasterUserPassword: true
  multiAZ: false
  publiclyAccessible: false
  storageEncrypted: true
```

### Scenario

PR #87 is about to merge. It contains three changes:

1. In `mgmt/aws/infrastructure/workload-resources/dbinstance.yaml`, the
   `db-eu-west-1` DBInstance's `dbInstanceClass` changes from `db.t4g.micro`
   to `db.m6g.large`.
2. A new workload cluster is onboarded in region `eu-central-1`: a new region
   bundle `mgmt/aws/addons/flux-apps/regions/eu-central-1/` (namespace,
   `cluster-vars` with `CLUSTER_NAME: eu-central-1-workload`, and a
   `FluxInstance` whose sync path is `workload/eu-central-01`), a new input
   `{region: eu-central-1, cluster: eu-central-1-workload}` in
   `flux-apps-rset.yaml`, a new `AWSManagedCluster`
   `eu-central-1-workload` in `aws-clusters-resources/`, a new cluster
   directory `clusters/eu-central-1/` (a `Cluster` labeled `fluxcd: enabled`
   plus one x86 MachinePool), and a new Flux Kustomization `eu-central-1`
   in `clusters/flux-ks.yaml` with `dependsOn: [capa-system]`.
3. The `workload-arm` MachinePool for `eu-north-1` changes `replicas` from
   3 to 5 (and `scaling.maxSize` from 5 to 8).
4. (Bonus context, no question) A fourth commit adds a new key
   `TELEMETRY_ENABLED: "true"` to
   `mgmt/aws/addons/flux-apps/regions/eu-west-1/cluster-vars.yaml`.

**Questions:**

1. What reconciles when PR #87 merges, and in what order? (Cover the
   management-cluster Flux Kustomizations and what the updated ResourceSet
   does; explain the `dependsOn` gates that matter.)
2. Does the eu-west-1 RDS instance get recreated by the class change? Why or
   why not?
3. What exactly appears on the management cluster as a result of the new
   `eu-central-1` input, and what sequence of events happens on the new
   workload cluster?
4. What does CAPA do about the eu-north-1 MachinePool replica change?
5. How does the new `TELEMETRY_ENABLED` key reach the eu-west-1 workload
   cluster, and does the management `flux-apps` Kustomization (the one that
   applies the ResourceSet) apply anything new because of it?

### Context Files

{{scenario_spec}}
