# Neovim / LazyVim

OMES does **not** install or template a Neovim configuration. Per
[docs/omarchy-compatibility-inventory.md](../../docs/omarchy-compatibility-inventory.md),
Neovim/LazyVim is categorized **DEFER**: Neovim itself is packaged for
Ubuntu/Linux Mint, but LazyVim is a configuration distribution (a git-based
config template), not a binary package, and vendoring/templating it is out
of scope for this issue (#8).

`modules/desktop-config` never touches `~/.config/nvim`.

If you want LazyVim on a host OMES manages, install it yourself using
LazyVim's own starter/installer, following LazyVim's official
documentation: <https://www.lazyvim.org/installation>. That is an
independent, upstream install step - OMES neither depends on it nor
modifies anything it creates.
