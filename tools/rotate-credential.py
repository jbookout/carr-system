#!/usr/bin/env python3
"""
rotate-credential.py — rotate a CARR credential without any value ever being
displayed, echoed, logged, or placed on a command line.

RENAMED FROM tools/set-jobs-password.py on 2026-08-14 (no callers anywhere; the
old name described one role and the job is now three credentials, and a name
that does not match its behaviour is the thing rule 3578d799 forbids). The
carr_jobs path below is the original tool's logic, kept intact — the interactive
prompt still works exactly as it did.

WHY IT EXISTS IN THIS FORM. On 2026-08-14 a session diagnosing a silent failure
in bin/migrate-prod.sh ran it under `zsh -x`. The trace expanded the line that
sources ~/.config/carr/db.env and printed all three secrets in full: the local
exporter DSN, the nightly jobs DSN, and NEON_API_KEY — the last of which is not
scoped to one database and does not expire. Rotation was the remedy, and doing
it by hand would have meant a password on a command line, which is the same
class of mistake one step further along.

THE FOUR THINGS IT ROTATES

  --role carr_jobs             ALTER ROLE, then rewrite CARR_DB_JOBS_URL
  --role app_exporter_local    ALTER ROLE, then rewrite CARR_DB_EXPORTER_URL
  --role carr_backup           ALTER ROLE, then rewrite CARR_DB_BACKUP_URL —
                               and MINT that line if it does not exist yet,
                               which is the only minting this tool does. See
                               the MINTABLE block below for why that exception
                               is one role wide.
  --neon-api-key               mint a new Neon API key, then revoke the old one

  --generate makes it non-interactive: a 40-character password from the
  URL-safe alphabet, generated in-process, never shown. Without it the tool
  prompts twice with hidden input, which is the original behaviour. A mint is
  --generate only: a credential no human ever types should not be one a human
  chooses.

  --github-secret also sets the BACKUP_DATABASE_URL repo secret, passed on
  stdin so it never reaches the process table or shell history. Use it for
  carr_backup every time. bin/backup-dump.sh serves this Mac AND
  .github/workflows/backup-nightly.yml off that one role (rule a8c55a47), so
  moving one end alone does not degrade the backup — it breaks the other end
  silently, on a night nobody is watching.

  DATABASE_URL=<owner> .venv/bin/python tools/rotate-credential.py \
      --role carr_backup --generate --github-secret

THE PASSWORD IS SWAPPED INTO THE EXISTING URL, NOT REBUILT FROM THE OWNER URL.
The original tool derived the jobs URL from the owner DSN by substituting the
userinfo, which silently inherits the owner's host and query string. The jobs
entry carries `&channel_binding=require` and the exporter entry does not, so
rebuilding from the owner would have quietly changed connection semantics for
one of them. Only the password is replaced; everything else in the line is left
byte-identical.

ORDER OF OPERATIONS FOR THE API KEY, and it matters: mint, verify the new key
works, write it to db.env, and only then revoke the old one. Revoking first
would take out every unattended path at once — bin/migrate-prod.sh,
bin/import-doctrine.sh, bin/restore-rehearse.sh and the partner ping all derive
their connection through neonctl — with no way back.

  DATABASE_URL=<owner> .venv/bin/python tools/rotate-credential.py --role carr_jobs --generate
  .venv/bin/python tools/rotate-credential.py --neon-api-key

The owner DSN comes from tools/db-tap.py's break-glass run mode, so it is never
typed. Nothing here prints a secret; the only place any value exists afterward
is db.env, mode 600.
"""

import argparse
import getpass
import json
import os
import re
import secrets
import stat
import string
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

ENV_PATH = os.path.expanduser("~/.config/carr/db.env")
NEON_API = "https://console.neon.tech/api/v2"
# The same organization tools/db-tap.py pins, for the same reason: a lookup that
# can drift is not an identity.
NEON_ORG = "org-dry-dew-75906281"

# The env var each role's connection string lives in.
ROLE_ENV = {
    "carr_jobs": "CARR_DB_JOBS_URL",
    "app_exporter_local": "CARR_DB_EXPORTER_URL",
    "carr_backup": "CARR_DB_BACKUP_URL",
}

# THE ONE ROLE THIS TOOL MAY MINT A CONNECTION FOR, rather than only rotate.
#
# Everywhere else the refusal below stands: an absent env key means somebody
# should look at why, not that a tool should invent a DSN. carr_backup is the
# exception for a reason with a date on it. On 2026-08-20 the nightly chain was
# found dead for three nights because CARR_DB_BACKUP_URL had never existed on
# this Mac — #288 began requiring it, migration 0119 ships the role with a
# placeholder password for a human to replace, and nothing in between could
# produce the line. The gap was not the credential; it was that no path existed
# to create it, so the only routes left were a hand-built DSN or a break-glass
# session, and both of those are worse than this.
#
# The password still never leaves this process, the human still runs the
# command, and the control-plane declaration (routine_backup.provisioning =
# external_human_approval) still holds: this is the tool that approval drives,
# not a way around it.
MINTABLE = {"carr_backup"}

