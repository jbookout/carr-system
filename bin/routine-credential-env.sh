#!/bin/zsh
# Narrow credential loader for unattended routines.  This file is safe to source;
# it never evaluates ~/.config/carr/db.env.

carr_clear_routine_db_env() {
  unset DATABASE_URL CARR_DB_WRITER_URL CARR_DB_OWNER_URL CARR_DB_CADENCE_URL \
    CARR_DB_MATCHER_URL CARR_DB_EXPORTER_URL CARR_DB_JOBS_URL CARR_DB_BACKUP_URL \
    BACKUP_DATABASE_URL CARR_IMPORT_DB_URL PGHOST PGHOSTADDR PGPORT PGDATABASE \
    PGUSER PGPASSWORD PGSERVICE PGOPTIONS
}

carr_credential_file_mode() {
  local file="$1" mode
  mode="$(/usr/bin/stat -f '%Lp' "$file" 2>/dev/null || true)"
  [[ "$mode" == <-> ]] || mode="$(/usr/bin/stat -c '%a' "$file" 2>/dev/null || true)"
  [[ "$mode" == <-> ]] || return 1
  print -r -- "$mode"
}

# carr_load_routine_db_env KEY [KEY ...]
# Accept only simple KEY=VALUE lines for the named keys.  A routine never needs
# shell expansion in a credential file, and accepting it would turn config into
# executable code.  Unknown keys stay unread rather than being inherited.
carr_load_routine_db_env() {
  local env_file="${CARR_ROUTINE_DB_ENV_FILE:-$HOME/.config/carr/db.env}"
  local line key value wanted mode
  local -A allowed seen
  for wanted in "$@"; do allowed[$wanted]=1; done
  [ -f "$env_file" ] || return 0
  mode="$(carr_credential_file_mode "$env_file" || true)"
  [[ "$mode" == <-> ]] || { print -ru2 -- "cannot determine routine credential file permissions"; return 78; }
  (( (8#$mode & 077) == 0 )) || { print -ru2 -- "routine credential file must be 0600"; return 78; }
  while IFS= read -r line || [ -n "$line" ]; do
    [[ -z "${line//[[:space:]]/}" || "$line" == \#* ]] && continue
    [[ "$line" =~ '^([A-Z][A-Z0-9_]*)=(.*)$' ]] || {
      print -ru2 -- "routine credential config is malformed"
      return 78
    }
    key="${match[1]}"; value="${match[2]}"
    [[ -n "${allowed[$key]:-}" ]] || continue
    [[ -z "${seen[$key]:-}" ]] || {
      print -ru2 -- "routine credential config repeats $key"
      return 78
    }
    # db.env uses quoted literal URIs; remove one matching outer pair only.
    # Never evaluate the contents: ampersands are URI query separators, while
    # substitutions/backticks remain executable-shell syntax and are refused.
    if [[ "$value" == \'*\' ]]; then value="${value#\'}"; value="${value%\'}"
    elif [[ "$value" == \"*\" ]]; then value="${value#\"}"; value="${value%\"}"; fi
    [[ "$value" != *'$('* && "$value" != *'`'* ]] || {
      print -ru2 -- "routine credential config has unsafe value for $key"; return 78; }
    [[ -n "$value" ]] || {
      print -ru2 -- "routine credential config has unsafe value for $key"
      return 78
    }
    typeset -gx "$key=$value"
    seen[$key]=1
  done < "$env_file"
}

# Runs a routine child with only the declared database capabilities.  Other
# credentials must be loaded by that child's own narrow adapter, never inherited
# from a broad db.env source.
carr_routine_exec() {
  local -a routine_env
  routine_env=(HOME="$HOME" PATH="$PATH" LANG="${LANG:-C}" TMPDIR="${TMPDIR:-/tmp}" \
    CARR_CORRELATION_ID="${CARR_CORRELATION_ID:-}" \
    CARR_EXPORT_LIVE="${CARR_EXPORT_LIVE:-}" \
    HC_EXPORTS_RC="${HC_EXPORTS_RC:-}" \
    HC_BACKUP_RC="${HC_BACKUP_RC:-}" \
    HC_CHAIN_RC="${HC_CHAIN_RC:-}" \
    CARR_DB_JOBS_URL="${CARR_DB_JOBS_URL:-}" \
    CARR_DB_EXPORTER_URL="${CARR_DB_EXPORTER_URL:-}")
  # A Drive root is not routine ambient state.  It crosses this boundary only
  # after the caller has explicitly opened its recovery envelope.
  if [ "${CARR_DRIVE_RECOVERY:-0}" = "1" ]; then
    routine_env+=(CARR_VAULT="${CARR_VAULT:-}")
  fi
  env -i "${routine_env[@]}" "$@"
}
