"""Regression tests for Agent Identity and Product Branding Separation.

Product Identity: openagent
Default Agent Identity: Jarvis (user-customizable)
"""

import os
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gui import theme
from memory import config_manager


class TestThemeBranding(unittest.TestCase):
    def test_app_version(self):
        # Product identity remains openagent
        self.assertEqual(theme.APP_VERSION, "openagent")

    def test_app_protocol(self):
        # APP_PROTOCOL is derived from APP_VERSION.split()[-1]
        self.assertEqual(theme.APP_PROTOCOL, "openagent")


class TestConfigManagerBranding(unittest.TestCase):
    @patch("memory.config_manager.load_api_keys")
    def test_get_assistant_name_default_when_missing(self, mock_load):
        # 1. Missing agent name returns "Jarvis"
        mock_load.return_value = {}
        self.assertEqual(config_manager.get_assistant_name(), "Jarvis")

    @patch("memory.config_manager.load_api_keys")
    def test_get_assistant_name_default_when_empty_or_whitespace(self, mock_load):
        # 2. Empty agent name returns "Jarvis"
        mock_load.return_value = {"assistant_name": ""}
        self.assertEqual(config_manager.get_assistant_name(), "Jarvis")

        # 3. Whitespace-only agent name returns "Jarvis"
        mock_load.return_value = {"assistant_name": "   "}
        self.assertEqual(config_manager.get_assistant_name(), "Jarvis")

    @patch("memory.config_manager.load_api_keys")
    def test_get_assistant_name_custom_name(self, mock_load):
        # 4. Custom agent name is preserved
        mock_load.return_value = {"assistant_name": "Friday"}
        self.assertEqual(config_manager.get_assistant_name(), "Friday")

        mock_load.return_value = {"assistant_name": "  Friday  "}
        self.assertEqual(config_manager.get_assistant_name(), "Friday")

    @patch("memory.config_manager._write_config")
    @patch("memory.config_manager._read_for_write")
    @patch("memory.config_manager.ensure_config_dir")
    def test_save_assistant_config_fallback(self, mock_ensure, mock_read, mock_write):
        # Saving empty string falls back to "Jarvis"
        mock_read.return_value = {}
        config_manager.save_assistant_config("", "Alice")
        written = mock_write.call_args[0][0]
        self.assertEqual(written["assistant_name"], "Jarvis")
        self.assertEqual(written["user_name"], "Alice")

    @patch("memory.config_manager._write_config")
    @patch("memory.config_manager._read_for_write")
    @patch("memory.config_manager.ensure_config_dir")
    def test_save_assistant_config_custom(self, mock_ensure, mock_read, mock_write):
        # Saving custom name preserves the custom name
        mock_read.return_value = {}
        config_manager.save_assistant_config("Friday", "Alice")
        written = mock_write.call_args[0][0]
        self.assertEqual(written["assistant_name"], "Friday")

    def test_custom_name_persists_after_reload(self):
        # 5. Custom agent name persists after configuration reload (in-memory test)
        fake_storage = {}

        def fake_write(data):
            fake_storage.clear()
            fake_storage.update(data)

        def fake_read():
            return dict(fake_storage)

        with (
            patch("memory.config_manager._write_config", side_effect=fake_write),
            patch("memory.config_manager._read_for_write", side_effect=fake_read),
            patch("memory.config_manager.load_api_keys", side_effect=fake_read),
            patch("memory.config_manager.ensure_config_dir"),
        ):
            # Save custom name
            config_manager.save_assistant_config("Aria", "User1")
            # Reload / query
            self.assertEqual(config_manager.get_assistant_name(), "Aria")

    def test_identity_and_product_remain_separate(self):
        # 7. Agent identity and product identity remain separate
        with patch("memory.config_manager.load_api_keys", return_value={}):
            default_agent = config_manager.get_assistant_name()
            product_brand = theme.APP_VERSION
            self.assertEqual(default_agent, "Jarvis")
            self.assertEqual(product_brand, "openagent")
            self.assertNotEqual(default_agent, product_brand)


class TestMainBrandingSource(unittest.TestCase):
    def setUp(self):
        main_py = Path(__file__).resolve().parent.parent / "main.py"
        self.content = main_py.read_text(encoding="utf-8")

    def test_fallback_prompt_identity(self):
        # Never hardcode openagent as agent identity in fallback prompt
        self.assertNotIn("You are openagent, a voice AI assistant.", self.content)
        self.assertTrue(
            ("You are {assistant_name}, a voice AI assistant." in self.content)
            or ("You are Jarvis, a voice AI assistant." in self.content)
        )

    def test_tool_descriptions(self):
        # Existing tool declarations preserved with agent-neutral wording
        self.assertIn('"name": "shutdown_jarvis"', self.content)
        self.assertIn("The assistant checks these topics once a day", self.content)
        self.assertIn("close the assistant, say goodbye, or stop the assistant.", self.content)
        self.assertNotIn("openagent checks these topics once a day", self.content)
        self.assertNotIn("stop openagent.", self.content)

    def test_asst_name_initialization(self):
        # Default assistant name initialized to Jarvis
        self.assertIn('self._asst_name     = "Jarvis"', self.content)

    def test_ptt_log_prefix(self):
        self.assertIn('print(f"[openagent] ⚠ Push-to-talk unavailable: {e}")', self.content)

    def test_online_log_messages(self):
        # Activity log dynamically uses configured name and preserves wakeword
        self.assertIn(
            "self.ui.write_log(f\"SYS: {self._asst_name} online — sleeping. Say 'Hey Jarvis' to wake me.\")",
            self.content,
        )
        self.assertIn('self.ui.write_log(f"SYS: {self._asst_name} online.")', self.content)
        self.assertNotIn('"SYS: openagent online', self.content)

    def test_wake_word_preserved(self):
        # 8. Existing wakeword identifiers remain unchanged
        self.assertIn("Say 'Hey Jarvis' to wake me", self.content)
        wake_file = Path(__file__).resolve().parent.parent / "core" / "wake_word.py"
        wake_content = wake_file.read_text(encoding="utf-8")
        self.assertIn('WAKE_MODEL = "hey_jarvis"', wake_content)

    def test_internal_identifiers_preserved(self):
        self.assertIn(
            "class JarvisUI:",
            (Path(__file__).resolve().parent.parent / "ui.py").read_text(encoding="utf-8"),
        )
        self.assertIn("class JarvisLive:", self.content)
        self.assertIn('"type": "log", "speaker": "jarvis"', self.content)
        self.assertIn('thread_name_prefix="jarvis"', self.content)


