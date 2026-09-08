from __future__ import annotations

import unittest

from src.ats_adapters import detect_ats


class AtsAdapterTests(unittest.TestCase):
    def test_common_ats_hosts_have_dedicated_adapters(self) -> None:
        cases = {
            "https://jobs.ashbyhq.com/example/apply": "ashby",
            "https://boards.greenhouse.io/example/jobs/1": "greenhouse",
            "https://example.wd3.myworkdayjobs.com/job/1": "workday",
            "https://example.taleo.net/careersection/apply": "taleo",
            "https://jobs.lever.co/example/1": "lever",
            "https://jobs.smartrecruiters.com/Example/1": "smartrecruiters",
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(detect_ats(url).name, expected)

    def test_unknown_host_uses_generic_adapter(self) -> None:
        self.assertEqual(detect_ats("https://careers.example.test/apply").name, "generic")


if __name__ == "__main__":
    unittest.main()
