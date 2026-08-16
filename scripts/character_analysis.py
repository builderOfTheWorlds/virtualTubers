#!/usr/bin/env python3

"""
Character analysis script for Harry Potter and the Philosopher's Stone.
Analyzes character development, relationships, and key story arcs.
"""

import re
from collections import defaultdict

def analyze_character_development():
    """Analyze key character arcs and development throughout the film."""

    # Read the transcript file
    with open('/home/secus/codeProjects/virtualTubers/sourceworks/harryPotter_sorcersStoneMovieScript.md', 'r') as f:
        content = f.read()

    # Extract character interactions and key scenes
    characters = {
        'Harry': [],
        'Hermione': [],
        'Ron': [],
        'Dumbledore': [],
        'Snape': [],
        'Quirrell': [],
        'Hagrid': [],
        'Voldemort': []
    }

    # Split content into sections for easier processing
    sections = re.split(r'<h2.*?>', content)

    # Extract key scenes and character interactions
    scene_patterns = {
        'Harry': r'Harry.*?(?:\n.*?){10,50}',
        'Hermione': r'Hermione.*?(?:\n.*?){10,50}',
        'Ron': r'Ron.*?(?:\n.*?){10,50}',
        'Dumbledore': r'Dumbledore.*?(?:\n.*?){10,50}',
        'Snape': r'Snape.*?(?:\n.*?){10,50}',
        'Quirrell': r'Quirrell.*?(?:\n.*?){10,50}',
        'Hagrid': r'Hagrid.*?(?:\n.*?){10,50}',
        'Voldemort': r'Voldemort.*?(?:\n.*?){10,50}'
    }

    # Process each character's dialogue and actions
    for char_name, pattern in scene_patterns.items():
        matches = re.findall(pattern, content, re.IGNORECASE)
        characters[char_name] = matches

    return characters

def analyze_character_relationships():
    """Analyze relationships between characters."""

    with open('/home/secus/codeProjects/virtualTubers/sourceworks/harryPotter_sorcersStoneMovieScript.md', 'r') as f:
        content = f.read()

    # Look for key relationship moments
    relationships = {
        'Harry_Hermione': re.findall(r'Harry.*?Hermione|Hermione.*?Harry', content, re.IGNORECASE),
        'Harry_Ron': re.findall(r'Harry.*?Ron|Ron.*?Harry', content, re.IGNORECASE),
        'Harry_Dumbledore': re.findall(r'Harry.*?Dumbledore|Dumbledore.*?Harry', content, re.IGNORECASE),
        'Harry_Snape': re.findall(r'Harry.*?Snape|Snape.*?Harry', content, re.IGNORECASE),
        'Hermione_Ron': re.findall(r'Hermione.*?Ron|Ron.*?Hermione', content, re.IGNORECASE),
        'Hagrid_Harry': re.findall(r'Hagrid.*?Harry|Harry.*?Hagrid', content, re.IGNORECASE)
    }

    return relationships

def analyze_key_moments():
    """Identify key narrative moments and turning points."""

    with open('/home/secus/codeProjects/virtualTubers/sourceworks/harryPotter_sorcersStoneMovieScript.md', 'r') as f:
        content = f.read()

    # Key moments identified from the transcript
    key_moments = {
        'Introduction': 'Harry arrives at Hogwarts and meets his new friends',
        'Mountain Troll Incident': 'Harry, Ron, and Hermione save Hermione from a troll',
        'Quidditch Win': 'Harry catches the Golden Snitch and wins the Quidditch match',
        'Philosopher\'s Stone Discovery': 'The trio discovers the Philosopher\'s Stone is hidden at Hogwarts',
        'Forbidden Forest': 'Harry and Draco encounter a unicorn in the Forbidden Forest',
        'Mirror of Erised': 'Harry discovers his parents in the Mirror of Erised',
        'Through the Trapdoor': 'The trio descend through the trapdoor to face Quirrell',
        'Voldemort Confrontation': 'Harry confronts Voldemort in the final confrontation'
    }

    return key_moments

def generate_character_profile(char_name, content):
    """Generate a detailed character profile."""

    # Extract specific patterns for each character
    char_content = re.search(f'{char_name}.*?(?:\n.*?){50,200}', content, re.IGNORECASE)

    if char_content:
        return char_content.group(0)
    else:
        return f"No detailed information found for {char_name}"

def main():
    """Main function to run the character analysis."""

    print("=== Harry Potter and the Philosopher's Stone Character Analysis ===\n")

    # Analyze character development
    print("1. Character Development Analysis:")
    characters = analyze_character_development()

    for char, scenes in characters.items():
        if scenes:
            print(f"   {char}: Found {len(scenes)} key scenes")

    print("\n2. Character Relationships:")
    relationships = analyze_character_relationships()

    for rel, matches in relationships.items():
        if matches:
            print(f"   {rel}: Found {len(matches)} relationship moments")

    print("\n3. Key Narrative Moments:")
    key_moments = analyze_key_moments()

    for moment, description in key_moments.items():
        print(f"   {moment}: {description}")

    # Generate detailed profiles for main characters
    with open('/home/secus/codeProjects/virtualTubers/sourceworks/harryPotter_sorcersStoneMovieScript.md', 'r') as f:
        content = f.read()

    print("\n4. Main Character Profiles:")
    main_chars = ['Harry', 'Hermione', 'Ron', 'Dumbledore', 'Snape', 'Hagrid']

    for char in main_chars:
        profile = generate_character_profile(char, content)
        print(f"\n   {char} Profile:")
        # Extract just the first few lines to give a summary
        lines = profile.split('\n')
        for i, line in enumerate(lines[:5]):
            if line.strip() and not line.startswith('<'):
                print(f"     {line}")

if __name__ == "__main__":
    main()