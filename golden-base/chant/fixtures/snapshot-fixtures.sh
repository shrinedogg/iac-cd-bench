#!/usr/bin/env bash
#
# snapshot-fixtures.sh — regenerate golden-base/chant/fixtures/ from scratch.
#
# Recreates a throwaway kind cluster, installs the CAPI/CAPA/CAAPH/ACK/Flux
# CRDs this golden's dist/ manifests declare, applies the six dist/ files
# (dev then prod, so a shared object like the ACK HelmRepository lands with
# each environment's own labels at the moment that environment is snapshot),
# takes one `chant lifecycle snapshot` per environment, and writes every
# fixture in this directory from genuine `chant` CLI output. Nothing here is
# hand-written — this script IS the provenance record; see MANIFEST.md for
# the exact command behind each output file.
#
# Requires: docker, kind, kubectl, node/npm, curl, python3.
#
# Usage: ./fixtures/snapshot-fixtures.sh
# Run from anywhere; paths below resolve relative to this script.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHANT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
FIXTURES_DIR="$SCRIPT_DIR"
CLUSTER_NAME="chant-snap"
KCTX="kind-${CLUSTER_NAME}"

CRD_DIR="$(mktemp -d /tmp/chant-snap-crds.XXXXXX)"
RUN_DIR="$(mktemp -d /tmp/chant-snapshot-run.XXXXXX)"
cleanup() {
  echo "--- tearing down scratch cluster ${CLUSTER_NAME} ---"
  kind delete cluster --name "$CLUSTER_NAME" >/dev/null 2>&1 || true
  rm -rf "$CRD_DIR" "$RUN_DIR"
}
trap cleanup EXIT

echo "--- 1. building dist/ from source (npm run build:dev / build:prod) ---"
( cd "$CHANT_DIR" && npm ci && npm run build:dev && npm run build:prod )

echo "--- 2. creating scratch kind cluster: ${CLUSTER_NAME} ---"
kind create cluster --name "$CLUSTER_NAME"

echo "--- 3. fetching CRDs pinned in the k8s lexicon's crd-sources.ts ---"
CAPI_BASE="https://raw.githubusercontent.com/kubernetes-sigs/cluster-api/v1.14.0/core/config/crd/bases"
CAAPH_BASE="https://raw.githubusercontent.com/kubernetes-sigs/cluster-api-addon-provider-helm/v0.6.4/config/crd/bases"
CAPA_BASE="https://raw.githubusercontent.com/kubernetes-sigs/cluster-api-provider-aws/v2.13.0/config/crd/bases"
ACK_S3_BASE="https://raw.githubusercontent.com/aws-controllers-k8s/s3-controller/v1.10.0/config/crd/bases"
ACK_RDS_BASE="https://raw.githubusercontent.com/aws-controllers-k8s/rds-controller/v1.11.1/config/crd/bases"
ACK_IAM_BASE="https://raw.githubusercontent.com/aws-controllers-k8s/iam-controller/v1.8.1/config/crd/bases"
ACK_EKS_BASE="https://raw.githubusercontent.com/aws-controllers-k8s/eks-controller/v1.20.0/config/crd/bases"
FLUX_INSTALL="https://github.com/fluxcd/flux2/releases/download/v2.9.1/install.yaml"

curl -sfL "$CAPI_BASE/cluster.x-k8s.io_clusters.yaml" -o "$CRD_DIR/capi-clusters.yaml"
curl -sfL "$CAPI_BASE/cluster.x-k8s.io_machinepools.yaml" -o "$CRD_DIR/capi-machinepools.yaml"
curl -sfL "$CAAPH_BASE/addons.cluster.x-k8s.io_helmchartproxies.yaml" -o "$CRD_DIR/caaph-helmchartproxies.yaml"
curl -sfL "$CAPA_BASE/controlplane.cluster.x-k8s.io_awsmanagedcontrolplanes.yaml" -o "$CRD_DIR/capa-awsmanagedcontrolplanes.yaml"
curl -sfL "$CAPA_BASE/infrastructure.cluster.x-k8s.io_awsmanagedclusters.yaml" -o "$CRD_DIR/capa-awsmanagedclusters.yaml"
curl -sfL "$CAPA_BASE/infrastructure.cluster.x-k8s.io_awsmanagedmachinepools.yaml" -o "$CRD_DIR/capa-awsmanagedmachinepools.yaml"
curl -sfL "$ACK_S3_BASE/s3.services.k8s.aws_buckets.yaml" -o "$CRD_DIR/ack-s3-buckets.yaml"
curl -sfL "$ACK_RDS_BASE/rds.services.k8s.aws_dbinstances.yaml" -o "$CRD_DIR/ack-rds-dbinstances.yaml"
curl -sfL "$ACK_IAM_BASE/iam.services.k8s.aws_roles.yaml" -o "$CRD_DIR/ack-iam-roles.yaml"
curl -sfL "$ACK_IAM_BASE/iam.services.k8s.aws_users.yaml" -o "$CRD_DIR/ack-iam-users.yaml"
curl -sfL "$ACK_IAM_BASE/iam.services.k8s.aws_policies.yaml" -o "$CRD_DIR/ack-iam-policies.yaml"
curl -sfL "$ACK_EKS_BASE/eks.services.k8s.aws_podidentityassociations.yaml" -o "$CRD_DIR/ack-eks-podidentityassociations.yaml"
curl -sfL "$FLUX_INSTALL" -o "$CRD_DIR/flux-install-full.yaml"

