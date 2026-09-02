# Herdr autonomic control plane

Herdr's own generated JSON API schema is the canonical capability graph. The autonomic controller discovers methods from that schema at runtime, so a new Herdr method becomes visible without maintaining a duplicate action list. `ggen/ontology.ttl` manufactures only the policy that must remain explicit: destructive/unbounded overrides and Groq model economics.

The loop is **observe → SELECT (Groq) → CONSTRUCT (typed intent) → admit/refuse → DO (Herdr API) → re-observe → receipt → replay**. Groq has zero ambient execution authority. Unknown methods fail closed. Destructive and unbounded methods remain visible as option capital but are excluded from planner choice unless the run receives `--allow-destructive` or `--allow-unbounded`.

Current selection is the lowest estimated cost among public-priced, production, JSON-capable models. On 2026-09-02 the seeded winner is `openai/gpt-oss-20b`. `llama-3.1-8b-instant` remains in the model graph but is excluded from automatic cheapest selection because current Groq documentation reports Enterprise / Contact Sales pricing.

```bash
export GROQ_API_KEY=...
python3 scripts/herdr_autonomic.py doctor
python3 scripts/herdr_autonomic.py capabilities
python3 scripts/herdr_autonomic.py run "Keep every coding agent productively closing its assigned work" --dry-run
python3 scripts/herdr_autonomic.py run "Keep every coding agent productively closing its assigned work"
python3 scripts/herdr_autonomic.py replay
```

`--max-steps` and `--max-cost-usd` are hard loop budgets. Every DO is followed by a new `session.snapshot`. Receipts form a SHA-256 predecessor chain; replay verifies evidence and never re-actuates.

## Manufacture

The semantic policy specializes the same admission/actuation/receipt doctrine as the ggen marketplace `automatic-autonomic-operations-pack`; Herdr does not claim that pack as a runtime dependency or ambient execution authority. The repository-root `ggen.toml` is the local manufacturing entrypoint. From the repository root, run `ggen sync run --dry-run` then `ggen sync run`. CI runs the same projection in `ghcr.io/seanchatmangpt/ggen-ecosystem:v26.8.28` and refuses drift.

## Standing

Manufacture, static admission, typed API parsing, and receipt replay can be certified without a live provider. End-to-end autonomic `ALIVE` standing requires observed execution against the exact running Herdr subject with a real Groq credential; CI metadata alone is not that receipt.
