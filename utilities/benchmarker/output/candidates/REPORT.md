# Live agent table: candidate model benchmark

Generated 2026-10-08 13:23 by scripts/bench_candidates.py. GB10, vLLM 0.30, gpu-mem-util 0.55, reasoning budgets GM 384 / THINK 256 / SPEAK 48 / adjudication 64.
Gate (P1.4 proposal): SPEAK p50 <= 15 s, THINK p95 <= 60 s, D&D round (N=4) <= 180 s, office (N=7) <= 300 s.

| model | status | backend | load | tok/s | N=4 round | N=7 round | SPEAK p50 | THINK pass | failed/capped | slice wall | retakes | GM fails | stuck |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| gemma-4-26b-a4b-fp8 | engine failed to start | None | None | None | None | None | None | None | -/- + -/- | None | None | None | None |
| glm-4.7-flash-fp8 | ok | torch | 30.8 GiB | 26.8 | 67.2 | 69.8 | 1.0 | None | 8/8 + 14/18 | 1500.0 | {'name_label': 1, 'no_take': 2, 'stage_direction': 1} | 2 | 1 |
| gpt-oss-20b | slice did not resolve | auto | 13.8 GiB | 25.8 | 48.0 | 69.3 | 3.6 | 17.6 | 9/9 + 14/14 | 1500.0 | {'no_take': 2} | 2 | 1 |
| hermes-4-14b-fp8 | slice did not resolve | torch | 15.34 GiB | 10.1 | 58.2 | 59.3 | 3.0 | 27.9 | 7/0 + 13/0 | 1500.0 | {} | 2 | 1 |
| nemotron-3.5-lightning-30b-a3b-nvfp4 | ok | auto | 17.82 GiB | 32.5 | 31.9 | 34.3 | 1.0 | 13.8 | 0/0 + 0/0 | 82.0 | {'name_label': 1, 'repeats': 1, 'stage_direction': 1} | 0 | 0 |
| qwen3-30b-a3b-thinking-2507-fp8 | ok | triton | 29.1 GiB | 42.8 | 37.1 | 42.2 | 1.8 | 13.3 | 0/0 + 0/0 | 175.8 | {'repeats': 3, 'stage_direction': 1} | 0 | 0 |
| qwen3.6-35b-a3b-fp8 | ok | triton | 33.38 GiB | 29.2 | 32.2 | 37.9 | 1.0 | 12.4 | 0/0 + 0/0 | 193.9 | {'stage_direction': 1} | 0 | 0 |
| qwen3.8-27b-fp8 | ok | triton | 27.64 GiB | 5.4 | 142.5 | 163.9 | 4.0 | 47.2 | 0/0 + 0/0 | 1500.1 | {'stage_direction': 1} | 3 | 1 |

## gemma-4-26b-a4b-fp8 (RedHatAI/gemma-4-26B-A4B-it-FP8-dynamic)

status=engine failed to start backend=None min MemAvailable=37.5 GiB

smoke: {}


```
le "/usr/local/lib/python3.12/dist-packages/vllm/entrypoints/launchers/api_server/entry.py", line 82, in build_async_engine_client_from_engine_args
(APIServer pid=1)     vllm_config = engine_args.create_engine_config(usage_context=usage_context)
(APIServer pid=1)                   ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
(APIServer pid=1)   File "/usr/local/lib/python3.12/dist-packages/vllm/engine/arg_utils.py", line 2073, in create_engine_config
(APIServer pid=1)     model_config = self.create_model_config()
(APIServer pid=1)                    ^^^^^^^^^^^^^^^^^^^^^^^^^^
(APIServer pid=1)   File "/usr/local/lib/python3.12/dist-packages/vllm/engine/arg_utils.py", line 1820, in create_model_config
(APIServer pid=1)     return ModelConfig(
(APIServer pid=1)            ^^^^^^^^^^^^
(APIServer pid=1)   File "/usr/local/lib/python3.12/dist-packages/pydantic/_internal/_dataclasses.py", line 121, in __init__
(APIServer pid=1)     s.__pydantic_validator__.validate_python(ArgsKwargs(args, kwargs), self_instance=s)
(APIServer pid=1) pydantic_core._pydantic_core.ValidationError: 1 validation error for ModelConfig
(APIServer pid=1)   Value error, Model architectures ['Gemma4ForConditionalGeneration'] failed to be inspected. Please check the logs for more details. [type=value_error, input_value=ArgsKwargs((), {'model': ...nderer_num_workers': 1}), input_type=ArgsKwargs]
(APIServer pid=1)     For further information visit https://errors.pydantic.dev/2.13/v/value_error

```

