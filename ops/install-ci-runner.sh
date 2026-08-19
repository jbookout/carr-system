#!/bin/bash
# install-ci-runner.sh — register this Mac as the self-hosted runner that carries
# the CI checks job.
#
# JOE RUNS THIS, NOT CLAUDE, and that is a boundary rather than a courtesy. Every
# step below either needs a password or hands a machine the ability to execute
# code: it creates a macOS user, installs a LaunchDaemon, and downloads and runs
# a binary from GitHub. A session that could do all of that unattended is a
# session that could quietly turn any Mac into a runner, so the script is written
# to be read first and run second.
#
#   sudo CARR_RUNNER_TOKEN="$(gh api -X POST \
#     repos/jbookout/carr-system/actions/runners/registration-token --jq .token)" \
#     ops/install-ci-runner.sh
#
# Mint the token as YOURSELF, in the same command, for two reasons: it is a
# repo-admin credential, and it expires one hour after it is created. Do not
# paste it into a file and do not let it reach a transcript.
#
# ─── THE SECURITY BOUNDARY ────────────────────────────────────────────────────
#
# A self-hosted runner executes whatever the workflow says, on this machine. On a
# GitHub-hosted runner that was uninteresting because the VM was destroyed
# afterwards and held nothing; here it is the entire design question, so the
# answer is written down rather than left to whoever installs it.
#
# THE CLASSIC FORK RISK DOES NOT APPLY. The documented danger of self-hosted
# runners is a pull request from a fork running attacker-controlled code on your
# hardware. jbookout/carr-system is private and single-owner: there are no forks
# and no outside contributors, so no untrusted party can propose a workflow.
#
# THE REAL RISK IS DIFFERENT AND IS NOT ZERO. Many agent sessions push branches to
# this repo, and a pull_request run executes that branch's ops/ci.sh — so anything
# that can push a branch can run code on this Mac. That is acceptable only if the
# code runs somewhere it cannot do damage, which is what the rest of this file is
# for.
#
# WHAT THE RUNNER CAN REACH, stated positively:
#   · its own home, /var/carr-ci-runner, and the _work checkout under it;
#   · world-readable parts of the disk — /usr, /opt/homebrew, /Applications — and
#     Joe's home directory as far as its 755 permissions go, which INCLUDES the
#     working tree at /Users/booko/carr-system;
#   · the network, in full. It has no CARR credential, so reaching Neon's host
#     buys it nothing, but it can talk to the internet;
#   · a throwaway Postgres it starts itself, on loopback, that dies with the job.
#
# WHAT IT CANNOT REACH:
#   · ~/.config/carr — the live Neon DSNs, the Neon API key, the age key the
#     encrypted backups are sealed with. That directory is mode 700 and every file
#     in it is 600, both owned by booko, so a different unprivileged user cannot
#     open them and cannot even list the directory;
#   · anything via sudo. This user is deliberately NOT in the admin group, which
#     is what stops it from reading around those file modes;
#   · the login keychain, which is a separate credential store keyed to Joe's
#     account;
#   · CARR production through inherited environment. A LaunchDaemon starts from
#     the system's environment and not from a login shell, so nothing that Joe has
#     ever sourced into a terminal is visible to it.
#
# AND IT IS CHECKED, NOT MERELY ASSERTED. ops/ci-runner-guard.sh runs as the first
# step of every CI job, before checkout, and fails the job if it can open any file
# under a CARR secret directory, if any production-shaped variable is in its
# environment, or if its user is in the admin group. Installing the runner as the
# wrong user therefore produces a loud red CI run, not a quiet compromise.
#
# RESIDUAL, RECORDED RATHER THAN HIDDEN: /Users/booko/carr-system is mode 755, so
# the runner user can READ Joe's working tree, including anything untracked left
# in out/. It cannot write there. Tightening that directory to 700 would close it
# and is a one-line follow-up, deliberately not done here because changing
# permissions on Joe's working tree is a bigger blast radius than this change
# should carry.

