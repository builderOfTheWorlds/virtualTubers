#!/bin/bash

# Configuration variables
PYTHONPATH="app"
PACK_PATH="campaigns/ashiorid"
SCENE_ID=""
FORCE_BRANCH=""
MAX_SCENES="100"
DRY_RUN=false
NO_PACE=true
NO_COLOR=false
VALIDATE=false
WATCH=true

# Function to display usage
usage() {
    echo "Usage: $0 [command]"
    echo ""
    echo "Commands:"
    echo "  validate   - Validate the campaign pack"
    echo "  watch      - Watch the show at performance speed"
    echo "  read       - Read instantly (plain text)"
    echo "  alternate  - Walk an alternate route"
    echo ""
    echo "Options:"
    echo "  --scene <scene_id>        - Start at this scene"
    echo "  --force-branch <branch_id> - Use forced branch selection"
    echo "  --max-scenes <count>      - Maximum scenes to run (default: 100)"
    echo "  --no-pace                 - Disable typing delays"
    echo "  --no-color                - Disable color output"
    echo "  --validate                - Validate the pack"
    echo ""
    echo "Example:"
    echo "  $0 watch"
    echo "  $0 alternate --scene party-attack --force-branch failure"
}

# Parse command line arguments
COMMAND=""
while [[ $# -gt 0 ]]; do
    case $1 in
        validate)
            COMMAND="validate"
            VALIDATE=true
            shift
            ;;
        watch)
            COMMAND="watch"
            WATCH=true
            shift
            ;;
        read)
            COMMAND="read"
            DRY_RUN=true
            NO_PACE=true
            NO_COLOR=true
            shift
            ;;
        alternate)
            COMMAND="alternate"
            DRY_RUN=true
            shift
            ;;
        --scene)
            SCENE_ID="$2"
            shift 2
            ;;
        --force-branch)
            FORCE_BRANCH="$2"
            shift 2
            ;;
        --max-scenes)
            MAX_SCENES="$2"
            shift 2
            ;;
        --no-pace)
            NO_PACE=true
            shift
            ;;
        --no-color)
            NO_COLOR=true
            shift
            ;;
        --validate)
            VALIDATE=true
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            usage
            exit 1
            ;;
    esac
done

# Build the command
CMD=".venv/bin/python app/campaign/cli.py --pack $PACK_PATH"

if [[ "$VALIDATE" == true ]]; then
    CMD="$CMD --validate"
fi

if [[ -n "$SCENE_ID" ]]; then
    CMD="$CMD --scene $SCENE_ID"
fi

if [[ -n "$FORCE_BRANCH" ]]; then
    CMD="$CMD --force-branch $FORCE_BRANCH"
fi

if [[ "$NO_PACE" == true ]]; then
    CMD="$CMD --no-pace"
fi

if [[ "$NO_COLOR" == true ]]; then
    CMD="$CMD --no-color"
fi

if [[ "$DRY_RUN" == true ]]; then
    CMD="$CMD --dry-run"
fi

CMD="$CMD --max-scenes $MAX_SCENES"

# Execute the command with proper environment
echo "Running: PYTHONPATH=$PYTHONPATH $CMD"
export PYTHONPATH=$PYTHONPATH
eval $CMD