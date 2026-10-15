#!/usr/bin/env python3
"""Runtime regression check for official ARC + Karmada. Never writes Secret data.

Start this BEFORE dispatching a workflow (or use --dispatch). Requires kubectl,
gh (for --dispatch), and the member contexts. Temporarily annotates the Listener
config Secret and its watched Role; both are restored in finally. With
--dispatch it also creates a GitHub workflow run. Output is sanitized JSON.
"""
import argparse
import datetime
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path


def command(*args):
    env = dict(os.environ, NO_PROXY="*", no_proxy="*")
    p = subprocess.run(args, capture_output=True, text=True, env=env,
                       timeout=90 if args[0] == "gh" else 25)
    if p.returncode:
        # Do not emit raw command output (it might contain credentials).
        raise RuntimeError(f"{args[0]} failed (exit {p.returncode})")
    return p.stdout


def get(*args):
    return json.loads(command(*args))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kubeconfig", default=os.path.expanduser("~/karmada-data/karmada-apiserver.config"))
    parser.add_argument("--contexts", nargs="+", default=["kind-member1", "kind-member2"])
    parser.add_argument("--duration", type=int, default=180)
    parser.add_argument("--interval", type=int, default=3)
    parser.add_argument("--dispatch", action="store_true", help="Dispatch test-arc.yml and require success, cluster coverage and cleanup")
    parser.add_argument("--required-contexts", nargs="+", help="Require Runner evidence on these contexts (default: all --contexts); use one for a directed placement test")
    parser.add_argument("--repo", default="pkking/arc-karmada-test")
    parser.add_argument("--output", default="/tmp/arc-015-validation/runtime.json")
    args = parser.parse_args()
    if not __debug__:
        parser.error("do not run with -O/PYTHONOPTIMIZE: runtime assertions must remain enabled")
    if args.duration < 30 or args.interval < 1:
        parser.error("duration must be >= 30 seconds and interval >= 1 second")
    required_contexts = set(args.required_contexts or args.contexts)
    if not required_contexts.issubset(set(args.contexts)):
        parser.error("--required-contexts must be included in --contexts")
    cp = ["kubectl", "--request-timeout=15s", "--kubeconfig=" + args.kubeconfig]
    annotation = "validation.arc-karmada-test/metadata-preservation"
    report = {"startedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(), "requiredContexts": sorted(required_contexts), "samples": [], "runners": {}, "passed": False}
    secret_name = None
    original_annotation = None
    reconcile_annotation = "validation.arc-karmada-test/reconcile"
    original_reconcile_annotation = None
    listener_name = None
    secret_touched = False
    reconcile_touched = False
    try:
        pods = get(*cp, "-n", "arc-systems", "get", "pods", "-l", "actions.github.com/scale-set-name=karmada-runner", "-o", "json")["items"]
        assert len(pods) == 1, "Expected exactly one Listener"
        listener = pods[0]
        listener_name = listener["metadata"]["name"]
        assert listener["spec"]["containers"][0]["image"].endswith(":0.15.0"), "Listener is not 0.15.0"
        assert any(c["type"] == "Ready" and c["status"] == "True" for c in listener.get("status", {}).get("conditions", [])), "Listener is not Ready"
        secret_name = next(v["secret"]["secretName"] for v in listener["spec"]["volumes"] if v["name"] == "listener-config")
        secret = get(*cp, "-n", "arc-systems", "get", "secret", secret_name, "-o", "json")
        original_annotation = secret["metadata"].get("annotations", {}).get(annotation)
        data_hash = hashlib.sha256(json.dumps(secret["data"], sort_keys=True).encode()).hexdigest()
        role = get(*cp, "-n", "arc-runners", "get", "role", listener_name, "-o", "json")
        original_reconcile_annotation = role["metadata"].get("annotations", {}).get(reconcile_annotation)
        command(*cp, "-n", "arc-systems", "annotate", "secret", secret_name, annotation + "=official-0.15.0", "--overwrite")
        secret_touched = True
        # Secrets are not watched by ARC. The generated Role IS watched by the
        # Listener reconciler. Annotate that Role to exercise the real path.
        # Do not mutate AutoscalingListener itself: its parent detects CR drift
        # and legitimately replaces it (which would test a different behavior).
        command(*cp, "-n", "arc-runners", "annotate", "role", listener_name, reconcile_annotation + "=initial", "--overwrite")
        reconcile_touched = True
        # The external write changes Secret resourceVersion and can legitimately
        # replace the Listener. Require stability AFTER propagation settles.
        time.sleep(15)
        command(*cp, "-n", "arc-systems", "wait", "--for=condition=Ready", "pod/" + listener_name, "--timeout=15s")
        listener = get(*cp, "-n", "arc-systems", "get", "pod", listener_name, "-o", "json")
        listener_uid = listener["metadata"]["uid"]
        secret = get(*cp, "-n", "arc-systems", "get", "secret", secret_name, "-o", "json")
        resource_version = secret["metadata"]["resourceVersion"]
        report.update(listener=listener_name, listenerUID=listener_uid, configSecret=secret_name, configSecretResourceVersion=resource_version)
        if args.dispatch:
            dispatch_time = datetime.datetime.now(datetime.timezone.utc).isoformat()
            report["dispatchTime"] = dispatch_time
            try:
                command("gh", "workflow", "run", "test-arc.yml", "--repo", args.repo)
            except subprocess.TimeoutExpired:
                # An accepted dispatch can outlive a lost/slow HTTP response.
                # Do NOT retry and create duplicate runs; discover the run below.
                report["dispatchResponseTimedOut"] = True
        deadline = time.monotonic() + args.duration
        run_id = None
        run_success = False
        remaining = None
        while time.monotonic() < deadline:
            # Repeated metadata-only events must not continually patch the Secret.
            command(*cp, "-n", "arc-runners", "annotate", "role", listener_name, reconcile_annotation + "=" + str(len(report["samples"])), "--overwrite")
            listener = get(*cp, "-n", "arc-systems", "get", "pod", listener_name, "-o", "json")
            assert listener["metadata"]["uid"] == listener_uid, "Listener UID changed repeatedly after settling"
            assert all(c.get("restartCount", 0) == 0 for c in listener.get("status", {}).get("containerStatuses", [])), "Listener container restarted"
            assert any(c["type"] == "Ready" and c["status"] == "True" for c in listener.get("status", {}).get("conditions", [])), "Listener became unready"
            secret = get(*cp, "-n", "arc-systems", "get", "secret", secret_name, "-o", "json")
            assert secret["metadata"]["resourceVersion"] == resource_version, "Config Secret is still being patched"
            assert secret["metadata"].get("annotations", {}).get(annotation) == "official-0.15.0", "External metadata removed"
            assert hashlib.sha256(json.dumps(secret["data"], sort_keys=True).encode()).hexdigest() == data_hash, "Config Secret data changed"
            control_pods = get(*cp, "-n", "arc-runners", "get", "pods", "-o", "json")["items"]
            control_status_names = {p["metadata"]["name"]: [c["name"] for c in p.get("status", {}).get("containerStatuses", [])] for p in control_pods}
            sample = {"at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "members": {}}
            member_remaining = []
            for context in args.contexts:
                resources = get("kubectl", "--request-timeout=15s", "--context=" + context, "-n", "arc-runners", "get", "pods,secrets", "-o", "json")["items"]
                secrets = {r["metadata"]["name"] for r in resources if r["kind"] == "Secret"}
                member_pods = [r for r in resources if r["kind"] == "Pod"]
                sample["members"][context] = [{"name": p["metadata"]["name"], "phase": p.get("status", {}).get("phase")} for p in member_pods]
                member_remaining.extend(p["metadata"]["name"] for p in member_pods)
                for pod in member_pods:
                    name = pod["metadata"]["name"]
                    refs = {e["valueFrom"]["secretKeyRef"]["name"] for c in pod["spec"]["containers"] for e in c.get("env", []) if "secretKeyRef" in e.get("valueFrom", {})}
                    entry = report["runners"].setdefault(name, {"context": context, "jitSecrets": sorted(refs), "readyWithSecrets": False, "namedStatusOnControlPlane": False})
                    entry["readyWithSecrets"] |= bool(refs) and refs.issubset(secrets) and any(c["type"] == "Ready" and c["status"] == "True" for c in pod.get("status", {}).get("conditions", []))
                    entry["namedStatusOnControlPlane"] |= "runner" in control_status_names.get(name, [])
                observed_jit = {s for r in report["runners"].values() for s in r["jitSecrets"]}
                member_remaining.extend(sorted(observed_jit.intersection(secrets)))
            report["samples"].append(sample)
            if args.dispatch:
                if run_id is None:
                    runs = get("gh", "run", "list", "--repo", args.repo, "--workflow", "test-arc.yml", "--event", "workflow_dispatch", "--limit", "10", "--json", "databaseId,createdAt,status")
                    candidates = [r for r in runs if r["createdAt"] >= report["dispatchTime"][:19] + "Z"]
                    assert len(candidates) <= 1, "Ambiguous dispatch: concurrent workflow runs detected"
                    if candidates:
                        run_id = candidates[0]["databaseId"]
                        report["runId"] = run_id
                if run_id:
                    run = get("gh", "run", "view", str(run_id), "--repo", args.repo, "--json", "status,conclusion,url,jobs")
                    report["workflow"] = run
                    if run["status"] == "completed":
                        assert run["conclusion"] == "success", "GitHub workflow failed"
                        assert len(run["jobs"]) == 3 and all(j["conclusion"] == "success" for j in run["jobs"]), "Expected exactly three successful jobs"
                        run_success = True
                observed_jit = {s for r in report["runners"].values() for s in r["jitSecrets"]}
                cp_secrets = get(*cp, "-n", "arc-runners", "get", "secrets", "-o", "json")["items"]
                cp_runners = get(*cp, "-n", "arc-runners", "get", "ephemeralrunners", "-o", "json")["items"]
                remaining = [p["metadata"]["name"] for p in control_pods] + member_remaining + [s["metadata"]["name"] for s in cp_secrets if s["metadata"]["name"] in observed_jit] + [r["metadata"]["name"] for r in cp_runners]
                report["remainingRuntimeResources"] = remaining
            time.sleep(args.interval)
        report["listenerStabilityPassed"] = True
        if args.dispatch:
            report["workflowPassed"] = run_success
            report["clusterCoveragePassed"] = required_contexts.issubset({r["context"] for r in report["runners"].values()})
            report["cleanupPassed"] = run_success and not remaining
            assert run_success, "Workflow did not complete successfully within duration"
            assert report["clusterCoveragePassed"], "Did not observe Runner Pods on every required member context"
            assert len(report["runners"]) >= 3, "Did not observe three Runner Pods"
            assert all(r["readyWithSecrets"] for r in report["runners"].values()), "Missing ready Runner/JIT Secret evidence"
            assert all(r["namedStatusOnControlPlane"] for r in report["runners"].values()), "Missing named container status on Karmada"
            assert not remaining, "Runner Pods or observed JIT Secrets leaked"
            report["cleanupPassed"] = True
        report["passed"] = True
    except Exception as error:
        report["error"] = str(error)
    finally:
        if reconcile_touched:
            try:
                value = reconcile_annotation + "-" if original_reconcile_annotation is None else reconcile_annotation + "=" + original_reconcile_annotation
                command(*cp, "-n", "arc-runners", "annotate", "role", listener_name, value, "--overwrite")
                report["reconcileAnnotationRestored"] = True
            except Exception:
                report["reconcileAnnotationRestored"] = False
                report["passed"] = False
        if secret_touched:
            try:
                value = annotation + "-" if original_annotation is None else annotation + "=" + original_annotation
                command(*cp, "-n", "arc-systems", "annotate", "secret", secret_name, value, "--overwrite")
                report["annotationRestored"] = True
            except Exception:
                report["annotationRestored"] = False
                report["passed"] = False
        report["finishedAt"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: report[k] for k in ("passed", "error", "runId", "cleanupPassed", "annotationRestored") if k in report}, ensure_ascii=False))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