set -euo pipefail

RUNNER_VERSION="2.336.0"
RUNNER_SHA256="8e8839c49b7060b6b2154f4931f815df330c27f167d53ef2239ee3dfce28b079"
RUNNER_TARBALL="actions-runner-osx-arm64-${RUNNER_VERSION}.tar.gz"
RUNNER_URL="https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/${RUNNER_TARBALL}"

REPO_URL="https://github.com/jbookout/carr-system"
SVC_USER="_carrci"
SVC_GROUP="_carrci"
SVC_HOME="/var/carr-ci-runner"
SVC_LABEL="com.carr.ci-runner"
PLIST="/Library/LaunchDaemons/${SVC_LABEL}.plist"
LABELS="carr-ci"

die() { echo "install-ci-runner: $*" >&2; exit 1; }
say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

# ─── PRECONDITIONS ────────────────────────────────────────────────────────────
[ "$(id -u)" -eq 0 ] || die "run me with sudo — creating a user and a LaunchDaemon both need root"
[ "$(uname -s)" = "Darwin" ] || die "this installs a macOS LaunchDaemon; it is not portable to Linux"
[ "$(uname -m)" = "arm64" ] || die "the pinned runner tarball is osx-arm64; this Mac is $(uname -m)"
[ -n "${CARR_RUNNER_TOKEN:-}" ] || die "set CARR_RUNNER_TOKEN — see the header for the one-liner that mints it"

# The migration class needs a Postgres 17 to initdb a throwaway cluster from, and
# ops/ci-postgres.sh looks in exactly these two prefixes. Checking now turns a
# confusing red CI run three days from now into a clear refusal right here.
[ -x /opt/homebrew/opt/postgresql@17/bin/initdb ] || [ -x /usr/local/opt/postgresql@17/bin/initdb ] \
  || die "no postgresql@17 — run: brew install postgresql@17"
command -v git >/dev/null || die "no git on PATH"

# ─── THE SERVICE USER ─────────────────────────────────────────────────────────
say "service account ${SVC_USER}"
if dscl . -read "/Users/${SVC_USER}" >/dev/null 2>&1; then
  echo "   already exists — leaving it alone"
else
  # A UID BELOW 500, which is what keeps this account out of the login window and
  # out of Fast User Switching: macOS treats sub-500 as a system account. The
  # first free one is taken rather than a fixed number, because a collision with
  # an existing service account would corrupt both.
  uid=""
  for candidate in $(seq 400 499); do
    if ! dscl . -search /Users UniqueID "$candidate" 2>/dev/null | grep -q .; then
      uid="$candidate"; break
    fi
  done
  [ -n "$uid" ] || die "no free UID in 400-499"

  gid=""
  for candidate in $(seq 400 499); do
    if ! dscl . -search /Groups PrimaryGroupID "$candidate" 2>/dev/null | grep -q .; then
      gid="$candidate"; break
    fi
  done
  [ -n "$gid" ] || die "no free GID in 400-499"

  dscl . -create "/Groups/${SVC_GROUP}"
  dscl . -create "/Groups/${SVC_GROUP}" PrimaryGroupID "$gid"

  dscl . -create "/Users/${SVC_USER}"
  dscl . -create "/Users/${SVC_USER}" UserShell /bin/bash
  dscl . -create "/Users/${SVC_USER}" RealName "CARR CI runner"
  dscl . -create "/Users/${SVC_USER}" UniqueID "$uid"
  dscl . -create "/Users/${SVC_USER}" PrimaryGroupID "$gid"
  dscl . -create "/Users/${SVC_USER}" NFSHomeDirectory "$SVC_HOME"
  dscl . -create "/Users/${SVC_USER}" IsHidden 1
  # NO PASSWORD IS SET, on purpose: an account with no password cannot be logged
  # into interactively, and the daemon does not need one to run as it.
  echo "   created uid=$uid gid=$gid"
