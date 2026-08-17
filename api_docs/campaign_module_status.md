# Campaign Module — API Documentation

## Overview
This module provides the foundation for virtualTubers' campaign system, allowing for genre-hopping, weekly-resetting simulation loops with interchangeable skins over one worker framework.

## Commands

### Validation
```bash
PYTHONPATH=app .venv/bin/python app/campaign/cli.py --pack campaigns/ashiorid --validate
```

### Watch Show (Performance Speed)
```bash
PYTHONPATH=app .venv/bin/python app/campaign/cli.py --pack campaigns/ashiorid
```

### Read Instantly (Plain Text)
```bash
PYTHONPATH=app .venv/bin/python app/campaign/cli.py --pack campaigns/ashiorid \
    --dry-run --no-pace --no-color
```

### Walk Alternate Route
```bash
PYTHONPATH=app .venv/bin/python app/campaign/cli.py --pack campaigns/ashiorid \
    --scene party-attack --force-branch failure --dry-run
```

## Key Features

### Module Structure
- `pack.py` - Load a pack off disk into typed dataclasses
- `primitives.py` - Registry of cosmetic action verbs
- `validator.py` - Semantic checks
- `scene_graph.py` - Graph traversal + the `BranchSelector` seam
- `renderer.py` - Scene rendering with TTS, avatar state, etc.
- `runtime.py` - Campaign runtime management
- `cli.py` - Command-line interface

### Seams for Future Work
- Chat voting (`BranchSelector.select()`)
- Viewer-power thresholds
- Weekly loop/reset cadence
- Sage memory across loops
- Second campaign (cyberpunk genre)

## Integration Points
- New message types: `campaign_start`, `scene_cue`, `beat_ack`, `branch_decision`, `campaign_end`
- Campaign mode gating in `main()`
- Integration with existing `role: manager|coder|tester` system