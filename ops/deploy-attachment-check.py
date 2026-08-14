#!/usr/bin/env python3
"""deploy-attachment-check.py — refuse a non-production deploy that would claim a
production hostname.

WHY THIS EXISTS, and it is not hypothetical. On 2026-08-13 the first staging
deploy took over all three production custom domains — api.doctorcre.com,
api.practicecre.com, dealroom.doctorcre.com — because wrangler treats `routes` as
an INHERITABLE key and the [env.staging] block did not override it. Production
answered from the staging Worker, bound to the empty staging database, for about
two minutes.

WHY NO EXISTING GUARD CAUGHT IT. deploy-worker.sh checks that the code is exactly
origin/main, that the tree is clean, and that the verb count did not shrink. All
three passed, correctly: every one of them is about the ARTIFACT. Not one of them
looks at what the artifact gets ATTACHED to. The build was right and its wiring
was wrong, which is a whole category the deploy path had no opinion about.

WHAT IT CHECKS. For any env other than production, the env's own routes must be
declared AND must share no hostname with the top-level (production) routes. An
absent `routes` key is treated as a REFUSAL, not as "no routes" — that
assumption is precisely what caused the incident, so the check refuses the exact
shape that failed rather than trying to predict wrangler's inheritance rules.

    ops/deploy-attachment-check.py <wrangler.toml> <env-name>
    exit 0 = safe, exit 1 = refuse
"""

import re
import sys


def _strip_comments(line: str) -> str:
    return line.split("#", 1)[0]


def parse_routes(text: str):
    """Return (top_level_routes, {env_name: routes_or_None}).

    A deliberately small TOML reader rather than a dependency: this runs inside a
    deploy preflight, and a guard that can fail to import is a guard that gets
    removed. None means the key was ABSENT, which is the dangerous case and is
    kept distinct from an explicit empty list.
    """
    top: list = []
    envs: dict = {}
    section, buf, collecting = None, "", False

    for raw in text.splitlines():
        line = _strip_comments(raw).strip()
        if not line:
            continue
        if collecting:
            buf += " " + line
            if "]" in line:
                collecting = False
                _assign(section, _hosts(buf), top, envs)
                buf = ""
            continue
        m = re.match(r"^\[+([^\]]+)\]+$", line)
        if m:
            section = m.group(1)
            em = re.match(r"^env\.([A-Za-z0-9_-]+)$", section)
            if em and em.group(1) not in envs:
                envs[em.group(1)] = None
            continue
        if re.match(r"^routes\s*=", line):
            value = line.split("=", 1)[1].strip()
            if "]" not in value:
                collecting, buf = True, value
                continue
            _assign(section, _hosts(value), top, envs)
    return top, envs


def _assign(section, hosts, top, envs):
    if section is None or section in ("assets", "observability"):
        top.extend(hosts)
        return
    em = re.match(r"^env\.([A-Za-z0-9_-]+)$", section or "")
    if em:
        envs[em.group(1)] = hosts
    else:
        top.extend(hosts)


def _hosts(blob: str):
    """Every pattern = "host" and every bare "host" inside a routes list."""
    hosts = re.findall(r'pattern\s*=\s*"([^"]+)"', blob)
    if hosts:
        return hosts
    inner = blob[blob.find("[") + 1: blob.rfind("]")] if "[" in blob else ""
    return [h for h in re.findall(r'"([^"]+)"', inner) if "=" not in h]


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 64
    path, env = sys.argv[1], sys.argv[2]

    if env == "production":
        return 0  # production is allowed to claim production's hostnames

    with open(path, encoding="utf-8") as fh:
        top, envs = parse_routes(fh.read())

    if env not in envs:
        print(f"deploy-attachment-check: wrangler.toml declares no [env.{env}]", file=sys.stderr)
        return 1

    env_routes = envs[env]
    if env_routes is None:
        print(
            f"REFUSED: [env.{env}] does not declare a `routes` key.\n"
            f"  wrangler treats routes as INHERITABLE, so this deploy would claim\n"
            f"  production's hostnames: {', '.join(top) or '(none declared)'}\n"
            f"  That is exactly what happened on 2026-08-13. Declare `routes = []`\n"
            f"  to attach none, or list the hostnames this environment owns.",
            file=sys.stderr)
        return 1

    shared = sorted(set(env_routes) & set(top))
    if shared:
        print(
            f"REFUSED: [env.{env}] claims hostname(s) production also claims:\n"
            f"  {', '.join(shared)}\n"
            f"  A non-production environment must never answer on a production host.",
            file=sys.stderr)
        return 1

    where = ", ".join(env_routes) if env_routes else "workers.dev only"
    print(f"  OK  env={env} attaches to: {where} (shares nothing with production)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