fi

# THE ADMIN CHECK IS NOT PARANOIA. If this account can sudo, then every file mode
# protecting ~/.config/carr is decorative and the whole boundary above is a
# fiction. ci-runner-guard.sh tests the same thing on every run; failing here as
# well means a mistake is caught before the runner ever starts rather than on its
# first job.
if dseditgroup -o checkmember -m "$SVC_USER" admin >/dev/null 2>&1; then
  die "${SVC_USER} is in the admin group — it could sudo past every protection this design relies on"
fi

install -d -o "$SVC_USER" -g "$SVC_GROUP" -m 700 "$SVC_HOME"

# ─── THE RUNNER PACKAGE ───────────────────────────────────────────────────────
say "actions/runner ${RUNNER_VERSION}"
if [ -x "${SVC_HOME}/config.sh" ]; then
  echo "   already unpacked — leaving it alone"
else
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT
  curl -fsSL -o "${tmp}/${RUNNER_TARBALL}" "$RUNNER_URL" || die "download failed"

  # VERIFY BEFORE UNPACKING, NEVER AFTER. This tarball is about to be executed as
  # a long-lived service on Joe's Mac, and doctrine already says to pin CI actions
  # to immutable identifiers rather than to tags for exactly this reason. A
  # release asset URL is not immutable; this digest is. `shasum -c` is used rather
  # than a string comparison so a truncated or empty download fails loudly instead
  # of comparing equal to nothing.
  echo "${RUNNER_SHA256}  ${tmp}/${RUNNER_TARBALL}" | shasum -a 256 -c - \
    || die "SHA256 MISMATCH — refusing to unpack. Do not retry; find out why."

  sudo -u "$SVC_USER" tar xzf "${tmp}/${RUNNER_TARBALL}" -C "$SVC_HOME" \
    || die "unpack failed"
  rm -rf "$tmp"; trap - EXIT
  echo "   verified and unpacked into ${SVC_HOME}"
fi

# THE RUNNER'S PATH, which a LaunchDaemon does not inherit from anywhere useful.
# A daemon starts with a minimal PATH that has no Homebrew in it, so without this
# the job would fail on `initdb` and `node` with "command not found" while the
# same commands work perfectly in Joe's terminal — the most confusing possible
# failure. The runner reads this file and applies it to every job it runs.
PG_BIN=""
for pg in /opt/homebrew/opt/postgresql@17/bin /usr/local/opt/postgresql@17/bin; do
  [ -d "$pg" ] && PG_BIN="$pg" && break
done
RUNNER_PATH="/opt/homebrew/bin:/opt/homebrew/sbin:${PG_BIN}:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
printf '%s\n' "$RUNNER_PATH" > "${SVC_HOME}/.path"
chown "$SVC_USER:$SVC_GROUP" "${SVC_HOME}/.path"

# ─── REGISTRATION ─────────────────────────────────────────────────────────────
say "registering with ${REPO_URL}"
# --replace so re-running this after a wiped or renamed runner does the obvious
# thing instead of registering a second ghost under a suffixed name.
# --unattended so it never stops on a prompt from inside a sudo invocation.
# The default labels self-hosted / macOS / ARM64 are added by the runner itself;
# carr-ci is ours, and ci.yml requires all four, so a machine that has not been
# through this script cannot start receiving CI jobs merely by existing.
sudo -u "$SVC_USER" -H \
  "${SVC_HOME}/config.sh" \
    --url "$REPO_URL" \
    --token "$CARR_RUNNER_TOKEN" \
    --name "$(scutil --get LocalHostName 2>/dev/null || hostname -s)-carr-ci" \
    --labels "$LABELS" \
    --work _work \
    --unattended \
    --replace \
  || die "config.sh failed — a registration token expires one hour after it is minted; mint a fresh one and retry"

