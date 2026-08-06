from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

from envcause.core import EnvCauseError, diff_envs, parse_dotenv, redact_value, reduce_environment, write_repro


class ParseDotenvTests(unittest.TestCase):
    def test_parse_common_syntax(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / ".env"
            path.write_text(
                "# comment\n"
                "A=1\n"
                "export B=two\n"
                "C='hello world'\n"
                'D="line\\nnext"\n'
                "E=value # trailing comment\n"
                "HASH=x#y\n",
                encoding="utf-8",
            )
            self.assertEqual(
                parse_dotenv(path),
                {
                    "A": "1",
                    "B": "two",
                    "C": "hello world",
                    "D": "line\nnext",
                    "E": "value",
                    "HASH": "x#y",
                },
            )

    def test_invalid_line(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / ".env"
            path.write_text("NOPE\n", encoding="utf-8")
            with self.assertRaises(EnvCauseError):
                parse_dotenv(path)


class ReductionTests(unittest.TestCase):
    def test_diff_includes_added_and_removed_keys(self):
        changes = diff_envs({"A": "1", "B": "2"}, {"A": "9", "C": "3"})
        self.assertEqual([c.key for c in changes], ["A", "B", "C"])

    def test_reduces_interacting_pair(self):
        good = {
            "FEATURE_NEW_AUTH": "false",
            "JWT_ALGORITHM": "HS256",
            "CACHE": "true",
            "LOG_LEVEL": "info",
            "REGION": "west",
        }
        bad = {
            "FEATURE_NEW_AUTH": "true",
            "JWT_ALGORITHM": "RS256",
            "CACHE": "false",
            "LOG_LEVEL": "debug",
            "REGION": "east",
        }
        code = (
            "import os,sys; "
            "bad=(os.getenv('FEATURE_NEW_AUTH')=='true' and os.getenv('JWT_ALGORITHM')=='RS256'); "
            "sys.exit(1 if bad else 0)"
        )
        result = reduce_environment(
            good,
            bad,
            [sys.executable, "-c", code],
            process_env=os.environ,
        )
        self.assertEqual({c.key for c in result.changes}, {"FEATURE_NEW_AUTH", "JWT_ALGORITHM"})

    def test_contains_mode_can_ignore_unrelated_exit_code(self):
        good = {"MODE": "good", "NOISE": "0"}
        bad = {"MODE": "bad", "NOISE": "1"}
        code = (
            "import os,sys; "
            "print('TARGET' if os.getenv('MODE')=='bad' else 'OTHER'); "
            "sys.exit(7)"
        )
        result = reduce_environment(
            good,
            bad,
            [sys.executable, "-c", code],
            contains="TARGET",
            process_env=os.environ,
        )
        self.assertEqual([c.key for c in result.changes], ["MODE"])

    def test_secret_redaction_does_not_hide_feature_auth_flag(self):
        self.assertEqual(redact_value("API_TOKEN", "abc"), "<REDACTED>")
        self.assertEqual(redact_value("FEATURE_NEW_AUTH", "true"), "true")

    def test_write_repro(self):
        good = {"A": "1"}
        bad = {"A": "two words"}
        change = diff_envs(good, bad)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "repro.env"
            write_repro(path, change)
            text = path.read_text(encoding="utf-8")
            self.assertIn('A="two words"', text)


if __name__ == "__main__":
    unittest.main()
