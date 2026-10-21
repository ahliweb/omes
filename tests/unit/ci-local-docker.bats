#!/usr/bin/env bats
# tests/unit/ci-local-docker.bats - unit tests for the container-facing half of
# scripts/ci-local.sh (issue #312): the Docker Hub mirror rewrite, proxy
# variable pass-through (names only, never values), the optional extra CA
# bundle, and credential redaction in logs.
#
# No real Docker is used: a recording fake `docker` stands in for it.

setup() {
  load '../test_helper.bash'
  omes_test_setup

  CI_LOCAL="${OMES_TEST_ROOT}/scripts/ci-local.sh"
  FAKE_BIN="${OMES_TEST_TMPDIR}/fakebin"
  FAKE_DOCKER_LOG="${OMES_TEST_TMPDIR}/docker-argv.log"
  export FAKE_DOCKER_LOG
  : >"$FAKE_DOCKER_LOG"
  mkdir -p "$FAKE_BIN"
  # Records one argument per line ("--- docker" separates calls); `image
  # inspect` fails unless FAKE_DOCKER_INSPECT_RC=0 (image "present").
  cat >"${FAKE_BIN}/docker" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "--- docker" "$@" >>"${FAKE_DOCKER_LOG:?}"
if [[ "${1:-}" == "image" && "${2:-}" == "inspect" ]]; then
  exit "${FAKE_DOCKER_INSPECT_RC:-1}"
fi
exit 0
EOF
  chmod +x "${FAKE_BIN}/docker"
  unset HTTP_PROXY HTTPS_PROXY NO_PROXY http_proxy https_proxy no_proxy ALL_PROXY all_proxy \
    OMES_CI_REGISTRY_MIRROR OMES_CI_EXTRA_CA_FILE OMES_CI_DOCKER_NETWORK || true
}

teardown() {
  omes_test_teardown
}

# lib <bash snippet> - run a snippet with scripts/ci-local.sh sourced.
lib() {
  run bash -c "source '${CI_LOCAL}'; $1"
}

# shim_call <docker args...> - generate the docker wrapper around the fake
# docker and invoke it; the fake docker's argv is then in $FAKE_DOCKER_LOG.
shim_call() {
  local shim_dir="${OMES_TEST_TMPDIR}/shim"
  bash -c "source '${CI_LOCAL}'; write_docker_shim '${shim_dir}' '${FAKE_BIN}/docker'"
  "${shim_dir}/docker" "$@"
}

logged_args() { grep -v '^--- docker$' "$FAKE_DOCKER_LOG" || true; }

# ---------------------------------------------------------------------------
# Registry mirror rewrite
# ---------------------------------------------------------------------------

@test "mirror_rewrite_ref leaves references untouched when no mirror is configured" {
  lib 'mirror_rewrite_ref ubuntu:24.04; echo; mirror_rewrite_ref koalaman/shellcheck@sha256:bb596a0d169b85ddd81d8b6d3a2ff6d5baf5fca10b97f575ebc647c3dff62b3d'
  [ "$status" -eq 0 ]
  [ "${lines[0]}" = "ubuntu:24.04" ]
  [ "${lines[1]}" = "koalaman/shellcheck@sha256:bb596a0d169b85ddd81d8b6d3a2ff6d5baf5fca10b97f575ebc647c3dff62b3d" ]
}

@test "mirror_rewrite_ref keeps the digest of a digest-pinned reference (image identity is preserved)" {
  export OMES_CI_REGISTRY_MIRROR=mirror.gcr.io
  local digest=sha256:bb596a0d169b85ddd81d8b6d3a2ff6d5baf5fca10b97f575ebc647c3dff62b3d
  lib "mirror_rewrite_ref koalaman/shellcheck@${digest}"
  [ "$output" = "mirror.gcr.io/koalaman/shellcheck@${digest}" ]
  lib "mirror_rewrite_ref docker.io/mvdan/shfmt@${digest}"
  [ "$output" = "mirror.gcr.io/mvdan/shfmt@${digest}" ]
}