# Flux's install.yaml bundles the controller Deployments + webhooks too; the
# golden only needs the CRDs (no controller is expected to reconcile), so
# pull just the CustomResourceDefinition documents out of the bundle.
python3 - "$CRD_DIR/flux-install-full.yaml" "$CRD_DIR/flux-crds.yaml" <<'PYEOF'
import sys, yaml
src, dst = sys.argv[1], sys.argv[2]
docs = [d for d in yaml.safe_load_all(open(src)) if d and d.get("kind") == "CustomResourceDefinition"]
yaml.safe_dump_all(docs, open(dst, "w"))
print(f"extracted {len(docs)} Flux CRDs")
PYEOF

echo "--- 4. applying CRDs ---"
kubectl --context "$KCTX" apply -f "$CRD_DIR/capi-clusters.yaml"
kubectl --context "$KCTX" apply -f "$CRD_DIR/capi-machinepools.yaml"
kubectl --context "$KCTX" apply -f "$CRD_DIR/caaph-helmchartproxies.yaml"
kubectl --context "$KCTX" apply -f "$CRD_DIR/capa-awsmanagedcontrolplanes.yaml"
kubectl --context "$KCTX" apply -f "$CRD_DIR/capa-awsmanagedclusters.yaml"
kubectl --context "$KCTX" apply -f "$CRD_DIR/capa-awsmanagedmachinepools.yaml"
kubectl --context "$KCTX" apply -f "$CRD_DIR/ack-s3-buckets.yaml"
kubectl --context "$KCTX" apply -f "$CRD_DIR/ack-rds-dbinstances.yaml"
kubectl --context "$KCTX" apply -f "$CRD_DIR/ack-iam-roles.yaml"
kubectl --context "$KCTX" apply -f "$CRD_DIR/ack-iam-users.yaml"
kubectl --context "$KCTX" apply -f "$CRD_DIR/ack-iam-policies.yaml"
kubectl --context "$KCTX" apply -f "$CRD_DIR/ack-eks-podidentityassociations.yaml"
kubectl --context "$KCTX" apply -f "$CRD_DIR/flux-crds.yaml"
kubectl --context "$KCTX" wait --for=condition=Established crd --all --timeout=60s >/dev/null

echo "--- 5. creating namespaces ---"
for ns in flux-system infra clusters app ack-system; do
  kubectl --context "$KCTX" create namespace "$ns" >/dev/null
done

echo "--- 6. isolating a scratch copy of the project for git-plumbing safety ---"
# `chant lifecycle snapshot` writes to a `chant/lifecycle` orphan branch on
# whatever git repo it finds walking up from cwd, and pushes it if a remote
# is configured. Running it inside the bench repo directly would create (and
# try to push) that branch on origin — unwanted here. A standalone git repo
# with no remote sidesteps that entirely: `pushLifecycle` no-ops when `git
# remote` lists nothing.
rsync -a --exclude=dist --exclude=node_modules --exclude=fixtures "$CHANT_DIR/" "$RUN_DIR/"
( cd "$RUN_DIR" && npm ci )
( cd "$RUN_DIR" && git init -q \
    && git -c user.email=snapshot@chant-snap.local -c user.name="chant-snap fixtures" add -A \
    && git -c user.email=snapshot@chant-snap.local -c user.name="chant-snap fixtures" commit -q -m "snapshot run baseline" )

echo "--- 7. applying dev/ manifests, then snapshotting dev BEFORE prod overwrites shared objects ---"
kubectl --context "$KCTX" apply -f "$CHANT_DIR/dist/dev/delivery.yaml"
kubectl --context "$KCTX" apply -f "$CHANT_DIR/dist/dev/infra/manifests.yaml"
kubectl --context "$KCTX" apply -f "$CHANT_DIR/dist/dev/clusters/manifests.yaml"
( cd "$RUN_DIR" && npx chant lifecycle snapshot dev --src src/envs/dev )

