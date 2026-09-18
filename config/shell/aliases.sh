#!/usr/bin/env bash
# shellcheck shell=bash
# Managed by OMES (modules/desktop-config) - installed at
# ~/.config/omes/shell.sh and sourced from ~/.bashrc via a marker block.
# Do not edit by hand if you plan to keep re-applying `omes install
# --profile desktop` non-interactively (--yes); edits are preserved by
# default (module_apply never overwrites a differing file without --yes).
#
# Every alias below is guarded with `command -v` so this file is safe to
# source whether or not the underlying tool ended up installed (e.g. eza's
# availability in the configured repositories must be verified per release
# - see docs/packages.md).

if command -v eza >/dev/null 2>&1; then
  alias ls='eza'
  alias ll='eza -la'
  alias lt='eza --tree'
fi

if command -v batcat >/dev/null 2>&1; then
  alias cat='batcat'
elif command -v bat >/dev/null 2>&1; then
  alias cat='bat'
fi

if command -v fdfind >/dev/null 2>&1; then
  alias fd='fdfind'
fi

if command -v rg >/dev/null 2>&1; then
  alias grep='rg'
fi
