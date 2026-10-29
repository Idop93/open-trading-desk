# HW7 benchmark plan (pre-registered 2026-10-29, before any benchmark run)

## Claim
The Scorekeeper lets a portfolio-manager (PM) agent tell skilled providers from unskilled ones:
its ranking of providers matches their true skill, and a PM that follows the top-scored provider
on a new day is right more often than a PM that picks a provider at random or always uses one
fixed provider (today's DayTrader approach).

## How "right" is checked (independent of the Scorekeeper)
Truth for one view = what the price actually did over the view's horizon, read straight from the
market path: a LONG view is correct if the price at entry + horizon is above entry; an AVOID view is
correct if it is not. This check never uses the Scorekeeper's code.

## Providers (known skill, so the right answer is known in advance)
- `skill-pXX`: with probability p it returns the correct direction (it peeks at the future path,
  which only a test provider may do), otherwise a coin flip. p in {0.0, 0.2, 0.4, 0.6, 0.8}.
  Expected accuracy = 0.5 + p/2.
- `lazy-avoid`: always AVOID. No skill.
- `liar-long`: always LONG with 95% confidence. No skill.
- `technical-momentum-01`: the team's real rule-based provider (skill unknown; measured).

## Cases
- A, ordinary: the 5 known-skill providers + technical-momentum, 48 views each per day.
- B, difficult: A plus lazy-avoid and liar-long (zero-skill providers that game a scoring rule).
- C, small sample: B with only 6 views per provider per day.

## Protocol
One run = a training day (scores are built) and a separate test day (new prices). After the
training day the PM picks the top-scored provider and follows it on the test day.
Each case runs on 30 seeds. Tuning seeds: 1-30. Fresh seeds (used only after the change): 1001-1030.

## Metrics
1. Rank quality: Spearman correlation between true skill and score (known-skill providers only).
2. Selection quality: test-day accuracy of the provider the PM picked, against
   random pick (mean over all providers) and fixed pick (technical-momentum-01).
3. False promotion: share of runs where a zero-skill provider is ranked first.
4. Cost: wall-clock time per run; model calls (expected 0, all providers are offline).

## Pre-set success checks
- S1: mean Spearman >= 0.8 in case A.
- S2: in case B, the PM's pick beats random pick by >= 10 points of test-day accuracy.
- S3: in case B, a zero-skill provider is ranked first in <= 10% of runs.
- S4: the change (if any) must not reduce mean Spearman in case A on fresh seeds.

## Change and retest rule
If a check fails, make ONE change to the Scorekeeper motivated by the evidence, then rerun all
cases on the fresh seeds (1001-1030), and also on the tuning seeds for a like-for-like comparison.
