---
id: <stable_snake_case_id>
status: proposed                       # proposed | active | deprecated
output_kind: <percentage | ratio | rate | scalar | index>
bounds: [<lower>, <upper>]             # null if unbounded
dataset: <dataset_id>                  # references datasets/<id>.md; can be a list for cross-dataset features
default: false                         # whether bundled into PRESET catalogue by default
tags: [<freeform tags>]
numerator:
  expression: <field | sum | weighted_sum | custom>
  fields:                              # if expression is sum / weighted_sum
    - <dataset_id>.<field>
    - <dataset_id>.<field>
  weights:                             # if expression is weighted_sum
    - <number>
    - <number>
denominator:
  expression: <field | sum>
  field: <dataset_id>.<field>          # if expression is field
  # OR fields/expression for sums
edge_cases:
  zero_denominator: <null | zero | error>
  perturbation_tolerance: <warn_only | strict>
  out_of_bounds_behaviour: <clip | warn | error>
sources:
  - url: <URL>
    note: <one-line citation context>
---

# <feature_id>

<One-paragraph plain-English description of what this feature represents. Should
read like a stat in a press release: "Share of X who do Y, on Census day."
The reader should understand both the concept and the population scope.>

## Why this denominator

<Defend the choice of denominator concretely. Concrete numbers help — "using X
instead of Y under-states by ~30 percentage points in retirement-coast SA2s
because retirees are in Y but never in the numerator". This is the section
that protects against silent denominator-mismatch bugs.>

## Why not <obvious-but-wrong-denominator>

<Address the most likely mistake explicitly. If a reader's first instinct
would be "just use total population", this is where you head that off.>

## Edge cases

<List the ways this feature can produce surprising values:>

- Zero denominator → <behaviour>
- Suppressed source counts → <behaviour>
- Perturbation effects → <behaviour>
- Boundary-edge SA2s (industrial, no usual address, migratory) → <behaviour>

## Notes / config knobs

<Any meaningful interpretive choices the user might want to control. Surface as
PRESET parameters where reasonable. E.g. "if 'drive' should mean driving-yourself-only,
narrow numerator to <field>".>

## Bounds (typical, not theoretical)

<Theoretical bounds are in front-matter. This section gives the realistic SA2-level
distribution: national average, typical range, what an outlier value tells you.>

## Sources

<Primary citations for the variable definitions and population applicability.
Repeat from front-matter `sources:` for human readability.>
