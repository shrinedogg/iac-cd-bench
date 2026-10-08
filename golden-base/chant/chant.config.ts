import type { ChantConfig } from "@intentius/chant";

/**
 * chant project config for the benchmark golden.
 *
 * `environments` names the two the SPEC defines. They are identities chant
 * threads through the operational layer (lifecycle diffs, the component
 * ledger) — there is no per-environment config block. The dev/prod split
 * itself lives in the source tree: `src/envs/dev` and `src/envs/prod` are
 * separate build roots. See README, "Environment isolation".
 */
export default {
  lexicons: ["k8s"],

  environments: ["dev", "prod"],

  // Stamped onto every emitted resource as `chant.intentius.io/stack`, next to
  // `app.kubernetes.io/managed-by=chant`. It is what lets a later prune tell
  // this estate's resources from anything else in the cluster.
  ownership: { stack: "iac-cd-bench" },

  lint: {
    rules: {
      // COR001 wants every inline object extracted to an exported const. That
      // is good advice for a file of hand-written resources and the wrong
      // advice for a composite layer, where the whole spec is built from props
      // at the call site. Same call the cockroachdb-multi-region example makes.
      COR001: "off",
      // COR013 flags a file that mixes resource Declarables with configuration
      // Declarables. ReaderIam groups a Policy with the Role and User that
      // reference it — that grouping is the composite.
      COR013: "off",
    },
  },
} satisfies ChantConfig;
