# Golden answers — chant T6-semantics

```json
{
  "q1": {"behavior": "falls-back-to-run", "reason": "chant build never fails outright because a file can't fold; folding is per-file, and a file outside the fold subset is transparently imported and run instead while other files in the same build still fold if they can"},
  "q2": {"answered_by": "live-marker", "reason": "chant has no authoritative state file; ownership is answered by reading the live ownership marker (a label/tag stamped at synthesis time from ownership.stack) off the resource itself, never from a hosted record"},
  "q3": "orphan",
  "q4": {"action": "adopt", "reason": "chant lifecycle plan only ever proposes delete for an undeclared resource whose live ownership marker confirms it belongs to this stack; an orphan with no marker at all is classified adopt, never an automatic delete"},
  "q5": {"calls_cloud": false, "reason": "chant build is pure synthesis -- it parses TypeScript and serializes output with no network call to any cloud provider or Kubernetes API server; only the separate chant lifecycle commands read live state"},
  "q6": {"requires_controller_reconcile": false, "reason": "chant lifecycle snapshot only needs each declared entity's live counterpart to exist and be readable through the provider's own read API; for k8s that means the objects need to exist and be GET-able, not that any controller has reconciled them into real cloud resources"},
  "q7": {"specific_to_k8s_lexicon": true, "reason": "edge reconstruction for --at/--live graphs is a per-lexicon capability; the AWS lexicon has it (it enriches from the deployed CloudFormation template), the k8s lexicon's describeResources() records identity-depth attributes only with no reference-catalog step, so a k8s --at/--live graph has zero edges regardless of how many real references the source declares"}
}
```

## Rationale

- **q1** — `concepts/evaluation-pipeline` (Stage 4, "Fold or Collect
  Resources") and `concepts/typescript-as-data` ("Folded vs Run"): folding
  is chant build's default path since core #1134, and it is a per-file
  decision. "A file that steps outside the fold subset is instead imported
  with the TypeScript runtime and its exported resource objects
  collected" — not an error. `cli/build`'s own summary of what a build
  prints confirms the fallback is logged, not fatal: `fold: N files
  folded, M ran`. This golden's own recorded verify output shows both
  outcomes side by side in one successful `npm run verify` run — the
  delivery build root's `FluxGitSource`/`FluxAppFor` calls happen to run
  (0 folded, 1 ran) while the clusters build root's `RegionCluster` call
  folds (1 folded, 0 ran), and both builds still produce valid output.

- **q2** — `concepts/lifecycle-models` ("Who answers 'is this mine?'") and
  `configuration/config-file` (`ownership`): "chant separates them on
  purpose" — the state-file axis and the ownership axis. The `ownership`
  marker is "the record that later lets `delete` be precise without an
  authoritative state file — ownership lives on the cloud resource, not
  in a file chant has to host or lock." The stated invariant: "The
  projection reads ownership from the live marker, never from the
  snapshot."

- **q3** — `concepts/drift-detection` ("The diff categories"): **orphan**
  is defined exactly as "In cloud but not declared — manual creation,
  untracked tooling, or imported-pending." The scenario in Q3 (live,
  never declared, not in any prior snapshot) is the textbook orphan case;
  it is not `missing` (that requires it being declared) and not `newly
  observed` alone (that category doesn't carry the "not declared" fact
  that makes it orphan rather than a normal new resource).

- **q4** — `cli/lifecycle` (`lifecycle plan`, the plan-action table and
  crosswalk) and `concepts/drift-detection` ("Resolving an orphan"):
  "`delete` is only ever proposed for an undeclared resource whose live
  ownership marker confirms it is chant's; a foreign orphan is `adopt`,
  and an orphan with no marker data is `adopt`, never `delete`."

- **q5** — `cli/lifecycle`: "`lifecycle plan` is strictly read-only: it
  builds, queries, and classifies. It never mutates the cloud and never
  deploys — `chant build` stays pure." Cloud/cluster reads are the job of
  the separate `chant lifecycle snapshot`/`diff --live`/`plan` commands
  (via each lexicon's `describeResources()`), never of `chant build`
  itself, which only parses source and serializes output.

- **q6** — `concepts/drift-detection` (the fixtures' own golden README
  states this directly for this project): "`chant lifecycle snapshot
  <env>` reads each declared entity back through the k8s API
  (`describeResources()` — a typed GET per entity)... Nothing in that
  path calls out to AWS... No controller needs to reconcile anything —
  CAPI/CAPA never provision an EKS cluster, ACK never calls AWS, Flux
  never fetches the git source — the snapshot only needs the object to
  exist and be GET-able." Every resource in `lifecycle-show-dev.txt`
  reads `PRESENT`, which is chant's honest fallback status for an object
  no controller has touched, not evidence that a controller did touch it.

- **q7** — `cli/graph` ("`--live` — the provisioned graph"): "Edge
  reconstruction is only as rich as the observed node attributes... A
  lexicon without that enrichment yields nodes with fewer edges." The AWS
  lexicon enriches from the deployed CloudFormation template's own
  `{Ref}`/`{Fn::GetAtt}` references; the k8s lexicon's `describeResources()`
  has no equivalent reference-catalog step, so `--at`/`--live` graphs are
  node inventories with no edges for k8s specifically — not a general
  property of the `--at`/`--live` graph format, which the declared graph
  (`graph-ir-dev-declared.json`, same source, offline) proves by showing
  the same 3 edges the live graph is missing.
