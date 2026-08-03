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


if __name__ == "__main__":
    unittest.main()
