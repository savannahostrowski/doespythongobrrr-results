# Contributing results

This archive is for CPython interpreter benchmarks. Standard and tail-call
interpreter builds are supported today. Other interpreter configurations, such
as free-threaded builds, may be added later. JIT results are out of scope.

Keep running benchmarks in your own repository; this archive copies the results
and records where they came from.

To add a machine, open a pull request with:

1. Your public benchmark repository and branch in `sources.json`, with a unique
   machine name and the hostnames recorded in its pyperf files. The current
   importer accepts the `bench_runner` results directory and filename format.
2. The machine’s CPU, operating system, architecture, and build configuration.
   Add its display information and a distinct chart color to the dashboard’s
   `api/dashboard.json` in a corresponding dashboard pull request.
3. At least two interpreter measurements from upstream `python/cpython` with
   matching build settings and benchmark sets, so there is a previous run to
   compare against. Include the original pyperf JSON with its measurement dates.

Use a consistent benchmark setup on dedicated hardware. Configuration or suite
changes create separate comparison series; machines are always compared with
their own earlier measurements. Do not modify raw results to fit the importer.

Verify the source locally:

```sh
python3 -m unittest discover -s tests -v
python3 scripts/import_results.py --source OWNER/REPOSITORY
```

The importer keeps the original samples and source commit, verifies Git blob
hashes, and creates the raw archive, summaries, and index. Scheduled imports pick
up new results from all registered sources. No access to your runner is needed.
