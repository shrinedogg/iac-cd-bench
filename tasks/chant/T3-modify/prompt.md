## Task: Add a spot-capacity node pool to the prod cluster

**Stack:** chant (TypeScript composites compiling to Flux + CAPI/CAPA + ACK Kubernetes manifests)

You are given a slice of a chant golden repo: `src/composites/region-cluster.ts`
(the `RegionCluster` and `RegionNodePool` composite factories, plus their
shared `defaults.ts`/`labels.ts`) and both environments' `clusters` build
roots (`src/envs/{dev,prod}/clusters/main.ts`).

### Current State

Both `dev` and `prod` currently call `RegionCluster({...})` exactly once.
`RegionCluster` itself creates one `RegionNodePool` internally (its
`nodePool` member) from the cluster-level `nodeCount`/`instanceType`
props. `RegionNodePool` is also exported on its own — the composite's own
doc comment explains why a *second* pool is not a prop on `RegionCluster`:

> Additional node pools are extra `RegionNodePool({ clusterName: ... })`
> calls at the call site rather than an array prop: a composite factory
> returns a flat record of named members, and iterating a pool array
> inside the factory is exactly what EVL010 exists to prevent. One pool
> per cluster is what the SPEC asks for; a second is one more call.

### Your Task

Prod needs a second, spot-capacity node pool alongside its existing
on-demand pool, to absorb burst batch workloads at lower cost. Add it to
`src/envs/prod/clusters/main.ts` as a direct `RegionNodePool({...})` call
— **do not** modify `region-cluster.ts`, and do not touch
`src/envs/dev/clusters/main.ts` (this change is prod-only; changing prod
must not modify dev).

The new pool:
1. `name: "myapp-prod-nodes-spot"`
2. `clusterName: "myapp-prod"` (must join the same cluster the existing
   `RegionCluster({...})` call declares)
3. `env: "prod"`
4. `instanceType: "t3.large"` (same instance type as the existing pool)
5. `replicas: 2`
6. `capacityType: "spot"`
7. `availabilityZones`: the same three AZs the existing prod cluster call
   already uses (`us-east-1a`, `us-east-1b`, `us-east-1c`)

The existing `RegionCluster({...})` call in `src/envs/prod/clusters/main.ts`
stays exactly as it is — you are adding a call, not editing the cluster's
own props. Import `RegionNodePool` alongside the existing `RegionCluster`
import.

Return the full updated `src/envs/prod/clusters/main.ts` as a single fenced
code block, preceded by its file path in backticks.

### Context Files

{{scenario_spec}}
