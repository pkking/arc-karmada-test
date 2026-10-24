#!/usr/bin/env python3
"""Sanitized, read-only ARC + Karmada environment preflight.

Not a bootstrapper. --mode ready checks the *current* API/Listener status,
not GitHub job success. Never print kubeconfig, Secret.data or kubectl stderr.
"""
import argparse
import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def command_json(argv, timeout=30):
    try:
        completed = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(type(exc).__name__) from None
    if completed.returncode:
        raise RuntimeError("command failed (" + str(completed.returncode) + ")")
    try:
        return json.loads(completed.stdout)
    except (ValueError, TypeError):
        raise RuntimeError("non-JSON response") from None


def ready_condition(item):
    return any(c.get("type") == "Ready" and c.get("status") == "True"
               for c in item.get("status", {}).get("conditions", []))


def evaluate_state(clusters, member_names, listeners, expected_image_suffix=":0.15.0"):
    """Pure evaluation of sanitized API data with explicit fail reasons."""
    errors = []
    members = {item.get("metadata", {}).get("name"): item
               for item in clusters.get("items", [])}
    for name in member_names:
        if name not in members:
            errors.append("missing-member:" + name)
        elif not ready_condition(members[name]):
            errors.append("member-not-ready:" + name)
    items = listeners.get("items", [])
    if len(items) != 1:
        errors.append("listener-count:" + str(len(items)))
    for pod in items:
        containers = pod.get("spec", {}).get("containers", [])
        if not containers or not containers[0].get("image", "").endswith(expected_image_suffix):
            errors.append("listener-image-mismatch")
        if not ready_condition(pod):
            errors.append("listener-not-ready")
    return {
        "allMembersReady": all(name in members and ready_condition(members[name])
                               for name in member_names),
        "listenerCount": len(items),
        "listenerReady": len(items) == 1 and ready_condition(items[0]),
        "errorCodes": errors,
        "passed": len(errors) == 0,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("host", "ready"), default="host")
    parser.add_argument("--kubeconfig",
                        default=os.path.expanduser("~/karmada-data/karmada-apiserver.config"))
    parser.add_argument("--members", nargs="+", default=["member1", "member2"])
    parser.add_argument("--namespace", default="arc-systems")
    parser.add_argument("--selector", default="actions.github.com/scale-set-name=karmada-runner")
    parser.add_argument("--output", default="/tmp/arc-015-validation/preflight.json")
    args = parser.parse_args(argv)
    results = {
        "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "mode": args.mode,
        "expectedMembers": args.members,
        "checks": {},
        "ready": False,
        "e2eVerified": False,
    }
    required = ["python3", "docker", "kind", "kubectl", "karmadactl", "gh"]
    results["checks"]["toolsPresent"] = {tool: bool(shutil.which(tool)) for tool in required}
    results["checks"]["kubeconfigExists"] = os.path.isfile(args.kubeconfig)
    # Paths only ever appear as booleans; kubeconfig bytes never enter report.
    all_tools = all(results["checks"]["toolsPresent"].values())
    if args.mode == "ready":
        if not shutil.which("kubectl") or not results["checks"]["kubeconfigExists"]:
            results["checks"]["liveError"] = "missing-kubeconfig-or-kubectl"
        else:
            api = ["kubectl", "--request-timeout=20s", "--kubeconfig", args.kubeconfig]
            try:
                clusters = command_json([*api, "get", "clusters.cluster.karmada.io", "-o", "json"])
                listeners = command_json([*api, "-n", args.namespace, "get", "pods",
                                          "-l", args.selector, "-o", "json"])
                results["checks"]["runtime"] = evaluate_state(clusters, args.members, listeners)
                results["ready"] = results["checks"]["runtime"]["passed"]
            except RuntimeError as exc:
                results["checks"]["liveError"] = str(exc)
    results["checks"]["hostToolsComplete"] = all_tools
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"mode": args.mode, "hostToolsComplete": all_tools,
                      "ready": results["ready"], "e2eVerified": False,
                      "report": str(out)}, ensure_ascii=False))
    if args.mode == "ready":
        return 0 if results["ready"] else 2
    return 0 if all_tools else 2


if __name__ == "__main__":
    sys.exit(main())
