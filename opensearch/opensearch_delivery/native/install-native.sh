#!/usr/bin/env bash
set -euo pipefail

# Register OpenSearch 2.19 as a native Linux systemd service. This script
# does not import, delete, or switch indexes.

if [[ "$(id -u)" != "0" ]]; then
  echo "run as root (or through sudo)" >&2
  exit 1
fi
: "${OPENSEARCH_HOME:?set OPENSEARCH_HOME to the unpacked OpenSearch directory}"
: "${OPENSEARCH_INITIAL_ADMIN_PASSWORD:?set OPENSEARCH_INITIAL_ADMIN_PASSWORD in the process environment}"

OPENSEARCH_HOME="$(readlink -f "$OPENSEARCH_HOME")"
DATA_ROOT="$(readlink -f "${ZHOUXU_DATA_ROOT:-/srv/zhouxu/opensearch/vector_db_data/opensearch}")"
CONF_ROOT="$(readlink -f "${ZHOUXU_OPENSEARCH_CONF:-/etc/zhouxu-opensearch}")"
SERVICE_USER="${ZHOUXU_SERVICE_USER:-opensearch}"
SECRET_FILE="${ZHOUXU_OPENSEARCH_ENV:-/etc/zhouxu-opensearch/opensearch.env}"
UNIT_FILE="/etc/systemd/system/zhouxu-opensearch.service"

[[ -x "$OPENSEARCH_HOME/bin/opensearch" ]] || { echo "OpenSearch executable not found under $OPENSEARCH_HOME" >&2; exit 1; }
[[ "$OPENSEARCH_HOME" != "/" ]] || { echo "refusing root as OPENSEARCH_HOME" >&2; exit 1; }
[[ "$DATA_ROOT" = /* && "$CONF_ROOT" = /* && "$SECRET_FILE" = /* ]] || {
  echo "data, config and secret paths must be absolute" >&2
  exit 1
}

if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --home-dir "$OPENSEARCH_HOME" --shell /usr/sbin/nologin "$SERVICE_USER"
fi
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" "$DATA_ROOT"
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" "$CONF_ROOT"
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" /var/log/opensearch

# The official tar distribution does not initialize the Security and
# performance-analyzer plugins until this helper is run. Docker's entrypoint
# does this automatically; native deployments must do it once before the
# service starts. The marker keeps a later installer rerun from replacing an
# existing demo certificate/password.
ONE_TIME_SETUP="$OPENSEARCH_HOME/opensearch-onetime-setup.sh"
if [[ ! -f "$CONF_ROOT/.zhouxu-security-initialized" ]]; then
  [[ -x "$ONE_TIME_SETUP" ]] || {
    echo "OpenSearch one-time setup not found: $ONE_TIME_SETUP" >&2
    exit 1
  }
  command -v runuser >/dev/null 2>&1 || {
    echo "runuser is required to initialize OpenSearch as the service user" >&2
    exit 1
  }
  # The tarball is normally unpacked by root. The setup helper writes demo
  # certificates and plugin settings, so let the service account own config
  # before running it rather than starting OpenSearch as root.
  chown -R "$SERVICE_USER:$SERVICE_USER" "$OPENSEARCH_HOME/config"
  runuser -u "$SERVICE_USER" -- env \
    OPENSEARCH_HOME="$OPENSEARCH_HOME" \
    OPENSEARCH_INITIAL_ADMIN_PASSWORD="$OPENSEARCH_INITIAL_ADMIN_PASSWORD" \
    bash "$ONE_TIME_SETUP"
  touch "$OPENSEARCH_HOME/config/.zhouxu-security-initialized"
fi

# Preserve the official security plugin files and demo certificates.  The
# source config is copied only when the native config has not been initialized;
# this keeps a repeat install from silently replacing a working deployment.
if [[ ! -f "$CONF_ROOT/opensearch.yml" ]]; then
  cp -a "$OPENSEARCH_HOME/config/." "$CONF_ROOT/"
fi
template="$(dirname "$0")/opensearch.yml.example"
sed "s#__DATA_ROOT__#${DATA_ROOT}#g" "$template" > "$CONF_ROOT/opensearch.yml"
touch "$CONF_ROOT/.zhouxu-security-initialized"

cat > "$SECRET_FILE" <<EOF
OPENSEARCH_INITIAL_ADMIN_PASSWORD=$OPENSEARCH_INITIAL_ADMIN_PASSWORD
EOF
chmod 600 "$SECRET_FILE"
chown "$SERVICE_USER:$SERVICE_USER" "$SECRET_FILE" "$CONF_ROOT/opensearch.yml"
chown -R "$SERVICE_USER:$SERVICE_USER" "$CONF_ROOT" "$DATA_ROOT" /var/log/opensearch

template="$(dirname "$0")/zhouxu-opensearch.service.template"
sed \
  -e "s#__SERVICE_USER__#$SERVICE_USER#g" \
  -e "s#__OPENSEARCH_HOME__#$OPENSEARCH_HOME#g" \
  -e "s#__OPENSEARCH_CONF__#$CONF_ROOT#g" \
  -e "s#__OPENSEARCH_ENV__#$SECRET_FILE#g" \
  "$template" > "$UNIT_FILE"
chmod 644 "$UNIT_FILE"
systemctl daemon-reload
systemctl enable zhouxu-opensearch.service
systemctl restart zhouxu-opensearch.service
echo "zhouxu-opensearch.service installed; wait for HTTPS https://127.0.0.1:19200 before importing indexes"
