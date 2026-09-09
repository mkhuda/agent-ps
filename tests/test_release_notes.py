"""The release page is built from the changelog, so this guards that path.

If this breaks, a release either carries the wrong notes or does not publish
at all, and neither is visible until a tag has already been pushed.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))), "scripts"))

import release_notes

SAMPLE = """# Changelog

## 0.6.0

Reclaiming what ended sessions left behind.

- one thing that changed
- another, which wraps
  onto a second line

## 0.5.0

An earlier release.

- something older
"""


class Reading(unittest.TestCase):
    def parts(self, version, text=SAMPLE):
        return release_notes.split(release_notes.section(version, text))

    def test_the_summary_becomes_the_title(self):
        title, _ = self.parts("0.6.0")
        self.assertEqual(title, "Reclaiming what ended sessions left behind.")

    def test_the_printed_title_carries_the_version(self):
        import io
        import contextlib
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            release_notes.main(["0.6.1", "--title"])
        printed = out.getvalue().strip()
        self.assertTrue(printed.startswith("v0.6.1: "), printed)
        self.assertFalse(printed.endswith("."), printed)

    def test_the_notes_are_the_bullets(self):
        _, notes = self.parts("0.6.0")
        self.assertEqual(notes[0], "- one thing that changed")
        self.assertIn("onto a second line", "\n".join(notes))

    def test_a_section_stops_at_the_next_version(self):
        _, notes = self.parts("0.6.0")
        self.assertNotIn("something older", "\n".join(notes))
        self.assertNotIn("An earlier release", "\n".join(notes))

    def test_an_older_section_is_readable_too(self):
        title, notes = self.parts("0.5.0")
        self.assertEqual(title, "An earlier release.")
        self.assertEqual(notes, ["- something older"])

    def test_a_missing_version_stops_the_release(self):
        with self.assertRaises(SystemExit):
            release_notes.section("9.9.9", SAMPLE)

    def test_bullets_with_no_summary_still_give_notes(self):
        title, notes = self.parts("0.1.0", "## 0.1.0\n\n- only a bullet\n")
        self.assertEqual(title, "")
        self.assertEqual(notes, ["- only a bullet"])


class AgainstTheRealChangelog(unittest.TestCase):
    def test_every_released_version_has_notes(self):
        """A title is optional now: a section of plain bullets gets a plain
        `vX.Y.Z` release title, which is the convention going forward. See
        CONTRIBUTING.md's "Changelog entries" section.
        """
        import re
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "CHANGELOG.md")) as handle:
            text = handle.read()
        versions = re.findall(r"^## (\d+\.\d+\.\d+)$", text, re.M)
        self.assertTrue(versions, "no released versions found")
        for version in versions:
            with self.subTest(version):
                _, notes = release_notes.split(
                    release_notes.section(version, text))
                self.assertTrue(notes, f"{version} has no notes")


if __name__ == "__main__":
    unittest.main()
