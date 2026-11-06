# doespythongobrrr results

CPython interpreter benchmark results for doespythongobrrr.

Results are copied from [pyperf_bench](https://github.com/savannahostrowski/pyperf_bench)
and [pyperf-bench](https://github.com/diegorusso/pyperf-bench).
The archive includes standard and tail-call interpreter builds, with the original
samples and metadata preserved. JIT, free-threaded, and pystats runs are excluded.

CI imports new results after the `pyperf_bench` workflows finish. An hourly
sync checks both sources. This copies existing measurements; it doesn’t run
benchmarks or change doesjitgobrrr’s jobs.

- `raw/<machine>/<date>/`: original result files, gzip-compressed.
- `summaries/<machine>/<date>/`: per-run benchmark timings and source details.
- `index.json`: the list of archived runs.
- `sources.json`: repositories and machines to import.

Filenames include the CPython commit and a short checksum to distinguish runs.

To update the archive locally:

```sh
python3 scripts/import_results.py
```

To add your machine, see [CONTRIBUTING.md](CONTRIBUTING.md).
