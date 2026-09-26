I've read review_2.md. Your answers settle almost everything, and I'm ready to write plan v2 to .claude/prompts/character_generator_updater_v2.md. Four points need a decision or confirmation first, and a few details I'd resolve inside the plan unless you object.

Please confirm or decide

  1. Profile reset each week. Your answer to the drift question (week N = background + fragments from weeks 1..N-1) means personality drift, profile changes and objective progress all go back to the starting state every week. That removes the plan's weekly profile updates, the drift limits and goal tracking across weeks. Only fragments carry forward. Is that what you want?
  Yes, for starting the characters have no clue they are in a timeloop and should proceede with their original personality and objectives, after many weeks when they gain enough fragments will they start to understand whats happening, thats when we will need to start updating their objectives

  2. How the trigger works (still open). My suggestion is to build it in two steps and test it before building the rest:
     - A cheap similarity match on each turn picks a few candidate fragments.
     - An LLM then judges whether the scene really fits one.
     The rest of the design doesn't depend on which method wins, so it can stay undecided until the test.

OK lets do a 2step process to test 





  3. Timezone. "Server time 00:00" changes meaning if the server moves or its clock changes. I'd put an explicit timezone in the config, for example America/New_York.

Sounde good, newyork timezoen is correct


  4. Order of events at Sunday 00:00. The daily summary and the weekly reset fire at the same moment, so they need a fixed order:
     1. Close out Saturday's daily summary.
     2. Choose the week's fragments from the seven daily summaries.
     3. Archive the week's knowledge.
     4. Open the new week.
     Each step would be recorded as done so a re-run skips it instead of repeating it.
Sounds good-



Details I'd resolve in v2

  - Kafka to Postgres. Every Kafka message carries a timestamp. The catch is that the existing messages table records agent IDs, not characters. v2 would add a mapping from agent to character, then copy each message into experience_events tagged with its character and day. Kafka also deletes old messages after a set retention period, so they need to be copied to Postgres promptly.
  Can we have a process that reads from kafka and inserts to postgres in real time?

  - Variation between weeks. Your note that no two weeks will play out the same contradicts the old plan's claim that the reset is a replayable, predictable function. v2 would say only the database step is predictable. Each LLM response is stored, so any week can be audited later, but not re-run with the same result.

  New ideas are the correct ones, each week is different

  - Fragments never change. They'd have no strength or decay fields. The "new memories connected to a fragment" idea becomes a link from a node in the current week to the fragment it grew from. Those nodes can then be picked as future fragments, which is what lets memories chain together.

    yeah that sounds good


  - Who keeps fragments. A setting on each character, off by default and on for the main streamed character. Everyone else resets fully.

Correct


  - Starting point. One fragment per week to start, adjustable in the config. The knowledge graph stays its own separate process. The database is the single source of truth: source material is loaded into it and the cast YAML is generated from it.

Correct


  - Growth. Fragments pile up forever, so after a year there would be about 52 candidates to check on every turn. That's fine, but it's one reason the trigger step should narrow the field cheaply before anything expensive runs.   

Thats a good point, we need a better method for checking the fragments


Shall I write v2 now with my suggestions for points 2–4, leaving point 1 as a marked assumption until you confirm?