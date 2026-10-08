## Task: Semantic prediction quiz — chant build/lifecycle runtime behavior

**Stack:** chant (TypeScript composites compiling to Flux + CAPI/CAPA + ACK Kubernetes manifests, evaluated by `chant build`; a separate, read-only `chant lifecycle`/`chant search`/`chant graph` surface reads a live Kubernetes cluster to answer questions about what's actually deployed)

You are given a slice of a chant golden repo. Answer the questions about
what the `chant` CLI actually does — its build pipeline and its lifecycle
commands — not what you'd guess a generic IaC tool does. Ground every
answer in the seeded files where they're relevant.

### Repo files (workspace)

- `chant.config.ts` — declares `lexicons: ["k8s"]`, `environments: ["dev",
  "prod"]`, and `ownership: { stack: "iac-cd-bench" }`
- `src/composites/ack-controller.ts` — the `AckController` composite; its
  `HelmRelease` sets `spec.chart.spec.sourceRef.name: props.repositoryName`
- `src/envs/dev/infra/main.ts` — dev's infra build root, which calls
  `AckController` three times (`s3Controller`, `rdsController`,
  `iamController`), each passing `repositoryName: ackCharts.name`
- `fixtures/lifecycle-show-dev.txt` — genuine `chant lifecycle show dev`
  output: 18 resources recorded from a real snapshot taken against a live
  (if idle) Kubernetes cluster with this project's build output applied
- `fixtures/graph-ir-dev.json` — genuine `chant graph src/envs/dev
  --format ir --at latest --env dev` output: real nodes, `"edges": []`
- `fixtures/graph-ir-dev-declared.json` — genuine `chant graph src/envs/dev
  --format ir` (no `--at`/`--live`) output: the same nodes, plus 3 edges
  (`iamControllerRelease → ackCharts`, `rdsControllerRelease → ackCharts`,
  `s3ControllerRelease → ackCharts`)

### Questions

**Q1.** This golden's own recorded `chant build` output includes these two
lines from the same `npm run verify` run:

```
> chant build src/envs/dev/delivery -f yaml -o dist/dev/delivery.yaml
fold: 0 files folded, 1 ran
> chant build src/envs/dev/clusters -f yaml -o dist/dev/clusters/manifests.yaml
fold: 1 file folded, 0 ran
```

Both builds exit successfully and both produce output. In general, when a
file in a `chant build` cannot fold (reduce to a value with zero module
execution), does the build FAIL for that file, or does chant fall back,
per file, to importing and running it — with the rest of the files in the
same build still folding if they can?

**Q2.** `chant.config.ts` declares `ownership: { stack: "iac-cd-bench" }`.
Later, some tool (`chant lifecycle plan`, or a human) needs to answer "is
this live, undeclared Kubernetes object one this stack owns?" Is that
question answered by consulting an authoritative state file chant hosts
(e.g. something on the `chant/lifecycle` orphan branch), or by reading a
live marker stamped on the resource itself?

**Q3.** A resource exists in the live Kubernetes cluster. chant's source
has never declared it, and no prior `chant lifecycle snapshot` mentions
it either. Which one of chant's seven `--live` diff categories — missing,
orphan, disappeared, newly observed, drifted, unchanged, or unobserved —
does `chant lifecycle diff <env> --live` report it as?

**Q4.** An undeclared, live resource carries no chant ownership marker at
all (no `chant.intentius.io/stack` label, in this project's case). When
`chant lifecycle plan <env>` classifies it, does it propose `delete`, or
does it propose `adopt`?

**Q5.** Does `chant build src/envs/dev/infra` ever make a network call to
AWS or to a Kubernetes API server as part of producing
`dist/dev/infra/manifests.yaml` — for example, to check whether
`myapp-dev-db` already exists?

**Q6.** `fixtures/lifecycle-show-dev.txt` records 18 resources, every one
`STATUS: PRESENT`, from a `chant lifecycle snapshot dev` run. For that
snapshot to succeed and record `clusterCluster`
(`K8s::CAPI::Cluster`)/`clusterInfra` (`K8s::Infrastructure::AWSManagedCluster`)
as `PRESENT`, does CAPI/CAPA actually need to have provisioned a real EKS
cluster in AWS by the time the snapshot runs?

**Q7.** `graph-ir-dev.json`'s `edges` array is empty even though
`src/composites/ack-controller.ts` shows `iamControllerRelease`,
`rdsControllerRelease`, and `s3ControllerRelease` all setting
`spec.chart.spec.sourceRef.name` to the same `ackCharts.name` value —
and `graph-ir-dev-declared.json` (built from the same source, offline)
does show exactly those 3 edges. Is the empty `edges: []` in
`graph-ir-dev.json` evidence that the `--at latest` graph format doesn't
support edges for any lexicon, or is it specific to what the k8s lexicon
implements today?

### Answer format

Return ONLY a fenced JSON code block named `answers.json` in exactly this
shape (keys q1..q7, values as specified):

```json
{
  "q1": {"behavior": "falls-back-to-run | fails-build", "reason": "<short>"},
  "q2": {"answered_by": "live-marker | state-file", "reason": "<short>"},
  "q3": "missing | orphan | disappeared | newly-observed | drifted | unchanged | unobserved",
  "q4": {"action": "adopt | delete", "reason": "<short>"},
  "q5": {"calls_cloud": "<true|false>", "reason": "<short>"},
  "q6": {"requires_controller_reconcile": "<true|false>", "reason": "<short>"},
  "q7": {"specific_to_k8s_lexicon": "<true|false>", "reason": "<short>"}
}
```

For q5 `"calls_cloud"`, q6 `"requires_controller_reconcile"`, and q7
`"specific_to_k8s_lexicon"`, answer with JSON booleans (true/false).

### Context Files

{{scenario_spec}}
