# HW7 benchmark: can the Scorekeeper tell skill from luck?

Pre-registered plan, cases and success checks: `PLAN.md` (written before any run).

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cd benchmark
PYTHONPATH=../src:. ../.venv/bin/python -m pytest -q test_bench.py          # benchmark sanity checks
PYTHONPATH=../src:. ../.venv/bin/python run_benchmark.py --rule first_touch --seeds tuning
PYTHONPATH=../src:. ../.venv/bin/python run_benchmark.py --rule first_touch --seeds fresh
PYTHONPATH=../src:. ../.venv/bin/python run_benchmark.py --rule direction --seeds fresh
PYTHONPATH=../src:. ../.venv/bin/python run_benchmark.py --rule direction --seeds tuning
```

Every run is deterministic (seeded). `results/*_runs.jsonl` holds one line per run: scores, ranking,
pick, test-day accuracies, the hash-chain check and the chain head hash. `results/*_summary.json`
holds the per-case means and standard deviations.