# The database is the same one every routine credential already points at, so
# the host and database name are COPIED from a routine DSN that is known to
# work rather than typed or derived from the owner. Deriving from the owner is
# what the header of this file warns against; deriving from a peer is not the
# same act, because a peer has already proven the host reachable and carries no
# privilege the new line could inherit.
#
# The query string is SET, never inherited. CARR_DB_JOBS_URL carries
# &channel_binding=require and CARR_DB_EXPORTER_URL does not; copying whichever
# happened to be present would make the backup credential's connection
# semantics depend on which peer was read first. sslmode=require is the shape
# .github/workflows/backup-nightly.yml documents and the shape the cloud backup
# has been running on since 2026-08-14.
MINT_SOURCE_KEYS = ("CARR_DB_EXPORTER_URL", "CARR_DB_JOBS_URL")
MINT_QUERY = "sslmode=require"

# The GitHub repository secret the cloud nightly reads. Same script, same role,
# same database — see .github/workflows/backup-nightly.yml.
GITHUB_SECRET = "BACKUP_DATABASE_URL"

# No spaces, quotes, @ / : ? # — every one of them changes how a URL parses.
ALPHABET = string.ascii_letters + string.digits


def read_env() -> dict[str, str]:
    """Values from db.env. Never logged, never returned to a caller that prints."""
    out: dict[str, str] = {}
    try:
        for line in open(ENV_PATH, encoding="utf-8").read().splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            out[k.strip()] = v.strip().strip("\"'")
    except OSError as e:
        sys.exit(f"rotate-credential: cannot read {ENV_PATH}: {e}")
    return out


def shell_quote(value: str) -> str:
    """Single-quote a value so `set -a; . db.env` survives it.

    THIS FILE HAS TWO PARSERS AND THEREFORE TWO CONTRACTS (rule 73381d78). Python
    readers split on '=' and strip quotes, so they do not care. zsh SOURCES this
    file — bin/migrate-prod.sh, bin/nightly.sh and every other shell job do
    `set -a; . db.env` — and an unquoted '&' in a DSN is a background operator,
    not a character.

    The first version of this tool wrote the value bare. Every Python check
    passed, both roles connected, and bin/migrate-prod.sh died with
    "db.env:3: parse error near `&`" — the jobs DSN carries
    &channel_binding=require. Caught only because a completion gate demanded a
    fresh run of the shell path rather than a restatement of the Python one.

    Single quotes because a postgres URL cannot contain one; the escape below
    handles it anyway rather than trusting that."""
    return "'" + value.replace("'", "'\\''") + "'"


def write_env_key(key: str, value: str) -> None:
    """Replace exactly one key in db.env, atomically, preserving every other line
    including comments and blank lines. The original tool rebuilt the file from a
    filtered list, which is fine until a write is interrupted — os.replace makes
    the swap atomic so a crash can never leave a half-written credentials file."""
    value = shell_quote(value)
    try:
        original = open(ENV_PATH, encoding="utf-8").read().splitlines()
    except OSError:
        original = []

    replaced = False
    lines: list[str] = []
    for line in original:
        if line.startswith(f"{key}="):
            lines.append(f"{key}={value}")
            replaced = True
        else:
            lines.append(line)
    if not replaced:
        lines.append(f"{key}={value}")

    d = os.path.dirname(ENV_PATH)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".db.env.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(tmp, ENV_PATH)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def swap_password(url: str, password: str) -> str:
    """Replace ONLY the password inside a postgres URL. Host, user, database and
    every query parameter survive byte-identical."""
    m = re.match(r"^(?P<pre>\w+://)(?P<user>[^:/@]+):(?P<pw>[^@]*)@(?P<rest>.+)$", url)
    if not m:
        sys.exit("rotate-credential: the existing URL is not in scheme://user:pass@host form — "
                 "refusing to guess at its shape")
    return f"{m.group('pre')}{m.group('user')}:{password}@{m.group('rest')}"


def new_password() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(40))


