# Extension commands

Each `<name>.sh` in this directory adds `omes <name>` without editing
`bin/omes`. Contract:

- The file is sourced after every `lib/omes/*.sh` library, with the global
  flags already parsed (`OMES_JSON`, `OMES_DRY_RUN`, `OMES_NONINTERACTIVE`,
  `OMES_PROFILE`, `OMES_SELECTED_MODULES`, `OMES_VERBOSE`, `OMES_LOG_FILE`).
- It must define `cmd_<name>()`; it receives every argument from the first
  token `bin/omes` did not recognize as a global flag (subcommands, names,
  command-specific flags).
- It must start with a `# omes-help: <one line>` comment; `omes help`
  prints it.
- It must honor the CLI contract in `docs/cli.md`: stable exit codes,
  a single JSON object on stdout under `--json`, logs on stderr, no
  mutation under `--dry-run`, confirmation before mutation unless `--yes`.
- Name pattern: `^[a-z][a-z0-9-]*$`.
