## Task: Make the eu-west-1 workload cluster production-grade — without touching eu-north-1

**Stack:** krops (Flux + CAPI/CAPA + ACK)

You are given a krops GitOps repository. In krops there is **no base/overlay
pair**: each workload cluster has its own directories
(`mgmt/aws/clusters/<region>/`) and its own docs in the shared per-cloud
files, and per-cluster values flow through the per-region `cluster-vars`
ConfigMap. "Changing one cluster without the other" therefore means
editing only the docs/dirs that belong to that cluster.

### Current state (the three files you will edit)

`mgmt/aws/infrastructure/workload-resources/dbinstance.yaml` — one
`DBInstance` per workload cluster, reconciled by the ACK RDS controller on
the management cluster. The file currently holds exactly these two docs:

```yaml
apiVersion: rds.services.k8s.aws/v1alpha1
kind: DBInstance
metadata:
  name: db-eu-north-1
  namespace: ack-system
  annotations:
    services.k8s.aws/adoption-policy: adopt-or-create
spec:
  dbInstanceIdentifier: krops-eu-north-1-workload-db
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
  tags:
    - key: managed-by
      value: flux-ack
    - key: cluster
      value: eu-north-1-workload
---
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
  tags:
    - key: managed-by
      value: flux-ack
    - key: cluster
      value: eu-west-1-workload
```

`mgmt/aws/clusters/eu-west-1/staging/cluster.yaml` — the CAPI definition of
the eu-west-1 cluster (the kustomization applies `namePrefix: eu-west-1-`
and `configurations: [capi-nameref.yaml]`). The eu-west-1 file currently
holds: a `Cluster` named `workload` (labels `fluxcd: enabled`,
`region: eu-west-1`; `controlPlaneRef` → `AWSManagedControlPlane
workload-control-plane`; `infrastructureRef` → `AWSManagedCluster
eu-west-1-workload`); an `AWSManagedControlPlane` named
`workload-control-plane` (region eu-west-1, version "1.34.6", endpointAccess
public+private, vpc-cni/coredns/kube-proxy addons); an x86 node pool
(`MachinePool` + `AWSManagedMachinePool` named `workload-x86`,
`instanceType: t3a.medium`, `replicas: 1`, `scaling: {minSize: 1, maxSize: 2}`,
`amiType: AL2023_x86_64_STANDARD`, `capacityType: onDemand`); and an ARM
node pool:

```yaml
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
      bootstrap:
        dataSecretName: ""
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
  scaling:
    minSize: 3
    maxSize: 5
  amiType: AL2023_ARM_64_STANDARD
  capacityType: onDemand
  availabilityZones:
    - eu-west-1a
    - eu-west-1b
    - eu-west-1c
  labels:
    node.kubernetes.io/arch: arm64
```

(The eu-north-1 cluster file is the same shape, `namePrefix: eu-north-1-`,
region eu-north-1, AZs eu-north-1a/b/c, x86 pool `t3.medium`.)

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

### Your task

The eu-west-1 workload cluster is moving from staging-grade to
production-grade. Make exactly these changes, scoped to eu-west-1 only:

1. **RDS** — in `mgmt/aws/infrastructure/workload-resources/dbinstance.yaml`,
   change the `db-eu-west-1` doc's `dbInstanceClass` from `db.t4g.micro` to
   `db.m6g.large` and set its `multiAZ: true`. The `db-eu-north-1` doc must
   remain exactly as it is.
2. **Nodes** — in `mgmt/aws/clusters/eu-west-1/staging/cluster.yaml`, scale
   the `workload-arm` MachinePool from 3 to 5 replicas and raise
   `AWSManagedMachinePool` `scaling.maxSize` from 5 to 8. Leave the control
   plane and the x86 pool untouched.
3. **Cluster vars** — in
   `mgmt/aws/addons/flux-apps/regions/eu-west-1/cluster-vars.yaml`, add the
   key `TELEMETRY_ENABLED: "true"` to the ConfigMap data.

Constraints:

- Do not change anything about the eu-north-1 cluster, its RDS instance, or
  its region bundle.
- `mgmt/aws/infrastructure/workload-resources/dbinstance.yaml` is a
  multi-doc file: your returned version must still contain BOTH docs
  (the eu-north-1 doc unchanged, the eu-west-1 doc updated).
- Keep every other field of every changed file as-is.

Return each changed file as a fenced YAML code block, preceded by its repo
path in backticks (e.g.
`` `mgmt/aws/infrastructure/workload-resources/dbinstance.yaml` ``).

### Context Files

{{scenario_spec}}
