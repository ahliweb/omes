# OMES unit-economics model

Status: draft, pre-pilot. Every numeric input in this document is an **ASSUMPTION**
until it is replaced with a measured value from a pilot (see `docs/business/pilot-program.md`,
issue [#24](https://github.com/ahliweb/omes/issues/24)). This document and
`docs/business/unit-economics-model.csv` must stay numerically consistent; the CSV is the
machine-readable copy of the input tables below.

OMES is an independent, MIT-licensed, Omarchy-inspired compatibility layer and deployment
toolkit for Ubuntu Server 24.04 LTS and Linux Mint 22.x, with Hermes Agent as the automation
layer. It is **not** the official Omarchy project. No number in this model should be read as a
market-size, willingness-to-pay, or final-price claim — see
[section 9, "Research limitations"](research-and-implementation-plan.md) of the implementation
plan and the "Sources" note at the end of this document.

Per the plan's MVP commercial recommendation (`docs/research-and-implementation-plan.md`,
section 5.3), this model treats OMES as a **productized-service delivery engine**: a paid
one-time setup/migration engagement per deployment, with an optional recurring support
subscription. It intentionally does not model SaaS/hosting revenue.

## 1. Unit of analysis

One **deployment** = one onboarded host (one Ubuntu Server headless host, or one Linux Mint
workstation, or one Hermes Telegram deployment) that goes through a paid OMES setup engagement.
All hourly figures are labor hours of the person delivering/operating OMES (owner or contractor),
not end-user hours.

## 2. Inputs table (base scenario)

| Input | Unit | Base value | Assumption? | Source / validation method |
|---|---|---|---|---|
| `setup_fee_usd` | USD, one-time per deployment | 150 | ASSUMPTION | Price test during external pilots (#24): offer this price to 3+ prospects per deployment type, record accept/reject and stated objections. |
| `support_subscription_usd_per_month` | USD/month per subscribed deployment | 25 | ASSUMPTION | Same price test; ask pilot participants what they would pay for ongoing support (`docs/business/pilot-program.md`, willingness-to-pay/objections metric). |
| `subscription_attach_rate` | ratio (subscribed / total setup deployments) | 0.40 | ASSUMPTION | Measured from pilot conversion: subscribed deployments ÷ completed setups, once ≥10 setups have run. |
| `preflight_time_hours` | hours, one-time per deployment | 0.5 | ASSUMPTION | Timestamp delta in the OMES log file: `omes check` start → end (see pilot instrumentation). |
| `install_time_hours` | hours, one-time per deployment | 1.5 | ASSUMPTION | Timestamp delta: `omes install` start → completion/exit. |
| `troubleshooting_time_hours` | hours, one-time per deployment | 1.0 | ASSUMPTION | Operator worksheet: sum of manual-intervention time outside the timed CLI run (support tickets, manual fixes). |
| `hourly_labor_cost_usd` | USD/hour, fully loaded operator cost | 20 | ASSUMPTION | Owner's own opportunity-cost rate; revisit once any paid contractor delivers a setup. |
| `infra_cost_usd_per_month` | USD/month per subscribed deployment | 3 | ASSUMPTION | OMES-side monitoring/backup/telemetry cost for deployments on Assisted tier or above (`docs/business/support-tiers.md`); Community-tier (non-subscribed) deployments carry no OMES-side infra cost by design. |
| `support_hours_per_deployment_per_month` | hours/deployment/month | 0.5 | ASSUMPTION | Total monthly support hours (from ticket/incident log) ÷ number of active subscribed deployments that month. |
| `cac_usd` | USD, cost to acquire one paying deployment | 40 | ASSUMPTION | Time spent on outreach/sales calls × `hourly_labor_cost_usd`, tracked per closed deal during pilots. |
| `fixed_monthly_overhead_usd` | USD/month, independent of deployment count | 200 | ASSUMPTION | Sum of recurring non-deployment costs (CI minutes, domain, owner's own Hermes hosting, misc SaaS tools); reconcile against actual invoices monthly. |

`delivery_labor_hours` (derived) = `preflight_time_hours` + `install_time_hours` +
`troubleshooting_time_hours` = 0.5 + 1.5 + 1.0 = **3.0 hours** in the base scenario.

## 3. Formulas

All formulas are explicit so any reader can recompute them from the inputs table.

```text
delivery_labor_hours              = preflight_time_hours + install_time_hours + troubleshooting_time_hours
delivery_labor_cost_usd           = delivery_labor_hours * hourly_labor_cost_usd

month1_gross_contribution_usd     = setup_fee_usd - delivery_labor_cost_usd
  # one-time result of the paid setup engagement itself; subscription revenue/cost start month 2
  # by convention, to keep the setup-month and recurring-month arithmetic separate.

support_cost_usd_per_month        = support_hours_per_deployment_per_month * hourly_labor_cost_usd

steady_state_monthly_contribution_usd  = support_subscription_usd_per_month
                                          - infra_cost_usd_per_month
                                          - support_cost_usd_per_month
  # applies only to a deployment that is on a paid subscription (Assisted tier+).
  # a non-subscribed (Community-tier) deployment has $0 recurring OMES-side revenue and $0
  # recurring OMES-side cost, by the infra_cost_usd_per_month assumption above.

blended_monthly_gross_contribution_usd = subscription_attach_rate * steady_state_monthly_contribution_usd
  # average monthly contribution across ALL deployments (subscribed + not), used for
  # portfolio-level payback and break-even math.

payback_months = cac_usd / blended_monthly_gross_contribution_usd
  # months of blended recurring contribution needed to recover the cost of acquiring one
  # deployment. If month1_gross_contribution_usd already exceeds cac_usd, payback is effectively
  # immediate (<= 0 months) and this formula describes additional/ongoing recovery only.
  # Undefined (reported as "infinite") when the denominator is <= 0.

effective_hourly_margin_usd_per_hour = month1_gross_contribution_usd / delivery_labor_hours
  # profit per hour of delivery labor spent on the paid setup engagement, i.e. how much the
  # setup fee earns above and beyond delivery_labor_hours * hourly_labor_cost_usd.

support_burden_hours_per_deployment_per_month = support_hours_per_deployment_per_month
  # restated as a formula for measurement: total monthly support hours logged (across all
  # subscribed deployments) / count of active subscribed deployments that month.

break_even_deployments = fixed_monthly_overhead_usd / blended_monthly_gross_contribution_usd
  # number of active deployments needed for blended monthly contribution to cover fixed
  # monthly overhead. Undefined ("cannot break even") when the denominator is <= 0; negative
  # when the denominator is < 0, meaning adding deployments makes the loss worse, not better.

year1_value_per_deployment_usd = month1_gross_contribution_usd
                                  + 11 * blended_monthly_gross_contribution_usd
                                  - cac_usd
  # composite "value of one acquired deployment across its first 12 months", used only for
  # the sensitivity ranking in section 5; month 1 is the setup month, months 2-12 are the
  # 11 subsequent recurring months.
```

## 4. Scenarios

Each scenario restates every input; deltas from base are noted in parentheses. Arithmetic is
shown step by step so it can be checked against section 3's formulas.

### 4.1 Base

Inputs: `setup_fee_usd`=150, `support_subscription_usd_per_month`=25,
`subscription_attach_rate`=0.40, `preflight_time_hours`=0.5, `install_time_hours`=1.5,
`troubleshooting_time_hours`=1.0, `hourly_labor_cost_usd`=20, `infra_cost_usd_per_month`=3,
`support_hours_per_deployment_per_month`=0.5, `cac_usd`=40, `fixed_monthly_overhead_usd`=200.

```text
delivery_labor_hours              = 0.5 + 1.5 + 1.0 = 3.0
delivery_labor_cost_usd           = 3.0 * 20 = 60.00
month1_gross_contribution_usd     = 150 - 60.00 = 90.00
support_cost_usd_per_month        = 0.5 * 20 = 10.00
steady_state_monthly_contribution_usd = 25 - 3 - 10.00 = 12.00
blended_monthly_gross_contribution_usd = 0.40 * 12.00 = 4.80
payback_months                    = 40 / 4.80 = 8.33
effective_hourly_margin_usd_per_hour = 90.00 / 3.0 = 30.00
support_burden_hours_per_deployment_per_month = 0.5
break_even_deployments            = 200 / 4.80 = 41.67  -> 42 (round up)
```

Reading: a base-case deployment is net-positive after its first month (setup fee more than
covers delivery labor). Each subscribed deployment then contributes $12.00/month in steady
state; blended across subscribers and non-subscribers, an average deployment contributes
$4.80/month, so recovering the $40 CAC (beyond month 1) takes about 8.3 months, and roughly 42
active deployments are needed to cover the $200/month fixed overhead.

### 4.2 Upside

Deltas from base: `setup_fee_usd` 150→200 (+33%), `support_subscription_usd_per_month` 25→35
(+40%), `subscription_attach_rate` 0.40→0.60, `preflight_time_hours` 0.5→0.4,
`install_time_hours` 1.5→1.0, `troubleshooting_time_hours` 1.0→0.5, `support_hours_per_deployment_per_month`
0.5→0.4, `cac_usd` 40→30 (word-of-mouth reduces sales cost). `hourly_labor_cost_usd`,
`infra_cost_usd_per_month`, `fixed_monthly_overhead_usd` unchanged.

```text
delivery_labor_hours              = 0.4 + 1.0 + 0.5 = 1.9
delivery_labor_cost_usd           = 1.9 * 20 = 38.00
month1_gross_contribution_usd     = 200 - 38.00 = 162.00
support_cost_usd_per_month        = 0.4 * 20 = 8.00
steady_state_monthly_contribution_usd = 35 - 3 - 8.00 = 24.00
blended_monthly_gross_contribution_usd = 0.60 * 24.00 = 14.40
payback_months                    = 30 / 14.40 = 2.08
effective_hourly_margin_usd_per_hour = 162.00 / 1.9 = 85.26
support_burden_hours_per_deployment_per_month = 0.4
break_even_deployments            = 200 / 14.40 = 13.89  -> 14 (round up)
```

Reading: faster installs (product maturity), a higher price, and better subscription attach
roughly triple the blended monthly contribution and cut break-even deployments to 14.

### 4.3 Downside

Deltas from base: `setup_fee_usd` 150→120, `support_subscription_usd_per_month` 25→20,
`subscription_attach_rate` 0.40→0.25, `preflight_time_hours` 0.5→0.6, `install_time_hours`
1.5→2.0, `troubleshooting_time_hours` 1.0→2.0, `infra_cost_usd_per_month` 3→4,
`support_hours_per_deployment_per_month` 0.5→0.8, `cac_usd` 40→60. `hourly_labor_cost_usd` and
`fixed_monthly_overhead_usd` unchanged.

```text
delivery_labor_hours              = 0.6 + 2.0 + 2.0 = 4.6
delivery_labor_cost_usd           = 4.6 * 20 = 92.00
month1_gross_contribution_usd     = 120 - 92.00 = 28.00
support_cost_usd_per_month        = 0.8 * 20 = 16.00
steady_state_monthly_contribution_usd = 20 - 4 - 16.00 = 0.00
blended_monthly_gross_contribution_usd = 0.25 * 0.00 = 0.00
payback_months                    = 60 / 0.00 = undefined (infinite: CAC beyond month 1 is never recovered)
effective_hourly_margin_usd_per_hour = 28.00 / 4.6 = 6.09
support_burden_hours_per_deployment_per_month = 0.8
break_even_deployments            = 200 / 0.00 = undefined (cannot break even at any deployment count)
```

Reading: this is a warning scenario, not a target. More troubleshooting time plus a lower
price erases the entire subscription margin (`steady_state_monthly_contribution_usd` = 0), so
no number of additional deployments recovers the fixed overhead. The only levers back to
viability are cutting `troubleshooting_time_hours` (product/docs quality) or raising price.

### 4.4 Stress

Deltas from base: `setup_fee_usd` 150→0 (waived, e.g. free pilot conversion attempt),
`support_subscription_usd_per_month` 25→15, `subscription_attach_rate` 0.40→0.10,
`preflight_time_hours` 0.5→1.0, `install_time_hours` 1.5→3.0, `troubleshooting_time_hours`
1.0→4.0, `hourly_labor_cost_usd` 20→25 (contractor premium after owner's time is exhausted),
`infra_cost_usd_per_month` 3→5, `support_hours_per_deployment_per_month` 0.5→1.5, `cac_usd`
40→80, `fixed_monthly_overhead_usd` 200→250.

```text
delivery_labor_hours              = 1.0 + 3.0 + 4.0 = 8.0
delivery_labor_cost_usd           = 8.0 * 25 = 200.00
month1_gross_contribution_usd     = 0 - 200.00 = -200.00
support_cost_usd_per_month        = 1.5 * 25 = 37.50
steady_state_monthly_contribution_usd = 15 - 5 - 37.50 = -27.50
blended_monthly_gross_contribution_usd = 0.10 * (-27.50) = -2.75
payback_months                    = 80 / -2.75 = negative (never recovers; every additional month deepens the loss)
effective_hourly_margin_usd_per_hour = -200.00 / 8.0 = -25.00
support_burden_hours_per_deployment_per_month = 1.5
break_even_deployments            = 250 / -2.75 = negative (adding deployments increases the loss)
```

Reading: this is a kill-criteria scenario (see `docs/business/release-gates.md`). A waived
setup fee combined with heavy troubleshooting time loses $200 in month 1 alone and continues
losing $2.75/deployment/month afterward. If pilot data converges toward this scenario, stop
paid delivery and fix the product/process before re-testing pricing.

## 5. Sensitivity: which input moves margin most

Method: starting from the base scenario, perturb one input at a time by ±20% (holding all
others at base), recompute `year1_value_per_deployment_usd` (defined in section 3), and record
the average absolute change across the +20%/-20% pair. `hourly_labor_cost_usd` perturbs both
`delivery_labor_cost_usd` and `support_cost_usd_per_month` since it appears in both. Base
`year1_value_per_deployment_usd` = 90.00 + 11 × 4.80 − 40 = **102.80**.

| Rank | Input | ±20% range | Avg. |Δ year-1 value\| (USD) | Avg. |Δ| (% of base) |
|---|---|---|---|---|
| 1 | `setup_fee_usd` | 120 / 180 | 30.00 | 29.2% |
| 2 | `support_subscription_usd_per_month` | 20 / 30 | 22.00 | 21.4% |
| 3 | `hourly_labor_cost_usd` | 16 / 24 | 20.80 | 20.2% |
| 4 | `delivery_labor_hours` (total) | 2.4 / 3.6 | 12.00 | 11.7% |
| 5 | `subscription_attach_rate` | 0.32 / 0.48 | 10.56 | 10.3% |
| 6 | `support_hours_per_deployment_per_month` | 0.4 / 0.6 | 8.80 | 8.6% |
| 7 | `cac_usd` | 32 / 48 | 8.00 | 7.8% |
| 8 | `infra_cost_usd_per_month` | 2.4 / 3.6 | 2.64 | 2.6% |

Reading: the setup fee (price of the one-time engagement) is the single largest lever on
year-1 value per deployment, followed by the subscription price and the operator's hourly
labor cost. `infra_cost_usd_per_month` barely matters at this scale — it is not worth
optimizing before the pricing and labor-time inputs are validated.

## 6. How to update this model from pilot data

1. Run pilots per `docs/business/pilot-program.md` (issue #24) and collect, for every
   deployment: `omes check`/`install`/`doctor` log timestamps, the operator worksheet, and the
   feedback template (willingness-to-pay and objections).
2. Recompute each input in section 2 from real data:
   - `preflight_time_hours`, `install_time_hours` = median timestamp deltas from the OMES log
     file across all pilot deployments of that type.
   - `troubleshooting_time_hours` = median operator-worksheet manual-intervention time.
   - `hourly_labor_cost_usd` = actual contractor rate, or owner's documented opportunity cost.
   - `setup_fee_usd`, `support_subscription_usd_per_month` = observed accepted price (not
     asking price) from the price test; if no pilot participant accepted a price, keep the
     value marked ASSUMPTION and lower it for the next round rather than treating it as fact.
   - `subscription_attach_rate`, `support_hours_per_deployment_per_month` = ratios/averages
     computed only once at least 10 completed setups and one full month of subscription
     support exist; below that sample size, keep the prior ASSUMPTION and widen the
     downside/stress scenarios instead of narrowing base.
   - `cac_usd`, `infra_cost_usd_per_month`, `fixed_monthly_overhead_usd` = actual time/invoices
     logged during the pilot period.
3. Update both this file's tables/arithmetic (section 2 and section 4) and
   `docs/business/unit-economics-model.csv` in the same commit; re-derive `month1_gross_contribution_usd`
   through `break_even_deployments` by hand or with a spreadsheet using the formulas in section 3
   — do not hand-edit only the outputs.
4. Re-run the sensitivity procedure in section 5 against the updated base case; if the ranked
   order of inputs changes, note it explicitly rather than silently reordering the table.
5. Re-check this document's numbers against `docs/business/release-gates.md`'s business gates
   and KPI thresholds; if the updated base or downside scenario now matches or exceeds the
   stress scenario above, treat that as a kill-criteria trigger for release-gates purposes.
6. Never replace an ASSUMPTION with a "fact" based on fewer than the sample sizes noted above,
   and never state a market-size or willingness-to-pay figure without citing the specific pilot
   observation it came from.
