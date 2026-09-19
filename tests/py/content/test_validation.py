import unittest

from . import _pathfix  # noqa: F401

from content import validation  # noqa: E402


GENERIC = validation.load_platform_profile("generic")
YOUTUBE = validation.load_platform_profile("youtube")


class TestProfileLoading(unittest.TestCase):
    def test_generic_profile_loads(self):
        self.assertEqual(GENERIC["platform"], "generic")

    def test_youtube_profile_loads(self):
        self.assertEqual(YOUTUBE["platform"], "youtube")

    def test_unknown_platform_falls_back_to_generic(self):
        profile = validation.load_platform_profile("some-platform-with-no-profile")
        self.assertEqual(profile["platform"], "generic")

    def test_list_platform_profiles_includes_both_shipped_profiles(self):
        names = validation.list_platform_profiles()
        self.assertIn("generic", names)
        self.assertIn("youtube", names)

    def test_youtube_verified_numeric_fields_are_marked_verified_with_a_source(self):
        self.assertTrue(YOUTUBE["caption"]["title_max_length"]["verified"])
        self.assertIn("support.google.com", YOUTUBE["caption"]["title_max_length"]["source"])
        self.assertTrue(YOUTUBE["caption"]["description_max_length"]["verified"])

    def test_youtube_unverified_fields_are_explicitly_marked_as_such(self):
        self.assertFalse(YOUTUBE["hashtags"]["max_count"]["verified"])
        self.assertIn("note", YOUTUBE["hashtags"]["max_count"])

    def test_generic_profile_has_no_fields_falsely_marked_verified(self):
        # generic.json is a made-up-safe placeholder profile; nothing in
        # it may claim to be a verified, sourced platform fact.
        def _walk(node):
            if isinstance(node, dict):
                if "verified" in node:
                    yield node["verified"]
                for v in node.values():
                    yield from _walk(v)
            elif isinstance(node, list):
                for v in node:
                    yield from _walk(v)

        self.assertTrue(all(v is False for v in _walk(GENERIC)))


class TestCaptionLength(unittest.TestCase):
    def test_caption_within_limit_is_clean(self):
        issues = validation.validate_caption_length("short caption", YOUTUBE)
        self.assertEqual(issues, [])

    def test_caption_over_verified_limit_is_an_error(self):
        long_caption = "x" * 5001
        issues = validation.validate_caption_length(long_caption, YOUTUBE)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].severity, "error")
        self.assertEqual(issues[0].rule, "caption_length")

    def test_caption_over_unverified_generic_limit_is_only_a_warning(self):
        long_caption = "x" * 2201
        issues = validation.validate_caption_length(long_caption, GENERIC)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].severity, "warning")

    def test_title_length_validated_separately_from_caption(self):
        issues = validation.validate_title_length("x" * 101, YOUTUBE)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].severity, "error")
        self.assertEqual(issues[0].field, "title")


class TestHashtags(unittest.TestCase):
    def test_find_hashtags(self):
        self.assertEqual(validation.find_hashtags("hello #world #foo bar"), ["world", "foo"])

    def test_too_many_hashtags_on_unverified_field_is_a_warning(self):
        caption = " ".join(f"#tag{i}" for i in range(20))
        issues = validation.validate_hashtags(caption, YOUTUBE)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].severity, "warning")

    def test_hashtags_within_limit_is_clean(self):
        issues = validation.validate_hashtags("#one #two", YOUTUBE)
        self.assertEqual(issues, [])


class TestUnsupportedClaims(unittest.TestCase):
    def test_guaranteed_is_flagged(self):
        issues = validation.validate_unsupported_claims("This is a guaranteed way to grow", GENERIC)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].severity, "error")

    def test_cures_is_flagged(self):
        issues = validation.validate_unsupported_claims("this cures for all diseases", GENERIC)
        self.assertTrue(any(i.rule == "unsupported_claim" for i in issues))

    def test_clean_caption_has_no_claim_issues(self):
        issues = validation.validate_unsupported_claims("Check out my new video about cooking!", GENERIC)
        self.assertEqual(issues, [])

    def test_claim_severity_is_always_error_even_for_generic_profile(self):
        # Unsupported-claim detection is about the caption's own content,
        # not an unverified platform limit - always blocking.
        issues = validation.validate_unsupported_claims("100% guaranteed results", GENERIC)
        self.assertTrue(all(i.severity == "error" for i in issues))


