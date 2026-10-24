#!/usr/bin/env python3
"""Read-only audit: check member PodSpec references to a named Secret.

Does NOT read Secret.data, kubeconfig contents, events, or pod environment values.
Absence of PodSpec references is necessary but not sufficient proof that
a credential can safely be removed from a propagation policy.
"""
import argparse
import datetime
import json
import os
import subprocess
import sys
from pathlib import Path


def secret_references(pod, secret_name):
    """Return stable usage categories; never return secret values."""
    spec = pod.get("spec", {})
    refs = set()
    for volume in spec.get("volumes", []):
        source = volume.get("secret") or {}
        if source.get("secretName") == secret_name:
            refs.add("volume")
        for item in volume.get("projected", {}).get("sources", []):
            if item.get("secret", {}).get("name") == secret_name:
                refs.add("projected")
    for item in spec.get("imagePullSecrets", []):
        if item.get("name") == secret_name:
            refs.add("imagePullSecret")
    for container in spec.get("containers", []) + spec.get("initContainers", []) + spec.get("ephemeralContainers", []):
        for var in container.get("env", []):
            if var.get("valueFrom", {}).get("secretKeyRef", {}).get("name") == secret_name:
                refs.add("env")
        for group in container.get("envFrom", []):
            if group.get("secretRef", {}).get("name") == secret_name:
                refs.add("envFrom")
    return sorted(refs)


def pod_reference_report(pod_list, secret_name):
    observed = []
    for pod in pod_list.get("items", []):
        kinds = secret_references(pod, secret_name)
        if kinds:
            meta = pod.get("metadata", {})
            observed.append({
                "namespace": meta.get("namespace"),
                "pod": meta.get("name"),
                "referenceTypes": kinds,
            })
    return observed


def kubectl_json(context, namespace, resource, timeout):
    cmd = ["kubectl", "--context", context, "--request-timeout", str(timeout) + "s"]
    if namespace:
        cmd.extend(["-n", namespace])
    else:
        cmd.append("-A")
    cmd.extend(["get", resource, "-o", "json"])
    p = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout + 5)
    if p.returncode:
        raise RuntimeError("kubectl failed in context " + context + " (exit " + str(p.returncode) + ")")
    return json.loads(p.stdout)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contexts", nargs="+", default=["kind-member1", "kind-member2"])
    parser.add_argument("--namespace", default="arc-runners")
    parser.add_argument("--secret-name", default="arc-github-secret")
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--output", default="/tmp/arc-015-validation/secret-consumer-audit.json")
    args = parser.parse_args(argv)
    if args.timeout < 1 or not args.contexts:
        parser.error("timeout must be positive and contexts nonempty")
    result = {
        "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "secretName": args.secret_name,
        "scope": args.namespace,
        "contexts": {},
        "allContextsAudited": False,
        "safeToStopPropagation": False,
        "limitation": "No PodSpec reference does not rule out API clients or other credential consumers. Requires staged E2E before policy removal.",
    }
    errors = []
    for context in args.contexts:
        try:
            pods = kubectl_json(context, args.namespace, "pods", args.timeout)
            result["contexts"][context] = {
                "podCount": len(pods.get("items", [])),
                "podReferences": pod_reference_report(pods, args.secret_name),
            }
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            # Never display captured Kubernetes responses that might hold data.
            result["contexts"][context] = {"error": type(exc).__name__}
            errors.append(context)
    result["allContextsAudited"] = not errors
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"allContextsAudited": result["allContextsAudited"],
                      "contexts": {c: {"podReferences": len(info.get("podReferences", [])),
                                       "error": info.get("error")} for c, info in result["contexts"].items()},
                      "report": str(destination)}, ensure_ascii=False))
    return 0 if not errors else 2


if __name__ == "__main__":
    sys.exit(main())
