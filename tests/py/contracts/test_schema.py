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


class TestSecretNameLists(unittest.TestCase):
    """A list of secret NAMES under a secret-like key is a reference (agent
    manifest `spec.secrets`), never a value; anything value-shaped is still
    rejected."""

    def test_list_of_identifiers_is_allowed(self):
        s = {"type": "object"}
        self.assertEqual(schema.validate({"secrets": ["provider-primary", "telegram_bot"]}, s), [])
        self.assertEqual(schema.validate({"secrets": []}, s), [])

    def test_list_with_value_shaped_item_is_rejected(self):
        s = {"type": "object"}
        sample = "sk_live_" + "a" * 20
        self.assertTrue(schema.validate({"secrets": [sample]}, s))
        self.assertTrue(schema.validate({"secrets": ["ok", "has space"]}, s))
        self.assertTrue(schema.validate({"secrets": [{"store": "env", "key": "X"}, 5]}, s))


class TestSchemaKeywords(unittest.TestCase):
    """Tests for fail-closed schema keyword validation (issue #172)."""

    def test_unsupported_top_level_keyword_fails(self):
        unsupported = [
            "$ref",
            "format",
            "if",
            "then",
            "else",
            "allOf",
            "not",
            "uniqueItems",
            "patternProperties",
        ]
        for kw in unsupported:
            s = {"type": "string", kw: "something"}
            with self.assertRaises(schema.SchemaError) as ctx:
                schema.validate("val", s)
            self.assertIn("unsupported JSON Schema keyword", str(ctx.exception))
            self.assertIn(repr(kw), str(ctx.exception))

    def test_unsupported_nested_keyword_fails(self):
        # Nested in properties
        s_prop = {"type": "object", "properties": {"a": {"type": "string", "$ref": "#/defs/Foo"}}}
        with self.assertRaises(schema.SchemaError) as ctx:
            schema.validate({"a": "foo"}, s_prop)
        self.assertIn("unsupported JSON Schema keyword '$ref'", str(ctx.exception))
        self.assertIn("$.properties.a", str(ctx.exception))

        # Nested in items
        s_items = {"type": "array", "items": {"type": "string", "format": "email"}}
        with self.assertRaises(schema.SchemaError) as ctx:
            schema.validate(["test@example.com"], s_items)
        self.assertIn("unsupported JSON Schema keyword 'format'", str(ctx.exception))
        self.assertIn("$.items", str(ctx.exception))

        # Nested in additionalProperties
        s_add = {"type": "object", "additionalProperties": {"type": "string", "uniqueItems": True}}
        with self.assertRaises(schema.SchemaError) as ctx:
            schema.validate({"k": "v"}, s_add)
        self.assertIn("unsupported JSON Schema keyword 'uniqueItems'", str(ctx.exception))
        self.assertIn("$.additionalProperties", str(ctx.exception))

        # Nested in oneOf
        s_oneof = {"oneOf": [{"type": "string"}, {"type": "string", "allOf": []}]}
        with self.assertRaises(schema.SchemaError) as ctx:
            schema.validate("val", s_oneof)
        self.assertIn("unsupported JSON Schema keyword 'allOf'", str(ctx.exception))
        self.assertIn("$.oneOf[1]", str(ctx.exception))

        # Nested in anyOf
        s_anyof = {"anyOf": [{"type": "string", "not": {"type": "number"}}]}
        with self.assertRaises(schema.SchemaError) as ctx:
            schema.validate("val", s_anyof)
        self.assertIn("unsupported JSON Schema keyword 'not'", str(ctx.exception))
        self.assertIn("$.anyOf[0]", str(ctx.exception))

    def test_annotation_keywords_pass(self):
        s = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "https://example.com/schema.json",
            "title": "Example Schema",
            "description": "A schema with annotations",
            "type": "string",
        }
        self.assertEqual(schema.validate("hello", s), [])

    def test_validator_subset_mismatch_detected(self):
        from agent import jsonschema_lite

        self.assertEqual(
            schema.SUPPORTED_VALIDATION_KEYWORDS,
            jsonschema_lite.SUPPORTED_VALIDATION_KEYWORDS,
        )
        self.assertEqual(
            schema.ALLOWED_ANNOTATION_KEYWORDS,
            jsonschema_lite.ALLOWED_ANNOTATION_KEYWORDS,
        )
        self.assertEqual(
            schema.ALLOWED_SCHEMA_KEYWORDS,
            jsonschema_lite.ALLOWED_SCHEMA_KEYWORDS,
        )

    def test_string_min_max_length(self):
        s = {"type": "string", "minLength": 2, "maxLength": 5}
        self.assertEqual(schema.validate("abc", s), [])
        self.assertTrue(any("shorter than minLength" in e for e in schema.validate("a", s)))
        self.assertTrue(any("longer than maxLength" in e for e in schema.validate("abcdef", s)))

    def test_secret_defense_remains_independent_of_schema_validation(self):
        s = {"type": "object", "properties": {"token": {"type": "string"}}}
        errors = schema.validate({"token": "raw-secret-scalar"}, s)
        self.assertTrue(any("secret pattern" in e for e in errors))
