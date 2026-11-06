import copy
import gzip
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "import_results", Path(__file__).parents[1] / "scripts/import_results.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

PATH = "results/bm-20261105-3.16.0a1+dev-abcdef0/bm-20261105-ripley-x86_64-python-main-3.16.0a1+dev-abcdef0.json"
DATA = {
    "version": "1.0",
    "metadata": {
        "commit_fork": "python",
        "hostname": "ripley",
        "commit_branch": "a" * 40,
        "unit": "second",
        "benchmark_hash": "suite1",
    },
    "benchmarks": [
        {
            "metadata": {
                "name": "example",
                "python_compiler": "GCC 13",
                "python_config_args": "'--enable-optimizations' '--with-lto=full' 'PKG_CONFIG_PATH=/tmp/build'",
            },
            "runs": [
                {"metadata": {"date": "2026-11-06 08:00:00"}, "warmups": [[1, 1000]]},
                {
                    "values": [1.0, 3.0],
                    "warmups": [[1, 1000]],
                    "metadata": {"date": "2026-11-06 08:01:00"},
                },
            ],
        }
    ],
}


class ImportTests(unittest.TestCase):
    def test_only_interpreter_files(self):
        self.assertTrue(module.candidate(PATH))
        tailcall = PATH.replace("abcdef0/", "abcdef0-TAILCALL/")
        self.assertTrue(module.candidate(tailcall))
        for path in [
            PATH.replace("abcdef0/", "abcdef0-JIT/"),
            PATH.replace("abcdef0/", "abcdef0-JIT,TAILCALL/"),
            PATH.replace("abcdef0/", "abcdef0-NOGIL/"),
            PATH.replace("abcdef0/", "abcdef0-PYTHON_UOPS/"),
            PATH.replace(".json", "-pystats.json"),
            PATH.replace(".json", "-vs-base.json"),
            PATH.replace("ripley", "sulaco"),
            PATH.replace("-python-", "-other-fork-"),
        ]:
            with self.subTest(path=path):
                self.assertFalse(module.candidate(path))

    def test_contributor_machine_and_hostname(self):
        source = "diegorusso/pyperf-bench"
        path = PATH.replace("ripley", "sulaco")
        self.assertTrue(module.candidate(path, source))
        self.assertFalse(module.candidate(path))
        self.assertFalse(module.candidate(PATH, source))
        changed = copy.deepcopy(DATA)
        changed["metadata"]["hostname"] = "cesw-gbt-r263-p30-01"
        summary = module.summarize(json.dumps(changed).encode(), path, "blob", "commit", source)
        self.assertEqual(summary["machine"], "sulaco")
        self.assertEqual(summary["source_repo"], source)
        self.assertEqual(summary["configuration"]["machine"], "sulaco")
        changed["metadata"]["hostname"] = "unregistered-host"
        with self.assertRaisesRegex(ValueError, "Unexpected hostname"):
            module.summarize(json.dumps(changed).encode(), path, "blob", "commit", source)

    def test_source_identity_keeps_matching_paths_separate(self):
        other = "contributor/benchmarks"
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            module.SOURCES, {other: {"machines": {"ripley": ["ripley"]}}}
        ):
            root = Path(temp) / "archive"
            source = Path(temp) / "source"
            path = source / PATH
            path.parent.mkdir(parents=True)
            snapshots = []
            for repository, values in [(module.SOURCE, [1.0, 3.0]), (other, [2.0, 4.0])]:
                data = copy.deepcopy(DATA)
                data["benchmarks"][0]["runs"][1]["values"] = values
                raw = json.dumps(data).encode()
                path.write_bytes(raw)
                tree = {"sha": "snapshot", "tree": [{"type": "blob", "path": PATH,
                        "sha": module.git_blob_sha(raw)}]}
                self.assertEqual(module.import_results(root, tree, source, source_repo=repository), 1)
                snapshots.append((repository, tree, raw))
            index = json.loads((root / "index.json").read_text())
            self.assertEqual({run["source_repo"] for run in index["runs"]}, {module.SOURCE, other})
            self.assertEqual(len(index["runs"]), 2)
            for repository, tree, raw in snapshots:
                path.write_bytes(raw)
                self.assertEqual(module.import_results(root, tree, source, source_repo=repository), 0)
            self.assertEqual(json.loads((root / "index.json").read_text()), index)

    def test_samples_and_actual_run_date(self):
        summary = module.summarize(json.dumps(DATA).encode(), PATH, "blob", "commit")
        self.assertEqual(summary["date"], "2026-11-06")
        name = f"aaaaaaaaaaaa-{summary['id'][:12]}"
        self.assertEqual(summary["raw_path"], f"raw/ripley/2026-11-06/{name}.json.gz")
        self.assertEqual(summary["summary_path"], f"summaries/ripley/2026-11-06/{name}.json")
        self.assertEqual(summary["benchmarks"]["example"]["mean"], 2)
        self.assertEqual(summary["benchmarks"]["example"]["samples"], 2)
        self.assertEqual(
            summary["configuration"]["build_options"],
            ["--enable-optimizations", "--with-lto=full"],
        )

    def test_filters_fork_and_rejects_bad_values_and_builds(self):
        changed = copy.deepcopy(DATA)
        changed["metadata"]["commit_fork"] = "other"
        self.assertIsNone(
            module.summarize(json.dumps(changed).encode(), PATH, "blob", "commit")
        )
        for value in [0, -1, float("nan"), float("inf")]:
            changed = copy.deepcopy(DATA)
            changed["benchmarks"][0]["runs"][1]["values"] = [value]
            with self.assertRaises(ValueError):
                module.summarize(json.dumps(changed).encode(), PATH, "blob", "commit")
        changed = copy.deepcopy(DATA)
        changed["benchmarks"][0]["metadata"]["python_config_args"] = (
            "--enable-experimental-jit=yes"
        )
        with self.assertRaises(ValueError):
            module.summarize(json.dumps(changed).encode(), PATH, "blob", "commit")

    def test_lossless_idempotent_import_and_revisions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "archive"
            source = Path(temp) / "source"
            path = source / PATH
            path.parent.mkdir(parents=True)
            raw = json.dumps(DATA).encode()
            path.write_bytes(raw)
            tree = {
                "sha": "snapshot",
                "truncated": False,
                "tree": [
                    {"type": "blob", "path": PATH, "sha": module.git_blob_sha(raw)}
                ],
            }
            self.assertEqual(module.import_results(root, tree, source), 1)
            index = json.loads((root / "index.json").read_text())
            first = index["runs"][0]
            self.assertEqual(
                gzip.decompress((root / first["raw_path"]).read_bytes()), raw
            )
            saved_index = (root / "index.json").read_bytes()
            self.assertEqual(module.import_results(root, tree, source), 0)
            self.assertEqual((root / "index.json").read_bytes(), saved_index)
            changed = copy.deepcopy(DATA)
            changed["benchmarks"][0]["runs"][1]["values"] = [2.0, 4.0]
            raw2 = json.dumps(changed).encode()
            path.write_bytes(raw2)
            tree["tree"][0]["sha"] = module.git_blob_sha(raw2)
            self.assertEqual(module.import_results(root, tree, source), 1)
            index = json.loads((root / "index.json").read_text())
            self.assertEqual(len(index["runs"]), 1)
            self.assertNotEqual(index["runs"][0]["id"], first["id"])
            self.assertNotEqual(index["runs"][0]["raw_path"], first["raw_path"])
            self.assertTrue((root / first["raw_path"]).exists())
            self.assertEqual(gzip.decompress((root / first["raw_path"]).read_bytes()), raw)
            self.assertEqual(gzip.decompress((root / index["runs"][0]["raw_path"]).read_bytes()), raw2)

    def test_refuses_truncated_tree(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "truncated"):
                module.import_results(Path(temp), {"truncated": True, "tree": []})


if __name__ == "__main__":
    unittest.main()