class TestWindowBrandingSource(unittest.TestCase):
    def setUp(self):
        win_py = Path(__file__).resolve().parent.parent / "gui" / "window.py"
        self.content = win_py.read_text(encoding="utf-8")

    def test_window_title_only_app_version(self):
        # Product title is openagent
        self.assertIn("self.setWindowTitle(APP_VERSION)", self.content)

    def test_shortcut_description(self):
        self.assertIn('sc.Description      = "openagent AI Assistant"', self.content)
        self.assertIn('desktop / "openagent.lnk"', self.content)
        self.assertNotIn('desktop / "J.A.R.V.I.S.lnk"', self.content)

    def test_window_icon_configuration(self):
        self.assertIn("self.setWindowIcon(QIcon(str(ico)))", self.content)
        self.assertIn('ico_path = CONFIG_DIR / "openagent.ico"', self.content)

    def test_window_fallbacks_use_jarvis(self):
        # Window fallbacks must use Jarvis, not openagent
        self.assertIn(
            'self._assistant_name: str = (_cfg.get("assistant_name") or "Jarvis").strip()',
            self.content,
        )
        self.assertIn('cfg.get("assistant_name", "Jarvis") or "Jarvis"', self.content)
        self.assertIn('self._assistant_name = name.strip() or "Jarvis"', self.content)
        self.assertIn(
            '_read_full_config().get("assistant_name", "Jarvis") or "Jarvis"', self.content
        )

    def test_window_omits_openagent_subtitle_and_protocol_labels(self):
        # Header subtitle shows PERSONAL AI SYSTEM without APP_VERSION prefix,
        # and footer status strip omits the PROTOCOL · APP_PROTOCOL label.
        self.assertIn('self._sub_lbl = QLabel("PERSONAL AI SYSTEM")', self.content)
        self.assertNotIn(
            'self._sub_lbl = QLabel(f"{APP_VERSION}  ·  PERSONAL AI SYSTEM")', self.content
        )
        self.assertNotIn('(f"PROTOCOL · {APP_PROTOCOL}", "PRI")', self.content)


class TestPromptRendering(unittest.TestCase):
    def test_render_prompt_default_and_custom_name(self):
        from main import _render_prompt

        tmpl = "You are {assistant_name}, a voice AI assistant."
        self.assertEqual(
            _render_prompt(tmpl, {"assistant_name": "Jarvis"}),
            "You are Jarvis, a voice AI assistant.",
        )
        self.assertEqual(
            _render_prompt(tmpl, {"assistant_name": "Friday"}),
            "You are Friday, a voice AI assistant.",
        )

    def test_system_prompt_template_and_rendering(self):
        from main import _load_system_prompt, _render_prompt

        tmpl = _load_system_prompt()
        self.assertIn("{assistant_name}", tmpl)

        # Default Jarvis rendering
        rendered_default = _render_prompt(
            tmpl,
            {
                "assistant_name": "Jarvis",
                "platform": "Windows 11",
                "capabilities": "tools",
                "limits": "limits",
            },
        )
        self.assertIn("Jarvis", rendered_default)
        self.assertNotIn("{assistant_name}", rendered_default)

        # Custom name rendering
        rendered_custom = _render_prompt(
            tmpl,
            {
                "assistant_name": "Aria",
                "platform": "Windows 11",
                "capabilities": "tools",
                "limits": "limits",
            },
        )
        self.assertIn("Aria", rendered_custom)
        self.assertNotIn("{assistant_name}", rendered_custom)


class TestAppIconsAndLogos(unittest.TestCase):
    def test_icon_and_logo_files_exist(self):
        base_dir = Path(__file__).resolve().parent.parent
        ico_file = base_dir / "config" / "openagent.ico"
        png_file = base_dir / "config" / "openagent.png"
        self.assertTrue(ico_file.exists(), "config/openagent.ico should exist")
        self.assertTrue(png_file.exists(), "config/openagent.png should exist")
        self.assertGreater(ico_file.stat().st_size, 0)
        self.assertGreater(png_file.stat().st_size, 0)

    def test_widgets_logo_definition(self):
        widgets_py = Path(__file__).resolve().parent.parent / "gui" / "widgets.py"
        content = widgets_py.read_text(encoding="utf-8")
        self.assertIn("class ArcLogo(QWidget):", content)
        self.assertIn("AppLogo = ArcLogo", content)
        self.assertIn('CONFIG_DIR / "openagent.png"', content)


if __name__ == "__main__":
    unittest.main()