echo "--- 8. applying prod/ manifests (updates the shared GitRepository/HelmRepository/HelmReleases in place), then snapshotting prod ---"
kubectl --context "$KCTX" apply -f "$CHANT_DIR/dist/prod/delivery.yaml"
kubectl --context "$KCTX" apply -f "$CHANT_DIR/dist/prod/infra/manifests.yaml"
kubectl --context "$KCTX" apply -f "$CHANT_DIR/dist/prod/clusters/manifests.yaml"
( cd "$RUN_DIR" && npx chant lifecycle snapshot prod --src src/envs/prod )

echo "--- 9. writing fixtures ---"
# Every command below is also recorded, verbatim, in fixtures/MANIFEST.md —
# that file is the sidecar provenance record for this directory; keep it in
# sync when adding or changing a fixture here.

# Raw snapshot artifacts — the exact blob `chant lifecycle snapshot` wrote to
# the (local-only, unpushed) chant/lifecycle orphan branch.
( cd "$RUN_DIR" && git show chant/lifecycle:dev/k8s.json ) > "$FIXTURES_DIR/snapshot-dev.json"
( cd "$RUN_DIR" && git show chant/lifecycle:prod/k8s.json ) > "$FIXTURES_DIR/snapshot-prod.json"

# chant lifecycle show
( cd "$RUN_DIR" && npx chant lifecycle show dev ) > "$FIXTURES_DIR/lifecycle-show-dev.txt" 2>&1
( cd "$RUN_DIR" && npx chant lifecycle show prod ) > "$FIXTURES_DIR/lifecycle-show-prod.txt" 2>&1

# chant search --explain, snapshot-backed (--at latest)
( cd "$RUN_DIR" && npx chant search "kind:Bucket" --at latest --env dev --src src/envs/dev --explain --show labels ) \
  > "$FIXTURES_DIR/search-buckets-dev.txt" 2>&1
( cd "$RUN_DIR" && npx chant search "kind:Bucket" --at latest --env prod --src src/envs/prod --explain --show labels ) \
  > "$FIXTURES_DIR/search-buckets-prod.txt" 2>&1
( cd "$RUN_DIR" && npx chant search "kind:PodIdentityAssociation" --at latest --env dev --src src/envs/dev --explain ) \
  > "$FIXTURES_DIR/search-prod-only-pod-identity-dev.txt" 2>&1
( cd "$RUN_DIR" && npx chant search "kind:PodIdentityAssociation" --at latest --env prod --src src/envs/prod --explain ) \
  > "$FIXTURES_DIR/search-prod-only-pod-identity-prod.txt" 2>&1
( cd "$RUN_DIR" && npx chant search "kind:Iam attr:labels=component\":\"identity" --at latest --env prod --src src/envs/prod --explain ) \
  > "$FIXTURES_DIR/search-iam-by-component-prod.txt" 2>&1

# chant search over the declared (offline) graph — no cluster needed, but
# demonstrates the referenced-secret provenance the README's "Secrets"
# section documents: nothing else in this golden's declared graph names the
# DB secret except the DBInstance that consumes it.
( cd "$CHANT_DIR" && npx chant search "myapp-dev-db-master" --src src/envs/dev --explain ) \
  > "$FIXTURES_DIR/search-db-secret-reference.txt" 2>&1

# chant graph --format ir, snapshot-backed (--at latest). The k8s lexicon
# does not yet implement live/replay edge reconstruction (only the AWS
# lexicon does, per docs/cli/graph.mdx's "a lexicon without that enrichment
# yields nodes with fewer edges") — these carry nodes with physicalId/attrs
# and zero edges. See fixtures/README.md.
( cd "$RUN_DIR" && npx chant graph src/envs/dev --format ir --at latest --env dev ) \
  2>"$FIXTURES_DIR/.graph-ir-dev.stderr" > "$FIXTURES_DIR/graph-ir-dev.json"
( cd "$RUN_DIR" && npx chant graph src/envs/prod --format ir --at latest --env prod ) \
  2>"$FIXTURES_DIR/.graph-ir-prod.stderr" > "$FIXTURES_DIR/graph-ir-prod.json"
rm -f "$FIXTURES_DIR"/.graph-ir-*.stderr

# chant graph --format ir over the declared (offline) graph — supplementary:
# this is where the real cross-resource edges are, since they're a property
# of source references, not (yet) of k8s live/replay observation.
( cd "$CHANT_DIR" && npx chant graph src/envs/dev --format ir ) > "$FIXTURES_DIR/graph-ir-dev-declared.json" 2>/dev/null
( cd "$CHANT_DIR" && npx chant graph src/envs/prod --format ir ) > "$FIXTURES_DIR/graph-ir-prod-declared.json" 2>/dev/null

echo "--- done. fixtures written to ${FIXTURES_DIR} ---"
echo "--- rebuilding dist/ one more time so the working tree matches committed output ---"
( cd "$CHANT_DIR" && npm run build:dev && npm run build:prod )