## glm-4.7-flash-fp8 (unsloth/GLM-4.7-Flash-FP8-Dynamic)

status=ok backend=torch min MemAvailable=28.7 GiB

smoke: {"wall_s": 8.4, "ttft_s": 1.1, "approx_tok_s": 26.8, "reasoning_chars": 969, "content": "\"Oh stars above, it\u2019s open! Now, please don\u2019t let a dragon eat us while I\u2019m trying to play a happy tune!\"", "split_ok": true}

- **GM ** The iron gates groan open. Chadwick steps onto the stone path. Three others stand near the portico; they are strangers, but a sudden, sharp tug pulls at their chests. The heavy oak doors stand wide. Inside, guests laugh and pour wine.

A scream shatters the music. A servant drops a crystal goblet. I
- **CHADWICK** *draws his sword* This is the way.
- **CHADWICK** 
- **CHADWICK** This is the way.
- **GM ** GM block: truth
Ten thousand years ago the Age of War burned the world's magic as fuel; the moonwells drained
and went grey one by one. A small group went to the last well to close it. That day is the
Event. Everyone present, Bahadur included, came out of it immortal.
The Bahadur are from Ord, a fut
- **CHADWICK** I see what you are.
- **GM ** A figure drops from the shattered glass, landing amidst the shards. It wears the uniform of the night watch, but its face is pale and slack. It does not get up.

The Duke stands on the balcony above, wine in one hand, a dagger in the other. He screams something about heresy.

