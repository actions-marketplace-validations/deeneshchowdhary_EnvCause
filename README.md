# EnvCause

**Git bisect finds the bad commit. EnvCause finds the bad configuration.**

EnvCause compares a known-good `.env` file with a known-bad one, repeatedly runs your reproduction command, and uses delta debugging to reduce all changed variables to a **1-minimal failure-inducing set**.

It is deliberately local and dependency-free: your environment values are not sent anywhere.

## Example

```bash
pip install -e .

envcause \
  --good examples/good.env \
  --bad examples/bad.env \
  -- python examples/demo_app.py
```

Example output:

```text
Original differing variables : 8
Failure-inducing variables    : 2

1-minimal failure-inducing change set:
  FEATURE_NEW_AUTH: false -> true
  JWT_ALGORITHM: HS256 -> RS256
```

## Why this is useful

Configuration failures often come from many changes landing together: feature flags, URLs, credentials, timeouts, pool sizes, provider choices, or deployment-specific switches. Testing them manually is slow, and checking one variable at a time misses failures caused by combinations.

EnvCause searches combinations automatically.

## Usage

```text
envcause --good GOOD.env --bad BAD.env [options] -- COMMAND [ARGS...]
```

By default, a non-zero process exit code means the failure reproduced.

### Match a specific error instead

```bash
envcause \
  --good .env.local \
  --bad .env.staging \
  --contains "Connection refused" \
  -- npm test
```

This is useful when the command can fail for unrelated reasons.

For patterns that vary between runs, use a Python regular expression:

```bash
envcause --good good.env --bad bad.env --matches 'HTTP (500|503)' -- pytest -q
```

`--contains` and `--matches` search the combined stdout and stderr.

### Match failures from JUnit XML

```bash
envcause \
  --good good.env \
  --bad bad.env \
  --junit test-results.xml \
  -- pytest --junitxml=test-results.xml
```

A candidate fails when the report contains a `<failure>` or `<error>` element. The command should overwrite the report on every run. Relative report paths are resolved from `--cwd` when supplied.

### Reduce flaky failures

```bash
envcause --good good.env --bad bad.env --repeat 3 -- pytest -q
```

A candidate counts as failing only if it reproduces on every repeat.

### Write a small reproduction file

```bash
envcause \
  --good good.env \
  --bad bad.env \
  --write-repro minimal.env \
  -- pytest -q
```

The generated file contains the actual bad-state values. Terminal output redacts values whose variable names look secret-sensitive unless `--show-values` is supplied.

### Save a machine-readable report

```bash
envcause --good good.env --bad bad.env --report-json result.json -- pytest -q
```

The JSON report includes the command, matching mode, run and cache counts, and the reduced changes. Secret-looking values remain redacted unless `--show-values` is supplied.

### Candidate caching

EnvCause caches candidate results in memory during each reduction, avoiding duplicate command executions when the delta-debugging search revisits a change set. Use `--no-cache` when the reproduction command is stateful and every candidate must be rerun.

To reuse results across invocations, provide a cache file:

```bash
envcause --good good.env --bad bad.env --cache-file .envcause-cache.json -- pytest -q
```

The cache stores SHA-256 fingerprints and pass/fail outcomes, not raw environment values. Fingerprints include the complete execution environment, command, matcher, working directory, timeout, and repeat count. Known-good and known-bad configurations are always verified with fresh runs before cached candidates are used.

### Follow long reductions

```bash
envcause --good good.env --bad bad.env --progress -- pytest -q
```

Progress is written to stderr and shows the candidate number, number of changed variables, command-run count, and whether the result came from cache.

## How the configuration model works

EnvCause starts from the **good file as the baseline**. Each differing variable can then be switched independently into its state from the bad file.

This also handles variables that exist in only one file:

- present only in `bad.env` → candidate change sets the variable
- present only in `good.env` → candidate change unsets the variable

Variables inherited from the parent shell remain available unless overridden by the supplied files.

## Important limitation: 1-minimal is not globally smallest

EnvCause uses the classic `ddmin` delta-debugging strategy. The result is **1-minimal**: removing any one remaining change stops reproducing the failure. There may theoretically be another unrelated failure-inducing set with fewer variables.

That tradeoff keeps the number of command executions practical.

## Safety

`.env` files commonly contain secrets. EnvCause:

- runs locally
- has no telemetry or network code
- redacts values for names containing terms such as `SECRET`, `TOKEN`, `PASSWORD`, `KEY`, or `AUTH`
- shows variable names by default because names themselves can still be sensitive in some organizations

Use `--show-values` only when appropriate.

## MVP roadmap

Potential next steps:

- JSON / YAML / TOML config reduction
- parallel candidate execution
- Docker / Kubernetes environment adapters
- `envcause explain` reports
- GitHub Action integration
- multiple known-good / known-bad runs for nondeterministic systems

## Development

```bash
python -m unittest discover -s tests -v
```

No runtime dependencies are required.
