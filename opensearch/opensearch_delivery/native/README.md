# Native OpenSearch deployment (no Docker)

This directory is the cloud-server alternative to `dev/compose.yaml`. It
keeps the existing OpenSearch contract unchanged: version 2.19, HTTPS on
`127.0.0.1:19200`, cluster name `maritime-law-dev`, BGE-M3 vectors, BM25 and
HNSW queries, and the `maritime_law_chunks_read` / `maritime_cases_read`
aliases. SQLite, files and application code stay outside OpenSearch.

The service runs as a dedicated Linux user under `systemd`. The installer
expects an unpacked official OpenSearch 2.19.0 distribution in
`OPENSEARCH_HOME`; it does not run Docker and does not import or delete an
index. The persistent Lucene data directory defaults to
`/srv/zhouxu/opensearch/vector_db_data/opensearch` and can be changed with
`ZHOUXU_DATA_ROOT`.

## Install

Install Java and the normal OpenSearch prerequisites, download and unpack the
official `opensearch-2.19.0-linux-x64.tar.gz`, then run as root. The password
is read from the process and never printed:

```bash
export OPENSEARCH_HOME=/opt/opensearch-2.19.0
export OPENSEARCH_INITIAL_ADMIN_PASSWORD='use-a-secret-from-your-secret-store'
export ZHOUXU_DATA_ROOT=/srv/zhouxu/opensearch/vector_db_data/opensearch
sudo --preserve-env=OPENSEARCH_HOME,OPENSEARCH_INITIAL_ADMIN_PASSWORD,ZHOUXU_DATA_ROOT \
  bash opensearch_delivery/native/install-native.sh
```

The installer runs the same Security-plugin initialization used by the
official Docker entrypoint, copies the distribution security files, writes the
native configuration, creates `opensearch`, and registers
`zhouxu-opensearch.service`. The service remains bound to loopback so the
backend can keep its current `https://localhost:19200` setting. Use a
CA-issued certificate and a private application account before exposing
OpenSearch outside the host; demo certificates are for the controlled-host
development-equivalent only. A repeat install preserves the existing native
configuration and password; remove the `.zhouxu-security-initialized` marker
only when deliberately rebuilding the security setup.

Check readiness without changing data:

```bash
systemctl status zhouxu-opensearch --no-pager
curl --fail --silent --show-error --insecure \
  -u "admin:${OPENSEARCH_INITIAL_ADMIN_PASSWORD}" \
  https://127.0.0.1:19200/_cluster/health
```

## Import the existing law and case data

Create the same credential file expected by the existing import tools, with
the native endpoint still on port 19200:

```bash
cd /srv/zhouxu/opensearch
cp opensearch_delivery/dev/.env.example opensearch_delivery/dev/.env
# edit only OPENSEARCH_INITIAL_ADMIN_PASSWORD on the server
```

Run the existing explicit release pipeline. It rebuilds physical indexes from
the separately supplied corpus and vectors, preserving retrieval behaviour and never
silently changing aliases:

```bash
python -B opensearch_delivery/scripts/maritime_index.py status --run-dir .ai-tmp/native_status
python -B opensearch_delivery/scripts/maritime_index.py validate --run-dir .ai-tmp/native_validate
python -B opensearch_delivery/scripts/maritime_index.py import --index maritime_law_chunks_v2 --run-dir .ai-tmp/native_import
python -B opensearch_delivery/scripts/maritime_index.py verify --index maritime_law_chunks_v2 --run-dir .ai-tmp/native_verify
python -B opensearch_delivery/scripts/acceptance.py --index maritime_law_chunks_v2 --run-dir .ai-tmp/native_acceptance
python -B opensearch_delivery/scripts/maritime_index.py activate --index maritime_law_chunks_v2 --run-dir .ai-tmp/native_activate
```

The law release should report 93,858 chunks. The separately supplied case snapshot is
`case_eb1be2c7fa1cd1fb` with 825 cases and 3,156 chunks. Keep snapshot files
and OpenSearch data on the same persistent disk. If a release fails, existing
aliases remain on the last published index; inspect the run directory before
retrying.

The case importer is part of the application repository and reads the same
configured endpoint and credentials. Run it from the application root (for a
deployment rooted at `/srv/zhouxu`, use that directory as the working
directory):

```bash
cd /srv/zhouxu
python -m backend.infrastructure.corpus.import_cases
```

Run the FastAPI application as a separate low-privilege account (for example
`zhouxu`), with read/write access only to the application SQLite file, material
storage, case snapshot and execution workspace. Native work commands run as
that account; this preserves the product contract on a single-tenant cloud
host, but it is not a Docker-equivalent multi-tenant sandbox. Add bubblewrap,
systemd sandboxing or a microVM before allowing mutually untrusted users to
run arbitrary code.

## Rollback and maintenance

`systemctl stop zhouxu-opensearch` stops only the native service. Do not delete
`vector_db_data/opensearch` during an application rollback. Keep old physical
indexes and snapshots until the replacement passes `verify` and the full
application trace. Docker compose remains available for local development; it
is not required on the cloud host.
