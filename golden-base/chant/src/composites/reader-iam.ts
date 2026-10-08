/**
 * ReaderIam — the service-account identity from the SPEC's IAM row, with the
 * least-privilege policy attached and the trust relationship as a prop.
 *
 * SPEC acceptance criterion 4 (least privilege, no wildcard actions on prod)
 * and the dev/prod split of the IAM row (dev: programmatic access via an IAM
 * user; prod: an assumed role with OIDC trust) are the same two props:
 * `trust` and `programmaticAccess`.
 *
 * Members:
 *   policy       K8s::Iam::Policy
 *   role         K8s::Iam::Role
 *   user         K8s::Iam::User                  (when programmaticAccess)
 *   podIdentity  K8s::Eks::PodIdentityAssociation (when podIdentity props set)
 *
 * The policy document is built by `readerPolicyDocument` (./policies.ts) from
 * an enumerated action list, with the bucket ARN and the object ARN as separate
 * statements. Neither carries a wildcard action or a wildcard resource, which
 * is the clause reviewers check first.
 *
 * ACK models policy attachment as `policyRefs` on the Role and the User — a
 * reference to the `Policy` custom resource, resolved to an ARN by the
 * controller. There is no `PolicyAttachment` kind to declare and none is
 * needed.
 */

import { Composite } from "@intentius/chant";
import { IamRole, PodIdentityAssociation, Policy, User } from "@intentius/chant-lexicon-k8s";

import { DEFAULT_MAX_SESSION_DURATION_SECONDS, INFRA_NAMESPACE } from "./defaults.js";
import { ackTags, infraLabels } from "./labels.js";
import { assumeRolePolicy, readerPolicyDocument, type RoleTrust } from "./policies.js";

export interface ReaderIamProps {
  /** Base name. The role, user, and policy names derive from it. */
  name: string;
  /** Environment identity, stamped as a label and a tag. */
  env: string;
  /** Bucket the identity may read. */
  bucketName: string;
  /**
   * How the role is assumed. `{ mode: "account" }` in dev; `{ mode: "oidc" }`
   * in prod, pinning both the service account subject and the STS audience.
   */
  trust: RoleTrust;
  /** Restrict object reads to this key prefix. Omit for the whole bucket. */
  prefix?: string;
  /**
   * Extra actions, enumerated. There is deliberately no prop that appends a
   * wildcard — SPEC criterion 4 is a property of the factory, not of the call.
   */
  additionalActions?: string[];
  /**
   * Create an IAM user for programmatic access (the SPEC's dev arm). Long-lived
   * access keys are not declared: ACK's IAM coverage in this lexicon has no
   * `AccessKey` kind. See README, "Coverage gaps".
   */
  programmaticAccess?: boolean;
  /**
   * Bind the role to a Kubernetes service account through EKS Pod Identity —
   * the successor to IRSA, and the reason the role needs no node-level trust.
   */
  podIdentity?: {
    /** EKS cluster the association is scoped to. */
    clusterName: string;
    /** Namespace of the service account. */
    serviceAccountNamespace: string;
    /** Name of the service account. */
    serviceAccountName: string;
  };
  /** Max session duration in seconds. */
  maxSessionDurationSeconds?: number;
  /** Namespace the ACK custom resources live in. */
  namespace?: string;
  /** Logical component name for labels/tags. */
  component?: string;
}

export const ReaderIam = Composite<ReaderIamProps>((props) => {
  const policy = new Policy({
    metadata: {
      name: `${props.name}-reader`,
      namespace: props.namespace ?? INFRA_NAMESPACE,
      labels: infraLabels(props.component ?? "identity", props.env),
    },
    spec: {
      name: `${props.name}-reader`,
      description: `Least-privilege read access to ${props.bucketName}`,
      policyDocument: readerPolicyDocument({
        bucketName: props.bucketName,
        prefix: props.prefix,
        additionalActions: props.additionalActions,
      }),
      tags: ackTags(props.component ?? "identity", props.env),
    },
  });

  const role = new IamRole({
    metadata: {
      name: `${props.name}-role`,
      namespace: props.namespace ?? INFRA_NAMESPACE,
      labels: infraLabels(props.component ?? "identity", props.env),
    },
    spec: {
      name: `${props.name}-role`,
      description: `Service-account role for ${props.name} (${props.env})`,
      assumeRolePolicyDocument: assumeRolePolicy(props.trust),
      maxSessionDuration:
        props.maxSessionDurationSeconds ?? DEFAULT_MAX_SESSION_DURATION_SECONDS,
      policyRefs: [{ from: { name: `${props.name}-reader` } }],
      tags: ackTags(props.component ?? "identity", props.env),
    },
  });

  const user = props.programmaticAccess !== true
    ? undefined
    : new User({
        metadata: {
          name: `${props.name}-sa`,
          namespace: props.namespace ?? INFRA_NAMESPACE,
          labels: infraLabels(props.component ?? "identity", props.env),
        },
        spec: {
          name: `${props.name}-sa`,
          path: "/service-accounts/",
          policyRefs: [{ from: { name: `${props.name}-reader` } }],
          tags: ackTags(props.component ?? "identity", props.env),
        },
      });

  const podIdentity = props.podIdentity === undefined
    ? undefined
    : new PodIdentityAssociation({
        metadata: {
          name: `${props.name}-pod-identity`,
          namespace: props.namespace ?? INFRA_NAMESPACE,
          labels: infraLabels(props.component ?? "identity", props.env),
        },
        spec: {
          clusterName: props.podIdentity.clusterName,
          namespace: props.podIdentity.serviceAccountNamespace,
          serviceAccount: props.podIdentity.serviceAccountName,
          roleRef: { from: { name: `${props.name}-role` } },
        },
      });

  return {
    policy,
    role,
    ...(user !== undefined ? { user } : {}),
    ...(podIdentity !== undefined ? { podIdentity } : {}),
  };
}, "ReaderIam");
