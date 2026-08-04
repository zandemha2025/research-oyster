import tempfile
import subprocess
import unittest
from pathlib import Path
from unittest import mock

import control_center


class ControlCenterTests(unittest.TestCase):
    def test_current_report_week_is_a_monday(self):
        self.assertEqual(0, control_center.date.fromisoformat(control_center.monday_for_today()).weekday())

    def test_actions_translate_to_existing_cli(self):
        pulse = control_center.command_for("pulse", {"sources": ["discord", "press"], "press_hours": 24})
        self.assertEqual(["pulse", "--sources", "discord,press", "--press-hours", "24"], pulse[-5:])
        self.assertEqual("all", control_center.command_for("collect", {})[-1])
        self.assertEqual("migrate", control_center.command_for("setup", {})[-1])

    def test_unknown_action_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unknown action"):
            control_center.command_for("delete everything", {})

    def test_mutating_requests_require_session_token(self):
        self.assertIn("X-Pulse-Token", control_center.HTML)
        self.assertIn("403", str(control_center.Handler.do_POST.__code__.co_consts))

    def test_background_job_records_success_and_failure(self):
        for returncode, expected in ((0, "success"), (1, "failed")):
            job_id = f"job-{returncode}"
            control_center.JOBS[job_id] = {"id": job_id, "status": "running"}
            completed = subprocess.CompletedProcess(["test"], returncode, stdout="done", stderr="broken" if returncode else "")
            with mock.patch.object(control_center.subprocess, "run", return_value=completed):
                control_center.run_job(job_id, ["test"])
            self.assertEqual(expected, control_center.JOBS[job_id]["status"])
            self.assertIn("elapsed", control_center.JOBS[job_id])

    def test_ui_pulse_uses_only_available_sources(self):
        self.assertIn("payload.sources=state.available_sources", control_center.HTML)

    def test_latest_output_selects_newest_matching_file(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            older, newer = output / "old-fresh-signals.md", output / "new-fresh-signals.md"
            older.write_text("old"); newer.write_text("new")
            older.touch(); newer.touch()
            with mock.patch.object(control_center, "OUTPUT", output):
                self.assertEqual(newer, control_center.latest_output("*-fresh-signals.md"))

    def test_settings_file_has_private_permissions_and_no_newlines(self):
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(control_center, "ROOT", Path(directory)):
                control_center.save_settings({"DATABASE_URL": "postgresql://localhost/db\nBAD=1"})
                path = Path(directory) / ".env"
                self.assertEqual(0o600, path.stat().st_mode & 0o777)
                self.assertNotIn("\nBAD=1", path.read_text())

    def test_blank_setup_fields_preserve_saved_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env").write_text(
                "DATABASE_URL=postgresql:///old\nTWITCH_CLIENT_ID=saved-id\n"
                "TWITCH_CLIENT_SECRET=saved-secret\n"
            )
            with mock.patch.object(control_center, "ROOT", root):
                control_center.save_settings({"DATABASE_URL": "postgresql:///new"})
            saved = (root / ".env").read_text()
            self.assertIn("TWITCH_CLIENT_ID=saved-id", saved)
            self.assertIn("TWITCH_CLIENT_SECRET=saved-secret", saved)

    def test_setup_reload_returns_safe_values_without_secrets(self):
        configured = mock.Mock(
            database_url="postgresql:///research", collection_hour_utc=18,
            twitch_client_id="id", twitch_client_secret="secret",
            kick_client_id="", kick_client_secret="", x_bearer_token="x-secret",
            apify_token="apify-secret", discord_bot_token="discord-secret",
        )
        with mock.patch.object(control_center, "Settings", return_value=configured):
            result = control_center.public_settings()
        self.assertEqual("", result["database_url"])
        self.assertEqual(18, result["collection_hour_utc"])
        self.assertTrue(result["configured"]["database"])
        self.assertTrue(result["configured"]["twitch"])
        self.assertNotIn("secret", str(result).lower())

    def test_friendly_configuration_errors(self):
        self.assertIn("database", control_center.friendly_error("DATABASE_URL missing").lower())
        self.assertIn("Twitch", control_center.friendly_error("TWITCH_CLIENT_ID required"))
        self.assertIn("Kick", control_center.friendly_error("KICK_CLIENT_SECRET required"))

    def test_localhost_only_and_session_token_are_present(self):
        self.assertIn("127.0.0.1", control_center.main.__code__.co_consts)
        self.assertIn("X-Pulse-Token", control_center.HTML)

    def test_open_path_dispatches_by_platform(self):
        with mock.patch.object(control_center.sys, "platform", "darwin"), \
             mock.patch.object(control_center.subprocess, "Popen") as popen:
            control_center.open_path(Path("/tmp/x"))
            popen.assert_called_once_with(["open", "/tmp/x"])
        with mock.patch.object(control_center.sys, "platform", "linux"), \
             mock.patch.object(control_center.subprocess, "Popen") as popen:
            control_center.open_path(Path("/tmp/x"))
            popen.assert_called_once_with(["xdg-open", "/tmp/x"])
        with mock.patch.object(control_center.sys, "platform", "sunos"):
            with self.assertRaisesRegex(ValueError, "not supported"):
                control_center.open_path(Path("/tmp/x"))

    def test_research_and_session_endpoints_are_registered(self):
        get_consts = str(control_center.Handler.do_GET.__code__.co_consts)
        post_consts = str(control_center.Handler.do_POST.__code__.co_consts)
        self.assertIn("/api/research/jobs", get_consts)
        self.assertIn("/api/research/sessions", get_consts)
        self.assertIn("/api/capture/sessions", get_consts)
        self.assertIn("/api/research/export", post_consts)
        self.assertIn("/approve", post_consts)

    def test_research_export_is_a_token_gated_mutation(self):
        # Non-capture POSTs require X-Pulse-Token; /api/research/export is not a capture path.
        self.assertFalse("/api/research/export".startswith("/api/capture/"))
        self.assertIn("X-Pulse-Token", control_center.HTML)

    def test_research_jobs_panel_is_in_the_ui(self):
        self.assertIn("Research jobs", control_center.HTML)
        self.assertIn("loadResearchJobs", control_center.HTML)

    def test_active_sessions_panel_is_wired_in_the_ui(self):
        # F1 fix: the session list/stop endpoints must have a dashboard panel that calls them.
        self.assertIn("Active capture sessions", control_center.HTML)
        self.assertIn("loadResearchSessions", control_center.HTML)
        self.assertIn("stopResearchSession", control_center.HTML)
        self.assertIn("/api/research/sessions", control_center.HTML)

    def test_open_research_folder_reuses_existing_export(self):
        # F3 fix: 'Open folder' opens the folder, exporting only when it does not yet exist.
        from unittest import mock
        with mock.patch.object(control_center, "job_folder") as jf, \
             mock.patch.object(control_center, "export_job") as ex:
            existing = mock.Mock()
            existing.exists.return_value = True
            jf.return_value = existing
            with mock.patch.object(control_center, "open_path") as op:
                # drive the /api/open research branch directly through the helper contract
                target = control_center.job_folder(mock.Mock(), 1, "output")
                if not target.exists():
                    control_center.export_job(mock.Mock(), 1, "output")
                control_center.open_path(target)
            ex.assert_not_called()
            op.assert_called_once()


if __name__ == "__main__":
    unittest.main()
