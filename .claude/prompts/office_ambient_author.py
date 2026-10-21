#!/usr/bin/env python3
"""WS3 - author, gate and STAGE a pool of plot-free ambient office scenes.

Pack:   campaigns/ashiorid_office   (go-live plan D1, "Seam: ambient office scene")
Model:  hermes3-70b on the local vLLM server (OpenAI-compatible, :8092)
Output: .claude/prompts/office_ambient_stage/<phase>/<NN>-<id>.yaml
        + manifest.jsonl (events + accepted) and rejects.jsonl (with reasons)

Nothing here touches campaigns/ or Postgres.  Promotion is a user approval step.

Ratchet note: ambient scene `prompt`s are only a short *situation* handed to
the live improvisers; the model writes that situation text, never lines the
characters speak.  Who is in the scene (seats), the day phase and the
intensity are decided HERE (hooks below), not by the model.

Content rules are enforced in code AFTER generation (see `gate()`), never
trusted to prompt wording:
  * no plot decisions/reveals (hiring/firing, Halvard pilot, Corvane fraud,
    regulator audit, secrets) -> banned-term + context checks
  * no out-of-fiction words (Twitch, stream, AI, viewers, chat, model, agent..)
  * only real cast names, only the seated cast mentioned, no invented people
  * 40-110 words, token-set Jaccard <= 0.5 against every accepted prompt
  * max 3 arrival-type scenes in the whole pool
  * the Party Member never speaks / eats / opens the notebook; every scene has
    at least one speaking seat
  * clock times in the prompt must fall inside the day phase
Resumable: slots already staged (NN-*.yaml present) are skipped; accepted
prompts, ids and arrival count are reloaded from disk at start.

Modes:  (default) author | --verify | --report | --selftest
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import pathlib
import random
import re
import shutil
import signal
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]
PACK = REPO / "campaigns" / "ashiorid_office"
STAGE = pathlib.Path(os.environ.get("AMBIENT_STAGE_DIR") or (REPO / ".claude" / "prompts" / "office_ambient_stage"))
MANIFEST = STAGE / "manifest.jsonl"
REJECTS = STAGE / "rejects.jsonl"
RUNLOG = STAGE / "run.log"
ENV_FILE = pathlib.Path.home() / "environments/argyreServer/deployments/vllm-hermes3-70b/.env"
BASE_URL = os.environ.get("AMBIENT_LLM_URL", "http://127.0.0.1:8092/v1")
MODEL = "hermes3-70b"

PHASES = ["off_hours", "morning", "workday", "evening"]
QUOTA = {"off_hours": 40, "morning": 30, "workday": 55, "evening": 25}
PREFIX = {"off_hours": "off-", "morning": "mor-", "workday": "wrk-", "evening": "eve-"}
WINDOW = {  # inclusive minutes-of-day range the phase covers
    "off_hours": (0, 6 * 60 - 1), "morning": (6 * 60, 9 * 60 - 1),
    "workday": (9 * 60, 17 * 60 - 1), "evening": (17 * 60, 24 * 60),
}
PHASE_LABEL = {
    "off_hours": "the small hours (00:00-06:00): lights are out, the floor is dark and locked up",
    "morning": "morning (06:00-09:00): the 06:00 standup is already over and the day's work is starting",
    "workday": "the working day (09:00-17:00)",
    "evening": "the evening, the Ship watch (17:00-24:00): fixes, the last test run, release notes, wind-down",
}
MAX_ARRIVALS = 3
JACCARD_MAX = 0.5
WORDS_MIN, WORDS_MAX = 40, 110
MAX_ATTEMPTS_PER_SLOT = 8
CONCURRENCY = 2  # hard cap - WS1 shares this server

# ----------------------------------------------------------------- cast data
NAMES = {
    "ceo": ("Graham", "Ellery"), "tech_lead": ("Anselm", "Brody"),
    "analyst": ("Maren", "Voss"), "engineer": ("Theo", "Palliser"),
    "tester": ("Owen", "Hask"), "marketing": ("Julian", "Faire"),
    "office_manager": ("Nora", "Blakeley"),
}
FULL = {
    "ceo": "Graham Ellery, the CEO (warm, clipped, manager-ish, logistics metaphors)",
    "tech_lead": "Anselm Brody, the Tech Lead (slow, dry, few words, long memory, called Brody)",
    "analyst": "Maren Voss, the Analyst (structured, numbered points, asks 'for whom?')",
    "engineer": "Theo Palliser, the Engineer (fast, chatty, jokes when cornered)",
    "tester": "Owen Hask, the Tester (flat, deadpan, always gives a number)",
    "marketing": "Julian Faire, Marketing (smooth, taglines, customer images)",
    "office_manager": "Nora Blakeley, the Office Manager (bright, brisk, kind, lists, 'love' and 'pet')",
    "party_member": "the Party Member (silent observer at the corner desk; present, never speaks)",
}
TITLE_RE = {
    "ceo": r"\bCEO\b", "tech_lead": r"\bTech Lead\b", "analyst": r"\bAnalyst\b",
    "engineer": r"\bEngineer\b", "tester": r"\bTester\b", "marketing": r"\bMarketing\b",
    "office_manager": r"\bOffice Manager\b", "party_member": r"\bParty Member\b|\bPenhale\b",
}
ALL_SEATS = list(FULL)

# Capitalised words allowed mid-sentence (everything else = invented proper noun).
ALLOWED_CAPS = set("""
Graham Ellery Anselm Brody Maren Voss Theo Palliser Owen Hask Julian Faire Nora Blakeley
CEO Tech Lead Analyst Engineer Tester Marketing Office Manager Party Member
Ashiorid Fraud Stop Fraud-Stop Tallow Street Moonwell Glass Box Open Floor Kitchen Reception
Small Room Server Closet Corvane National Bank Malmont Savings Trust Pellbridge Credit Union
Ostry Mercantile Halvard Sons Harrowgate Delphine Maro Kest Maro-Kest
Monday Tuesday Wednesday Thursday Friday Saturday Sunday
REVIEW APPROVE DECLINE PR PRs HR VELOCITY AMOUNT_OUTLIER COUNTRY_MISMATCH CHANGELOG API OK
Standup Ship Morning Build Off Watch Hm Okay
""".split())
FORBIDDEN_NAMES = set("Dennis Ruth Ilse Ilka Elke Ledger Penhale Oswin".split())

BANNED_WORDS = re.compile(
    r"\b(twitch|stream|streaming|streamer|streamers|livestream\w*|ai|a\.i\.|llm|gpt|chatbot|bot|bots|"
    r"viewers?|audience|chat|chats|chatting|model|models|modell?ing|agents?|simulat\w+|"
    r"script|scripted|roleplay\w*|npc|npcs|character|characters|episode|episodes|"
    r"broadcast\w*|camera|cameras|subscribers?|prompt|prompts|neural|algorithm\w*)\b",
    re.I)
PLOT_WORDS = re.compile(
    r"\b(fired|firing|sacked|dismiss\w*|resign\w*|redundan\w*|laid off|layoffs?|hired|hiring|"
    r"recruit\w*|promot(?:ed|ion)|demot\w*|quit|quits|quitting|"
    r"reveal\w*|confess\w*|confide\w*|admit|admits|admitted|discover\w*|uncover\w*|"
    r"realis\w*|realiz\w*|turns out|finds out|found out|learns that|secret\w*|conspir\w*|"
    r"embezzl\w*|scandal|breach\w*|whistle\w*|audit\w*|regulat\w*|investigat\w*|subpoena\w*|"
    r"lawsuit|pilot|signed|signs|decided|decides|decision|decisions|announce\w*|agreed|agrees|"
    r"cancel\w*|terminat\w*|spy|spies|unsent|long call|badge|finally|at last|once and for all|"
    r"for good|proof|evidence|accus\w*|blackmail\w*|stolen|steal\w*|theft|"
    r"promises? to leave|walks? out|storms? out)\b", re.I)
CLIENT_WORDS = re.compile(r"\b(Halvard|Corvane)\b")
CLIENT_OUTCOME = re.compile(
    r"\b(sign\w*|agree\w*|win|wins|won|lose|loses|lost|leave|leaves|leaving|renew\w*|drop\w*|"
    r"walk\w*|pull\w* out|fraud\w*|approv\w*|reject\w*|accept\w*|cancel\w*)\b", re.I)
PM_SPEECH = re.compile(
    r"\b(says?|said|speaks?|spoke|asks?|asked|repl\w+|whisper\w*|answers?|mutters?|tells?|told|"
    r"laughs?|nods?|replies|responds?|greets?|smiles?|sighs?|clears? (?:his )?throat)\b", re.I)
PM_ACT = re.compile(
    r"\b(eats?|eating|drinks?|drinking|sips?|sipping|takes? the cup|picks? up the cup|"
    r"arriv\w+|leav\w+|vanish\w*|disappear\w*|exits?|walks? out|uses? the (?:lift|stairs))\b", re.I)
NOTEBOOK_OPEN = re.compile(r"\b(open\w*|writ\w+|scribbl\w*|page|pages|flips?|flipping|turns? (?:a )?page)\b", re.I)
ARRIVAL = re.compile(
    r"\b(arriv\w+|first (?:one )?in|first to (?:arrive|come|get in)|unlock\w*|comes? (?:up|in)\b|"
    r"walks? in|opening up|opens? up (?:the )?(?:office|floor)|lets? (?:herself|himself) in|"
    r"before (?:anyone|the others)|already warm|turns? on the lights)\b", re.I)
TIME_RE = re.compile(r"\b([01]?\d|2[0-4]):([0-5]\d)\b")
SPEAKER_LABEL = re.compile(r"(?:^|[.!?]\s+)[A-Z][a-z]+(?: [A-Z][a-z]+)?: ")


# ------------------------------------------------------------ hook catalogue
# (seats, situation hint, clock-or-None).  Seats, phase and intensity come from
# here, NOT from the model.  Hints are mundane and plot-free by construction.
OFF = [
    (("office_manager",), "Just after lights-out Nora finishes the last kitchen jobs by the single light over the stove, unhurried, humming.", "00:10"),
    (("office_manager", "party_member"), "Nora walks the dark Open Floor with a torch, wiping desks and cleaning around the Party Member's corner desk, which the emergency light keeps lit.", None),
    (("tech_lead",), "Brody alone at his window desk in the dark; only the latency graph glows. He watches it the way other people watch a fire.", None),
    (("tech_lead", "engineer"), "A routine staging deploy crawls along. Brody and Theo wait in the Server Closet doorway for the progress bar, in the cold and the hum.", None),
    (("engineer",), "Theo is the last one still in, a chess game propped against a monitor, biscuits on a napkin, the floor black around him.", None),
    (("tester",), "Owen sits at the spare laptop watching the overnight test suite crawl, counting passes under his breath.", None),
    (("office_manager",), "Nora sorts the post tray at the reception desk by torchlight and puts it back square.", None),
    (("analyst",), "Maren, working late in a pool of desk-lamp light, annotates a client printout in three colours of ink.", None),
    (("marketing",), "Julian rehearses a tagline to the plant on his desk, softly, trying it three different ways.", None),
    (("ceo",), "Graham stands in the Glass Box with the blinds open, looking at the pale rectangle on the back wall and the dark street below.", None),
    (("ceo", "office_manager"), "Nora reminds Graham the floor lights are already off. He is reading his folded index card under the Glass Box desk lamp.", "00:20"),
    (("engineer", "tester"), "Theo and Owen wait out a long test run in the dark, sharing biscuits, hardly talking.", None),
    (("tech_lead", "office_manager"), "A temperature blip lights the panel in the Server Closet. Brody and Nora check the cold room together; they know the keypad code.", None),
    (("tech_lead", "party_member"), "Brody crosses the dark floor to the kitchen, passing the Party Member's corner desk without looking at it.", None),
    (("office_manager",), "Nora descales the temperamental espresso machine at the kitchen counter, a slow job that she does not let anyone else touch.", None),
    (("party_member", "office_manager"), "Nora sets a cup on the Party Member's desk even at this hour, straightens the pen beside the closed notebook, and talks quietly to the empty room.", "01:00"),
    (("analyst", "tech_lead"), "Maren and Brody find themselves the only two left on the floor, working in separate pools of lamplight, a long way apart.", None),
    (("engineer",), "Theo tidies his sticky notes, stacking the crossed-out 'easy' ones, in the quiet of a dark Open Floor.", None),
    (("tester",), "Owen refills the spare laptop's charger and stares at the suite's progress, deadpan, the only light on in the Open Floor.", None),
    (("marketing", "party_member"), "Julian is back for a forgotten charger and notices the Party Member's corner lit by the emergency light. He lowers his voice without being asked.", None),
    (("office_manager",), "Nora sorts the four bin streams for the morning, checks the snack shelf, and rattles her oversized ring of keys.", None),
    (("tech_lead",), "Brody reads an old technical manual at his desk by the light of the latency graph, turning pages very slowly.", None),
    (("ceo",), "Graham ties up a ticket comment on his laptop in the Glass Box, glances at the dark street, and decides it can wait for morning.", None),
    (("office_manager", "engineer"), "Theo has stayed past lights-out; Nora, gently insistent, wants the Open Floor emptied so she can finish the floors.", None),
    (("party_member", "tester"), "Owen checks a red light on the Tester's spare laptop, aware of the Party Member's corner behind him and the emergency light overhead.", "03:00"),
    (("office_manager",), "At the Office Manager's station Nora checks the breaker panel and the first-aid kit, ticking items off a list in pencil.", None),
    (("analyst",), "Maren sorts printouts into labelled piles on her desk, three colours of tab, in the quiet.", None),
    (("engineer", "marketing"), "Theo and Julian run into each other in the dark kitchen, both after a snack from the shelf, both trying to sound like they are leaving soon.", None),
    (("tech_lead", "tester"), "Brody stops by Owen's desk to watch the overnight suite run. Neither says much. A green line scrolls.", None),
    (("marketing",), "Julian waters nothing, because the plant on his desk is the one nobody else may water, and adjusts its pot a quarter turn.", None),
    (("office_manager",), "Nora mops the kitchen floor in long even strokes. The coffee machine ticks as it cools. The servers hum through the wall.", "00:40"),
    (("office_manager", "tech_lead"), "Brody lingers by the kitchen door while Nora makes him tea. They share a long, comfortable silence about nothing in particular.", None),
    (("engineer",), "Theo sits in the Small Room with the door shut, trying out an explanation of 'what the rule actually does' for tomorrow's lunch.", None),
    (("analyst", "party_member"), "Maren pauses at the corner desk to pick up a dropped printout and sees the closed notebook and the pen exactly where they always are.", None),
    (("tester", "office_manager"), "Nora brings Owen a double shot at his desk; he thanks her with a number of passes.", "04:30"),
    (("ceo", "tech_lead"), "Graham and Brody share the Glass Box doorway late, Graham asking about the dock and Brody answering in very few words.", None),
    (("office_manager",), "Nora checks the keys, switches off the last lamp and stands a moment looking at the dark floor from reception before going.", None),
    (("tech_lead",), "Brody taps the latency graph twice before he sits down, even now, with nobody to see.", None),
    (("engineer", "party_member"), "Theo, working late, catches himself glancing at the corner desk and keeps his screen angled away from the room.", None),
    (("office_manager",), "Nora puts the lights on in the kitchen only, boils a kettle, and reads the laminated values card by the door.", "05:15"),
    (("office_manager",), "Nora is already at the kitchen counter in the grey before dawn, sleeves pushed up, the coffee machine just coming to temperature.", "05:35"),
    (("office_manager", "party_member"), "The first grey light shows in the windows. Nora sets out the day's cups; the one for the corner desk goes down last.", "05:45"),
    (("tech_lead", "office_manager"), "Before six the Server Closet door is propped open; Brody and Nora go over the grey box with a torch, the way they have done before. It only hums.", None),
    (("analyst",), "Maren does her crossword in ink at the Open Floor window, the first hint of morning on the street behind her.", "05:20"),
    (("tester",), "Owen reads the end of the overnight run results off the spare laptop and writes the totals on a card.", "05:10"),
    (("marketing",), "Julian, in early, starts a draft release note at his desk for something that does not exist yet.", "05:50"),
    (("engineer", "tester"), "Owen reads the overnight totals to Theo, who offers a joke and gets a number back.", "05:40"),
    (("ceo", "party_member"), "Graham is early, alone in the Glass Box with the first coffee in his hands, aware of the corner desk through the glass.", "05:55"),
    (("office_manager",), "Nora labels the odd-looking jar in the fridge, tapes a note to the door, and redoes the fridge shelf order.", None),
    (("analyst", "office_manager"), "Maren's oat milk carton is in the wrong place in the fridge. Nora has already moved it back without saying a word.", None),
    (("engineer",), "Theo practises a new analogy for 'what it actually does': a bouncer at a door, then a ferry, then a lift.", None),
    (("marketing", "tech_lead"), "Julian rehearses a question aloud in the dark kitchen before taking it to Brody's window desk in the morning.", None),
]
MOR = [
    (("tech_lead", "engineer"), "Brody taps the latency graph twice and sits. Theo, across from him, has a question he has already half asked.", "06:25"),
    (("office_manager", "ceo", "tech_lead"), "Nora hands the first cup to Graham, black, as always, and the second sits waiting for Brody. She notes who has already taken theirs.", "06:20"),
    (("marketing", "analyst"), "Julian reads a tagline aloud for the draft release note; Maren hears it with a pencil, and asks for whom it is meant.", "07:00"),
    (("analyst",), "Maren writes 'For whom?' in red ink at the top of a clean page and stares at it, pen tapping, deciding where to begin.", "06:40"),
    (("tester", "tech_lead"), "Owen reports the overnight suite to Brody: a number, then another number, no adjective.", "06:30"),
    (("engineer",), "Theo writes 'easy' on a sticky note, stares at it, crosses it out, and writes a bigger estimate beneath.", "06:50"),
    (("engineer", "marketing"), "Over the snack shelf Julian asks Theo what the velocity rule actually does; Theo reaches for a new analogy.", "07:30"),
    (("analyst", "tech_lead"), "Maren and Brody trade a polite remark about whether docs/requirements or docs/design should come first today. Neither raises a voice.", "07:15"),
    (("office_manager",), "Nora tapes a short notice to the kitchen door about fridge labels and smooths it flat with both hands.", "06:35"),
    (("ceo", "tech_lead"), "Graham, tickets open on his laptop, asks Brody what is holding things up at the dock. Brody takes his time answering.", "07:40"),
    (("analyst", "office_manager"), "A formal email from Corvane arrives. Maren prints it and Nora, who owns the printer, retrieves it and hands it over.", "07:20"),
    (("analyst",), "Maren says Pellbridge phoned her desk again about a REVIEW reason that did not make sense, and sets about explaining it.", "08:10"),
    (("engineer", "tester"), "An Ostry ticket lands in minutes with a loud subject line. Theo reads it out to Owen, who asks for the number.", "08:00"),
    (("office_manager", "engineer"), "Nora restocks the snack shelf. Theo, hovering, gets whatever is left and two biscuits.", "06:45"),
    (("tester", "engineer"), "Owen hands Theo a printed test request with a number on it. Theo makes a joke. Owen waits for the second joke.", "08:20"),
    (("tech_lead", "engineer"), "Theo suggests an easy change; Brody says 'we tried that in year three' and lets that sit.", "08:05"),
    (("analyst", "marketing"), "In the Moonwell room Maren pins a diagram to the cork wall while Julian waits for the screen, which takes two minutes to wake.", "08:30"),
    (("ceo",), "Graham walks the Open Floor with his coffee, checking in on the dock, one desk at a time, the folded card in his pocket.", "08:40"),
    (("office_manager", "party_member"), "Nora pours a cup for the corner desk, as she always does, and leaves it where it will stay untouched.", "06:55"),
    (("marketing",), "Julian rewrites yesterday's release note before anyone asks, plays with an opening line, and mouths it silently to the plant.", "07:05"),
    (("tech_lead", "analyst"), "Brody reads Maren's plan standing up, slowly, with his coffee, while she watches his eyes move.", "08:15"),
    (("tester", "office_manager"), "Nora delivers Owen's double shot with the overnight results folded beside it; he thanks her with a count.", "06:30"),
    (("engineer", "party_member"), "Theo, typing, catches the Party Member turned slightly toward his screen across the room and quietly closes a tab.", "07:50"),
    (("ceo", "marketing"), "Graham and Julian talk through a client story at Julian's desk, Graham nodding, Julian drawing a customer at a till in the air with one hand.", "08:25"),
    (("office_manager", "tech_lead"), "Nora brings Brody his second cup, black with one sugar, without asking, and they discuss the weather on the stairs.", "06:15"),
    (("analyst", "engineer"), "Maren asks Theo to explain one line in last week's change. He does it with a lot of 'and then's.", "07:45"),
    (("marketing", "office_manager"), "Julian checks his pipeline whiteboard against the post tray while Nora lines up the day's letters, pointedly.", "08:35"),
    (("tech_lead",), "Brody stands at the window with the first light on the street, the longest-serving person on the floor, quiet.", "06:10"),
    (("tester",), "Owen rereads the overnight run line by line and writes totals in his notebook of numbers. Fourteen of fifteen again.", "06:20"),
    (("ceo", "analyst"), "Graham asks Maren to say what done looks like in one sentence, and then to say it again slower.", "07:55"),
    (("engineer", "tester", "tech_lead"), "Three desks in a row: Brody reading, Theo typing, Owen announcing the first small result of the day.", "08:45"),
    (("marketing", "engineer"), "Julian and Theo try to agree on a single word for what a REVIEW verdict means to a person at a till.", "08:10"),
    (("office_manager",), "Nora checks the Moonwell room: eight chairs squared, the cork wall pins counted, the screen switched on early.", "06:50"),
    (("analyst",), "Maren fixes a printout's tab colours, then looks at the clock above the Glass Box and starts the first numbered list of the day.", "08:50"),
    (("tech_lead", "office_manager", "ceo"), "Graham, Brody and Nora meet by the coffee machine; it hisses, and none of them quite say what they came for.", "07:35"),
    (("marketing", "analyst"), "Julian asks Maren for a client quote he can use. She asks which client and what he plans to promise with it.", "08:00"),
]
WRK = [
    (("engineer", "tester"), "Theo finishes a pull request and sends Owen a test request with a joke on it. Owen replies with a count.", None),
    (("tech_lead", "engineer"), "Brody reads Theo's pull request on the left monitor, slowly, with the latency graph on the right.", None),
    (("analyst", "marketing"), "Julian wants a bold sentence for the website; Maren wants to know what the system actually promises first.", None),
    (("ceo", "analyst", "tech_lead"), "In the Moonwell room Graham asks for one sentence from each of Maren and Brody about the afternoon's plan, then waits.", None),
    (("engineer", "marketing", "tester"), "Lunch at the old kitchen table: Julian asks Theo what it actually does and Owen supplies the number. Nobody mentions the latency graph.", "13:00"),
    (("office_manager",), "Nora prunes stale branches from the repository on her laptop, deleting merged ones carefully and leaving the learned-scoring branch alone.", None),
    (("tech_lead", "analyst"), "Brody and Maren mark up the same document in two ink colours at his desk, politely arguing over a heading.", None),
    (("tester", "engineer"), "Owen files a bug about the amount-outlier rule on new accounts. Theo says it is flaky, not broken.", None),
    (("marketing", "ceo"), "Julian pitches a new line for the release note to Graham across the Glass Box desk. Graham asks if it will play in Malmont.", None),
    (("analyst", "office_manager"), "Maren asks Nora to retrieve a client file from the post tray cabinet; Nora asks for the colour of the folder.", None),
    (("tech_lead",), "Brody examines the latency graph with his head tilted, finds nothing, and looks again.", None),
    (("engineer",), "Theo tries to explain COUNTRY_MISMATCH to himself aloud with a ferry analogy and gets stuck at the harbour.", None),
    (("ceo", "marketing"), "Graham and Julian rehearse an opening for a call with a bank, Graham asking 'Fair?' at the end of every sentence.", None),
    (("tester",), "Owen reruns one test five times, writing the result of each run on a card in a neat column.", None),
    (("office_manager", "marketing"), "Nora asks Julian to keep the pipeline whiteboard off the kitchen wall. He promises, and the whiteboard stays.", None),
    (("analyst", "engineer"), "Maren asks Theo for a plain-language reason for a DECLINE and he writes three, each shorter than the last.", None),
    (("tech_lead", "tester"), "Brody stops at Owen's desk to ask for the suite's number before approving anything. He receives it.", None),
    (("engineer", "tech_lead", "tester"), "A small change moves through review: Theo submits, Owen runs, Brody reads. Everyone waits on the last step.", None),
    (("ceo",), "Graham reads a folded index card at his desk in the Glass Box and tries a logistics metaphor on himself for the fourth time.", None),
    (("marketing",), "Julian rearranges the pipeline whiteboard, moving Ostry Mercantile to 'stable' again.", None),
    (("office_manager", "party_member"), "Nora vacuums around the Party Member's corner desk, careful not to touch anything on it, as the afternoon light crosses the floor.", "15:00"),
    (("analyst",), "Maren phones Pellbridge from the Small Room, writes numbered points on a pad, and closes the door against the half-hearing floor.", None),
    (("tech_lead", "ceo"), "Brody tells Graham about year three, in four sentences. Graham asks what the hold-up at the dock is now.", None),
    (("engineer", "analyst", "marketing"), "Over a shared document, Theo, Maren and Julian argue gently about whether a reason code needs translating.", None),
    (("tester", "office_manager"), "Nora delivers a fresh double shot to Owen's spare laptop. He logs it as '1' on a card, deadpan.", None),
    (("ceo", "office_manager"), "Graham asks Nora where the stock of printer paper is kept. She answers with a list of three places and one rule.", None),
    (("tech_lead", "engineer"), "Theo asks Brody about an old function; Brody says 'Hm' and answers with the history of why it exists.", None),
    (("marketing", "tester"), "Julian asks Owen what a green run means to a buyer. Owen answers with a number and no adjective.", None),
    (("ceo", "tech_lead", "marketing"), "A short stand-around at the Glass Box about when a bank would see the next release note.", None),
    (("office_manager",), "Nora labels the fridge shelves in her careful hand, frowning at a jar without a name.", None),
    (("analyst", "tester"), "Maren hands Owen a list of numbered acceptance points; he reads them twice and answers in numbers.", None),
    (("engineer",), "Theo stubs out a function, crosses out 'easy' on a sticky note, writes 'three days', and then 'two days if no one asks questions'.", None),
    (("tech_lead", "analyst", "engineer"), "Maren and Brody both look at the same line of Theo's work from opposite sides; Theo watches them watch.", None),
    (("ceo", "analyst"), "Graham asks Maren to translate a bank's wording into plain terms; she restates it in her own numbered way.", None),
    (("office_manager", "tech_lead"), "Nora and Brody share a quiet word in the kitchen about the bins and the learned-scoring branch, saying very little.", None),
    (("marketing", "office_manager"), "Julian asks Nora for a quote of the company values card for the website; she dictates the fourth value twice.", None),
    (("tester",), "Owen runs the full suite once and stares at the result for a long beat before writing a number on a card.", None),
    (("engineer", "tester", "party_member"), "Theo and Owen sit back to back at the long desk, a test running; the Party Member stands near the spare laptop, looking at no screen in particular.", None),
    (("tech_lead",), "Brody paces slowly down the line of desks, looking at screens without speaking, then goes back to his window desk.", None),
    (("analyst",), "Maren pencils margin notes on a Corvane requirements printout while the afternoon light moves across her desk.", None),
    (("ceo", "engineer"), "Graham drops by Theo's desk to ask how the dock looks; Theo gives him the cheerful version.", None),
    (("marketing", "analyst", "engineer"), "Julian asks, again, for a one-line description of what a rule does; two answers arrive at once and conflict.", None),
    (("office_manager", "analyst"), "Nora tidies the Moonwell room after a meeting, squaring eight chairs, while Maren collects her last printouts.", "14:30"),
    (("tech_lead", "marketing"), "Julian walks to the window desk with a rehearsed question, loses his place, and begins again.", None),
    (("engineer",), "Theo carries a laptop to the Server Closet and stands in the cold hum plugging a cable into the staging rack.", None),
    (("ceo", "tester"), "Graham asks Owen for the suite's number; Owen gives it and watches Graham not know what to do with it.", None),
    (("analyst", "tech_lead", "ceo"), "A three-way debate in the Glass Box about wording on a requirement. The blinds, as ever, stay open.", None),
    (("office_manager",), "Nora tries the roof key in a door she has never used it on. It does not fit. She drops it back on the ring.", None),
    (("tester", "engineer"), "Owen waits for Theo's pull request, deadpan, arms folded; Theo is mid-sentence on something else.", None),
    (("marketing",), "Julian practises a pitch to the plant on his desk, tries it a second time slower, and writes down what he said.", None),
    (("tech_lead", "office_manager"), "Brody and Nora, at the end of a long afternoon, share a pot of tea and the kind of quiet two long-timers share.", "16:00"),
    (("ceo", "marketing", "analyst"), "A short huddle by Julian's whiteboard: Graham points at Ostry, Maren raises an eyebrow, Julian says 'stable'.", None),
    (("analyst",), "Maren reads aloud, to herself, the numbered points of a plan she is about to hand over, and rearranges the third.", None),
    (("engineer", "tester"), "Theo and Owen eat sandwiches at their desks on a slow afternoon, the suite running between them.", "12:30"),
    (("office_manager", "engineer"), "Nora asks Theo to take a dead cable out of the kitchen and he tries to find a bin for it in the right stream.", None),
    (("tech_lead", "engineer", "analyst"), "A pull request, a requirement and a design doc sit open on three desks. Brody reads the middle one first.", None),
    (("marketing", "ceo"), "Julian drafts a one-page sheet for a prospect while Graham reads it over his shoulder and adds a logistics metaphor.", None),
    (("tester",), "Owen sits with the spare laptop, watches one test fail for the third time, and writes a precise note about it.", None),
    (("office_manager", "party_member", "tech_lead"), "Nora and Brody discuss lunch rotation in low voices beside the corner desk. The Party Member sits as he always sits.", "12:45"),
    (("ceo",), "Graham stands at the Glass Box window, coffee gone cold, and rehearses a line for the board in his head.", None),
    (("engineer", "marketing"), "Julian sits on the edge of Theo's desk and learns a new analogy: a ferry, a bouncer, a lift.", None),
]
EVE = [
    (("tester", "tech_lead", "engineer"), "Owen announces the 18:00 full run with a single word. The three of them watch the suite begin.", "18:00"),
    (("tech_lead",), "Brody reviews the day's last pull request by lamplight and hesitates over the merge button.", "19:30"),
    (("marketing",), "Julian rewrites the release note he drafted at seven, crossing out the tagline and writing a plainer one.", "18:40"),
    (("ceo",), "Graham reads open tickets aloud one by one in the Glass Box, closing the ones that are done and leaving a comment on the rest.", "23:00"),
    (("office_manager",), "At 23:30 Nora takes the bins out in their four streams, wipes the kitchen table and turns the coffee machine off.", "23:30"),
    (("office_manager", "engineer"), "Nora asks Theo, politely, whether he will be leaving soon. He says 'a few more minutes' and means it for the third time.", "23:45"),
    (("office_manager", "party_member"), "At 23:30 Nora clears the untouched cup from the corner desk and washes it. The Party Member's chair is exactly where it was.", "23:30"),
    (("engineer", "tester"), "Theo and Owen wait for the last full run of the evening; Theo makes tea, Owen counts.", "21:15"),
    (("analyst",), "Maren finishes a plan note in green ink for the end of the day and tidies the printouts into piles.", "20:45"),
    (("tech_lead", "ceo"), "Brody walks Graham through the day's merges without mentioning the latency graph once.", "22:10"),
    (("marketing", "ceo"), "Julian reads Graham the finished release note while Graham stares at the ceiling, nodding on every second sentence.", "22:20"),
    (("engineer",), "Theo packs his bag, unpacks it, and sits down again to finish the one thing he said he would finish.", "22:40"),
    (("tester", "office_manager"), "Nora brings Owen a double shot to see him through the late run, and checks the clock above the Glass Box.", "20:10"),
    (("analyst", "tech_lead"), "Maren and Brody share the end-of-day lull at the window, mostly in silence, glasses of water in hand.", "21:30"),
    (("ceo", "office_manager"), "Graham tells Nora he's staying until the last ticket closes; Nora lists three reasons he should go home.", "23:15"),
    (("tech_lead", "tester"), "Brody waits at Owen's desk for the last full run to finish before he will approve a merge.", "19:00"),
    (("office_manager",), "Nora stocks tomorrow's snack shelf and sets the first cups out, for the morning she will be first to see.", "21:00"),
    (("engineer", "marketing"), "Theo and Julian stay for a late supper of crisps from the snack shelf and rehearse their lunch question.", "20:30"),
    (("office_manager", "tech_lead"), "At 23:30 Nora and Brody are the last pair on the floor: she tidies the branches, he taps the graph twice.", "23:35"),
    (("analyst", "marketing"), "Julian asks Maren to look over a client one-pager; she reads it with a pencil and returns it with footnotes.", "19:45"),
    (("ceo", "engineer", "tester"), "Graham stops by the two desks at the end of the day to ask what the dock looks like; Theo jokes and Owen counts.", "22:00"),
    (("office_manager", "analyst"), "Nora brings Maren her labelled oat milk carton at her desk, as the floor winds down.", "21:45"),
    (("tech_lead",), "Brody looks at the graph one last time, taps it twice, and puts on his coat; then takes it off again.", "23:20"),
    (("marketing",), "Julian writes the last line of the day's release note on a sticky note and presses it flat on the whiteboard.", "23:10"),
    (("office_manager", "ceo"), "Nora offers Graham the last of the pastries; he thanks her like a man who suspects he has been seen through.", "22:50"),
    (("tester",), "Owen reads out the last result of the day to the empty floor, then writes it down. A single number.", "23:25"),
    (("engineer", "tech_lead"), "A last small merge goes through while Theo watches Brody's face for any verdict at all.", "21:20"),
    (("analyst", "ceo", "party_member"), "Maren and Graham talk through tomorrow's reading list at the Glass Box. The Party Member stays in his corner, still.", "22:30"),
    (("office_manager",), "Nora checks the windows along the street side, one by one, and tries the lift before she says goodnight to the stairwell.", "23:40"),
    (("marketing", "tester"), "Julian reads Owen the release note, and Owen replies with the count of the words in it.", "20:20"),
    (("ceo",), "Graham stands in the Glass Box with the lights low and the index card unfolded, rehearsing tomorrow's first sentence to himself.", "23:05"),
    (("engineer",), "Theo tidies the sticky notes on his monitor, the crossed-out 'easy' ones in a neat stack.", "23:00"),
]
# The seam wants 2-4 seats for normal-intensity scenes; single-person hooks get a
# deterministic companion who is "nearby" so the scene still has someone to talk to.
COMPANIONS = {
    "ceo": ("office_manager", "tech_lead"), "tech_lead": ("engineer", "office_manager"),
    "analyst": ("ceo", "marketing"), "engineer": ("tester", "marketing"),
    "tester": ("engineer", "tech_lead"), "marketing": ("analyst", "ceo"),
    "office_manager": ("tech_lead", "engineer"),
}


def _with_companions(phase, hooks):
    if phase == "off_hours":
        return list(hooks)
    out = []
    for n, (seats, hint, clock) in enumerate(hooks):
        if len([s for s in seats if s != "party_member"]) == 1 and len(seats) == 1:
            c = COMPANIONS[seats[0]][n % 2]
            seats = seats + (c,)
            who = NAMES[c][0]
            hint = hint + f" {who} is within earshot at a nearby desk or doorway and could be drawn in."
        out.append((seats, hint, clock))
    return out


HOOKS = {"off_hours": OFF, "morning": _with_companions("morning", MOR),
         "workday": _with_companions("workday", WRK), "evening": _with_companions("evening", EVE)}

# --------------------------------------------------------------- model prompt
SYSTEM = """You write one-paragraph SITUATION cards for a slow-burn workplace drama set at Ashiorid, a small financial-software company with one product, Fraud-Stop, on the third floor of a brick building on Tallow Street. Eight people work there, 06:00 to 00:00, every day, and think that is normal.