class TestRequiredDisclosures(unittest.TestCase):
    def test_affiliate_code_without_disclosure_is_flagged(self):
        issues = validation.validate_required_disclosures("Use my code SAVE10 for a discount!", GENERIC)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].rule, "missing_disclosure")

    def test_affiliate_code_with_disclosure_is_clean(self):
        issues = validation.validate_required_disclosures("Use my code SAVE10 for a discount! #ad", GENERIC)
        self.assertEqual(issues, [])

    def test_caption_without_trigger_is_clean(self):
        issues = validation.validate_required_disclosures("just a normal caption", GENERIC)
        self.assertEqual(issues, [])


class TestLinks(unittest.TestCase):
    def test_find_urls(self):
        urls = validation.find_urls("check https://example.com/x and http://foo.test")
        self.assertEqual(urls, ["https://example.com/x", "http://foo.test"])

    def test_malformed_url_is_an_error(self):
        issues = validation.validate_links("see ftp://example.com/x", GENERIC)
        self.assertTrue(any(i.rule == "invalid_link_syntax" and i.severity == "error" for i in issues))

    def test_valid_url_has_no_syntax_issue(self):
        issues = validation.validate_links("see https://example.com/x", GENERIC)
        self.assertEqual([i for i in issues if i.rule == "invalid_link_syntax"], [])

    def test_too_many_links_over_unverified_limit_is_a_warning(self):
        caption = " ".join(f"https://example.com/{i}" for i in range(6))
        issues = validation.validate_links(caption, GENERIC)
        self.assertTrue(any(i.rule == "link_count" and i.severity == "warning" for i in issues))

    def test_reachability_check_is_opt_in_and_off_by_default(self):
        # Without check_reachability, an unreachable-but-well-formed URL
        # produces no issue at all (no network access attempted).
        issues = validation.validate_links("https://this-domain-should-not-resolve.invalid/x", GENERIC)
        self.assertEqual([i for i in issues if i.rule == "link_unreachable"], [])

    def test_reachability_check_flags_an_unreachable_url_as_a_warning_not_an_error(self):
        issues = validation.validate_links(
            "https://this-domain-should-not-resolve.invalid/x", GENERIC, check_reachability=True, timeout=1
        )
        unreachable = [i for i in issues if i.rule == "link_unreachable"]
        self.assertEqual(len(unreachable), 1)
        self.assertEqual(unreachable[0].severity, "warning")


class TestCover(unittest.TestCase):
    def test_no_cover_when_not_required_is_clean(self):
        issues = validation.validate_cover(None, YOUTUBE)
        self.assertEqual(issues, [])

    def test_missing_required_cover_is_flagged(self):
        profile = {"cover": {"required": {"value": True, "verified": True}}}
        issues = validation.validate_cover(None, profile)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].severity, "error")

    def test_wrong_format_cover_is_flagged(self):
        issues = validation.validate_cover("cover.bmp", YOUTUBE)
        # bmp IS in youtube's (unverified) formats list, so should be clean;
        # try an unsupported extension instead.
        self.assertEqual(issues, [])
        issues2 = validation.validate_cover("cover.tiff", YOUTUBE)
        self.assertEqual(len(issues2), 1)
        self.assertEqual(issues2[0].severity, "warning")  # unverified formats field


class TestValidatePlatformAndBlocking(unittest.TestCase):
    def test_clean_input_has_no_issues(self):
        issues = validation.validate_platform("A perfectly normal caption about my trip.", YOUTUBE)
        self.assertEqual(issues, [])
        self.assertFalse(validation.has_blocking_issues(issues))

    def test_unsupported_claim_blocks(self):
        issues = validation.validate_platform("guaranteed to work every time", YOUTUBE)
        self.assertTrue(validation.has_blocking_issues(issues))

    def test_only_unverified_warnings_do_not_block(self):
        # Exceeds YouTube's (unverified) hashtag count only.
        caption = " ".join(f"#tag{i}" for i in range(20))
        issues = validation.validate_platform(caption, YOUTUBE)
        self.assertFalse(validation.has_blocking_issues(issues))
        self.assertTrue(any(i.severity == "warning" for i in issues))


if __name__ == "__main__":
    unittest.main()
