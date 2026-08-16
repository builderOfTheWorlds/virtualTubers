#!/usr/bin/env python3

"""
Harry Potter Character Development Analyzer
Analyzes character arcs and relationships in the Harry Potter script for virtual tuber performance.
"""

import re
from collections import defaultdict, Counter
import json


def analyze_character_arc(script_content):
    """Analyze character development throughout the script."""

    # Define characters to track
    characters = ['Harry', 'Hermione', 'Ron', 'Dumbledore', 'Snape', 'Quirrell', 'Hagrid', 'Voldemort']

    # Initialize character analysis
    character_analysis = {}

    for char in characters:
        character_analysis[char] = {
            'key_scenes': [],
            'emotional_tone': [],
            'interactions': [],
            'themes_mentioned': []
        }

    # Split into scenes/sections
    sections = re.split(r'\n\n', script_content)

    # Analyze each section for character mentions and emotional tone
    for i, section in enumerate(sections):
        if not section.strip():
            continue

        # Look for character mentions in the section
        for char in characters:
            if char.lower() in section.lower():
                # Extract context around character mention
                pattern = r'([^.!?]*?' + re.escape(char) + r'[^.!?]*?[.!?])'
                matches = re.findall(pattern, section, re.IGNORECASE)
                for match in matches:
                    character_analysis[char]['key_scenes'].append({
                        'scene_id': i,
                        'context': match.strip(),
                        'section': section[:100] + "..." if len(section) > 100 else section
                    })

        # Analyze emotional tone and themes
        if 'excited' in section.lower() or 'happy' in section.lower():
            character_analysis['Harry']['emotional_tone'].append('positive')
        elif 'scared' in section.lower() or 'afraid' in section.lower():
            character_analysis['Harry']['emotional_tone'].append('negative')
        elif 'courage' in section.lower() or 'brave' in section.lower():
            character_analysis['Harry']['themes_mentioned'].append('courage')

    return character_analysis


def analyze_character_relationships(script_content):
    """Analyze relationships between characters."""

    # Look for relationship mentions
    relationships = {
        'Harry_Hermione': re.findall(r'Harry.*?Hermione|Hermione.*?Harry', script_content, re.IGNORECASE),
        'Harry_Ron': re.findall(r'Harry.*?Ron|Ron.*?Harry', script_content, re.IGNORECASE),
        'Harry_Dumbledore': re.findall(r'Harry.*?Dumbledore|Dumbledore.*?Harry', script_content, re.IGNORECASE),
        'Harry_Snape': re.findall(r'Harry.*?Snape|Snape.*?Harry', script_content, re.IGNORECASE),
        'Hermione_Ron': re.findall(r'Hermione.*?Ron|Ron.*?Hermione', script_content, re.IGNORECASE),
        'Hagrid_Harry': re.findall(r'Hagrid.*?Harry|Harry.*?Hagrid', script_content, re.IGNORECASE)
    }

    return relationships


def analyze_themes(script_content):
    """Analyze key themes mentioned in the script."""

    themes = {
        'friendship': len(re.findall(r'friendship|friends|together', script_content, re.IGNORECASE)),
        'courage': len(re.findall(r'courage|brave|scared', script_content, re.IGNORECASE)),
        'love': len(re.findall(r'love|family|heart', script_content, re.IGNORECASE)),
        'knowledge': len(re.findall(r'knowledge|learn|study', script_content, re.IGNORECASE)),
        'good_vs_evil': len(re.findall(r'evil|dark|good|voldemort', script_content, re.IGNORECASE)),
        'magic': len(re.findall(r'magic|wizard|wizards', script_content, re.IGNORECASE))
    }

    return themes


def analyze_script_structure(script_content):
    """Analyze the overall structure of the script."""

    # Count different sections
    section_patterns = {
        'introduction': len(re.findall(r'introduction|begin|start', script_content, re.IGNORECASE)),
        'character_development': len(re.findall(r'character.*?deep|deep.*?character', script_content, re.IGNORECASE)),
        'mystery': len(re.findall(r'mystery|stone|secret', script_content, re.IGNORECASE)),
        'conflict': len(re.findall(r'battle|fight|confrontation|danger', script_content, re.IGNORECASE)),
        'resolution': len(re.findall(r'solution|end|conclusion|learned', script_content, re.IGNORECASE))
    }

    return section_patterns


def main():
    """Main function to run the analysis."""

    # Read the script file
    with open('/home/secus/codeProjects/virtualTubers/scripts/tuber_script.md', 'r') as f:
        content = f.read()

    print("=== Harry Potter Script Character Analysis ===\n")

    # Analyze character arcs
    print("1. Character Development Analysis:")
    char_analysis = analyze_character_arc(content)

    for char, data in char_analysis.items():
        if data['key_scenes']:
            print(f"   {char}: Found {len(data['key_scenes'])} key scenes")

    # Analyze relationships
    print("\n2. Character Relationships:")
    relationships = analyze_character_relationships(content)

    for rel, matches in relationships.items():
        if matches:
            print(f"   {rel}: Found {len(matches)} relationship mentions")

    # Analyze themes
    print("\n3. Key Themes:")
    themes = analyze_themes(content)

    for theme, count in themes.items():
        if count > 0:
            print(f"   {theme}: {count} mentions")

    # Analyze script structure
    print("\n4. Script Structure Analysis:")
    structure = analyze_script_structure(content)

    for section, count in structure.items():
        if count > 0:
            print(f"   {section}: {count} mentions")


if __name__ == "__main__":
    main()