#!/usr/bin/env python3
"""Set the carr_jobs role password and write CARR_DB_JOBS_URL — one command, value never displayed.

    DATABASE_URL=<owner url> .venv/bin/python tools/set-jobs-password.py

Prompts twice with hidden input (nothing echoes, nothing in shell history),
ALTERs the role over the owner connection, appends/replaces the
CARR_DB_JOBS_URL line in ~/.config/carr/db.env (chmod 600), then proves the
credential by connecting as carr_jobs and reporting current_user. The value
exists afterward only inside db.env.
"""
import getpass, os, re, sys, stat
import psycopg
from psycopg import sql

owner = os.environ.get("DATABASE_URL")
if not owner:
    sys.exit("DATABASE_URL is not set (owner credential; see prior orders for the neonctl incantation)")

pw = getpass.getpass("New carr_jobs password (hidden): ")
pw2 = getpass.getpass("Again: ")
if pw != pw2:
    sys.exit("passwords did not match — nothing changed")
if len(pw) < 12:
    sys.exit("use at least 12 characters — nothing changed")
if any(c in pw for c in " '\"@/:?#"):
    sys.exit("avoid spaces, quotes, and @ / : ? # (they break the URL form) — nothing changed")

with psycopg.connect(owner) as conn:
    conn.execute(sql.SQL("alter role carr_jobs with password {}").format(sql.Literal(pw)))
    conn.commit()

jobs_url = re.sub(r"://[^@]*@", f"://carr_jobs:{pw}@", owner)

env_path = os.path.expanduser("~/.config/carr/db.env")
lines = []
if os.path.exists(env_path):
    lines = [l for l in open(env_path).read().splitlines() if not l.startswith("CARR_DB_JOBS_URL=")]
lines.append(f"CARR_DB_JOBS_URL={jobs_url}")
with open(env_path, "w") as f:
    f.write("\n".join(lines) + "\n")
os.chmod(env_path, stat.S_IRUSR | stat.S_IWUSR)

with psycopg.connect(jobs_url) as conn:
    who = conn.execute("select current_user").fetchone()[0]

print(f"password set · db.env updated · verified connection as: {who}")
