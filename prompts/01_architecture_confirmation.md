# Prompt 01 — Architecture Confirmation

## Role
User asked Claude to read CLAUDE.md and confirm understanding of the extracteval architecture before writing any code.

## Request
> Read the CLAUDE.md file in this repository. It contains the complete architecture specification for a BE testing framework called "extracteval"...
> Before writing any code, confirm you understand:
> 1. The monorepo structure (framework/ + projects/docextract/)
> 2. The 8 framework modules and their responsibilities
> 3. The config.yaml schema and how it drives field validation
> 4. The custom_rules plugin system (register_normalizer, register_invariant)
> 5. The test files in projects/docextract/tests/ and what each one validates
> List the implementation order you will follow.

## Outcome
Claude confirmed understanding of all 5 points and listed the 14-step implementation order from CLAUDE.md.
