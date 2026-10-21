#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"
python3 -m compileall -q distributed-collector/controller distributed-collector/scripts
python3 -m unittest discover -s distributed-collector/tests -v
python3 - <<'PY'
from pathlib import Path
import yaml
root=Path('distributed-collector')
for y in root.rglob('*.yaml'):
    with y.open(encoding='utf8') as f:
        yaml.safe_load(f)
    print('YAML:',y,'OK')
PY
if command -v terraform >/dev/null 2>&1; then
  for path in distributed-collector/terraform/*; do
    if [[ -d "$path" ]]; then
      terraform -chdir="$path" fmt -recursive
      terraform -chdir="$path" fmt -check -recursive
      echo "FMT: $path OK"
    fi
  done
else
  echo 'Terraform binary absent. Run terraform fmt -recursive and terraform validate locally before apply.'
fi
