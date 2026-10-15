"""Safe standalone owner-check example: optional example-status.txt data only."""
import argparse
import hashlib
import json
from pathlib import Path
import sys


def source_digest(root):
    value = hashlib.sha256()
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root)
        if any(part in {'.git', '.workflow', '__pycache__'} for part in relative.parts) or path.suffix == '.pyc':
            continue
        if path.is_symlink():
            raise ValueError('source symlinks unsupported')
        if path.is_file():
            value.update(str(relative).encode() + b'\0' + path.read_bytes() + b'\0')
    return value.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', required=True)
    args = parser.parse_args()
    root = Path(args.repo).resolve()
    try:
        before = source_digest(root)
        target = root / 'example-status.txt'
        passed = not target.exists() or target.read_text() != 'needs-fix\n'
        after = source_digest(root)
        stable = before == after
        classification = ('passed' if passed else 'product_defect') if stable else 'infrastructure_error'
        report = {'id': 'example-status', 'passed': passed and stable,
                  'classification': classification, 'source_stable': stable,
                  'exactsourceSHA': before, 'evidence': ['Optional example-status.txt data check']}
        code = 0 if passed and stable else 1 if stable else 2
    except (OSError, ValueError) as error:
        report = {'id': 'example-status', 'passed': False, 'classification': 'infrastructure_error',
                  'source_stable': False, 'evidence': [type(error).__name__ + ': ' + str(error)]}
        code = 2
    print(json.dumps(report))
    return code


if __name__ == '__main__':
    sys.exit(main())