@test "mirror_rewrite_ref maps bare library names and tags onto library/ on the mirror" {
  export OMES_CI_REGISTRY_MIRROR=mirror.gcr.io
  lib 'mirror_rewrite_ref ubuntu:24.04'
  [ "$output" = "mirror.gcr.io/library/ubuntu:24.04" ]
  lib 'mirror_rewrite_ref docker.io/library/ubuntu:22.04'
  [ "$output" = "mirror.gcr.io/library/ubuntu:22.04" ]
  lib 'mirror_rewrite_ref linuxmintd/mint22-amd64'
  [ "$output" = "mirror.gcr.io/linuxmintd/mint22-amd64" ]
  lib 'mirror_rewrite_ref bats/bats:latest'
  [ "$output" = "mirror.gcr.io/bats/bats:latest" ]
}

@test "mirror_rewrite_ref never rewrites another registry, and tolerates a trailing slash" {
  export OMES_CI_REGISTRY_MIRROR=mirror.gcr.io/
  lib 'mirror_rewrite_ref ghcr.io/gitleaks/gitleaks:latest; echo; mirror_rewrite_ref localhost:5000/foo:1; echo; mirror_rewrite_ref mirror.gcr.io/library/ubuntu:24.04'
  [ "${lines[0]}" = "ghcr.io/gitleaks/gitleaks:latest" ]
  [ "${lines[1]}" = "localhost:5000/foo:1" ]
  [ "${lines[2]}" = "mirror.gcr.io/library/ubuntu:24.04" ]
  lib 'mirror_rewrite_ref ubuntu:24.04'
  [ "$output" = "mirror.gcr.io/library/ubuntu:24.04" ]
}

@test "an invalid OMES_CI_REGISTRY_MIRROR is a usage error (exit 2)" {
  run env OMES_CI_REGISTRY_MIRROR='bad mirror;rm -rf /' "$CI_LOCAL" --job workflow-sync
  [ "$status" -eq 2 ]
  [[ "$output" == *"OMES_CI_REGISTRY_MIRROR must look like host"* ]]
}

# ---------------------------------------------------------------------------
# Proxy pass-through
# ---------------------------------------------------------------------------

@test "proxy_host_is_loopback recognises loopback proxies and only those" {
  local url
  for url in http://127.0.0.1:3128 http://localhost:3128 http://user:pw@127.0.0.1:44199/ 'http://[::1]:3128' 127.0.0.1:3128; do
    lib "proxy_host_is_loopback '${url}'"
    [ "$status" -eq 0 ]
  done
  for url in http://proxy.corp.example:3128 http://10.1.2.3:3128 http://user:pw@proxy.example.invalid:8080/; do
    lib "proxy_host_is_loopback '${url}'"
    [ "$status" -ne 0 ]
  done
}

@test "proxy_vars_to_pass lists names only, passes NO_PROXY along, and is empty when nothing is set" {
  lib 'proxy_vars_to_pass'
  [ -z "$output" ]
  export HTTPS_PROXY=http://user:s3cret@proxy.example.invalid:3128 https_proxy=http://proxy.example.invalid:3128 \
    NO_PROXY=internal.example.invalid
  lib 'proxy_vars_to_pass'
  [[ "$output" == *"HTTPS_PROXY"* ]]
  [[ "$output" == *"https_proxy"* ]]
  [[ "$output" == *"NO_PROXY"* ]]
  [[ "$output" != *"s3cret"* ]]
  [[ "$output" != *"proxy.example.invalid"* ]]
}

@test "loopback proxies are not passed into bridge-network containers unless OMES_CI_DOCKER_NETWORK=host" {
  export HTTPS_PROXY=http://127.0.0.1:44199 NO_PROXY=example.invalid
  lib 'proxy_vars_to_pass'
  [ -z "$output" ]
  export OMES_CI_DOCKER_NETWORK=host
  lib 'proxy_vars_to_pass'
  [[ "$output" == *"HTTPS_PROXY"* ]]
}

