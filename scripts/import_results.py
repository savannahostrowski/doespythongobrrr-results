"""Import upstream interpreter measurements without depending on JIT pairs.

Only the standard library is needed. Raw input bytes are preserved in gzip
files; the index and summaries make consumers independent of the source repo.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import statistics
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

SOURCE = "savannahostrowski/pyperf_bench"
SOURCES = json.loads((Path(__file__).resolve().parents[1] / "sources.json").read_text())
DIRECTORY = re.compile(r"bm-(\d{8})-(\d+\.\d+[\w.+]*)-([a-f0-9]{7,40})(-TAILCALL)?$")
FILENAME = re.compile(
    r"bm-\d{8}-([a-z0-9_]+)-[^/]+-python-[^/]+\.json$"
)


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, sort_keys=True, indent=2, allow_nan=False) + "\n")
    temp.replace(path)


def candidate(path, source_repo=SOURCE):
    parts = Path(path).parts
    if len(parts) != 3 or parts[0] != "results":
        return False
    filename = FILENAME.fullmatch(parts[2])
    if not DIRECTORY.fullmatch(parts[1]) or not filename:
        return False
    if filename[1] not in SOURCES[source_repo]["machines"]:
        return False
    return not any(word in parts[2] for word in ("-pystats", "-vs-"))


def git_blob_sha(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def archive_paths(run):
    date = datetime.fromisoformat(run["date"]).date().isoformat()
    if not re.fullmatch(r"[a-f0-9]{7,40}", run["commit"]):
        raise ValueError(f"Invalid CPython commit: {run['commit']}")
    variant = "-tailcall" if "TAILCALL" in run["flags"] else ""
    # Keep repeated measurements and corrected source files distinct.
    name = f"{run['commit'][:12]}{variant}-{run['id'][:12]}"
    path = f"{run['machine']}/{date}/{name}"
    return {
        "raw_path": f"raw/{path}.json.gz",
        "summary_path": f"summaries/{path}.json",
    }


def fetch(url):
    headers = {"User-Agent": "doespythongobrrr-results"}
    token = os.environ.get("GITHUB_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"Bearer {token}"
    for attempt in range(4):
        try:
            with urlopen(Request(url, headers=headers), timeout=90) as response:
                return response.read()
        except (HTTPError, URLError, TimeoutError) as exc:
            if isinstance(exc, HTTPError) and exc.code not in (429, 500, 502, 503, 504):
                raise
            if attempt == 3:
                raise
            time.sleep(2**attempt)


def metadata_values(data, key):
    values = set()
    root = data.get("metadata", {})
    for benchmark in data["benchmarks"]:
        bm = root | benchmark.get("metadata", {})
        for run in benchmark.get("runs", []):
            value = (bm | run.get("metadata", {})).get(key)
            if value is not None:
                values.add(str(value))
    return sorted(values)


def summarize(raw, source_path, source_blob, source_commit, source_repo=SOURCE):
    data = json.loads(raw)
    metadata = data.get("metadata", {})
    if metadata.get("commit_fork") != "python" or not data.get("benchmarks"):
        return None
    directory = DIRECTORY.fullmatch(Path(source_path).parent.name)
    filename = FILENAME.fullmatch(Path(source_path).name)
    if not directory or not filename:
        raise ValueError(f"Invalid result path: {source_path}")
    machine = filename[1]
    hostnames = SOURCES[source_repo]["machines"].get(machine, [])
    if metadata.get("hostname", "").split(".")[0].lower() not in hostnames:
        raise ValueError(f"Unexpected hostname in {source_path}")
    flags = ["TAILCALL"] if directory[4] else []
    configs = metadata_values(data, "python_config_args")
    # Keep build options, but do not split a series on a workspace path or a
    # patch update to the system Python used to launch the benchmark harness.
    build_options = sorted(
        {
            arg
            for config in configs
            for arg in shlex.split(config)
            if arg.startswith("--")
        }
    )
    if any(
        arg.startswith(
            ("--enable-experimental-jit", "--disable-gil", "--enable-pystats")
        )
        and arg not in ("--enable-experimental-jit=no", "--enable-pystats=no")
        for arg in build_options
    ):
        raise ValueError(
            f"Unexpected instrumented/JIT/free-threaded build: {source_path}"
        )
    benchmarks = {}
    dates = []
    for benchmark in data["benchmarks"]:
        bm = metadata | benchmark.get("metadata", {})
        name = bm.get("name")
        values = []
        for run in benchmark.get("runs", []):
            rm = bm | run.get("metadata", {})
            dates.extend([rm["date"]] if rm.get("date") else [])
            if rm.get("unit", "second") != "second":
                continue
            values.extend(run.get("values", []))
        if name and values:
            if not all(
                isinstance(v, (int, float)) and math.isfinite(v) and v > 0
                for v in values
            ):
                raise ValueError(f"Invalid timings for {name} in {source_path}")
            benchmarks[name] = {
                "mean": statistics.mean(values),
                "stdev": statistics.stdev(values) if len(values) > 1 else 0,
                "samples": len(values),
            }
    if not benchmarks:
        return None
    digest = hashlib.sha256(raw).hexdigest()
    commit = metadata.get("commit_branch", "")
    if not re.fullmatch(r"[a-f0-9]{40}", commit):
        commit = metadata.get("commit_id", directory[3])
    measured_at = (
        min(dates) if dates else datetime.strptime(directory[1], "%Y%m%d").isoformat()
    )
    configuration = {
        "machine": machine,
        "python_series": ".".join(directory[2].split(".")[:2]),
        "flags": flags,
        "benchmark_hash": metadata.get("benchmark_hash", "unknown"),
        "performance_version": metadata.get("performance_version", "unknown"),
        "compiler": metadata_values(data, "python_compiler"),
        "cflags": metadata_values(data, "python_cflags"),
        "build_options": build_options,
        "platform": metadata.get("platform", "unknown"),
        "cpu_model": metadata.get("cpu_model_name", "unknown"),
        "cpu_config": metadata.get("cpu_config", "unknown"),
    }
    summary = {
        "id": digest,
        "date": measured_at[:10],
        "measured_at": measured_at,
        "commit": commit,
        "commit_date": metadata.get("commit_date"),
        "python_version": directory[2],
        "machine": machine,
        "flags": flags,
        "configuration": configuration,
        "source_path": source_path,
        "source_blob": source_blob,
        "source_commit": source_commit,
        "source_repo": source_repo,
        "source_run_url": metadata.get("github_action_url"),
        "benchmarks": benchmarks,
    }
    summary.update(archive_paths(summary))
    return summary


def import_results(root, tree, local_source=None, since=None, workers=6, source_repo=SOURCE):
    index_path = root / "index.json"
    old = json.loads(index_path.read_text()) if index_path.exists() else {"runs": []}
    known = {(run.get("source_repo", SOURCE), run["source_path"]): run for run in old["runs"]}
    state_path = root / "import-state.json"
    skipped = json.loads(state_path.read_text()) if state_path.exists() else {}
    # Upgrade the original single-repository skip cache without losing entries.
    skipped = {f"{SOURCE}:{key}" if key.startswith("results/") else key: value
               for key, value in skipped.items()}
    if tree.get("truncated"):
        raise ValueError("Source tree is truncated; refusing an incomplete import")
    entries = [e for e in tree["tree"] if e["type"] == "blob" and candidate(e["path"], source_repo)]
    if since:
        entries = [
            e
            for e in entries
            if Path(e["path"]).parent.name[3:11] >= since.replace("-", "")
        ]
    pending = [
        e
        for e in entries
        if known.get((source_repo, e["path"]), {}).get("source_blob") != e["sha"]
        and skipped.get(f"{source_repo}:{e['path']}") != e["sha"]
    ]
    print(
        f"{source_repo}: found {len(entries)} interpreter files; importing {len(pending)} new or changed files",
        flush=True,
    )

    def load(entry):
        path = entry["path"]
        raw = None
        if local_source and (local_source / path).is_file():
            cached = (local_source / path).read_bytes()
            if git_blob_sha(cached) == entry["sha"]:
                raw = cached
        if raw is None:
            raw = fetch(
                f"https://raw.githubusercontent.com/{source_repo}/{tree['sha']}/{quote(path)}"
            )
        if git_blob_sha(raw) != entry["sha"]:
            raise ValueError(f"Source checksum mismatch: {path}")
        summary = summarize(raw, path, entry["sha"], tree["sha"], source_repo)
        if summary:
            target = root / summary["raw_path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            # mtime=0 makes compression deterministic and decompression restores
            # exactly the original bytes, including all pyperf metadata.
            target.write_bytes(gzip.compress(raw, mtime=0))
            write_json(root / summary["summary_path"], summary)
        return entry, summary

    imported = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, (entry, summary) in enumerate(pool.map(load, pending), 1):
            if summary:
                known[(source_repo, entry["path"])] = {
                    k: v for k, v in summary.items() if k != "benchmarks"
                }
                imported += 1
            else:
                skipped[f"{source_repo}:{entry['path']}"] = entry["sha"]
            if i % 50 == 0:
                print(f"Processed {i}/{len(pending)}", flush=True)
    if imported or not index_path.exists():
        write_json(
            index_path,
            {
                "schema_version": 1,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "runs": sorted(
                    known.values(),
                    key=lambda r: (r["measured_at"], r["machine"], r["source_repo"], r["source_path"]),
                ),
            },
        )
    if pending:
        write_json(state_path, skipped)
    print(f"Imported {imported}; archive now contains {len(known)} runs", flush=True)
    return imported


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--source", choices=sorted(SOURCES), help="Import one source instead of all configured sources")
    parser.add_argument("--local-source", type=Path)
    parser.add_argument(
        "--tree",
        type=Path,
        help="Previously fetched GitHub tree (for repeatable imports)",
    )
    parser.add_argument(
        "--since", help="Only import commit directories on or after YYYY-MM-DD"
    )
    args = parser.parse_args()
    if args.tree and not args.source:
        parser.error("--tree requires --source to identify the snapshot's repository")
    for source_repo in [args.source] if args.source else SOURCES:
        branch = SOURCES[source_repo]["branch"]
        tree = json.loads(
            args.tree.read_bytes()
            if args.tree
            else fetch(f"https://api.github.com/repos/{source_repo}/git/trees/{branch}?recursive=1")
        )
        local_source = args.local_source if args.source or source_repo == SOURCE else None
        import_results(args.root, tree, local_source, args.since, source_repo=source_repo)


if __name__ == "__main__":
    main()
