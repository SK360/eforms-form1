# The helper's own Python (it has PyYAML), else whatever python is around.
for PY in .venv/bin/python .venv/Scripts/python.exe python3 python; do
  command -v "$PY" >/dev/null 2>&1 && break
done
