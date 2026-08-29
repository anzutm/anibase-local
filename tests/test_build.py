import unittest

import build


class BuildPythonVersionTests(unittest.TestCase):
    def test_python_312_is_supported(self):
        build.ensure_supported_build_python((3, 12, 0))
        build.ensure_supported_build_python((3, 12, 9))

    def test_older_python_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, r"Python 3\.12\.x"):
            build.ensure_supported_build_python((3, 11, 9))

    def test_newer_unvalidated_python_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, r"Python 3\.12\.x"):
            build.ensure_supported_build_python((3, 13, 0))


if __name__ == "__main__":
    unittest.main()
