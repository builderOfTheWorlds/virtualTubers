# Clients and Prospects

Fraud-Stop has four paying clients and one prospect that the whole company is chasing.
Marketing keeps the pipeline on the whiteboard by their desk, and the CEO keeps a shorter version
in their head. The client's name often decides what gets built next.

## Paying clients

### Corvane National Bank

- **Relationship:** the oldest large client, since year two, and the one the payroll depends on
- **Traffic:** most of Fraud-Stop's card volume
- **What they care about:** latency, above all. Corvane's contract carries the 150-millisecond
  promise and financial penalties if Fraud-Stop misses it. Their risk team is conservative and
  distrusts anything described as "learned."
- **History:** the founder used to work at Corvane, and some of his old colleagues are still
  there. Corvane renews every two years, and each renewal is taken very seriously. The next one is
  due this year.
- **Contact style:** formal, slow, and always by email. Corvane never calls, and that is part of
  why the "long call" rumour about them seems unlikely to most of the floor.

### Malmont Savings & Trust

- **Relationship:** signed under the second CEO. Ashiorid's first client in the south.
- **Traffic:** a regional savings bank of modest size in the market town of Malmont, a few days'
  travel south of the capital, with a busy cross-border trade
- **What they care about:** currency handling and the country-mismatch rule. Malmont's customers
  travel and trade across borders constantly, so the rule fires more often for them than for any
  other client. Most of the tuning Ashiorid has ever done on that rule was done for Malmont.
- **Quirk:** Malmont's traffic has an unusual pattern. It is quiet in the daytime and busier late
  at night than a town that size should produce. Their risk officer describes this as "local
  custom" and does not want it flagged.
- **Contact style:** friendly and chatty, and occasionally sends a hamper at midwinter that the
  Office Manager rations for a month.

### Pellbridge Credit Union

- **Relationship:** the credit union that ran the founder's first nightly batch. It is the
  smallest client and the most loyal.
- **Traffic:** small in volume, mostly local debit transactions
- **What they care about:** explanations. Pellbridge's staff read every `REVIEW` reason by hand,
  and they phone the Analyst directly when a reason doesn't make sense. The second CEO's rule that
  every verdict carries a reason began with a Pellbridge complaint.
- **Commercial position:** they pay the least and complain the least. Nobody would say it at
  standup, but everyone treats them as family.

### Ostry Mercantile

- **Relationship:** a payments processor for mid-size online merchants, signed under the second
  CEO
- **Traffic:** online-only, with high volume and wild spikes during sales
- **What they care about:** the velocity rule and false positives. Ostry's merchants lose money
  every time a real customer is declined, and Ostry will say so, loudly, in a ticket, within
  minutes.
- **Status:** they have threatened to leave twice. Marketing counts them as "stable."

## The prospect

### Halvard & Sons

- **Who they are:** a large, old private bank. On paper it is bigger than all four current clients
  combined.
- **Stage:** in talks for most of the current CEO's tenure. Signing Halvard is the company's
  stated goal for the year, and it drives much of the directive at standup.
- **What they want:**
  - learned scoring, which Fraud-Stop doesn't have yet
  - explainability that their compliance team can hand to a regulator
  - references from existing clients, especially Corvane
- **Complication:** Halvard's due-diligence questions sometimes arrive before Ashiorid has
  announced the thing they are asking about. The Party Member writes in the notebook during every
  Halvard meeting. Both facts are regularly mentioned together at lunch.

## Lost and past clients

- **The first dozen trial banks.** The founder signed them one by one and lost most of them in the
  move from nightly batches to live scoring. Their names survive only as old configuration files
  that the Office Manager wants to garbage-collect.
- **An unnamed eastern bank.** It walked away during the second CEO's final quarter. The contract
  file for it is not in the cabinet where it should be.