@test "the docker wrapper passes proxy variables to run by NAME only: the value never reaches argv" {
  export HTTPS_PROXY=http://user:s3cret@proxy.example.invalid:3128 OMES_CI_SHIM_PROXY_VARS="HTTPS_PROXY"
  shim_call run --rm ubuntu:24.04 true
  run logged_args
  [[ "$output" == *$'-e\nHTTPS_PROXY'* ]]
  [[ "$output" != *"s3cret"* ]]
  [[ "$output" != *"proxy.example.invalid"* ]]
  # injected options come right after `run`, before the image
  [ "${lines[0]}" = "run" ]
  [ "${lines[1]}" = "-e" ]
  [ "${lines[2]}" = "HTTPS_PROXY" ]
}

@test "the docker wrapper passes proxy variables to build as --build-arg NAME (no value)" {
  export HTTPS_PROXY=http://user:s3cret@proxy.example.invalid:3128 OMES_CI_SHIM_PROXY_VARS="HTTPS_PROXY NO_PROXY"
  shim_call build -q -t omes-bats-python3-git:local -
  run logged_args
  [ "${lines[0]}" = "build" ]
  [ "${lines[1]}" = "--build-arg" ]
  [ "${lines[2]}" = "HTTPS_PROXY" ]
  [ "${lines[3]}" = "--build-arg" ]
  [ "${lines[4]}" = "NO_PROXY" ]
  [[ "$output" != *"s3cret"* ]]
}

@test "the docker wrapper is a no-op for other subcommands and for commands run inside containers" {
  export OMES_CI_SHIM_PROXY_VARS="HTTPS_PROXY" HTTPS_PROXY=http://proxy.example.invalid:3128
  shim_call exec some-container apt-get update
  run logged_args
  [ "${lines[0]}" = "exec" ]
  [ "${#lines[@]}" -eq 4 ]
}

@test "OMES_CI_DOCKER_NETWORK adds --network to run and build unless the caller chose one" {
  export OMES_CI_DOCKER_NETWORK=host
  shim_call run --rm ubuntu:24.04 true
  run logged_args
  [[ "$output" == *$'--network\nhost'* ]]

  : >"$FAKE_DOCKER_LOG"
  shim_call run --rm --network none ubuntu:24.04 true
  run logged_args
  [[ "$output" != *$'--network\nhost'* ]]
  [[ "$output" == *$'--network\nnone'* ]]

  : >"$FAKE_DOCKER_LOG"
  shim_call build -t x -
  run logged_args
  [[ "$output" == *$'--network\nhost'* ]]
}

@test "with no setting configured the wrapper forwards the command line unchanged" {
  shim_call run --rm -v /a:/b ubuntu:24.04 sleep infinity
  run logged_args
  [ "${lines[0]}" = "run" ]
  [ "${lines[1]}" = "--rm" ]
  [ "${lines[2]}" = "-v" ]
  [ "${#lines[@]}" -eq 7 ]
}

@test "the runner installs no docker wrapper unless a mirror, proxy, CA or network is configured" {
  lib 'docker_shim_needed'
  [ "$status" -ne 0 ]
  export OMES_CI_REGISTRY_MIRROR=mirror.gcr.io
  lib 'docker_shim_needed'
  [ "$status" -eq 0 ]
  unset OMES_CI_REGISTRY_MIRROR
  export HTTPS_PROXY=http://proxy.example.invalid:3128
  lib 'docker_shim_needed'
  [ "$status" -eq 0 ]
}

# ---------------------------------------------------------------------------
# Digest-pinned images through the mirror
# ---------------------------------------------------------------------------

