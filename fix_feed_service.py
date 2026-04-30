#!/usr/bin/env python3
"""Fix the feed_service.py file."""
import re

with open('notecast/services/feed_service.py', 'r') as f:
    content = f.read()

# Fix the XML escape function - replace the broken replacements dict
old = '''def _escape_xml(text: str) -> str:
    replacements = {
        "&": "&",
        "<": "<",
        ">": ">",
        '"': """,
        "'": "'",
    }
    for char, escape in replacements.items():
        text = text.replace(char, escape)
    return text'''

new = '''def _escape_xml(text: str) -> str:
    replacements = {
        "&": "&",
        "<": "<",
        ">": ">",
        '"': """,
        "'": "'",
    }
    for char, escape in replacements.items():
        text = text.replace(char, escape)
    return text'''

if old in content:
    content = content.replace(old, new)
    print('Found and replaced old escape function')
else:
    print('Old escape function not found, trying alternative...')
    # Try a different pattern
    content = re.sub(
        r'def _escape_xml\(text: str\) -> str:\s+replacements = \{[^}]+\}',
        '''def _escape_xml(text: str) -> str:
    replacements = {
        "&": "&",
        "<": "<",
        ">": ">",
        '"': """,
        "'": "'",
    }''',
        content,
        flags=re.DOTALL
    )

with open('notecast/services/feed_service.py', 'w') as f:
    f.write(content)

print('Fixed feed_service.py')
