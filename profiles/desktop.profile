# OMES desktop profile
# One module name per line. Lines starting with # are comments.
# Order here is advisory only; lib/omes/module.sh topologically sorts
# modules by MODULE_REQUIRES before applying them.

apt-base
desktop-preflight
hyprland-session
desktop-config
# hermes (#11) - optional on desktop; enable by uncommenting once you want
# Hermes Agent alongside the desktop session (installs per-user, same as
# the server/hermes profiles - see docs/hermes-integration.md).
# hermes
# hermes-gateway (#12)