@test "the docker wrapper rewrites a digest-pinned reference to the mirror when it is not local" {
  export OMES_CI_REGISTRY_MIRROR=mirror.gcr.io FAKE_DOCKER_INSPECT_RC=1
  local digest=sha256:bb596a0d169b85ddd81d8b6d3a2ff6d5baf5fca10b97f575ebc647c3dff62b3d
  shim_call run --rm koalaman/shellcheck@${digest} --version
  run logged_args
  [[ "$output" == *"mirror.gcr.io/koalaman/shellcheck@${digest}"* ]]
}

@test "the docker wrapper leaves a digest-pinned reference alone when the image is already local" {
  export OMES_CI_REGISTRY_MIRROR=mirror.gcr.io FAKE_DOCKER_INSPECT_RC=0
  local digest=sha256:bb596a0d169b85ddd81d8b6d3a2ff6d5baf5fca10b97f575ebc647c3dff62b3d
  shim_call run --rm koalaman/shellcheck@${digest} --version
  run logged_args
  [[ "$output" != *"mirror.gcr.io"* ]]
  [[ "$output" == *"koalaman/shellcheck@${digest}"* ]]
}

# ---------------------------------------------------------------------------
# Extra CA bundle
# ---------------------------------------------------------------------------

@test "OMES_CI_EXTRA_CA_FILE is mounted read-only and trusted via the standard variables" {
  local ca="${OMES_TEST_TMPDIR}/ca.pem"
  printf -- '-----BEGIN CERTIFICATE-----\nMIIBfake\n-----END CERTIFICATE-----\n' >"$ca"
  export OMES_CI_EXTRA_CA_FILE="$ca" OMES_CI_SHIM_APT_CONF="${OMES_TEST_TMPDIR}/apt.conf"
  shim_call run --rm ubuntu:24.04 true
  run logged_args
  [[ "$output" == *$'-v\n'"${ca}:/usr/local/share/ca-certificates/omes-ci-extra-ca.crt:ro"* ]]
  [[ "$output" == *"${OMES_TEST_TMPDIR}/apt.conf:/etc/apt/apt.conf.d/99omes-ci-extra-ca:ro"* ]]
  local v
  for v in SSL_CERT_FILE CURL_CA_BUNDLE REQUESTS_CA_BUNDLE PIP_CERT GIT_SSL_CAINFO NODE_EXTRA_CA_CERTS; do
    [[ "$output" == *"${v}=/usr/local/share/ca-certificates/omes-ci-extra-ca.crt"* ]]
  done
}

@test "the runner refuses an unreadable, key-bearing or non-PEM OMES_CI_EXTRA_CA_FILE (exit 2)" {
  run env OMES_CI_EXTRA_CA_FILE="${OMES_TEST_TMPDIR}/missing.pem" "$CI_LOCAL" --job workflow-sync
  [ "$status" -eq 2 ]
  [[ "$output" == *"OMES_CI_EXTRA_CA_FILE is not a readable file"* ]]

  printf -- '-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n' >"${OMES_TEST_TMPDIR}/key.pem"
  run env OMES_CI_EXTRA_CA_FILE="${OMES_TEST_TMPDIR}/key.pem" "$CI_LOCAL" --job workflow-sync
  [ "$status" -eq 2 ]
  [[ "$output" == *"contains a private key"* ]]

  printf 'not a certificate\n' >"${OMES_TEST_TMPDIR}/plain.txt"
  run env OMES_CI_EXTRA_CA_FILE="${OMES_TEST_TMPDIR}/plain.txt" "$CI_LOCAL" --job workflow-sync
  [ "$status" -eq 2 ]
  [[ "$output" == *"does not look like a PEM certificate bundle"* ]]
}

# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

@test "redact_stream blanks proxy URLs, their host:port, and any URL credentials" {
  export HTTPS_PROXY=http://user:s3cret@proxy.example.invalid:3128/ http_proxy=http://127.0.0.1:44199
  lib 'WORK="$(mktemp -d)"; setup_redaction;
    printf "%s\n" "using http://user:s3cret@proxy.example.invalid:3128/ now" \
      "dial tcp proxy.example.invalid:3128: refused" "other https://tok:en@host.example/x" "plain line" | redact_stream; rm -rf "$WORK"'
  [ "$status" -eq 0 ]
  [[ "$output" != *"s3cret"* ]]
  [[ "$output" != *"proxy.example.invalid"* ]]
  [[ "$output" != *"tok:en"* ]]
  [[ "$output" == *"[REDACTED]"* ]]
  [[ "$output" == *"plain line"* ]]
}

