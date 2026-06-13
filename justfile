current_branch := shell("git branch --show-current")

[group('service')]
run *ARGS:
    uv run tabgroups {{ ARGS }}

[group('python')]
update:
    uvx uv-bump
    uv sync

[group('lint')]
lint:
    autocorrect --lint .
    uv sync
    uv run ruff check .
    uv run ruff format --check --diff .
    uv run ty check .

[group('lint')]
fix-lint:
    autocorrect --fix .
    uv run ruff check --fix --unsafe-fixes .
    uv run ruff format .

[group('git')]
switch:
    if [ {{ current_branch }} != "master" ]; then \
      git switch master; \
      git fetch -p; \
      git branch -D {{ current_branch }}; \
    fi