# ─── THE SERVICE ──────────────────────────────────────────────────────────────
say "LaunchDaemon ${SVC_LABEL}"
# A DAEMON, NOT THE RUNNER'S OWN svc.sh. svc.sh installs a LaunchAGENT, which only
# runs while its user has a GUI session — and this user deliberately cannot log
# in, so an agent would never start. A LaunchDaemon with UserName runs at boot,
# as the unprivileged account, with no session required, which is the combination
# this design needs.
#
# The plist carries no DOCTYPE line. launchd parses it with CFPropertyList, which
# does not need one, and the conventional DOCTYPE names a URL on apple.com that
# this repo's own unattended guard reads as an unrecognised network host. Dropping
# a line nothing parses beats teaching a security gate to ignore a hostname.
RUN_SCRIPT="${SVC_HOME}/bin/runsvc.sh"
[ -x "$RUN_SCRIPT" ] || RUN_SCRIPT="${SVC_HOME}/run.sh"

launchctl bootout "system/${SVC_LABEL}" 2>/dev/null || true
cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0">
<dict>
  <key>Label</key><string>${SVC_LABEL}</string>
  <key>UserName</key><string>${SVC_USER}</string>
  <key>GroupName</key><string>${SVC_GROUP}</string>
  <key>WorkingDirectory</key><string>${SVC_HOME}</string>
  <key>ProgramArguments</key>
  <array><string>${RUN_SCRIPT}</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>SessionCreate</key><true/>
  <key>EnvironmentVariables</key>
  <dict>
    <key>HOME</key><string>${SVC_HOME}</string>
    <key>PATH</key><string>${RUNNER_PATH}</string>
  </dict>
  <key>StandardOutPath</key><string>${SVC_HOME}/runner.out.log</string>
  <key>StandardErrorPath</key><string>${SVC_HOME}/runner.err.log</string>
</dict>
</plist>
PLISTEOF
chown root:wheel "$PLIST"
chmod 644 "$PLIST"
plutil -lint "$PLIST" >/dev/null || die "the generated plist does not parse"
launchctl bootstrap system "$PLIST"
launchctl enable "system/${SVC_LABEL}"

# ─── VERIFY, RATHER THAN ANNOUNCE SUCCESS ─────────────────────────────────────
say "verification"
sleep 3
if launchctl print "system/${SVC_LABEL}" >/dev/null 2>&1; then
  echo "   ok   daemon is loaded"
else
  die "the daemon did not load — check ${SVC_HOME}/runner.err.log"
fi

# The guard is the property that actually matters, so it is run here as the
# service user rather than trusted to be right on the first real job.
GUARD="$(cd "$(dirname "$0")" && pwd)/ci-runner-guard.sh"
if sudo -u "$SVC_USER" -H env -i HOME="$SVC_HOME" PATH=/usr/bin:/bin "$GUARD" >/dev/null 2>&1; then
  echo "   ok   runner guard passes as ${SVC_USER} — no CARR credential reachable"
else
  die "THE RUNNER GUARD FAILS AS ${SVC_USER}. Do not leave this installed; run
     ops/ci-runner-guard.sh as that user to see which check refused."
fi

cat <<EOF

Done. Confirm GitHub agrees, as yourself:

  gh api repos/jbookout/carr-system/actions/runners \\
    --jq '.runners[] | {name, status, labels: [.labels[].name]}'

Expect status "online" and all four of self-hosted, macOS, ARM64, carr-ci. Until
that shows online, CI jobs will queue rather than fail — ci.yml requires the
carr-ci label, so nothing runs on an unprepared machine by accident.

To take this machine back out of CI:
  sudo launchctl bootout system/${SVC_LABEL}
  sudo rm ${PLIST}
  sudo -u ${SVC_USER} ${SVC_HOME}/config.sh remove --token "\$(gh api -X POST \\
    repos/jbookout/carr-system/actions/runners/remove-token --jq .token)"
EOF
