# OMES server profile
# One module name per line. Lines starting with # are comments.
# Order here is advisory only; lib/omes/module.sh topologically sorts
# modules by MODULE_REQUIRES before applying them.

apt-base
# security-baseline (#7)
hermes
hermes-gateway
# hermes-gateway-system is intentionally NOT listed here (opt-in only via
# `sudo omes install --module hermes-gateway-system`); see
# docs/hermes-integration.md part 2's user-vs-system decision table.
# containers (#7, optional)