Leena:
(You don't know 
- **CHADWICK** I keep my word. You are not it.
- **GM ** The shards of stained glass crunch under boots. More figures pour through the breach, their faces hidden by hoods. They do not speak. They draw swords from beneath their cloaks. Themultitude of guests screams and scatters into the hall. Chadwick, the pale man at your feet twitches. His eyes snap ope
- **CHADWICK** The strings are cut; it is time.
- **GM ** The gates of Leto’s manor loom high against the sky. You step across the threshold into a foyer lined with mirrors that do not reflect the room behind you. Four doors open onto this hall. You stand there, the four of you, the air thick with dust and silence.

A chandelier swings slowly overhead, cat
- **CHADWICK** I am taking the middle door. This is the way.
- **GM ** The heavy oak swings inward to reveal a long corridor of dark stone. Portraits line the walls; eyes track your movement. Through an archway, a grand dining hall opens up. Dust motes drift in shafts of light from high windows. The air tastes of stale wine and aging wood. The other doors open simultan
- **CHADWICK** Chadwick: The Duke does not usually play games. Someone is watching. Ask them plainly, or I will.
- **CHADWICK** 
- **CHADWICK** 
- _adjudication_ pass: Chadwick asserts direction with his characteristic bluntness. The scene contract mandates a sudden attack interrupting the arrival. Chadwick
- _adjudication_ pass: Chadwick declares the direction. The attack has begun.
- _adjudication_ pass: Chadwick's line 'I see what you are' is a good opening threat, aligning with his Vengeance oath and blunt nature. It sets the stage for comb
- _adjudication_ pass: The scene has reached a critical juncture with the attack on the manor and the reveal of the 'pale men' as corrupted guests. The party is en
- _adjudication_ pass: Chadwick takes the middle door. The others follow suit.

```
(EngineCore pid=241) INFO 10-08 14:43:43 [__init__.py:758] Selected ChannelWiseTorchFP8ScaledMMLinearKernel for CompressedTensorsW8A8Fp8
(EngineCore pid=241) INFO 10-08 14:47:08 [model_runner.py:428] Model loading took 30.8 GiB memory and 205.253710 seconds
(EngineCore pid=241) INFO 10-08 14:47:52 [gpu_worker.py:655] CUDA graph memory profiling is enabled (default since v0.21.0). The current --gpu-memory-utilization=0.5500 is equivalent to --gpu-memory-utilization=0.5456 without CUDA graph memory profiling. To maintain the same effective KV cache size as before, increase --gpu-memory-utilization to 0.5544. To disable, set VLLM_MEMORY_PROFILER_ESTIMATE_CUDAGRAPHS=0.
(EngineCore pid=241) INFO 10-08 14:47:52 [kv_cache_utils.py:2395] GPU KV cache size: 661,808 tokens, Maximum concurrency for 32,768 tokens per request: 20.20x

```

## gpt-oss-20b (openai/gpt-oss-20b)

status=slice did not resolve backend=auto min MemAvailable=36.3 GiB

smoke: {"wall_s": 10.2, "ttft_s": 1.13, "approx_tok_s": 25.8, "reasoning_chars": 1147, "content": "Shh! Listen\u2014this vault door groans like a sleepy dragon\u2019s sigh. I hope the treasure is worth the tremors!", "split_ok": true}

- **GM ** The heavy oak doors groan open on Leto's manor. A corridor of polished stone stretches ahead, lined with tapestries of fading banners. The air smells of pine resin and old paper. Chadwick steps forward, his boots making measured thuds. Leena follows, her eyes wide as she takes in the marble arches. 
- **CHADWICK** 
- **CHADWICK** 
- **CHADWICK** 

```
(EngineCore pid=249) INFO 10-08 16:15:01 [model_runner.py:428] Model loading took 13.8 GiB memory and 77.028006 seconds
(EngineCore pid=249) INFO 10-08 16:15:46 [kv_cache_utils.py:2395] GPU KV cache size: 1,968,672 tokens, Maximum concurrency for 32,768 tokens per request: 60.08x

```

## hermes-4-14b-fp8 (NousResearch/Hermes-4-14B-FP8)

status=slice did not resolve backend=torch min MemAvailable=37.5 GiB

smoke: {"wall_s": 4.1, "ttft_s": 0.57, "approx_tok_s": 10.1, "reasoning_chars": 186, "content": "", "split_ok": false}


```
(EngineCore pid=241) INFO 10-08 16:50:18 [__init__.py:758] Selected ChannelWiseTorchFP8ScaledMMLinearKernel for CompressedTensorsW8A8Fp8
(EngineCore pid=241) INFO 10-08 16:51:39 [model_runner.py:428] Model loading took 15.34 GiB memory and 81.388257 seconds
(EngineCore pid=241) INFO 10-08 16:53:12 [gpu_worker.py:655] CUDA graph memory profiling is enabled (default since v0.21.0). The current --gpu-memory-utilization=0.5500 is equivalent to --gpu-memory-utilization=0.5400 without CUDA graph memory profiling. To maintain the same effective KV cache size as before, increase --gpu-memory-utilization to 0.5600. To disable, set VLLM_MEMORY_PROFILER_ESTIMATE_CUDAGRAPHS=0.
(EngineCore pid=241) INFO 10-08 16:53:12 [kv_cache_utils.py:2395] GPU KV cache size: 319,328 tokens, Maximum concurrency for 32,768 tokens per request: 9.75x

```

## nemotron-3.5-lightning-30b-a3b-nvfp4 (nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4)

status=ok backend=auto min MemAvailable=33.1 GiB

smoke: {"wall_s": 7.4, "ttft_s": 0.6, "approx_tok_s": 32.5, "reasoning_chars": 986, "content": "The hinges scream! Is it a dragon, or just... treasure? I\u2014I should probably check what's in there, but my feet seem to have a mind of their own.", "split_ok": true}

- **GM ** The four of you stand at the iron gates of Leto's Manor, candles flickering in the high archways, the scent of old stone and lavender heavy in the air. A servant in livery of deep green opens the doors without a word, gesturing you inside toward a grand foyer where three other figures already pause,
- **CHADWICK** *draws his sword* this is the way.
- **CHADWICK** this is the way.
- **GM ** The gatehouse gates groan open as you crest the drive. A liveried steward steps forward, clipboard in hand, and without a word hands each of you a sealed card—heavy vellum, the Leto seal pressed deep into the wax. He gestures toward the manor house, its windows dark save for a single lantern in the 
- **CHADWICK** Ask them plainly, or I will.
- **GM ** The steward inclines his head and moves toward the oak-lined avenue, leaving the four of you standing at the drive's edge. Chadwick tucks the heavy card into his belt, his hand resting on the hilt of his sword. Leena's eyes flicker between the sealed wax and the dark manor windows; she smiles, alrea
- **CHADWICK** Ask them plainly, or I will.
- **CHADWICK** Chadwick: This is the way.
- **CHADWICK** Ask them plainly, or I will.
- _adjudication_ pass: Chadwick's line 'this is the way' fits his character and the scene's contract. The party has arrived at Leto's Manor and the first beat is r
- _adjudication_ pass: Chadwick's line establishes his character bluntly and opens the interaction. The party is at the manor gate as contracted. Ready for Round 1
- _adjudication_ pass: Chadwick asserts direction; the party continues up the avenue. No mechanical conflict, just narration of movement.

```
(EngineCore pid=239) INFO 10-08 14:28:10 [__init__.py:758] Selected FlashInferFP8ScaledMMLinearKernel for MergedColumnParallelLinear
(EngineCore pid=239) INFO 10-08 14:28:10 [__init__.py:758] Selected FlashInferFP8ScaledMMLinearKernel for RowParallelLinear
(EngineCore pid=239) INFO 10-08 14:29:53 [model_runner.py:428] Model loading took 17.82 GiB memory and 102.832656 seconds
(EngineCore pid=239) INFO 10-08 14:31:03 [gpu_worker.py:655] CUDA graph memory profiling is enabled (default since v0.21.0). The current --gpu-memory-utilization=0.5500 is equivalent to --gpu-memory-utilization=0.5440 without CUDA graph memory profiling. To maintain the same effective KV cache size as before, increase --gpu-memory-utilization to 0.5560. To disable, set VLLM_MEMORY_PROFILER_ESTIMATE_CUDAGRAPHS=0.
(EngineCore pid=239) INFO 10-08 14:31:03 [kv_cache_utils.py:2395] GPU KV cache size: 7,778,304 tokens, Maximum concurrency for 32,768 tokens per request: 237.38x

```

## qwen3-30b-a3b-thinking-2507-fp8 (Qwen/Qwen3-30B-A3B-Thinking-2507-FP8)

status=ok backend=triton min MemAvailable=37.7 GiB

smoke: {"wall_s": 6.1, "ttft_s": 0.42, "approx_tok_s": 42.8, "reasoning_chars": 953, "content": "*gulp* \"Uh... maybe we should sing 'Hush Little Halfling' before we step in? Just in case?\"", "split_ok": true}

- **GM ** The grand entrance door swings shut behind you. Marble floors gleam under gas lamps. Four figures stand in the hall, each holding a heavy invitation. Chadwick checks his sword hilt. Leena smiles, bright and sudden. Vigil scans the ceiling beams. Sodacan Bob adjusts his hat with a grin. A clock chime
- **CHADWICK** *draws his sword* 

This is the way.
- **CHADWICK** This is the way.
- **GM ** Leena’s smile fades as she touches her invitation. The metal scent sharpens. Vigil’s hand drifts to his dagger hilt. Bob’s grin tightens. A shadow detaches itself from the wall. It steps into the lamplight. Its eyes glow faintly blue. The clock chimes again. Too loud.
- **CHADWICK** This is the way.  
Ask them plainly, or I will.
- **GM ** The shadow steps closer. Its blue eyes fix on Chadwick. A voice like grinding stone says, "The Duke expects you." It raises a hand. The gas lamps flicker violently. Shadows stretch across the marble floor, forming sharp points. Leena’s invitation trembles in her grip. Vigil’s dagger is half-drawn. B
- **CHADWICK** This is the way.  
Ask them plainly, or I will.
- **CHADWICK** This way is clear. Draw your blades.
- **GM ** The shadow figure snaps its fingers. Gas lamps die. Darkness swallows the hall. Footsteps rush from the shadows—too many. A blade slices the air near Chadwick's ear. Leena gasps. Vigil curses. Bob's hat lies forgotten on the marble. The clock chimes once. Then silence.
- **CHADWICK** This is the way.
- **CHADWICK** This is the way. They're here.
- **GM ** The foyer gleams with polished oak floors and candlelight. A string quartet plays a slow, mournful tune near the stairs. The air hums faintly, vibrating the candle flames. Portraits of stern-faced men line the walls. One portrait’s eyes seem to follow you as you walk past. The scent of beeswax and o
- **CHADWICK** This is the way. Ask them plainly, or I will.
- **GM ** A silver tray glides past on its own, bearing a single black card. The quartet stops playing. The portrait eyes lock on Chadwick. Candle flames gutter low. A draft slams the front door shut. The air thickens with the scent of ozone and burnt sugar. Someone steps from the shadows near the stairs.
- **CHADWICK** This is the way. Ask them plainly, or I will.
- **CHADWICK** This is the way. The duke sent you. Say it plainly.
- _adjudication_ pass: Chadwick's line matches his character: blunt, authoritative, and in line with his 'this is the way' tic. Scene setting is concrete (marble f
- _adjudication_ pass: Chadwick's line follows his established style: short, blunt, authoritative. He addresses the shadow without hesitation, fulfilling his role 
- _adjudication_ pass: Chadwick's line follows character voice (short, directive) and advances scene contract (confrontation). No ruling needed as he states action
- _adjudication_ pass: Chadwick's line 'This is the way. They're here.' matches his established voice and character traits (blunt, oath-focused, leading in crisis)
- _adjudication_ pass: Chadwick states his stance clearly per character traits. No action taken, only dialogue.
- _adjudication_ pass: Chadwick's line matches his established speech pattern (short, blunt, oath-driven). He correctly identifies the host's messenger without hes

```
(EngineCore pid=234) INFO 10-08 16:00:01 [__init__.py:726] Selected TritonFp8BlockScaledMMKernel for Fp8LinearMethod
(EngineCore pid=234) INFO 10-08 16:03:17 [model_runner.py:428] Model loading took 29.1 GiB memory and 196.194290 seconds
(EngineCore pid=234) INFO 10-08 16:04:46 [gpu_worker.py:655] CUDA graph memory profiling is enabled (default since v0.21.0). The current --gpu-memory-utilization=0.5500 is equivalent to --gpu-memory-utilization=0.5410 without CUDA graph memory profiling. To maintain the same effective KV cache size as before, increase --gpu-memory-utilization to 0.5590. To disable, set VLLM_MEMORY_PROFILER_ESTIMATE_CUDAGRAPHS=0.
(EngineCore pid=234) INFO 10-08 16:04:46 [kv_cache_utils.py:2395] GPU KV cache size: 394,016 tokens, Maximum concurrency for 32,768 tokens per request: 12.02x

```

## qwen3.6-35b-a3b-fp8 (Qwen/Qwen3.6-35B-A3B-FP8)

status=ok backend=triton min MemAvailable=29.4 GiB

smoke: {"wall_s": 7.3, "ttft_s": 0.97, "approx_tok_s": 29.2, "reasoning_chars": 949, "content": "Oh dear, oh dear\u2026 please tell me it\u2019s just gold and not a dragon\u2019s breakfast buffet!", "split_ok": true}

- **GM ** The iron gates loom ahead. They bear the seal of House Leto. The heavy doors stand open. A host in grey livery bows low. He holds a list. He checks your names against the cards. The air smells of beeswax and roasted meat. Inside, three hundred guests mingle in the foyer. Crystal chandeliers cast sha
- **CHADWICK** *draws his sword* 

This is the way. Stand behind me and speak only when I ask.
- **CHADWICK** This is the way. Stand fast, I'll handle it.
- **GM ** The foyer stretches wide beneath the crystal lights. Three hundred guests wear silk and steel. A steward in grey livery approaches you. He holds a silver tray with wine cups. His eyes track each of you in turn. He stops before Chadwick. He nods to Leena, Vigil, and Sodacan Bob. He offers a cup to th
- **CHADWICK** Keep your cups. This wine smells like iron and bad faith. Stand ready.
- **GM ** The steward does not blink. He lowers the tray slowly. The wine cups stay full. He steps back into the shadow of a marble pillar. The room feels suddenly colder. You notice the other guests stop talking. They turn their heads toward you in unison. Their eyes are flat. They do not breathe. A heavy si
- **CHADWICK** This is the way. Draw steel and hold the line.
- **GM ** The kitchen doors blow inward. Smoke rolls across the flagstones. Three figures drop from the rafters. They wear grey cloaks. They carry curved blades. The first slash takes the nearest guest. Blood sprays the marble. The crowd screams. The four of you stand between the attackers and the dais. Steel
- **CHADWICK** This is the way. I step forward and break his wrist before he finishes that swing. Hold your ground.
- **GM ** The iron-bound doors swing inward. Cold air hits your faces. The hall stretches long and dark above you. Tapestles hang heavy on the stone walls. They show no figures. Only grey shapes that shift when you look away. Hundreds of guests fill the room. They wear masks. Their voices sound like dry leave
- **CHADWICK** I do not drink from strangers. Ask them plainly, or I will.
- **GM ** The velvet man lowers the tray. He does not blink. He turns to Leena and offers the glass. She takes it. Her fingers brush the rim. Frost blooms on her knuckles. Vigil steps to the left wall. His hand finds a seam in the stone. It is warm. Sodacan Bob watches the fountain. The water climbs the basin
- **CHADWICK** This is the way. Move back. Now.
- **GM ** A blade flashes in the dark. It passes through the velvet man's neck. He does not bleed. His mask splits down the center. Underneath, there is no face. Only grey ash. The fountain shatters. Water sprays upward. It hangs in the air like jagged ice. Three hundred guests draw weapons. Their blades hum 
- **CHADWICK** Hold the line. Shield wall. Let them break on us.
- **GM ** The crowd hits your front rank. Blades ring against steel. The impact jars your teeth. Chadwick, you brace. Your boots slide back on the stone. The pressure builds. Dust rises from the floor. A wave of force washes over you. It is not wind. It is magic. The air thickens. You feel a hand pressing aga
- _adjudication_ pass: Scene established. All four players present at threshold. Doors close behind them.
- _adjudication_ pass: Chadwick's line fits his blunt, oath-driven persona and rejects the wine naturally. The scene continues smoothly into the next beat.
- _adjudication_ pass: Chadwick's line fits his character and advances the scene. The GM has set up the next beat: an ominous attack/shift in the room. The scene c
- _adjudication_ pass: Chadwick moves first. He steps in, intercepts the curved blade, and drives his shoulder into the attacker's arm. Bone cracks. The wrist snap
- _adjudication_ pass: Chadwick rejects the drink. The velvet-coated man does not flinch. He sets the glass down on the fountain's stone rim. The water flows over 
- _adjudication_ pass: Chadwick's line fits his blunt, oath-bound nature and matches the scene's escalating tension. The GM has advanced the scene correctly with a

```
(EngineCore pid=295) INFO 10-08 13:08:20 [__init__.py:726] Selected TritonFp8BlockScaledMMKernel for Fp8LinearMethod
(EngineCore pid=295) INFO 10-08 13:11:58 [model_runner.py:428] Model loading took 33.38 GiB memory and 217.621817 seconds
(EngineCore pid=295) INFO 10-08 13:13:23 [gpu_worker.py:655] CUDA graph memory profiling is enabled (default since v0.21.0). The current --gpu-memory-utilization=0.5500 is equivalent to --gpu-memory-utilization=0.5413 without CUDA graph memory profiling. To maintain the same effective KV cache size as before, increase --gpu-memory-utilization to 0.5587. To disable, set VLLM_MEMORY_PROFILER_ESTIMATE_CUDAGRAPHS=0.
(EngineCore pid=295) INFO 10-08 13:13:23 [kv_cache_utils.py:2395] GPU KV cache size: 1,468,523 tokens, Maximum concurrency for 32,768 tokens per request: 44.82x

```

## qwen3.8-27b-fp8 (Qwen/Qwen3.8-27B-FP8)

status=ok backend=triton min MemAvailable=39.1 GiB

smoke: {"wall_s": 29.6, "ttft_s": 0.51, "approx_tok_s": 5.4, "reasoning_chars": 584, "content": "Oh dear, oh dear, oh dear\u2014does it always groan so dramatically? I do hope we\u2019re not supposed to be sneaking in.", "split_ok": true}

- **GM ** Rain beads on the black gravel before Leto's Manor. The great door stands open, brass handles worn bright. Four cloaks glisten under it.

Chadwick, Leena, Vigil, and Sodacan Bob stop on the same stone step. Each feels the others, a thin pull behind the ribs, like a bell struck in another room.

Cand
- **CHADWICK** *draws his sword* 

This is the way. Sorensen Leto. Now.
- **CHADWICK** Where is Sorensen Leto? Speak plainly, or I will make you.
- **GM ** The steward’s bow does not break.

“His Grace is in the east hall. He has been expecting you.” His thumb rests on the tray’s edge. “The doors will close at the ninth bell.”

A low chord rolls from somewhere behind the wall. Glass in the hallway shivers.

Chadwick, Leena, Vigil, Sodacan Bob, the pull
- **CHADWICK** East hall. You lead. If the manor decides anything else, I decide first.
- **GM ** The steward turns first. His boots make no sound on wet stone. He leads you down a corridor lined with black doors. Each door opens a hand’s width as he passes. No hinges move.

Chadwick, Leena, Vigil, Sodacan Bob, the pull behind your ribs tightens. The candles ahead burn blue at the edge. A chair 
- **CHADWICK** East hall, then. Let it come through that door, or not. But it will not make me kneel.
- **GM ** The double doors open.

The east hall is a great hall. Three hundred guests stand around long tables. Candles fill the ceiling. Music starts, then stops.

Chadwick, Leena, Vigil, Sodacan Bob, the pull behind your ribs turns sharp.

A crack runs up the nearest wall. Dust falls from the beams. Three b
- **CHADWICK** Who sent you? Say it plain, or I will make you.
- **GM ** The manor doors open onto a long hall. Candles burn low. A harp plays by the stair.

Chadwick, Leena, Vigil, and Bob stand near the entrance. Each holds a heavy card. Leto’s seal is pressed deep in black wax.

The four look at one another. No one knows the others’ names. Something old presses agains
- **CHADWICK** Guests behind me. You’ll burn nothing.
- **GM ** The blade stops an inch from the Duke’s throat.

The Duke does not flinch. His hand rises. Black light rings the floor. Grey men freeze mid-step. The leader spits.

“Guests first.”

He nods to his men. Three turn toward Chadwick. Two cut off the stair. One blocks the hall door.

The harp string snap
- **CHADWICK** Stair, door, east wing. Three jobs. No one dies until they try me.
- **GM ** Black light holds the grey men still. Their boots stop. Their blades stay up.

A hairline crack opens in the east wing wall. Dust drops. An old hum rises under the floor, low as a struck bell. The candles lean toward it.

The Duke’s hand trembles once. “That room must not open,” he says.

At the sta
- **CHADWICK** Vigil, the stair. Bob, the door. I take the east wing. This is the way.
- _adjudication_ pass: Chadwick's line is in character, first person, and does not decide outcomes. The steward can answer plainly.
- _adjudication_ pass: Chadwick stays in character and advances the arrival beat without deciding outcomes for other players.
- _adjudication_ pass: Chadwick's line is in character and does not decide outcomes for other characters. The scene can continue toward the attack interruption.
- _adjudication_ pass: Round 4 advances the spine: the four arrive, meet under the pull, the party begins, and an attack interrupts them. Chadwick is in character 
- _adjudication_ pass: Chadwick’s line is in-character and does not decide the outcome. The attack has begun; the scene can continue.
- _adjudication_ pass: The attack is underway. The Duke has ordered the party to protect the east wing. Chadwick has taken a defensive position and issued a warnin

```
(EngineCore pid=294) INFO 10-08 15:18:33 [__init__.py:726] Selected TritonFp8BlockScaledMMKernel for Fp8LinearMethod
(EngineCore pid=294) INFO 10-08 15:21:42 [model_runner.py:428] Model loading took 27.64 GiB memory and 189.264403 seconds
(EngineCore pid=294) INFO 10-08 15:23:28 [gpu_worker.py:655] CUDA graph memory profiling is enabled (default since v0.21.0). The current --gpu-memory-utilization=0.5500 is equivalent to --gpu-memory-utilization=0.5381 without CUDA graph memory profiling. To maintain the same effective KV cache size as before, increase --gpu-memory-utilization to 0.5619. To disable, set VLLM_MEMORY_PROFILER_ESTIMATE_CUDAGRAPHS=0.
(EngineCore pid=294) INFO 10-08 15:23:28 [kv_cache_utils.py:2395] GPU KV cache size: 445,781 tokens, Maximum concurrency for 32,768 tokens per request: 13.60x

```
