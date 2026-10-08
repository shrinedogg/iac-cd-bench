# chant runtime/build behavior — reference notes

Condensed from chant's own docs (`concepts/lifecycle-models`,
`concepts/drift-detection`, `concepts/evaluation-pipeline`,
`concepts/typescript-as-data`, `cli/build`, `cli/lifecycle`, `cli/graph`,
`configuration/config-file`). Cited inline so you can trace each claim.

## `chant build` is pure synthesis (`cli/lifecycle`, `concepts/evaluation-pipeline`)

`chant build` parses TypeScript source, evaluates resource definitions,
and serializes output. It never queries or mutates a cloud provider or a
Kubernetes API server — no credentials are consulted, no network call to
AWS/K8s happens. "`chant build` stays pure" is stated explicitly in the
`chant lifecycle plan` reference as the contrast against the lifecycle
commands, which *do* read live state. The only I/O `chant build` performs
is reading your `.ts` source files and writing its own output.

## Fold vs run (`concepts/evaluation-pipeline`, `concepts/typescript-as-data`, `cli/build`)

`chant build`'s default path (`--fold`, on since core issue #1134) reduces
each file's AST directly to resource objects, with **zero module
execution** — chant never imports or runs a folding file's code. A file
that steps outside the supported subset (documented in
`concepts/typescript-as-data`) falls back, **per file**, to being imported
and run instead. This fallback is **not a build error** — the build
still succeeds, the output is the same either way, and the whole decision
collapses to one summary line: `fold: N files folded, M ran`. A file
either folds entirely or runs entirely; chant never partially executes a
file, and other files in the same build may still fold even when one
falls back to running.

## Ownership is answered by a live marker, never a state file (`concepts/lifecycle-models`, `configuration/config-file`)

chant has no authoritative state file. `chant.config.ts`'s `ownership:
{ stack: "<name>" }` block, when set, stamps a marker onto every emitted
resource at synthesis time (Kubernetes: labels
`app.kubernetes.io/managed-by=chant` + `chant.intentius.io/stack`; AWS/Azure:
tags). "Is this resource mine?" is answered later by reading that marker
back off the live resource — never by consulting a snapshot or any other
hosted record. The invariant stated in `concepts/lifecycle-models`: *"The
projection reads ownership from the live marker, never from the
snapshot."* The snapshot chant records (`chant lifecycle snapshot <env>`)
can be deleted between runs with no change in this behavior.

## The seven `--live` diff categories (`concepts/drift-detection`, `cli/lifecycle`)

`chant lifecycle diff <env> --live` compares three axes — declared in
source now, in the last snapshot, observed live now — into seven
categories: **missing** (declared, provider reports absent), **orphan**
(live, not declared — manual creation, untracked tooling), **disappeared**
(was in last snapshot, gone now), **newly observed** (live now, not in any
prior snapshot), **drifted** (in both, something changed), **unchanged**
(in both, identical), **unobserved** (declared, chant could not read live
state — a hole in the report, never treated as absence).

## Prune/delete ownership rules (`cli/lifecycle`, `concepts/drift-detection`)

`chant lifecycle plan <env>` classifies every resource into one action:
`create`, `update`, `delete`, `adopt`, `noop`, `effect`, or `unobserved`.
**`delete` is only ever proposed for an undeclared (orphan) resource whose
live ownership marker confirms it belongs to this stack.** An orphan with
no marker, or a marker naming a different stack, is classified `adopt` —
a candidate to pull into source — **never** an automatic delete. This
reads the marker on the live resource at plan time; it does not consult
the snapshot to make the delete/adopt decision.

## Snapshot semantics: what `chant lifecycle snapshot` actually needs (`concepts/drift-detection`)

`chant lifecycle snapshot <env>` calls each lexicon's `describeResources()`
— a typed read per declared entity — and records the result to a
`chant/lifecycle` git orphan branch. This requires only that each declared
entity's live counterpart **exists and is readable** through the
provider's own read API. For the Kubernetes lexicon specifically, that
means the objects need to exist and be GET-able through the k8s API
server — **no controller needs to have reconciled anything**: CAPI/CAPA
never has to have actually provisioned real infrastructure, ACK never has
to have called AWS, Flux never has to have fetched anything, for a
snapshot to succeed and record every resource as `PRESENT`.

## `--at`/`--live` graph edges are per-lexicon, and not implemented for k8s yet (`cli/graph`)

`chant graph --format ir --live --env <name>` (the snapshot/live-observed
graph) reconstructs edges from live references — but only where a
lexicon ships the enrichment needed to do it. Per `cli/graph`: *"Edge
reconstruction is only as rich as the observed node attributes... A
lexicon without that enrichment yields nodes with fewer edges."* The AWS
lexicon has this enrichment (it resolves `{Ref}`/`{Fn::GetAtt}` from the
deployed CloudFormation template); the Kubernetes lexicon's
`describeResources()` records identity-depth attributes only
(`namespace`, `labels`, `resourceVersion`, `conditions`), with no
reference-catalog step — so a **k8s** `--at`/`--live` graph today has real
nodes but **zero edges**, regardless of how many real cross-resource
references the source declares. The **declared** graph (no `--at`/`--live`,
built offline from source) is unaffected by this gap — it resolves edges
directly from the TypeScript's own references and has them for every
lexicon.
