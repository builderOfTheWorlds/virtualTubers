# Fraud-Stop

Fraud-Stop is Ashiorid's only product. It is an enterprise software-as-a-service platform that
banks route their transactions through, in real time, to get a fraud verdict before the money
moves.

## What it does

A client bank's payment system sends Fraud-Stop one request per transaction: a card purchase, a
transfer or a withdrawal. The request carries the transaction details: account, amount, currency,
merchant, the country the card was issued in, the country the transaction came from, and a
timestamp. Fraud-Stop answers with three things:

- **Score**: a number from 0 to 1000. Higher means more likely to be fraud.
- **Verdict**: one of `APPROVE`, `REVIEW` or `DECLINE`.
- **Reasons**: one or more short reason codes, each with a plain-language explanation, saying why
  the score came out the way it did.

The bank decides what to do with the verdict. Most clients approve automatically, send `REVIEW`
to a human analyst queue, and block `DECLINE` at the till. Every client sets its own score
thresholds for the three verdicts.

The company's internal rule, carried over from the second CEO, is that **no verdict ships without
at least one reason**. A `DECLINE` with an empty reasons list counts as a bug, whatever the score
says.

## The rules

Fraud-Stop started as a rules engine and is still mostly one. Each rule looks at the transaction,
and at recent history for the same account, and adds points to the score if it fires. The core
rules, known on the floor by their codes, are:

- **VELOCITY**: too many transactions on one account in too short a window. This is the founder's
  original rule, and people still call it "Harrowgate's rule." Its thresholds are the most-argued
  numbers in the codebase.
- **AMOUNT_OUTLIER**: a transaction far above what the account normally spends. It needs a history
  of past amounts per account, which is where most of its bugs come from.
- **COUNTRY_MISMATCH**: the transaction comes from a different country than the one the card was
  issued in, or from two different countries within a window that is physically implausible.

A handful of smaller rules sit alongside these, such as merchant-category blocklists and
round-number amounts at odd hours. Clients can switch individual rules on or off, and tune them,
for their own traffic.

## Where it is going

The roadmap, which is pinned in the Moonwell room and redrawn every time a new CEO arrives, has
three stages:

1. **Rules, done properly.** Tested, versioned rule definitions that clients can configure without
   an engineer on the phone.
2. **Explainability.** Reasons that a bank's compliance team can hand straight to a regulator.
3. **Learned scoring.** A machine-learned model that scores alongside the rules, not instead of
   them. The second CEO started this and it has never been finished. The Tech Lead's position is
   that it will ship "when the rules have tests." The current CEO's position is that it will ship
   this year, because Halvard & Sons asked about it.

## How it is built

The Fraud-Stop code lives in a single repository that everyone on the floor commits to, each in
their own lane (see `org_chart.md`). In broad strokes:

- the scoring service and rules live under `src/`, and belong to the Engineer
- the tests live under `tests/`, and belong to the Tester
- functional requirements live under `docs/requirements/`, and belong to the Analyst
- technical designs live under `docs/design/`, and belong to the Tech Lead, who also reviews and
  merges every change
- landing-page copy and release notes live under `marketing/`, and belong to Marketing
- the `CHANGELOG.md`, dependency updates and the pruning of stale branches belong to the Office
  Manager, who calls the pruning "garbage collection" and treats it the same as the kitchen bins

Work arrives as tickets opened from the CEO's daily directive. Changes go in through reviewed pull
requests. Nothing merges without the Tech Lead's approval and a passing test run from the Tester.

## Service promises

Fraud-Stop's contracts promise:

- a verdict for 99 percent of requests within 150 milliseconds
- a fallback verdict of `REVIEW`, never a silent `APPROVE`, if the service cannot reach a decision
  in time

The Tech Lead has the latency graph on a second monitor at all times. Engineers learn quickly that
"it's slower" is the one bug report that gets answered within the minute.

## Known sore points

- The amount-outlier rule has no sensible behaviour yet for brand-new accounts with no history.
- Currency handling was bolted on for Malmont Savings & Trust, and it shows.
- The learned-scoring work from the second CEO's era sits on a branch that nobody will delete.
  The Office Manager has asked twice.
