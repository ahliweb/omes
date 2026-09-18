# OMES server profile
# One module name per line. Lines starting with # are comments.
# Order here is advisory only; lib/omes/module.sh topologically sorts
# modules by MODULE_REQUIRES before applying them.

apt-base
# security-baseline (#7)
# hermes (#11)
# hermes-gateway (#12)
# containers (#7, optional)
