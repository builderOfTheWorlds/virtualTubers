#!/usr/bin/env python3
"""
CLI script to interact with the virtualTubers campaign API.
This script provides easy access to campaign functionality from the command line.
"""

import subprocess
import sys
import os

def run_campaign_command(pack_path, **kwargs):
    """
    Run a campaign command with the specified parameters.
    
    Args:
        pack_path (str): Path to the campaign pack directory
        **kwargs: Command-line arguments for the campaign
        
    Returns:
        int: Exit code from the command
    """
    # Build the base command
    cmd = [
        sys.executable, 
        "app/campaign/cli.py",
        "--pack", pack_path
    ]
    
    # Add optional arguments
    if kwargs.get('validate'):
        cmd.append("--validate")
        
    if kwargs.get('dry_run'):
        cmd.append("--dry-run")
        
    if kwargs.get('no_pace'):
        cmd.append("--no-pace")
        
    if kwargs.get('no_color'):
        cmd.append("--no-color")
        
    if kwargs.get('scene'):
        cmd.extend(["--scene", kwargs['scene']])
        
    if kwargs.get('force_branch'):
        cmd.extend(["--force-branch", kwargs['force_branch']])
        
    if kwargs.get('max_scenes'):
        cmd.extend(["--max-scenes", str(kwargs['max_scenes'])])
    
    # Set the PYTHONPATH
    env = os.environ.copy()
    env['PYTHONPATH'] = 'app'
    
    print(f"Running: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, env=env, capture_output=True, text=True)
        print(result.stdout)
        if result.stderr:
            print("STDERR:", result.stderr)
        return result.returncode
    except Exception as e:
        print(f"Error running command: {e}")
        return 1

def main():
    """Main function to demonstrate the API usage."""
    if len(sys.argv) < 2:
        print("Usage: python campaign_api.py <command> [options]")
        print("\nCommands:")
        print("  validate <pack_path>     - Validate a campaign pack")
        print("  watch <pack_path>        - Watch the show at performance speed")
        print("  read <pack_path>         - Read instantly (plain text)")
        print("  alternate <pack_path>    - Walk an alternate route")
        print("\nExamples:")
        print("  python campaign_api.py validate campaigns/ashiorid")
        print("  python campaign_api.py watch campaigns/ashiorid")
        print("  python campaign_api.py read campaigns/ashiorid --no-pace --no-color")
        print("  python campaign_api.py alternate campaigns/ashiorid --scene party-attack --force-branch failure")
        return 1
    
    command = sys.argv[1]
    
    if command == "validate":
        if len(sys.argv) < 3:
            print("Usage: python campaign_api.py validate <pack_path>")
            return 1
        return run_campaign_command(sys.argv[2], validate=True)
        
    elif command == "watch":
        if len(sys.argv) < 3:
            print("Usage: python campaign_api.py watch <pack_path>")
            return 1
        return run_campaign_command(sys.argv[2])
        
    elif command == "read":
        if len(sys.argv) < 3:
            print("Usage: python campaign_api.py read <pack_path> [options]")
            return 1
        return run_campaign_command(sys.argv[2], dry_run=True, no_pace=True, no_color=True)
        
    elif command == "alternate":
        if len(sys.argv) < 5 or "--scene" not in sys.argv or "--force-branch" not in sys.argv:
            print("Usage: python campaign_api.py alternate <pack_path> --scene <scene_id> --force-branch <branch_id>")
            return 1
        # Find the scene and force_branch arguments
        scene_idx = sys.argv.index("--scene")
        branch_idx = sys.argv.index("--force-branch")
        if scene_idx == -1 or branch_idx == -1:
            print("Usage: python campaign_api.py alternate <pack_path> --scene <scene_id> --force-branch <branch_id>")
            return 1
        return run_campaign_command(
            sys.argv[2], 
            dry_run=True, 
            scene=sys.argv[scene_idx + 1],
            force_branch=sys.argv[branch_idx + 1]
        )
        
    else:
        print(f"Unknown command: {command}")
        return 1

if __name__ == "__main__":
    sys.exit(main())