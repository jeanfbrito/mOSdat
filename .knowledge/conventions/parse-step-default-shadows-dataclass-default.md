# `_parse_step` literal defaults shadow `FunctionalStep` dataclass defaults

**Context**: Changed `canary_char: str = "§"` → `"q"` in BOTH `automation/scenario.py` `_StepBase` AND `automation/runners/functional.py` `FunctionalStep`. Tests passed. Live smoke runtime still logged `canary verify (char='§')` and produced `§`-related VLM prompts.

**Root cause**: `automation/runners/functional.py::_parse_step` (around line 1368) reconstructs `FunctionalStep` from raw YAML using `raw.get("canary_char", "§")` — the literal `"§"` fallback overrides the dataclass field default. Since no scenario YAML specified `canary_char` explicitly, every parsed step got the parser-default `§`.

**Rule**: Whenever a `FunctionalStep` field default changes, also update the matching `raw.get(<key>, <default>)` literal in `_parse_step`. Keeping the parser default in sync with the dataclass default is mandatory — they live in different files but represent the same logical constant.

**Implication**: For new optional fields with defaults, prefer `raw.get(<key>)` (None fallback) and let the dataclass apply the real default — eliminates this duplication. Or: factor the defaults into module-level constants imported by both sites.

**Affects**: `automation/runners/functional.py::_parse_step`, `_resolve_vars`. Audit any field with a non-None default for the same drift pattern.