def mint_url(role: str, env: dict[str, str], password: str) -> str:
    """Build a first connection string for `role` from a peer routine DSN.

    Host and database are copied from a peer that already connects; user is the
    new role; the query string is SET to MINT_QUERY rather than inherited. See
    the MINTABLE block above for why this exists and why it is one role only.

    Returns a URL. Never logs it — the caller verifies it, writes it to a 0600
    file, and prints only that it did so."""
    for key in MINT_SOURCE_KEYS:
        peer = env.get(key)
        if not peer:
            continue
        m = re.match(r"^(?P<scheme>\w+://)[^:/@]+:[^@]*@(?P<host>[^/]+)/(?P<db>[^?]+)", peer)
        if not m:
            continue
        return (f"{m.group('scheme')}{role}:{password}@"
                f"{m.group('host')}/{m.group('db')}?{MINT_QUERY}")
    sys.exit(f"rotate-credential: cannot mint {ROLE_ENV[role]} — none of "
             f"{', '.join(MINT_SOURCE_KEYS)} is present in {ENV_PATH} in "
             f"scheme://user:pass@host/db form, and this tool copies a host from a "
             f"peer rather than inventing one.")


def set_github_secret(url: str) -> None:
    """Push the same value to the repo secret the cloud nightly reads.

    BOTH ENDS OR NEITHER. bin/backup-dump.sh serves two callers (rule a8c55a47):
    this Mac through db.env, and .github/workflows/backup-nightly.yml through
    this secret. Rotating one and not the other does not degrade the backup — it
    breaks whichever end was left behind, silently, until a night nobody is
    watching. On 2026-08-20 the cloud end was the ONLY working backup path, so
    the end that would have broken was the one actually protecting the data.

    The value goes in on stdin, never on a command line, so it stays out of the
    process table and out of shell history."""
    proc = subprocess.run(["gh", "secret", "set", GITHUB_SECRET],
                          input=url, text=True, capture_output=True)
    if proc.returncode != 0:
        sys.exit(f"rotate-credential: db.env IS WRITTEN but `gh secret set {GITHUB_SECRET}` "
                 f"failed ({proc.stderr.strip()[:200]}). The two ends are now out of step and "
                 f"the cloud nightly will fail on its next run. Re-run this command to reset "
                 f"both together.")
    print(f"  github secret {GITHUB_SECRET}: set (value passed on stdin, never displayed)")


def rotate_role(role: str, generate: bool, github_secret: bool = False) -> int:
    import psycopg
    from psycopg import sql

    owner = os.environ.get("DATABASE_URL")
    if not owner:
        sys.exit("rotate-credential: DATABASE_URL is not set. This needs the OWNER credential — "
                 "run it through tools/db-tap.py's break-glass run mode so the DSN is never typed.")

    env_key = ROLE_ENV[role]
    env = read_env()
    existing = env.get(env_key)
    minting = False
    if not existing:
        if role not in MINTABLE:
            sys.exit(f"rotate-credential: {env_key} is not in {ENV_PATH} — nothing to rotate. "
                     f"Add the line first; this tool changes a password, it does not mint a "
                     f"connection.")
        minting = True
        if not generate:
            # A first provision has no old value to preserve compatibility with,
            # so there is nothing a typed password buys and one thing it costs:
            # a human-chosen secret for an unattended role, typed twice, at the
            # keyboard. Generated is strictly better here.
            sys.exit(f"rotate-credential: {env_key} does not exist yet, so this run would MINT "
                     f"it. Pass --generate: a credential no human ever needs to type should not "
                     f"be one a human chooses.")

    if generate:
        pw = new_password()
    else:
        pw = getpass.getpass(f"New {role} password (hidden): ")
        if pw != getpass.getpass("Again: "):
            sys.exit("passwords did not match — nothing changed")
        if len(pw) < 12:
            sys.exit("use at least 12 characters — nothing changed")
        if any(c in pw for c in " '\"@/:?#"):
            sys.exit("avoid spaces, quotes, and @ / : ? # (they break the URL form) — nothing changed")

    with psycopg.connect(owner) as conn:
        conn.execute(sql.SQL("alter role {} with password {}").format(
            sql.Identifier(role), sql.Literal(pw)))
        conn.commit()

    # Branch on `existing` rather than on the flag: same two cases, but this
    # form lets a type checker see that swap_password never receives None.
    new_url = swap_password(existing, pw) if existing else mint_url(role, env, pw)

    # PROVE IT BEFORE WRITING IT. If the new credential does not connect, the old
    # line stays in db.env and the only damage is a role whose password no longer
    # matches a file — recoverable by re-running. Writing first and verifying
    # after would leave an unusable file if the connection failed.
    with psycopg.connect(new_url) as conn:
        row = conn.execute("select current_user").fetchone()
        if not row or row[0] != role:
            sys.exit(f"rotate-credential: verification connected as {row[0] if row else 'nobody'}, "
                     f"expected {role} — db.env NOT written")

    write_env_key(env_key, new_url)
    print(f"{role}: password {'set' if minting else 'rotated'} · {env_key} "
          f"{'created' if minting else 'rewritten'} · verified connection as {role}")

    if github_secret:
        set_github_secret(new_url)
    elif role == "carr_backup":
        # Not a suggestion. bin/backup-dump.sh runs in both places off the same
        # role, and the cloud end is the one that kept working through the
        # 2026-08-20 outage — so the end left behind here is the one currently
        # protecting the data.
        print(f"  WARNING: the cloud nightly still holds the OLD password. "
              f"{GITHUB_SECRET} must be set to match or "
              f".github/workflows/backup-nightly.yml fails on its next run. "
              f"Re-run this with --github-secret to set both together.")
    return 0


