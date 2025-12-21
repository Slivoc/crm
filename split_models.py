#!/usr/bin/env python3
"""Split models.py into sequential chunks by line number"""
from pathlib import Path
import re

CHUNK_SIZE = 100  # functions per file

def main():
    path = Path('models.py')
    content = path.read_text(encoding='utf-8')
    lines = content.splitlines(keepends=True)
    
    # Find where imports end (first 'def ' at start of line)
    import_end = 0
    for i, line in enumerate(lines):
        if line.startswith('def '):
            import_end = i
            break
    
    imports = ''.join(lines[:import_end])
    rest = lines[import_end:]
    
    # Find all function start lines
    func_starts = []
    for i, line in enumerate(rest):
        if line.startswith('def ') or line.startswith('class '):
            func_starts.append(i)
    
    print(f"Found {len(func_starts)} functions/classes")
    print(f"Import section: {import_end} lines")
    
    # Create models directory
    models_dir = Path('models')
    models_dir.mkdir(exist_ok=True)
    
    # Split into chunks
    chunks = []
    for i in range(0, len(func_starts), CHUNK_SIZE):
        start_idx = func_starts[i]
        if i + CHUNK_SIZE < len(func_starts):
            end_idx = func_starts[i + CHUNK_SIZE]
        else:
            end_idx = len(rest)
        chunks.append((start_idx, end_idx))
    
    # Write chunk files
    for i, (start, end) in enumerate(chunks):
        chunk_content = imports + '\n' + ''.join(rest[start:end])
        file_path = models_dir / f'part_{i+1}.py'
        file_path.write_text(chunk_content, encoding='utf-8')
        func_count = len([j for j in func_starts if start <= j < end])
        print(f"Created {file_path} ({func_count} functions)")
    
    # Create __init__.py
    init_content = "# models package - split from models.py\n"
    for i in range(len(chunks)):
        init_content += f"from .part_{i+1} import *\n"
    (models_dir / '__init__.py').write_text(init_content, encoding='utf-8')
    print(f"Created models/__init__.py")

if __name__ == '__main__':
    main()
