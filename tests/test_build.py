import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

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


class BuildPackagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def make_assets(self, root):
        for name in build.REQUIRED_ASSETS:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            content = b'\x00asm\x01\x00\x00\x00' if path.suffix == '.wasm' else b'wOF2font' if path.suffix == '.woff2' else b'asset'
            path.write_bytes(content)

    def test_missing_renderer_fails_before_build(self):
        self.make_assets(self.root)
        (self.root / 'static/vendor/subtitles-octopus/subtitles-octopus-worker.wasm').unlink()
        with self.assertRaisesRegex(FileNotFoundError, 'worker.wasm'):
            build.validate_assets(self.root)

    def test_html_download_instead_of_font_is_rejected(self):
        self.make_assets(self.root)
        (self.root / 'static/vendor/subtitles-octopus/InterVariable.woff2').write_bytes(b'<html>Error</html>')
        with self.assertRaisesRegex(ValueError, 'InterVariable'):
            build.validate_assets(self.root)

    def test_stale_packaged_asset_is_rejected(self):
        source = self.root / 'source'
        release = self.root / 'release'
        self.make_assets(source)
        self.make_assets(release / '_internal')
        with patch.object(build, 'PROJECT_ROOT', source):
            build.validate_packaged_assets(release)
            (release / '_internal/static/home-motion.js').write_bytes(b'old script')
            with self.assertRaisesRegex(ValueError, 'home-motion.js'):
                build.validate_packaged_assets(release)

    def test_source_package_assets_use_root_directory(self):
        self.make_assets(self.root)
        with patch.object(build, 'PROJECT_ROOT', self.root):
            build.validate_packaged_assets(self.root, executable=False)

    def test_preflight_failure_does_not_clean_outputs(self):
        with patch.object(build, 'preflight', side_effect=FileNotFoundError('ffprobe')), \
             patch.object(build, 'remove_path') as remove, \
             patch.object(build, 'run_pyinstaller') as compile_app:
            with self.assertRaises(FileNotFoundError):
                build.build_exe_release(self.root / 'release')
            remove.assert_not_called()
            compile_app.assert_not_called()

    def test_output_cleanup_rejects_root_and_outside_paths(self):
        output = self.root / 'build'
        child = output / 'AniBase'
        child.mkdir(parents=True)
        with patch.object(build, 'BUILD_DIR', output):
            for forbidden in (output, self.root):
                with self.assertRaises(ValueError):
                    build.remove_path(forbidden)
            build.remove_path(child)
            self.assertTrue(output.is_dir())

    def test_pyinstaller_command_has_explicit_resource_and_work_paths(self):
        # No compiler or build process is executed.
        with patch.object(build.subprocess, 'run') as run:
            build.run_pyinstaller()
        command = run.call_args.args[0]
        self.assertEqual(command[command.index('--contents-directory') + 1], '_internal')
        self.assertEqual(command[command.index('--workpath') + 1], str(build.BUILD_DIR / build.APP_NAME))
        self.assertEqual(command[command.index('--specpath') + 1], str(build.BUILD_DIR / build.APP_NAME))
        self.assertIn(f"{build.PROJECT_ROOT / 'static'}{build.os.pathsep}static", command)
        self.assertEqual(command[-1], str(build.PROJECT_ROOT / build.ENTRYPOINT))

    def test_check_mode_never_builds_or_cleans(self):
        with patch.object(build.sys, 'argv', ['build.py', '--check']), \
             patch.object(build, 'preflight') as preflight, \
             patch.object(build, 'build_exe_release') as compile_app, \
             patch.object(build, 'create_source_release') as source, \
             patch.object(build, 'remove_path') as remove:
            self.assertEqual(build.main(), 0)
            preflight.assert_called_once_with(source_only=False)
            compile_app.assert_not_called()
            source.assert_not_called()
            remove.assert_not_called()

    def test_release_modes_are_mutually_exclusive(self):
        with patch.object(build.sys, 'argv', ['build.py', '--source-only', '--exe-only']):
            with self.assertRaises(SystemExit) as result:
                build.main()
            self.assertEqual(result.exception.code, 2)

    def test_version_rejects_windows_trailing_dot_and_control_characters(self):
        for version in ('1.2.', '1.2\n3', '../outside'):
            with self.assertRaises(ValueError):
                build.normalize_version(version)


if __name__ == "__main__":
    unittest.main()