@test "redact_stream still redacts when sed rejects -u (BusyBox sed, issue #329)" {
  # A sed that rejects -u the way BusyBox does and otherwise runs the real sed.
  local real_sed fake_dir
  real_sed="$(command -v sed)"
  fake_dir="${OMES_TEST_TMPDIR}/busybox-sed"
  mkdir -p "$fake_dir"
  {
    printf '#!/usr/bin/env bash\n'
    printf 'for a in "$@"; do [[ "$a" == "-u" ]] && { echo "sed: unrecognized option: u" >&2; exit 1; }; done\n'
    printf 'exec %q "$@"\n' "$real_sed"
  } >"${fake_dir}/sed"
  chmod +x "${fake_dir}/sed"
  export HTTPS_PROXY=http://user:s3cret@proxy.example.invalid:3128/
  export PATH="${fake_dir}:${PATH}"
  lib 'WORK="$(mktemp -d)"; setup_redaction; echo "unbuffered=${SED_UNBUFFERED}";
    printf "%s\n" "using http://user:s3cret@proxy.example.invalid:3128/ now" "plain line" | redact_stream; rm -rf "$WORK"'
  [ "$status" -eq 0 ]
  [[ "$output" == *"unbuffered=0"* ]]
  [[ "$output" != *"unrecognized option"* ]]
  [[ "$output" != *"s3cret"* ]]
  [[ "$output" != *"proxy.example.invalid"* ]]
  [[ "$output" == *"[REDACTED]"* ]]
  [[ "$output" == *"plain line"* ]]
}

@test "a full run with a proxy and a mirror never prints the proxy URL, in the log or the summary" {
  # Fake docker "present", fake repo job that echoes its environment's proxy.
  export HTTPS_PROXY=http://user:s3cret@proxy.example.invalid:3128 OMES_CI_DOCKER_NETWORK=host \
    OMES_CI_REGISTRY_MIRROR=mirror.example.invalid FAKE_DOCKER_INSPECT_RC=1
  run env PATH="${FAKE_BIN}:/usr/local/bin:/usr/bin:/bin" "$CI_LOCAL" --job actionlint --json
  [ "$status" -eq 0 ]
  [[ "$output" != *"s3cret"* ]]
  [[ "$output" != *"proxy.example.invalid"* ]]
  run logged_args
  [[ "$output" != *"s3cret"* ]]
  [[ "$output" != *"proxy.example.invalid"* ]]
  # the container run got the proxy by name, over the mirror, with host networking
  [[ "$output" == *$'-e\nHTTPS_PROXY'* ]]
  [[ "$output" == *"mirror.example.invalid/rhysd/actionlint@sha256:"* ]]
  [[ "$output" == *$'--network\nhost'* ]]
}

@test "a skipped pull is reported with the redacted hint, not the proxy" {
  cat >"${FAKE_BIN}/docker" <<'EOF'
#!/usr/bin/env bash
case "${1:-}" in
  image) exit 1 ;;
  pull) echo "toomanyrequests: You have reached your pull rate limit via $HTTPS_PROXY" >&2; exit 1 ;;
esac
exit 0
EOF
  export HTTPS_PROXY=http://user:s3cret@proxy.example.invalid:3128
  run env PATH="${FAKE_BIN}:/usr/local/bin:/usr/bin:/bin" "$CI_LOCAL" --job actionlint
  [ "$status" -eq 3 ]
  [[ "$output" != *"s3cret"* ]]
  [[ "$output" == *"registry rate limit"* ]]
  [[ "$output" == *"INCOMPLETE"* ]]
}
