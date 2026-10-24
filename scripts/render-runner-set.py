#!/usr/bin/env python3
"""Render the fixed PoC AutoscalingRunnerSet for the current Kind API endpoint.

Only changes GitHub repo URL, listener IP and port in the template.
Do NOT change scale-set name, selectors or Kubernetes security policy here.
"""
import argparse
import ipaddress
from pathlib import Path
import re
import sys
from urllib.parse import urlparse


def render(template, repo_url, control_plane_ip, control_plane_port):
    url = urlparse(repo_url)
    if url.scheme != "https" or not url.netloc or url.username or url.password or url.fragment:
        raise ValueError("GitHub URL must be https without embedded credentials or fragments")
    try:
        ipaddress.ip_address(control_plane_ip)
    except ValueError:
        raise ValueError("control plane IP must be a valid IP address") from None
    if not 1 <= control_plane_port <= 65535:
        raise ValueError("port outside valid range")
    replacements = [
        (r"(?m)^(  githubConfigUrl: )[^\n]+$", lambda m: m.group(1) + repo_url),
        (r'(?m)^(      - ip: )"[^"\n]+"$', lambda m: m.group(1) + '"' + control_plane_ip + '"'),
        (r'(?m)^(        - name: KUBERNETES_SERVICE_PORT\n          value: )"[^"\n]+"$',
         lambda m: m.group(1) + '"' + str(control_plane_port) + '"'),
    ]
    for pattern, replacer in replacements:
        template, count = re.subn(pattern, replacer, template)
        if count != 1:
            raise ValueError("template differs from expected single RunnerSet field")
    if "runnerScaleSetName: karmada-runner" not in template:
        raise ValueError("unexpected scale-set naming")
    if 'app.kubernetes.io/version: "0.15.0"' not in template:
        raise ValueError("unexpected ARC controller version")
    return template


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control-plane-ip", required=True)
    parser.add_argument("--api-port", type=int, default=32443)
    parser.add_argument("--github-url", default="https://github.com/pkking/arc-karmada-test")
    parser.add_argument("--template", default="manifests/04-autoscalingrunnerset.yaml")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    if args.output == args.template:
        parser.error("refusing to overwrite checked-in template")
    try:
        rendered = render(Path(args.template).read_text(encoding="utf-8"),
                          args.github_url, args.control_plane_ip, args.api_port)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    p = Path(args.output)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(rendered, encoding="utf-8")
    print("rendered RunnerSet to " + str(p))
    return 0


if __name__ == "__main__":
    sys.exit(main())