def neon(method: str, path: str, key: str, body: dict | None = None) -> dict | list:
    req = urllib.request.Request(
        f"{NEON_API}{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {key}",
                 "Accept": "application/json",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode()
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        sys.exit(f"rotate-credential: Neon API {method} {path} returned {e.code}: "
                 f"{e.read().decode()[:200]}")
    except urllib.error.URLError as e:
        sys.exit(f"rotate-credential: cannot reach the Neon API: {e}")


def rotate_api_key(label: str) -> int:
    env = read_env()
    old = env.get("NEON_API_KEY")
    if not old:
        sys.exit(f"rotate-credential: NEON_API_KEY is not in {ENV_PATH}")

    before = neon("GET", "/api_keys", old)
    old_ids = {k["id"] for k in before} if isinstance(before, list) else set()

    created = neon("POST", "/api_keys", old, {"key_name": label})
    # Neon answers this endpoint with an object. A list back means the API
    # shape moved under us, and rotating a credential is the last place to
    # guess: say so and leave the working key alone.
    if not isinstance(created, dict):
        sys.exit("rotate-credential: Neon returned an unexpected shape for the new key "
                 "— nothing changed, old key intact")
    new_key = created.get("key")
    new_id = created.get("id")
    if not new_key or not new_id:
        sys.exit("rotate-credential: Neon did not return a key — nothing changed, old key intact")

    # VERIFY THE NEW KEY WORKS BEFORE ANYTHING IRREVERSIBLE HAPPENS.
    #
    # /projects requires org_id on this account and answers 400 without it — a
    # 400 that says nothing about whether the key is valid. The first run of this
    # tool hit exactly that, after the key was already minted and before db.env
    # was written, which left an orphaned key on the account and the old key
    # still in the file. Fixed by asking a question the API can actually answer.
    # The org is the same constant tools/db-tap.py pins.
    neon("GET", f"/projects?org_id={NEON_ORG}", new_key)

    write_env_key("NEON_API_KEY", new_key)

    # ONLY NOW IS THE OLD ONE SAFE TO REMOVE, and it is removed BY ID.
    #
    # Neon never returns key material on a list, so there is no way to look at
    # two keys and tell which one db.env held. The only honest identification is
    # "every key that existed before this run" — which is exact when the account
    # holds one key and dangerous when it holds several, because one of the
    # others could belong to something this tool knows nothing about. So: one
    # prior key is revoked without ceremony; more than one is REPORTED and left
    # alone, because guessing wrong there breaks something invisible.
    revoked: list[str] = []
    if len(old_ids) == 1:
        kid = next(iter(old_ids))
        neon("DELETE", f"/api_keys/{kid}", new_key)
        revoked.append(str(kid))
    elif len(old_ids) > 1:
        print(f"  NOTE: {len(old_ids)} prior keys exist and NONE were revoked — this tool "
              f"cannot tell which one db.env held, and revoking the wrong one breaks "
              f"something invisible. Revoke the exposed key by id in the Neon console:")
        for k in before:
            print(f"    id={k['id']} name={k.get('name')!r} last_used={k.get('last_used_at')}")

    remaining = neon("GET", "/api_keys", new_key)
    print(f"neon api key: minted '{label}' · db.env rewritten · verified against /projects · "
          f"revoked {len(revoked)} prior key(s){' (' + ', '.join(revoked) + ')' if revoked else ''} · "
          f"{len(remaining)} key(s) now on the account")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="rotate a CARR credential; no value is ever displayed")
    p.add_argument("--role", choices=sorted(ROLE_ENV))
    p.add_argument("--neon-api-key", action="store_true")
    p.add_argument("--generate", action="store_true",
                   help="generate the password in-process instead of prompting")
    p.add_argument("--github-secret", action="store_true",
                   help=f"also set the {GITHUB_SECRET} repo secret the cloud nightly reads, "
                        f"so both ends of the same role move together")
    p.add_argument("--label", default="carr-system",
                   help="name for the new Neon API key")
    args = p.parse_args()

    if bool(args.role) == bool(args.neon_api_key):
        p.error("choose exactly one of --role or --neon-api-key")

    return (rotate_role(args.role, args.generate, args.github_secret)
            if args.role else rotate_api_key(args.label))


if __name__ == "__main__":
    sys.exit(main())
