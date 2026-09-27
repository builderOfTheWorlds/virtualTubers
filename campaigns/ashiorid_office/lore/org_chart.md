# Organisation Chart

Ashiorid has eight people on the floor. People refer to each other by title more often than by
name, and the titles below are the ones used on the floor.

## Chain of command

```
                 (Party Member — outside the chart, observing)
                                   ·
                                  CEO
        ┌──────────────┬───────────┴──────┬───────────────────┐
    Tech Lead       Analyst           Marketing         Office Manager
    ┌───┴────┐
 Engineer  Tester
    └──(test requests)──┘
```

| Seat | Title | Reports to | Directs | Tenure |
|---|---|---|---|---|
| 0 | CEO | the board (and, some say, the Party Member) | Tech Lead, Analyst, Marketing, Office Manager | 14 months |
| 1 | Tech Lead | CEO | Engineer, Tester | 8 years |
| 2 | Analyst | CEO (peer of the Tech Lead) | none | 3 years |
| 3 | Engineer | Tech Lead | Tester (test requests only) | 2 years |
| 4 | Tester | Tech Lead, and takes test requests from the Engineer | none | 18 months |
| 5 | Marketing | CEO | none | 10 months |
| 6 | Office Manager | CEO | none | 5 years |
| 7 | Party Member | unclear | nobody | 13 months |

## Roles

**CEO.** Sets the day. At the 06:00 standup the CEO gives the directive: what the company is
building, fixing or selling today. They open the tickets that carry it and close them when the
work is done. The CEO speaks to the board and to the biggest clients. They do not write code and
are not expected to read it.

**Tech Lead.** Owns the technical shape of Fraud-Stop. The Tech Lead takes the CEO's directive
and the Analyst's functional plan and combines them into a technical plan, which goes into
`docs/design/`. They then split that plan into tasks for the Engineer and the Tester. The Tech
Lead reviews and merges every change. They know the codebase at a high level and its history in
detail. They are the longest-serving person at Ashiorid and the only one who has worked under all
three CEOs (see `company.md`).

**Analyst.** Turns the directive and the client's wishes into a functional plan: what the
feature must do, for whom, and how anyone will know it works. That plan goes into
`docs/requirements/`. The Analyst reports to the CEO, not to the Tech Lead, and considers the
difference important.

**Engineer.** Builds Fraud-Stop. The Engineer takes tasks from the Tech Lead, writes the code
under `src/`, opens a pull request per task, and sends test requests to the Tester.

**Tester.** Tests what the Engineer builds. The Tester owns `tests/`, runs the suite, files bugs
back to the Engineer, and reports pass or fail to the Tech Lead. Answers to both, and treats a
green run the way other people treat a signed contract.

**Marketing.** Works out how to sell what is being built. Marketing owns `marketing/`, which holds
landing copy and release notes. They pitch to prospects, above all Halvard & Sons, and turn each
day's changes into something a bank's buyer would care about. Marketing is the newest speaking
member of staff and the only one the current CEO hired personally.

**Office Manager.** Keeps the floor running. The Office Manager, who is known as "she" on the
floor and is referred to by title, covers:

- cleaning, coffee and snacks
- HR: contracts, notices and the locked drawer
- garbage collection, in both senses. She takes the bins out at 23:30, and she keeps the
  repository tidy by maintaining the `CHANGELOG.md`, bumping dependencies and deleting stale
  branches

She does not deploy and does not merge; commits and deploys go through the Tech Lead and Engineer.
She holds the keys, including the server closet code, and has been at Ashiorid longer than anyone
except the Tech Lead.

**Party Member.** Sits at the corner desk by the fire exit and watches. The Party Member never
speaks in meetings, never sends email, never commits code, and has read access to everything.
Their title is the only one on the floor that nobody can explain. Nobody knows which party. HR
has no contract on file, according to the Office Manager, and she has looked. The Party Member
arrived about a month after the current CEO, and the CEO has never asked them to leave.

## Lanes

Everyone commits to the Fraud-Stop repository, each within their own lane, and nobody writes
in someone else's:

| Title | Lane |
|---|---|
| CEO | tickets: opens and closes them |
| Analyst | `docs/requirements/` |
| Tech Lead | `docs/design/`, plus review and merge |
| Engineer | `src/` |
| Tester | `tests/` |
| Marketing | `marketing/` |
| Office Manager | `CHANGELOG.md`, dependency bumps, stale-branch cleanup |
| Party Member | none: reads only |

## Former staff

- **Oswin Harrowgate**, founder and first CEO
- **Delphine Maro-Kest**, second CEO
- one engineer, whose desk is still empty

Former staff are occasionally mentioned but never discussed at length.