Places: Reception; the Glass Box (CEO's glass-walled office); the Moonwell room (meeting room with a cork wall and a slow screen); the Open Floor (six desks, the latency graph on Brody's second monitor); the Party Member's corner desk by the fire exit (one chair, one closed notebook, one pen, no computer, emergency light stays on); the Kitchen (old table, temperamental espresso machine, labelled fridge, snack shelf, four bin streams); Nora's station; the Small Room (phone booth); the Server Closet (cold, humming, one unlabelled grey box with a green light).
Clients: Corvane National Bank, Malmont Savings & Trust, Pellbridge Credit Union, Ostry Mercantile; prospect Halvard & Sons.
Habits: Brody taps the latency graph twice before sitting. Owen announces results as numbers. Theo writes 'easy' on a sticky note then crosses it out. Maren starts plans with 'For whom?' in red ink. Julian tries taglines out loud. Nora keeps the floor in order and the keys. Graham reads a folded index card and speaks in logistics metaphors. Lunch is at the kitchen table around 13:00.

Your card is read by improvising performers who then play the scene live. So write a SITUATION, not a script:
- 55 to 90 words, one paragraph, present tense, third person.
- Name the place, the time-feel and what each present person is physically doing or wanting right now. Use first names or titles (Brody, Nora, the Party Member).
- End on a small open question or tension that gives them something to talk about for a few minutes.
- NO quoted speech, NO dialogue, NO stage directions in brackets.
- Everyday and low-stakes only. Nothing gets decided, found, revealed, fixed, announced, signed, resolved, hired or fired. No bank wins or loses. Nothing that changes the story.
- Mention ONLY the people listed as present. Invent no new people or names. The Party Member is silent: he may be present and watched, but never speaks, eats, drinks, moves from the corner or opens the notebook.
- Never use these words: stream, Twitch, AI, viewers, chat, model, agent, prompt, character, episode, audience, bot, script, simulation. Treat it all as real life.

