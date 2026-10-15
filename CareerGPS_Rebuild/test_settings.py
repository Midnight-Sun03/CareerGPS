import os
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from .settings import env_bool


class EnvironmentBooleanTests(SimpleTestCase):
    def test_recognized_true_and_false_values(self):
        for value in ("1", "true", "YES", "on"):
            with self.subTest(value=value), patch.dict(os.environ, {"TEST_BOOLEAN": value}):
                self.assertTrue(env_bool("TEST_BOOLEAN"))
        for value in ("0", "false", "NO", "off"):
            with self.subTest(value=value), patch.dict(os.environ, {"TEST_BOOLEAN": value}):
                self.assertFalse(env_bool("TEST_BOOLEAN", True))

    def test_missing_value_uses_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(env_bool("TEST_BOOLEAN", True))
            self.assertFalse(env_bool("TEST_BOOLEAN", False))

    def test_ambiguous_value_is_rejected(self):
        with patch.dict(os.environ, {"TEST_BOOLEAN": "release"}):
            with self.assertRaisesMessage(ImproperlyConfigured, "TEST_BOOLEAN must be one of"):
                env_bool("TEST_BOOLEAN")
