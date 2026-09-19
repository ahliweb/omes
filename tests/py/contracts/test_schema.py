"""Unit tests for lib/omes/py/jobs/schema.py, the stdlib-only JSON Schema
(draft 2020-12 subset) validator (issue #89)."""
import unittest

from . import _pathfix  # noqa: F401  (sets sys.path)

from jobs import schema  # noqa: E402


class TestTypeAndRequired(unittest.TestCase):
    def test_valid_object(self):
        s = {"type": "object", "required": ["a"], "properties": {"a": {"type": "string"}}}
        self.assertEqual(schema.validate({"a": "x"}, s), [])

    def test_missing_required(self):
        s = {"type": "object", "required": ["a"]}
        errors = schema.validate({}, s)
        self.assertTrue(any("missing required property 'a'" in e for e in errors))

    def test_wrong_type(self):
        s = {"type": "string"}
        errors = schema.validate(123, s)
        self.assertTrue(any("expected type" in e for e in errors))

    def test_additional_properties_false_rejects_extra_field(self):
        s = {"type": "object", "properties": {"a": {"type": "string"}}, "additionalProperties": False}
        errors = schema.validate({"a": "x", "command": "rm -rf /"}, s)
        self.assertTrue(any("additional properties not allowed" in e for e in errors))
        self.assertTrue(any("command" in e for e in errors))


class TestEnumConstPattern(unittest.TestCase):
    def test_enum(self):
        s = {"enum": ["a", "b"]}
        self.assertEqual(schema.validate("a", s), [])
        self.assertTrue(schema.validate("c", s))

    def test_const(self):
        s = {"const": "preflight"}
        self.assertEqual(schema.validate("preflight", s), [])
        self.assertTrue(schema.validate("install", s))

    def test_pattern(self):
        s = {"type": "string", "pattern": "^[a-z]+$"}
        self.assertEqual(schema.validate("abc", s), [])
        self.assertTrue(schema.validate("ABC", s))


class TestMinMaxItems(unittest.TestCase):
    def test_minimum_maximum(self):
        s = {"type": "integer", "minimum": 0, "maximum": 10}
        self.assertEqual(schema.validate(5, s), [])
        self.assertTrue(schema.validate(-1, s))
        self.assertTrue(schema.validate(11, s))

    def test_items(self):
        s = {"type": "array", "items": {"type": "string"}}
        self.assertEqual(schema.validate(["a", "b"], s), [])
        self.assertTrue(schema.validate(["a", 1], s))


class TestOneOfAnyOf(unittest.TestCase):
    def test_one_of_exactly_one(self):
        s = {"oneOf": [{"type": "string"}, {"type": "integer"}]}
        self.assertEqual(schema.validate("x", s), [])
        self.assertEqual(schema.validate(1, s), [])

    def test_any_of(self):
        s = {"anyOf": [{"const": "a"}, {"const": "b"}]}
        self.assertEqual(schema.validate("a", s), [])
        self.assertTrue(schema.validate("c", s))


class TestSecretBan(unittest.TestCase):
    def test_raw_value_under_a_secret_named_key_is_rejected_regardless_of_schema(self):
        # Even a schema with no opinion about "token" must not let a raw
        # scalar value through a secret-named field - the ban is
        # enforced independently, purely by field NAME here (not by
        # what the value looks like - a deliberately boring,
        # non-secret-shaped value still trips this key-based rule).
        s = {"type": "object"}
        errors = schema.validate({"token": "raw-value-not-allowed"}, s)
        self.assertTrue(any("secret pattern" in e for e in errors))

    def test_secret_shaped_value_is_rejected_even_under_an_innocuous_key_name(self):
        # Defense in depth: a value that LOOKS like a real credential is
        # rejected even under a field name that does not match the
        # secret-name pattern (e.g. "notes"). The sample is assembled at
        # runtime (never a secret-shaped literal committed to the repo)
        # so this test exercises the same detection a real leaked value
        # would trip, without putting a matching literal in git history.
        fake_stripe_style_value = "sk_" + "live_" + ("a" * 20)
        s = {"type": "object"}
        errors = schema.validate({"notes": fake_stripe_style_value}, s)
        self.assertTrue(any("secret-value shape" in e for e in errors))

    def test_secret_ref_object_is_allowed(self):
        s = {"type": "object"}
        errors = schema.validate({"api_key_ref": {"store": "env", "key": "MY_KEY"}}, s)
        self.assertEqual(errors, [])

    def test_null_secret_field_is_allowed(self):
        s = {"type": "object"}
        errors = schema.validate({"password": None}, s)
        self.assertEqual(errors, [])

    def test_nested_secret_leak_is_found(self):
        s = {"type": "object"}
        errors = schema.validate({"error": {"debug": {"password": "hunter2"}}}, s)
        self.assertTrue(any("password" in e and "secret pattern" in e for e in errors))
