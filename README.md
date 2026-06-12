# Self-Harness

Self-Harness automatically optimizes an LLM system prompt (a "harness") through
closed-loop iteration. It runs a model over a benchmark, mines the patterns
behind its mistakes, proposes minimal prompt edits, validates each edit on
held-in / held-out splits, and promotes the best candidate to a new harness
version — repeating until the gains flatten out. The included benchmark scores
the *XỬ LÝ TỪ CHỐI* (objection-handling) skill of telesales calls across 9 steps.

## Setup

```bash
git clone <repo-url>
cd self-harness

pip install openai pydantic python-dotenv pyyaml
pip install rich          # optional — enables colored tables

cp .env.example .env      # then edit, or create .env directly
```

`.env` needs three keys:

```env
MODEL_NAME=gpt-4.1-mini
API_KEY=sk-...
BASE_URL=https://your-openai-compatible-endpoint/openai
```

## Quick Start

From zero to a before/after comparison in three commands:

```bash
python3 cli.py baseline    # 1. evaluate the v0.1.0 baseline on all 21 records
python3 cli.py loop        # 2. optimize until convergence (promotes new versions)
python3 cli.py compare     # 3. print the baseline-vs-optimized comparison table
```

Already optimized? `python3 cli.py status` shows where things stand without
spending any tokens.

## CLI Commands

Run everything through `cli.py` from the project root.

| Command                  | What it does                                                        |
|--------------------------|---------------------------------------------------------------------|
| `python3 cli.py status`  | Current harness version, agreement rates, and number of rounds run. |
| `python3 cli.py history` | Every harness version with its held-in / held-out deltas.           |
| `python3 cli.py baseline`| Evaluate the baseline harness (v0.1.0) on all records.              |
| `python3 cli.py round`   | Run one optimization round from the current harness.                |
| `python3 cli.py loop`    | Run rounds until convergence (auto-stops on diminishing returns).   |
| `python3 cli.py eval`    | Evaluate the current promoted harness on all records.               |
| `python3 cli.py compare` | Print the before/after benchmark comparison table.                  |

`status` and `history` are read-only (no model calls). The other four stream
the underlying script's output live as it runs.

## How the Pipeline Works

A single optimization **round** runs this loop. `loop` repeats it until the
held-out gain falls below the threshold for two consecutive rounds.

```
                 current harness (system prompt)
                            |
            ┌───────────────▼───────────────┐
            │ 1. RUN        run model over   │
            │               the benchmark    │   src/runner.py
            │               → collect traces │
            └───────────────┬───────────────┘
                            |
            ┌───────────────▼───────────────┐
            │ 2. MINE       cluster failing  │   src/failure_miner.py
            │               steps into       │
            │               failure patterns │
            └───────────────┬───────────────┘
                            |
            ┌───────────────▼───────────────┐
            │ 3. PROPOSE    generate minimal │   src/proposer.py
            │               prompt edits     │   src/patcher.py
            │               (candidates)     │
            └───────────────┬───────────────┘
                            |
            ┌───────────────▼───────────────┐
            │ 4. VALIDATE   score each cand. │   src/validator.py
            │               on held_in AND   │
            │               held_out splits  │
            └───────────────┬───────────────┘
                            |
            ┌───────────────▼───────────────┐
            │ 5. PROMOTE    keep best cand.  │   src/promoter.py
            │  if Δ > 0  →  bump version,    │
            │               write new harness│
            └───────────────┬───────────────┘
                            |
                  new harness version  ──►  (loop back to step 1)
```

`src/orchestrator.py` wires these stages together for one round.

## File Structure

```
self-harness/
├── cli.py                     # this control surface (the only entry point you need)
├── README.md
├── .env                       # MODEL_NAME, API_KEY, BASE_URL (not committed)
│
├── scripts/                   # runnable entry points (invoked by cli.py)
│   ├── run_baseline.py        #   baseline eval
│   ├── run_round.py           #   one optimization round
│   ├── run_loop.py            #   rounds until convergence
│   ├── run_optimized.py       #   eval current harness
│   └── benchmark_compare.py   #   before/after table
│
├── src/                       # pipeline internals (do not run directly)
│   ├── orchestrator.py        #   one round = run → mine → propose → validate → promote
│   ├── runner.py              #   calls the model, parses + verifies steps
│   ├── failure_miner.py       #   clusters failures into patterns
│   ├── proposer.py            #   proposes prompt edits
│   ├── patcher.py             #   applies an edit to the prompt
│   ├── validator.py           #   scores candidates on held_in / held_out
│   ├── promoter.py            #   promotes the winning candidate
│   ├── trace_schema.py        #   pydantic trace models
│   └── config.py              #   paths + env loading
│
├── configs/
│   ├── harness/
│   │   ├── base.yaml           #   v0.1.0 definition
│   │   ├── current.yaml        #   active version + last deltas
│   │   └── versions/           #   versioned prompt files (v0.2.0.md, ...)
│   └── benchmarks/
│       ├── held_in.yaml        #   record ids used for mining + selection
│       └── held_out.yaml       #   record ids reserved for regression testing
│
├── prompts/
│   └── system.md               #   the original baseline prompt
│
└── data/                       # generated artifacts
    ├── runs/<version>/traces.json   #   per-version model traces
    ├── mined_failures/<version>.json
    ├── proposals/<version>.json
    └── reports/                     #   round_*.json, validation_*.json, comparison.json
```

## Interpreting Results

**Agreement rate.** Each record is scored across 9 steps; agreement is the
fraction of steps where the harness's verdict (`S` = success / `U` = unsuccess)
matches the ground-truth label. `66.7%` agreement means the harness agreed with
the reference on two of every three scored steps. It is reported overall, per
step (1–9), and per source (RTN / Digital / Ethical) in `compare`.

**held_in vs held_out.** The benchmark is split: `held_in` records drive failure
mining and candidate selection, while `held_out` records are never used to pick
edits. They exist only to detect overfitting — a candidate that improves
`held_in` but hurts `held_out` is memorizing, not generalizing.

**delta_in / delta_out.** The change in agreement on each split, relative to the
harness the round started from:

- `delta_in`  — improvement on the held-in split.
- `delta_out` — improvement on the held-out split *(the metric that matters)*.

A candidate is worth promoting when `delta_out > 0`. `loop` keeps going while
`delta_out` clears the minimum-improvement threshold and stops after two rounds
of diminishing returns. In `history`, a `+` delta (shown in green with `rich`)
means that version generalized better than its predecessor; the `current` row in
`status` reflects the most recently promoted harness.
