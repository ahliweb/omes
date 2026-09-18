# OMES server profile
# One module name per line. Lines starting with # are comments.
# Order here is advisory only; lib/omes/module.sh topologically sorts
# modules by MODULE_REQUIRES before applying them.

apt-base
security-baseline
hermes
hermes-gateway
# hermes-gateway-system is intentionally NOT listed here (opt-in only via
# `sudo omes install --module hermes-gateway-system`); see
# docs/hermes-integration.md part 2's user-vs-system decision table.
# containers is OPTIONAL and intentionally NOT applied by default: Docker
# access is a deliberate opt-in decision (docs/adr/0007-docker-access-
# policy.md). Enable it explicitly with:
#   sudo omes install --module containers
# or uncomment the line below to include it in the server profile:
# containers
