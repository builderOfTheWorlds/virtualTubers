I read both docs in docs/character_generator_updater_handoff/. Your notes are mostly in the review file, plus one in the plan at line 17. Overall they hold together and point to a clear design. The biggest change is that the reset is no longer a "forgotten links" mechanic. It becomes a time loop where the only thing that carries forward is a fragment of memory. That change affects the schema, the reset logic and the brief, so here is what it changes and where your notes leave open questions.

One caveat first: this repo only contains CLAUDE.md and docs/. The code the review cites (app/, services/, commit 1497bd9) isn't here, so I couldn't check any code claims from this checkout.

What your notes settle

  Plan line 17: The knowledge graph gets its own process. The generator only produces the profile data and the data format, so the knowledge stage moves out of the generator. The generator's output becomes a data contract that the graph process reads.

  #1: Initial knowledge comes from the source campaign, as the agent interprets its own character. Experience builds up from interactions during the week. This works, but something still has to publish those interactions with the character's name and a timestamp on each one. Nothing does that today.

The primary methof of communication btween character is a kafka topic, I believe al messages are timestamped here and will need to be preserved into postgres.


  #2: A week is the set of messages in a time window. That works, but the week's start and end need to be stored somewhere (a week or loop table). Re-running the same week should also do nothing the second time.

We will need to define the start and end in a config, the initial start and end is 00:00 sunday morning. Every week the acharacters have variateions in how they interperate data and communicate (live ai agents), it will never be exactly the same



  #4, #13, #14, #15, #16: No objections. Switch to DOUBLE PRECISION, add revert later, add the indexes, accept that characters' memories can diverge, and rename worker to character.

this is good


  #7: Keep a small, configurable number of fragments per week (for example 2 or 3).

Let it be configurable if I want to increase or decrease, start with 1 for testing


  #8 and #12: Summarise each day, then pick what's remembered at the end of the week. This fixes the context-size problem and spreads out the work. It also means you need a definition of a "day" (in-world, or real time?).

Real time day, server time 00:00 we shoudl start this process




  #9: Harry Potter is only a shared example. Your custom campaign is the real source, so the quarantine question no longer matters. I'll need that campaign's format and size to plan chunking.

At the moment we are just planning, we will look athe source material later.




The core model (#5, #6): a time loop with lossy fragments

  - Backstory from before the story starts is permanent.
  - Everything learned during a week is archived at the reset: nothing is deleted, but the character can't reach it.
  - A few fragments survive, and each one is a new, compressed record rather than the original node. The gist is emotional and sensory ("felt victorious grabbing his enemy's face"), not the full transcript.
  - A fragment stays hidden until the current scene triggers it, like déjà vu. After several weeks, fragments can trigger each other, so a character can recall them earlier and change the story. That's the outcome you want.

This contradicts the current plan in three places:

  1. The plan keeps the top N nodes fully active after a reset. Your model keeps a separate, lossy fragment instead.
    Lossy fragment is the better choice, if the character remember exactly what happened they will retake the same steps which i dont want
  2. The plan's brief lists the names of forgotten nodes at startup. Your model shows the character nothing until something triggers it.
    Correct, we are mmodifying, use my update as the correct idea
  3. D2 (build the brief at worker boot only) is no longer enough. Recall has to happen during play, which means matching each scene against fragment triggers on every turn.
    Thats a good point, we will need to figure out how to allow the characters to remember their fragments.


Questions you haven't answered yet

  - Personality drift: "back to his initial state" suggests the weekly profile drift and objective progress should also reset, with fragments as the only thing carried over. The plan currently keeps drift across weeks. Which do you want?
    Lets consider week 1 starts with just background, then week 2 is background + fragments froim weke 1, then week 3 is background + fragments from weeks 1 and 2

  - Fragment lifecycle: do fragments last forever? If Harry recalls one and relives the moment, does it get sharper or produce a new fragment?
    Fragments last forever (unless something specificalyl happnes and removes them but we dont have a plan for theis) they dont get modified sharper or duller, but new memories can be generated connected to those fragments, and its possible that those new memories become fragments as well


  - The world: do other characters and NPCs (Quirrell, for example) reset to the start too, with only the main characters keeping fragments?

    Yes, only the main character who we show in the stream will retain knowledge fragments unless explicitly stated in the config. 


  - Trigger mechanism: matching on embedding similarity, keyword cues, an LLM judge, or a mix?
  Not sure yet, lets investigate this further, what kind of options are you thinking about?

Data structure recommendations (you asked for these in #10 and #11)

  - Make the database the single source of truth and generate the cast YAML from it, so there's one ingestion path instead of two.
    Yeah lets do that, we will need to load source material into the DB, then use that for genration

  - Suggested tables:
      characters and character_baseline (permanent backstory, versioned)
      loop_weeks (window start and end, status)
      experience_events (from the bus: character, timestamp, day, week)
      daily_summaries
      week_knowledge_nodes and week_knowledge_edges (tagged with their week, archived at reset, never deleted)
      memory_fragments (gist, trigger cues, links to the source nodes and week, state)
      fragment_links (fragment to fragment, for chain recall)
      fragment_recalls (when each fragment was triggered)
  - Replace the active flag with archived_at_week. That matches "inaccessible but kept" more directly.

we can start with that structure of tables and expand as we need


If you answer the four questions, I'll write the revised plan (v2) to .claude/prompts/.