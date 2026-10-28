"""在线对照的随机种子复现和严格样本配对。"""

from pathlib import Path
import tempfile
import unittest

from bench.evidence_online_experiment import POLICIES, run


class OnlineExperimentTests(unittest.TestCase):
    def test_reproducible_and_complete(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            a=run(20261028,80,Path(first))
            b=run(20261028,80,Path(second))
            self.assertEqual(a,b)
            self.assertEqual(a["rows"],80*len(POLICIES))
            self.assertEqual((Path(first)/"SHA256SUMS.json").read_bytes(),
                             (Path(second)/"SHA256SUMS.json").read_bytes())
            self.assertTrue(all(s["unsafe"]==0 for s in a["summary"]))

    def test_invalid_length(self):
        with tempfile.TemporaryDirectory() as folder:
            for value in (0,39,41,10002):
                with self.subTest(value=value),self.assertRaises(ValueError):
                    run(1,value,Path(folder))


if __name__=="__main__":
    unittest.main()
