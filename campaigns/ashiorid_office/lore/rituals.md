# Rituals and Working Hours

## The hours

Ashiorid works from **06:00 to 00:00, every day of the week**. There are no weekends and no days
off, and nobody on the floor thinks of this as unusual. The founder set the hours in the first
year ("fraud doesn't sleep, so neither does the desk that catches it"). Neither of the CEOs who
followed him changed them. The third company value, *The desk is always open*, is taken literally.

Nobody treats the hours as a hardship. Asking for "a day off" earns a puzzled look, followed by the question "off
what?" Holidays are something clients have. They show up in Fraud-Stop only as a known source of
spending spikes that set off the amount-outlier rule.

The hours between **00:00 and 06:00** are the staff's own. People go home, sleep, eat, and live
whatever life they have outside the floor. The office is dark and locked, except for the server
closet and the corner by the fire exit.

The day is informally split into four watches:

| Hours | Name on the floor | What happens |
|---|---|---|
| 00:00–06:00 | Off | Lights out. The floor is empty. The nightly maintenance jobs run. |
| 06:00–12:00 | Morning | Coffee, standup, plans, delegation |
| 12:00–18:00 | Build | Code, tests, bug fixing, lunch, pitches |
| 18:00–00:00 | Ship | Fixes, final test run, release, garbage collection, wind-down |

## 06:00: First coffee

The Office Manager arrives first, before 06:00, and has the coffee machine warm and the snack
shelf stocked by the time anyone else comes up the stairs. The first cup of the day is poured for
the CEO and left on the Glass Box desk. The second goes to the Tech Lead. Beyond that the order is
unofficial but stable, and people notice when it changes.

## 06:00: Standup

Everyone stands around the Moonwell room table. The CEO gives **the directive**: one or two
sentences on what Ashiorid is doing today. Each speaking role then says in a line what they will
do toward it:

- The Analyst names the functional question.
- The Tech Lead names the technical plan, or says when it will exist.
- The Engineer and the Tester say what they are picking up.
- Marketing says who they are pitching.
- The Office Manager gives notices: HR, deliveries, the state of the kitchen.

The Party Member attends every standup, stands by
the door and says nothing. The CEO starts talking once the Party Member is in the room.

## The morning plans

After standup the Analyst writes the functional plan and the Tech Lead writes the technical plan
on top of it. By the end of the Morning watch the Engineer and the Tester should each have tickets
with their names on them. If they don't, the Tech Lead knows it and so does everyone else.

## Lunch

Lunch is eaten at the old table in the kitchen, sometime around 13:00, by whoever is free. It's the
one meeting with no agenda. Two customs apply:

- Nobody talks about the latency graph at lunch.
- Marketing is allowed to ask the Engineer "what it actually does" without being mocked.

The Office Manager decides the week's snacks. Feedback is accepted in writing only.

## The Ship watch

From 18:00 the floor works toward a clean end of day. Open bugs get fixed or explicitly deferred,
the Tester runs the full suite, and the Tech Lead merges what has passed. Marketing then writes the
release note for anything that shipped. The CEO closes the tickets that are done and leaves the
rest open, with a comment, for the next morning.

## Friday demo

The second CEO introduced the Friday demo, and it has survived her. At 17:00 on Fridays the team
gathers in the Moonwell room and the Engineer shows what was built that week, running against
staging. The Tester says what broke. Marketing says how they would sell it. The CEO decides which
client hears about it first. Friday is otherwise a normal working day, and so are Saturday and
Sunday.

## 23:30: Garbage collection

At 23:30 the Office Manager takes out the bins in their four streams, wipes the kitchen table and
turns the coffee machine off. She also does the repository's garbage collection: she prunes
merged and stale branches and adds the day's entries to the `CHANGELOG.md`. The one exception is
the old learned-scoring branch, which she has been told twice not to touch. Staff who are still at
their desks at 23:30 are asked, politely, whether they are leaving soon.

## 00:00: Lights out

At midnight the Office Manager turns off the floor lights from the panel at her station. That is
the end of the day, whatever state the work is in. Anyone left at a desk is working in the dark,
and nobody stays long. The emergency light over the fire exit stays on.

The Office Manager is the last to leave. She has said that she never sees the Party Member leave,
and she has said it more than once.

## Other customs

- **Harrowgate's rule.** Anyone who proposes changing the velocity thresholds has to buy the
  kitchen a box of pastries first. This is enforced.
- **The unsent draft.** It is bad form to ask the Tech Lead what the second CEO's last email said.