Reply with ONLY a JSON object: {"title": "<2-5 word title>", "prompt": "<the card>"}"""

EXAMPLE_USER = ("Day phase: the working day (09:00-17:00). Local time about 10:30.\nPresent: Theo Palliser, the Engineer; Owen Hask, the Tester.\n"
                "Seed: a change goes to test and both are waiting on a flaky result.")
EXAMPLE_ASSISTANT = ('{"title": "Flaky, Not Broken", "prompt": "Mid-morning on the Open Floor. Theo has pushed a small change and is perched on the corner of Owen\'s desk, '
                     'insisting the failing test is flaky, not broken. Owen has already rerun it twice and has the numbers in a neat column on a card. Neither wants to be the first to say '
                     'what a third red run would mean. Theo keeps reaching for a joke; Owen keeps waiting. Is a bad test a bad test, or just an unlucky one?"}')
EXAMPLE_TEXTS = [json.loads(EXAMPLE_ASSISTANT)["prompt"]]


# ----------------------------------------------------------------------- util
def now_iso():
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9']+", text.lower()))


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if (a or b) else 0.0


def slugify(title: str, maxwords=5, maxlen=34) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    s = "-".join(s.split("-")[:maxwords])[:maxlen].strip("-")
    return s or "scene"


def normalise(text: str) -> str:
    t = text.replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"')
    t = t.replace("\u2014", ", ").replace("\u2013", "-").replace("\u2026", "...")
    t = re.sub(r"[*_`#>]+", "", t)
    t = re.sub(r"\s+", " ", t).strip().strip('"').strip()
    t = re.sub(r"^(prompt|scene|situation)\s*:\s*", "", t, flags=re.I)
    return t


def sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text) if s]


def lore_stems_available() -> set[str]:
    return {p.stem for p in (PACK / "lore").glob("*.md")}


def derive_lore(prompt: str) -> list[str]:
    avail = lore_stems_available()
    out = []
    if re.search(r"\b(Corvane|Malmont|Pellbridge|Ostry|Halvard)\b", prompt):
        out.append("clients")
    if re.search(r"\b(Moonwell|Glass Box|Open Floor|Kitchen|Server Closet|Small Room|Reception|corner desk|fire exit|kitchen)\b", prompt):
        out.append("office")
    if re.search(r"\b(standup|lunch|bins|values card|release note|full run|lights|Friday|pastr\w+|snack)\b", prompt, re.I):
        out.append("rituals")
    if re.search(r"\b(velocity|VELOCITY|AMOUNT_OUTLIER|COUNTRY_MISMATCH|REVIEW|DECLINE|latency|verdict|score|rule)\b", prompt):
        out.append("product")
    return [s for s in out if s in avail][:3]


# ------------------------------------------------------------------------ gate
class State:
    """Accepted-so-far state, shared across worker threads under `lock`."""
    def __init__(self):
        self.lock = threading.Lock()
        self.prompts: dict[str, set] = {}      # id -> token set
        self.prompt_text: dict[str, str] = {}
        self.titles: set[str] = set()
        self.openings: set[str] = set()
        self.arrivals = 0
        self.slots_done: dict[str, set[int]] = {p: set() for p in PHASES}
        self.hooks_used: dict[str, set[int]] = {p: set() for p in PHASES}
        self.hook_tries: dict[tuple, int] = collections.Counter()
        self.reject_hist: collections.Counter = collections.Counter()
        self.accepted_this_run = 0
        self.tokens_used = 0

    def add(self, sid, title, prompt, arrival):
        self.prompts[sid] = tokens(prompt)
        self.prompt_text[sid] = prompt
        self.titles.add(title.lower())
        self.openings.add(" ".join(prompt.lower().split()[:5]))
        if arrival:
            self.arrivals += 1


def is_arrival(title: str, prompt: str) -> bool:
    return bool(ARRIVAL.search(title) or ARRIVAL.search(prompt))


def gate(title: str, prompt: str, phase: str, seats: tuple, state: State, check_unique=True) -> list[str]:
    """Return list of reject reasons (empty = accepted).  Pure checks, no I/O."""
    r: list[str] = []
    if not title or not prompt:
        return ["empty_field"]
    wc = len(prompt.split())
    if wc < WORDS_MIN:
        r.append(f"words_lt{WORDS_MIN}")
    if wc > WORDS_MAX:
        r.append(f"words_gt{WORDS_MAX}")
    full = f"{title}. {prompt}"
    for m in BANNED_WORDS.finditer(full):
        r.append(f"out_of_fiction:{m.group(0).lower()}")
    for m in PLOT_WORDS.finditer(full):
        r.append(f"plot_term:{m.group(0).lower()}")
    for s in sentences(full):
        if CLIENT_WORDS.search(s) and CLIENT_OUTCOME.search(s):
            r.append("client_outcome:" + CLIENT_OUTCOME.search(s).group(0).lower())
        if re.search(TITLE_RE["party_member"], s):
            if PM_SPEECH.search(s):
                r.append("party_member_speaks")
            if PM_ACT.search(s):
                r.append("party_member_acts:" + PM_ACT.search(s).group(0).lower())
        if re.search(r"\bnotebook\b", s, re.I) and NOTEBOOK_OPEN.search(s):
            r.append("notebook_opened")
    # dialogue / script shape
    if SPEAKER_LABEL.search(prompt):
        r.append("speaker_label")
    for q in re.findall(r'"([^"]*)"', prompt):
        if len(q.split()) > 8:
            r.append("long_quote")
    if prompt.count('"') % 2:
        r.append("unbalanced_quote")
    if re.search(r"[\[\]{}<>]", prompt):
        r.append("bracket_markup")
    # cast discipline
    for sid in ALL_SEATS:
        if sid in seats:
            continue
        hit = None
        if sid in NAMES:
            for nm in NAMES[sid]:
                if re.search(rf"\b{nm}\b", full):
                    hit = nm
        if re.search(TITLE_RE[sid], full):
            hit = hit or sid
        if hit:
            r.append(f"offseat_cast:{hit}")
    for sid in seats:
        if sid == "party_member":
            continue
        if not (any(re.search(rf"\b{nm}\b", prompt) for nm in NAMES[sid]) or re.search(TITLE_RE[sid], prompt)):
            r.append(f"seat_unmentioned:{sid}")
    for w in FORBIDDEN_NAMES:
        if re.search(rf"\b{w}\b", full):
            r.append(f"forbidden_name:{w}")
    for sent in sentences(prompt):
        toks = list(re.finditer(r"\b[A-Z][A-Za-z_'\-]*", sent))
        for i, m in enumerate(toks):
            if m.start() == 0:      # sentence-initial: cannot tell a name from a word
                continue
            w = m.group(0).rstrip("'-")
            w2 = re.sub(r"'s$", "", w)
            if w2 not in ALLOWED_CAPS and w2.split("-")[0] not in ALLOWED_CAPS:
                r.append(f"unknown_name:{w2}")
    # clock vs phase
    lo, hi = WINDOW[phase]
    for h, mnt in TIME_RE.findall(prompt):
        mod = int(h) * 60 + int(mnt)
        if not (lo <= mod <= hi):
            r.append(f"time_outside_phase:{h}:{mnt}")
    # uniqueness
    if check_unique:
        tk = tokens(prompt)
        worst, worst_id = 0.0, None
        for sid, t in state.prompts.items():
            j = jaccard(tk, t)
            if j > worst:
                worst, worst_id = j, sid
        for ex in EXAMPLE_TEXTS:
            j = jaccard(tk, tokens(ex))
            if j > worst:
                worst, worst_id = j, "<example>"
        if worst > JACCARD_MAX:
            r.append(f"jaccard>{JACCARD_MAX}")
        if title.lower() in state.titles:
            r.append("title_dup")
        if " ".join(prompt.lower().split()[:5]) in state.openings:
            r.append("same_opening")
        if is_arrival(title, prompt) and state.arrivals >= MAX_ARRIVALS:
            r.append("arrival_cap")
    # dedupe reasons, preserve order
    return list(dict.fromkeys(r))


# ----------------------------------------------------------------- LLM client
class AuthError(RuntimeError):
    pass


def load_key() -> str:
    if os.environ.get("AMBIENT_LLM_KEY"):          # explicit override beats the env file
        return os.environ["AMBIENT_LLM_KEY"]
    for line in ENV_FILE.read_text().splitlines():
        if line.startswith("VLLM_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("VLLM_API_KEY not found in env file")


class LLM:
    def __init__(self):
        self.key = load_key()

    def _req(self, path, payload=None, timeout=300):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(BASE_URL + path, data=data,
                                     headers={"Authorization": "Bearer " + self.key,
                                              "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())

    def wait_ready(self, minutes: float, stop: threading.Event) -> bool:
        end = time.time() + minutes * 60
        auth_fail, warned = None, False
        while time.time() < end and not stop.is_set():
            try:
                d = self._req("/models", timeout=10)
                if any(m.get("id") == MODEL for m in d.get("data", [])):
                    return True
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):
                    # keep polling, re-reading the key each time so a corrected env file / override
                    # is picked up without a restart; give up (AuthError) only at the deadline
                    auth_fail = f"server rejected the API key (HTTP {e.code}); the key from {ENV_FILE.name} / AMBIENT_LLM_KEY does not match the running container"
                    if not warned:
                        print("WARNING: " + auth_fail + " - will keep retrying", flush=True)
                        warned = True
                    try:
                        self.key = load_key()
                    except Exception:
                        pass
            except Exception:
                pass
            time.sleep(10)
        if auth_fail:
            raise AuthError(auth_fail)
        return False

    def chat(self, user: str, temperature=0.8, max_tokens=400):
        payload = {"model": MODEL, "temperature": temperature, "top_p": 0.95, "presence_penalty": 0.3,
                   "max_tokens": max_tokens,
                   "messages": [{"role": "system", "content": SYSTEM},
                                {"role": "user", "content": EXAMPLE_USER},
                                {"role": "assistant", "content": EXAMPLE_ASSISTANT},
                                {"role": "user", "content": user}]}
        d = self._req("/chat/completions", payload)
        return d["choices"][0]["message"]["content"], d.get("usage", {}).get("total_tokens", 0)


def parse_reply(text: str):
    """Defensive: JSON object, fenced JSON, or 'title:/prompt:' lines."""
    t = text.strip()
    t = re.sub(r"^```(?:json|yaml)?\s*|\s*```$", "", t, flags=re.S).strip()
    m = re.search(r"\{.*\}", t, re.S)
    if m:
        try:
            d = json.loads(m.group(0))
            if isinstance(d, dict):
                return str(d.get("title", "")).strip(), str(d.get("prompt", "")).strip()
        except Exception:
            pass
    tm = re.search(r'"?title"?\s*[:=]\s*"?([^"\n]+)"?', t, re.I)
    pm = re.search(r'"?prompt"?\s*[:=]\s*"?(.+?)"?\s*[,}]?\s*$', t, re.I | re.S)
    if tm and pm:
        return tm.group(1).strip().rstrip(","), pm.group(1).strip()
    try:
        d = yaml.safe_load(t)
        if isinstance(d, dict) and "prompt" in d:
            return str(d.get("title", "")).strip(), str(d["prompt"]).strip()
    except Exception:
        pass
    return None, None


# ---------------------------------------------------------------- file output
def jl_append(path: pathlib.Path, obj: dict, lock: threading.Lock):
    with lock:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")


FILE_LOCK = threading.Lock()


def write_scene(phase, slot, sid, title, prompt, seats, run_id, lore) -> pathlib.Path:
    d = STAGE / phase
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{slot:02d}-{sid}.yaml"
    doc = {
        "id": sid, "title": title, "ambient": True, "prompt": prompt, "lore": lore,
        "day_phase": phase,
        "intensity": "quiet" if phase == "off_hours" else "normal",
        "seats": list(seats),
        "source": {"authored": "generated", "model": MODEL, "run_id": run_id,
                   "batch": f"{phase}.{(slot - 1) // 10}"},
    }
    header = ("# Ambient office scene - a plot-free situation. No decisions, no reveals.\n"
              "# STAGED, not promoted. Seats/phase/intensity set by the author script.\n")
    body = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=100, default_flow_style=False)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(header + body, encoding="utf-8")
    tmp.replace(path)
    return path


def load_existing(state: State):
    """Reload staged scenes so the run is resumable."""
    for phase in PHASES:
        d = STAGE / phase
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.yaml")):
            m = re.match(r"(\d+)-", f.name)
            try:
                doc = yaml.safe_load(f.read_text())
            except Exception:
                continue
            if not m or not isinstance(doc, dict):
                continue
            state.slots_done[phase].add(int(m.group(1)))
            state.add(doc["id"], doc.get("title", ""), doc.get("prompt", ""),
                      is_arrival(doc.get("title", ""), doc.get("prompt", "")))
    if MANIFEST.exists():
        for line in MANIFEST.read_text().splitlines():
            try:
                e = json.loads(line)
            except Exception:
                continue
            if e.get("event") == "accepted" and "hook" in e and e["slot"] in state.slots_done[e["phase"]]:
                state.hooks_used[e["phase"]].add(e["hook"])


# ------------------------------------------------------------------- the run
def build_user(phase, seats, hint, clock) -> str:
    present = "; ".join(FULL[s] for s in seats)
    return (f"Day phase: {PHASE_LABEL[phase]}. Local time about {clock}.\n"
            f"Present: {present}.\nSeed: {hint}\n"
            "Write the card as a JSON object. Reword the seed in your own words; do not copy it.")


def random_clock(phase, rng):
    lo, hi = WINDOW[phase]
    m = rng.randint(lo + 5, min(hi, lo + 5 + (hi - lo)) - 5)
    return f"{m // 60:02d}:{m % 60:02d}"


def pick_hook(state: State, phase: str, order: list[int], quiet=False):
    """Least-tried unused hook (call under lock)."""
    unused = [i for i in order if i not in state.hooks_used[phase]]
    pool = unused or order
    pool = sorted(pool, key=lambda i: (state.hook_tries[(phase, i)], order.index(i)))
    i = pool[0]
    state.hook_tries[(phase, i)] += 1
    return i


def author(args):
    STAGE.mkdir(parents=True, exist_ok=True)
    stop = threading.Event()
    for sg in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sg, lambda *_: stop.set())
    fh = open(RUNLOG, "a", buffering=1)

    def log(msg):
        line = f"{dt.datetime.now():%H:%M:%S} {msg}"
        print(line, flush=True)
        fh.write(line + "\n")

    state = State()
    load_existing(state)
    run_id = "office_ambient_" + dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    quotas = {p: int(getattr(args, p)) if getattr(args, p) is not None else QUOTA[p] for p in PHASES}
    todo = {p: [n for n in range(1, quotas[p] + 1) if n not in state.slots_done[p]] for p in PHASES}
    log(f"run {run_id}: quotas={quotas} already_staged={ {p: len(state.slots_done[p]) for p in PHASES} } "
        f"todo={ {p: len(v) for p, v in todo.items()} } arrivals={state.arrivals}")
    mlock = threading.Lock()
    jl_append(MANIFEST, {"event": "run_start", "run_id": run_id, "ts": now_iso(), "quotas": quotas,
                         "concurrency": args.concurrency}, mlock)

    llm = LLM()
    log(f"waiting for {BASE_URL} (up to {args.wait_minutes} min)")
    try:
        ready = llm.wait_ready(args.wait_minutes, stop)
    except AuthError as e:
        log(f"FATAL: {e}")
        jl_append(MANIFEST, {"event": "run_abort", "run_id": run_id, "ts": now_iso(), "why": "auth_rejected"}, mlock)
        return 4
    if not ready:
        log("model server never became ready - exiting 3")
        jl_append(MANIFEST, {"event": "run_abort", "run_id": run_id, "ts": now_iso(), "why": "server_not_ready"}, mlock)
        return 3
    log("server ready")

    # interleave phases proportionally so partial runs cover every phase
    queue: list[tuple[str, int]] = []
    pending = {p: list(todo[p]) for p in PHASES}
    while any(pending.values()):
        for p in PHASES:
            k = max(1, round(QUOTA[p] / 25))
            for _ in range(k):
                if pending[p]:
                    queue.append((p, pending[p].pop(0)))
    qlock = threading.Lock()
    orders = {}
    for p in PHASES:
        o = list(range(len(HOOKS[p])))
        random.Random(f"{p}-hooks").shuffle(o)
        orders[p] = o
    t0 = time.time()
    deadline = t0 + args.max_minutes * 60 if args.max_minutes else None
    failed_slots = []

    def worker(wid):
        rng = random.Random(f"{run_id}-{wid}")
        while not stop.is_set():
            if deadline and time.time() > deadline:
                return
            with qlock:
                if not queue:
                    return
                phase, slot = queue.pop(0)
            done = False
            for attempt in range(1, MAX_ATTEMPTS_PER_SLOT + 1):
                if stop.is_set():
                    return
                with state.lock:
                    hi = pick_hook(state, phase, orders[phase])
                seats, hint, clock = HOOKS[phase][hi]
                clock = clock or random_clock(phase, rng)
                user = build_user(phase, seats, hint, clock)
                t1 = time.time()
                try:
                    raw, ntok = llm.chat(user, temperature=args.temperature, max_tokens=400)
                except Exception as e:  # server hiccup: wait, do not burn the attempt
                    log(f"[w{wid}] LLM error {type(e).__name__}: {str(e)[:120]}; waiting for server")
                    if not llm.wait_ready(25, stop):
                        return
                    continue
                dtm = time.time() - t1
                title, prompt = parse_reply(raw)
                rec = {"ts": now_iso(), "phase": phase, "slot": slot, "attempt": attempt, "hook": hi,
                       "seats": list(seats), "secs": round(dtm, 1), "run_id": run_id}
                if not prompt:
                    reasons = ["parse_fail"]
                    rec["raw"] = raw[:400]
                else:
                    title, prompt = normalise(title)[:60], normalise(prompt)
                    with state.lock:
                        reasons = gate(title, prompt, phase, seats, state)
                        if not reasons:
                            sid = PREFIX[phase] + slugify(title)
                            base, n = sid, 2
                            while sid in state.prompts:
                                sid = f"{base}-{n}"
                                n += 1
                            arrival = is_arrival(title, prompt)
                            path = write_scene(phase, slot, sid, title, prompt, seats, run_id, derive_lore(prompt))
                            state.add(sid, title, prompt, arrival)
                            state.slots_done[phase].add(slot)
                            state.hooks_used[phase].add(hi)
                            state.accepted_this_run += 1
                            state.tokens_used += ntok
                            nacc = state.accepted_this_run
                    rec.update(title=title, prompt=prompt)
                if reasons:
                    with state.lock:
                        for x in reasons:
                            state.reject_hist[x.split(":")[0]] += 1
                    rec["reasons"] = reasons
                    jl_append(REJECTS, rec, mlock)
                    log(f"[w{wid}] REJECT {phase}#{slot} a{attempt} {dtm:.0f}s {reasons[:4]}")
                else:
                    jl_append(MANIFEST, {"event": "accepted", "ts": now_iso(), "phase": phase, "slot": slot,
                                         "id": sid, "file": (str(path.relative_to(REPO)) if path.is_relative_to(REPO) else str(path)), "seats": list(seats),
                                         "hook": hi, "attempt": attempt, "secs": round(dtm, 1), "tokens": ntok,
                                         "words": len(prompt.split()), "arrival": arrival, "run_id": run_id}, mlock)
                    el = (time.time() - t0) / 60
                    log(f"[w{wid}] ACCEPT {phase}#{slot} a{attempt} {dtm:.0f}s {sid} "
                        f"(run total {nacc}, {nacc / max(el, 1e-6):.2f}/min)")
                    done = True
                    break
            if not done:
                failed_slots.append((phase, slot))
                log(f"[w{wid}] SLOT FAILED {phase}#{slot} after {MAX_ATTEMPTS_PER_SLOT} attempts")
                jl_append(MANIFEST, {"event": "slot_failed", "phase": phase, "slot": slot, "ts": now_iso(),
                                     "run_id": run_id}, mlock)

    def safe_worker(wid):
        try:
            worker(wid)
        except Exception as e:   # never let one bad slot kill the unattended run silently
            import traceback
            log(f"[w{wid}] WORKER CRASH {type(e).__name__}: {e}\n{traceback.format_exc()[-600:]}")

    threads = [threading.Thread(target=safe_worker, args=(i,), daemon=True) for i in range(min(args.concurrency, CONCURRENCY))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    el = (time.time() - t0) / 60
    jl_append(MANIFEST, {"event": "run_end", "run_id": run_id, "ts": now_iso(), "minutes": round(el, 1),
                         "accepted": state.accepted_this_run, "failed_slots": failed_slots}, mlock)
    log(f"run end: accepted={state.accepted_this_run} in {el:.1f} min "
        f"({state.accepted_this_run / max(el, 1e-6):.2f}/min); failed_slots={failed_slots}; "
        f"rejects={dict(state.reject_hist)}")
    return 0


# ---------------------------------------------------------------- verify/report
def staged_docs():
    out = []
    for phase in PHASES:
        for f in sorted((STAGE / phase).glob("*.yaml")) if (STAGE / phase).is_dir() else []:
            out.append((phase, f, yaml.safe_load(f.read_text())))
    return out


def verify():
    """Overlay the stage onto a temp copy of the pack and load_pack(); re-gate every file."""
    sys.path.insert(0, str(REPO / "app"))
    from campaign.pack import load_pack, PackError
    docs = staged_docs()
    errs = []
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td) / "ashiorid_office"
        shutil.copytree(PACK, tmp, ignore=shutil.ignore_patterns("generated", "__pycache__"))
        for phase, f, doc in docs:
            shutil.copy2(f, tmp / "scenes" / f"{phase}-{f.name}")
        try:
            pack = load_pack(tmp)
            amb = pack.ambient_scene_ids()
            print(f"load_pack: OK  scenes={len(pack.scenes)}  ambient_ids={len(amb)}  staged_files={len(docs)}")
        except PackError as e:
            errs.append(f"PackError: {e}")
            print(f"load_pack: FAIL {e}")
            pack = None
    lore_ok = lore_stems_available()
    st = State()
    seen_ids = set()
    arrivals = 0
    for phase, f, d in docs:
        p = f"{phase}/{f.name}"
        if d["id"] in seen_ids:
            errs.append(f"{p}: duplicate id")
        seen_ids.add(d["id"])
        if not d["id"].startswith(PREFIX[phase]):
            errs.append(f"{p}: bad id prefix")
        if d.get("ambient") is not True or d.get("day_phase") != phase:
            errs.append(f"{p}: ambient/day_phase")
        exp_int = "quiet" if phase == "off_hours" else "normal"
        if d.get("intensity") != exp_int:
            errs.append(f"{p}: intensity {d.get('intensity')}")
        seats = tuple(d["seats"])
        lo, hi = (1, 2) if phase == "off_hours" else (2, 4)
        if not lo <= len(seats) <= hi or not set(seats) <= set(ALL_SEATS) or not [s for s in seats if s != "party_member"]:
            errs.append(f"{p}: seats {seats}")
        if not set(d.get("lore") or []) <= lore_ok:
            errs.append(f"{p}: lore {d.get('lore')}")
        s = d.get("source") or {}
        if s.get("authored") != "generated" or s.get("model") != MODEL or not s.get("run_id") or not s.get("batch"):
            errs.append(f"{p}: source {s}")
        reasons = gate(d["title"], d["prompt"], phase, seats, st, check_unique=True)
        if reasons:
            errs.append(f"{p}: re-gate {reasons}")
        st.add(d["id"], d["title"], d["prompt"], is_arrival(d["title"], d["prompt"]))
    arrivals = st.arrivals
    maxj = 0.0
    ids = list(st.prompts)
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            maxj = max(maxj, jaccard(st.prompts[ids[i]], st.prompts[ids[j]]))
    print(f"files re-gated: {len(docs)}; arrival-type scenes: {arrivals} (cap {MAX_ARRIVALS}); max pairwise Jaccard: {maxj:.3f} (cap {JACCARD_MAX})")
    print("word counts: min/max =", min((len(d['prompt'].split()) for _, _, d in docs), default=0), "/",
          max((len(d['prompt'].split()) for _, _, d in docs), default=0))
    print(f"verify errors: {len(errs)}")
    for e in errs[:40]:
        print("  -", e)
    return 0 if not errs else 1


def report():
    docs = staged_docs()
    byp = collections.Counter(p for p, _, _ in docs)
    print("accepted by phase:", {p: f"{byp[p]}/{QUOTA[p]}" for p in PHASES}, "total", sum(byp.values()))
    hist = collections.Counter()
    nrej = 0
    if REJECTS.exists():
        for line in REJECTS.read_text().splitlines():
            e = json.loads(line)
            nrej += 1
            for r in e["reasons"]:
                hist[r.split(":")[0]] += 1
    print(f"rejects: {nrej} candidates; reason histogram (a candidate can have several):")
    for k, v in hist.most_common():
        print(f"  {v:4d}  {k}")
    acc = [json.loads(l) for l in MANIFEST.read_text().splitlines() if l.strip()] if MANIFEST.exists() else []
    acc = [e for e in acc if e.get("event") == "accepted"]
    if len(acc) >= 2:
        ts = [dt.datetime.fromisoformat(e["ts"]) for e in acc]
        span = (max(ts) - min(ts)).total_seconds() / 60
        secs = sorted(e["secs"] for e in acc)
        total = sum(QUOTA.values())
        rate = (len(acc) - 1) / span if span else 0
        rem = total - len(acc)
        print(f"rate: {len(acc)} accepted over {span:.1f} min between first/last = {rate:.2f} scenes/min "
              f"(median accepted call {secs[len(secs) // 2]:.0f}s); remaining {rem} -> ETA ~{rem / rate:.0f} min" if rate else "rate: n/a")
        att = collections.Counter(e["attempt"] for e in acc)
        print("accept attempt histogram:", dict(sorted(att.items())))
    return 0


def selftest():
    st = State()
    ok_prompt = ("Past midnight in the Kitchen. Nora is wiping the old table under the light over the stove, the coffee machine ticking as it cools. "
                 "She hums, checks the fridge labels, and straightens the snack shelf for tomorrow. The floor beyond the doorway is dark. "
                 "She wonders aloud whether tomorrow's pastries will last until lunch.")
    cases = [
        ("good", ok_prompt, "off_hours", ("office_manager",), True),
        ("twitch", ok_prompt + " Twitch is watching.", "off_hours", ("office_manager",), False),
        ("stream", ok_prompt.replace("pastries", "stream"), "off_hours", ("office_manager",), False),
        ("plot", ok_prompt + " She is fired tomorrow.", "off_hours", ("office_manager",), False),
        ("offseat", ok_prompt + " Brody waves.", "off_hours", ("office_manager",), False),
        ("unknownname", ok_prompt.replace("Nora is", "Nora and her friend Gregor are"), "off_hours", ("office_manager",), False),
        ("short", "Nora wipes the table.", "off_hours", ("office_manager",), False),
        ("time", ok_prompt + " It is 14:00.", "off_hours", ("office_manager",), False),
        ("pm_speaks", ok_prompt + " The Party Member says hello.", "off_hours", ("office_manager", "party_member"), False),
        ("notebook", ok_prompt + " The notebook is opened.", "off_hours", ("office_manager", "party_member"), False),
        ("halvard", ok_prompt + " Halvard signs nothing.", "off_hours", ("office_manager",), False),
    ]
    bad = 0
    for name, p, ph, seats, expect_ok in cases:
        r = gate("Wiping the Table", p, ph, seats, st)
        ok = not r
        flag = "ok " if ok == expect_ok else "BAD"
        bad += ok != expect_ok
        print(f"{flag} {name:12s} -> {r[:3]}")
    st.add("x", "Wiping the Table", ok_prompt, False)
    r = gate("Another", ok_prompt, "off_hours", ("office_manager",), st)
    print("ok " if any(x.startswith("jaccard") for x in r) else "BAD", "jaccard dup ->", r[:3]); bad += not any(x.startswith("jaccard") for x in r)
    for p in PHASES:
        print(p, "hooks:", len(HOOKS[p]), "quota:", QUOTA[p])
        for seats, hint, clock in HOOKS[p]:
            assert set(seats) <= set(ALL_SEATS) and [s for s in seats if s != "party_member"], hint
            lo, hi = (1, 2) if p == "off_hours" else (2, 4)
            assert lo <= len(seats) <= hi, (p, seats, hint)
            if clock:
                h, m = map(int, clock.split(":"))
                assert WINDOW[p][0] <= h * 60 + m <= WINDOW[p][1], (p, clock, hint)
    print("selftest failures:", bad)
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--concurrency", type=int, default=2)
    ap.add_argument("--temperature", type=float, default=0.8)
    ap.add_argument("--wait-minutes", type=float, default=25)
    ap.add_argument("--max-minutes", type=float, default=0, help="0 = run until all slots filled")
    for p in PHASES:
        ap.add_argument(f"--{p.replace('_', '-')}", dest=p, type=int, default=None, help=f"override quota (default {QUOTA[p]})")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.verify:
        return verify()
    if a.report:
        return report()
    return author(a)


if __name__ == "__main__":
    sys.exit(main())
