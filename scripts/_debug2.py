import sys, os
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "src"))
for i, p in enumerate(sys.path):
    candidate = os.path.join(p, "src")
    exists = os.path.exists(candidate)
    is_dir = os.path.isdir(candidate) if exists else False
    has_init = os.path.exists(os.path.join(candidate, "__init__.py")) if is_dir else False
    print(f'{i}: path={p!r} candidate={candidate!r} exists={exists} is_dir={is_dir} has_init={has_init}')

import importlib.util
spec = importlib.util.find_spec("src")
print(f"spec={spec!r}")
