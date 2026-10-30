#!/usr/bin/env python3
"""Tests for gg.py. Run with: python3 -I scripts/gg_test.py"""

import email.header
import json
import os
import sys
import tempfile
import re
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gg  # noqa: E402

GROUP_ID = "111"


def message(msg, name, account, seconds, subject, parts):
    head = [GROUP_ID, msg, [[name, "//avatar", None, account], [["The Group", "//avatar", None, GROUP_ID]]],
            0, None, subject, "snippet", [seconds, 0], [seconds], 2, GROUP_ID, ""]
    return [[head, [2, [[t, [None, h]] for t, h in parts]]], [GROUP_ID, msg, 0, 0, 0]]


def page(*blocks):
    """A page shaped like Google Groups': each block in its own script."""
    out = ["<html><body>"]
    for i, b in enumerate(blocks):
        out.append(f"<script>AF_initDataCallback({{key: 'ds:{i}', hash: '1', data:{json.dumps(b)}, "
                   f"sideChannel: {{}}}});</script>")
    out.append("</body></html>")
    return "".join(out)


def thread_page(thread, msgs, listed=None):
    summary = [GROUP_ID, thread, "Hello", "snippet", [1600000100, 0], [1600000100],
               len(msgs) if listed is None else listed, 1, 0]
    return page([[GROUP_ID, "group"], 413], [[GROUP_ID, "group"], summary, msgs])


class ParseThread(unittest.TestCase):
    def test_messages_come_out_in_time_order_with_their_fields(self):
        p = thread_page("T1", [
            message("M2", "Bea", "acct-b", 1600000100, "Re: Hello", [[1, "<div>Thanks</div>"]]),
            message("M1", "Al", "acct-a", 1600000000, "Hello", [[1, "<div>Hi</div>"]]),
        ])
        r = gg.parse_thread(p, "T1")
        self.assertEqual([m["id"] for m in r["messages"]], ["M1", "M2"])
        self.assertEqual(r["messages"][0]["date"], "2020-09-13T12:26:40Z")
        self.assertEqual(r["messages"][1]["subject"], "Re: Hello")
        self.assertEqual(r["messages_listed"], 2)
        self.assertEqual(r["last_activity"], "2020-09-13T12:28:20Z")

    def test_the_account_number_is_not_kept_but_one_account_maps_to_one_key(self):
        p = thread_page("T1", [
            message("M1", "Al", "acct-a", 1600000000, "Hello", [[1, "x"]]),
            message("M2", "Al Smith", "acct-a", 1600000100, "Re: Hello", [[1, "y"]]),
        ])
        r = gg.parse_thread(p, "T1")
        self.assertNotIn("acct-a", json.dumps(r))
        self.assertEqual(r["messages"][0]["author"], r["messages"][1]["author"])

    def test_a_sender_without_an_account_is_keyed_by_name(self):
        p = thread_page("T1", [message("M1", "Al", None, 1600000000, "Hello", [[1, "x"]])])
        self.assertEqual(gg.parse_thread(p, "T1")["messages"][0]["author"], gg.author_key(None, "Al"))

    def test_a_page_for_another_conversation_is_not_taken(self):
        p = thread_page("T1", [message("M1", "Al", "a", 1600000000, "Hello", [[1, "x"]])])
        self.assertIsNone(gg.parse_thread(p, "T2"))

    def test_a_page_without_data_gives_none(self):
        self.assertIsNone(gg.parse_thread("<html>nothing</html>", "T1"))


class ParseListing(unittest.TestCase):
    def test_the_first_page_gives_the_total_and_each_conversation(self):
        t = [GROUP_ID, "T9", "Subj", "snip", [1600000000, 5], [1600000000], 7, 0, 0]
        p = page([[GROUP_ID, "group"], 413, [[t, [GROUP_ID, "T9", 0, 0, 0]]]])
        self.assertEqual(gg.parse_listing(p), (413, [("T9", 7, "2020-09-13T12:26:40Z")]))

    def test_a_pinned_conversation_does_not_hide_the_rest(self):
        # A pinned conversation comes in a block of its own, sometimes first.
        pinned = [GROUP_ID, "P1", "Read me", "snip", [1500000000, 0], [1500000000], 1, 0, 0]
        newest = [GROUP_ID, "T9", "Subj", "snip", [1600000000, 5], [1600000000], 7, 0, 0]
        p = page([[GROUP_ID, "group"], 413, [[pinned, [GROUP_ID, "P1", 0, 0, 0]]]],
                 [[GROUP_ID, "group"], 413, [[newest, [GROUP_ID, "T9", 0, 0, 0]], [pinned, [GROUP_ID, "P1", 0, 0, 0]]]])
        total, listed = gg.parse_listing(p)
        self.assertEqual(total, 413)
        self.assertEqual(sorted(listed), [("P1", 1, "2017-07-14T02:40:00Z"), ("T9", 7, "2020-09-13T12:26:40Z")])

    def test_a_list_that_google_itself_ends_short_of_its_count_is_complete(self):
        # stan-users: Google counts 4100 and its last page shows 4081–4089.
        self.assertTrue(gg.google_ran_out(4092, "1–30 of 4100", "4081–4089 of 4100"))
        self.assertTrue(gg.google_ran_out(4089, "1–30 of 4,100", "4,081–4,089 of 4,100"))
        # A full last page, or rows numbered but not read, is a list cut short.
        self.assertFalse(gg.google_ran_out(4080, "1–30 of 4100", "4051–4080 of 4100"))
        self.assertFalse(gg.google_ran_out(4000, "1–30 of 4100", "4081–4089 of 4100"))
        # One page, or a page that said nothing: no way to tell.
        self.assertFalse(gg.google_ran_out(9, "1–9 of 12", "1–9 of 12"))
        self.assertFalse(gg.google_ran_out(9, "", ""))

    def test_only_a_link_into_the_group_is_a_conversation(self):
        # shinyapps-users' welcome text links to a forum category at /c/shiny.
        self.assertEqual(gg.thread_from_href("shinyapps-users", "./g/shinyapps-users/c/NakBajSs_4A"), "NakBajSs_4A")
        self.assertEqual(gg.thread_from_href("oscr_package", "https://groups.google.com/g/oscr_package/c/T9"), "T9")
        self.assertIsNone(gg.thread_from_href("shinyapps-users", "https://community.rstudio.com/c/shiny"))
        self.assertIsNone(gg.thread_from_href("shiny-discuss", "./g/shinyapps-users/c/NakBajSs_4A"))
        self.assertIsNone(gg.thread_from_href("shiny-discuss", None))


class HtmlToText(unittest.TestCase):
    def test_blocks_and_breaks_become_lines(self):
        self.assertEqual(gg.html_to_text("<div>one</div><div>two<br>three</div>"), "one\ntwo\nthree")

    def test_an_empty_div_with_a_break_is_a_blank_line(self):
        self.assertEqual(gg.html_to_text("<div>one</div><div><br></div><div>two</div>"), "one\n\ntwo")

    def test_quoted_text_is_marked(self):
        t = gg.html_to_text("<div>reply</div><div>On Monday, Al wrote:<blockquote><div>hi</div>"
                            "<blockquote>older</blockquote></blockquote></div>")
        self.assertEqual(t, "reply\nOn Monday, Al wrote:\n> hi\n> > older")

    def test_code_keeps_its_spacing(self):
        self.assertEqual(gg.html_to_text("<pre>x <- 1\n  y</pre>"), "x <- 1\n  y")

    def test_a_link_whose_text_differs_keeps_its_address(self):
        self.assertEqual(gg.html_to_text('<a href="https://a.org/x">here</a>'), "here <https://a.org/x>")
        self.assertEqual(gg.html_to_text('<a href="https://a.org/">https://a.org</a>'), "https://a.org")

    def test_a_plain_text_message_keeps_its_lines_paragraphs_and_spacing(self):
        t = gg.html_to_text("Hi all,<br>the code:<br>  f &lt;- function(x) {<br>    x + 1<br>  }"
                            "<p>Regards,  Dirk<br></p></p>")
        self.assertEqual(t, "Hi all,\nthe code:\n  f <- function(x) {\n    x + 1\n  }\n\nRegards,  Dirk")

    def test_raw_newlines_in_plain_text_are_line_breaks_and_not_doubled(self):
        self.assertEqual(gg.html_to_text("one\ntwo<br>\r\n<br>three"), "one\ntwo\n\nthree")

    def test_entities_and_spaces(self):
        self.assertEqual(gg.html_to_text("<div>a&amp;b&nbsp;&lt;c&gt;</div>"), "a&b <c>")


class Mbox(unittest.TestCase):
    def record(self):
        return [
            {"id": "M1", "from": "Al", "author": "aaaa", "date": "2020-09-13T12:26:40Z", "subject": "Hello",
             "parts": [[1, "<div>From here on</div>"]]},
            {"id": "M2", "from": "Bea", "author": "bbbb", "date": "2020-09-13T12:30:00Z", "subject": "Re: Hello",
             "parts": [[1, "<div>ok</div>"]]},
            {"id": "M3", "from": "Cy", "author": "cccc", "date": "2020-09-14T08:00:00Z", "subject": "Re: Hello",
             "parts": [[1, "<div>agreed</div>"], [3, "<div>On Sun, Bea wrote:<blockquote>ok</blockquote></div>"]]},
        ]

    def test_the_first_message_has_no_parent_and_says_where_it_lives(self):
        _, e = gg.mbox_entry("grp", "T1", self.record(), 0)
        self.assertTrue(e.startswith("From uaaaa at groups.google.com  Sun Sep 13 12:26:40 2020\n"))
        self.assertIn("From: uaaaa at groups.google.com (Al)\n", e)
        self.assertIn("Date: Sun, 13 Sep 2020 12:26:40 +0000\n", e)
        self.assertIn("Message-ID: <T1.M1@grp.groups.google.com>\n", e)
        self.assertIn("Archived-At: <https://groups.google.com/g/grp/c/T1/m/M1>\n", e)
        self.assertNotIn("In-Reply-To", e)

    def test_a_body_line_starting_with_from_is_escaped(self):
        _, e = gg.mbox_entry("grp", "T1", self.record(), 0)
        self.assertIn("\n>From here on\n", e)

    def test_a_reply_without_a_quote_answers_the_first_message(self):
        _, e = gg.mbox_entry("grp", "T1", self.record(), 1)
        self.assertIn("In-Reply-To: <T1.M1@grp.groups.google.com>\n", e)
        self.assertIn("References: <T1.M1@grp.groups.google.com>\n", e)

    def test_a_reply_answers_the_sender_it_quotes(self):
        _, e = gg.mbox_entry("grp", "T1", self.record(), 2)
        self.assertIn("In-Reply-To: <T1.M2@grp.groups.google.com>\n", e)
        self.assertIn("References: <T1.M1@grp.groups.google.com> <T1.M2@grp.groups.google.com>\n", e)
        self.assertIn("agreed\n\nOn Sun, Bea wrote:\n> ok", e)

    def thread(self):
        """Bea writes twice; a later reply quotes her first message, not her latest."""
        line = lambda words: f"<div>{words} and then some more words to make it long</div>"
        return [
            {"id": "M1", "from": "Al", "author": "a", "date": "2020-09-13T12:00:00Z", "subject": "Q",
             "parts": [[1, line("the model does not converge") + line("here is my lme4 call")]]},
            {"id": "M2", "from": "Bea", "author": "b", "date": "2020-09-13T13:00:00Z", "subject": "Re: Q",
             "parts": [[1, line("try scaling the predictors") + line("or another optimizer")]]},
            {"id": "M3", "from": "Cy", "author": "c", "date": "2020-09-13T14:00:00Z", "subject": "Re: Q",
             "parts": [[1, line("scaling did not help me")],
                       [3, "<div>On Sun, Bea wrote:<blockquote>" + line("try scaling the predictors")
                        + line("or another optimizer") + "</blockquote></div>"]]},
            {"id": "M4", "from": "Bea", "author": "b", "date": "2020-09-13T15:00:00Z", "subject": "Re: Q",
             "parts": [[1, line("then send a reproducible example")]]},
        ]

    def parent_of(self, record, i):
        _, e = gg.mbox_entry("grp", "T1", record, i)
        return re.search(r"In-Reply-To: <T1\.(\w+)@", e).group(1)

    def test_a_reply_answers_the_message_it_quotes_though_its_sender_wrote_later(self):
        r = self.thread()
        r.append({"id": "M5", "from": "Al", "author": "a", "date": "2020-09-13T16:00:00Z", "subject": "Re: Q",
                  "parts": [[1, "<div>thanks</div>"],
                            [3, "<div>On Sun, Bea wrote:<blockquote><div>try scaling the predictors and then some more"
                                " words to make it long</div><div>or another optimizer and then some more words to"
                                " make it long</div></blockquote></div>"]]})
        self.assertEqual("M2", self.parent_of(r, 4))

    def test_a_quote_under_a_header_in_another_language_finds_its_message(self):
        r = self.thread()
        r[2]["parts"][1][1] = r[2]["parts"][1][1].replace("On Sun, Bea wrote:", "Am So. schrieb Bea:")
        self.assertEqual("M2", self.parent_of(r, 2))

    def test_a_reply_answers_the_message_it_quotes_and_not_what_that_one_quoted(self):
        r = self.thread()
        r.append({"id": "M5", "from": "Dee", "author": "d", "date": "2020-09-13T16:00:00Z", "subject": "Re: Q",
                  "parts": [[1, "<div>same here</div>"],
                            [3, "<div><blockquote><div>scaling did not help me and then some more"
                                " words to make it long</div><div>(nothing else)</div><blockquote>"
                                "<div>try scaling the predictors and then some more words to make it long</div>"
                                "<div>or another optimizer and then some more words to make it long</div>"
                                "<div>a third line of hers, and then some more words</div>"
                                "</blockquote></blockquote></div>"]]})
        self.assertEqual("M1", self.parent_of(r, 4), "one line of Cy's is too few, and Bea's quoted twice do not count")
        r[4]["parts"][1][1] = r[4]["parts"][1][1].replace("<div>(nothing else)</div>",
            "<div>my data has many zeros, and then some more words</div>")
        r[2]["parts"][0][1] += "<div>my data has many zeros, and then some more words</div>"
        self.assertEqual("M3", self.parent_of(r, 4), "two of Cy's own lines, quoted once: Cy's message")

    def test_a_quote_too_short_to_tell_leaves_the_header_to_decide(self):
        self.assertEqual("M2", self.parent_of(self.record(), 2))

    def test_two_messages_quoted_alike_go_to_the_sender_the_header_names(self):
        r = self.thread()
        r[3]["parts"] = r[1]["parts"]
        r.append({"id": "M5", "from": "Al", "author": "a", "date": "2020-09-13T16:00:00Z", "subject": "Re: Q",
                  "parts": [[1, "<div>thanks</div>"], [3, r[2]["parts"][1][1]]]})
        self.assertEqual("M4", self.parent_of(r, 4), "Bea wrote both; the latest of hers, as before")
        r[3]["from"] = "Eve"
        self.assertEqual("M2", self.parent_of(r, 4), "the header names Bea, not Eve")

    def test_addresses_google_left_whole_are_cut_like_the_rest(self):
        r = self.record()
        r[0]["parts"] = [[1, "<div>write to someone@example.org, abc...@example.org or grp@googlegroups.com</div>"]]
        _, e = gg.mbox_entry("grp", "T1", r, 0)
        self.assertIn("write to som...@example.org, abc...@example.org or grp@googlegroups.com", e)

    def test_names_and_subjects_outside_ascii_are_encoded(self):
        r = self.record()
        r[0]["from"], r[0]["subject"] = "Zoë", "Grüße"
        _, e = gg.mbox_entry("grp", "T1", r, 0)
        heads = dict(line.split(": ", 1) for line in e.split("\n\n")[0].split("\n")[1:])
        self.assertTrue(heads["From"].isascii() and heads["Subject"].isascii())
        decode = lambda v: str(email.header.make_header(email.header.decode_header(v)))
        self.assertEqual(decode(heads["From"].split("(", 1)[1].rstrip(")")), "Zoë")
        self.assertEqual(decode(heads["Subject"]), "Grüße")

    def test_months_are_written_whole_and_in_order(self):
        with tempfile.TemporaryDirectory() as d:
            cwd = os.getcwd()
            os.chdir(d)
            try:
                os.makedirs("grp/source")
                with open("grp/source/T1.json", "w") as f:
                    json.dump({"thread": "T1", "messages": self.record()}, f)
                with open("grp/source/T0.json", "w") as f:
                    json.dump({"thread": "T0", "messages": [dict(self.record()[0], id="Z", date="2020-10-01T00:00:00Z")]}, f)
                os.makedirs("grp/raw")
                open("grp/raw/2019-May.mbox", "w").close()
                gg.cmd_mbox("grp")
                self.assertEqual(sorted(os.listdir("grp/raw")), ["2020-October.mbox", "2020-September.mbox"])
                with open("grp/raw/2020-September.mbox") as f:
                    text = f.read()
                self.assertEqual(text.count("\nMessage-ID: "), 3)
                self.assertLess(text.index("T1.M1@"), text.index("T1.M2@"))
            finally:
                os.chdir(cwd)


class GoogleName(unittest.TestCase):
    def test_a_group_can_be_kept_under_another_name(self):
        # Google allows "_" in a group's name; the viewer's list names do not.
        with tempfile.TemporaryDirectory() as d:
            cwd, saved = os.getcwd(), {k: os.environ.get(k) for k in ("IMAP_USERNAME", "IMAP_PASSWORD")}
            os.chdir(d)
            headers = HEADERS.replace("<grp.googlegroups.com>", "<oscr_package.googlegroups.com>").encode()
            real, gg.imaplib.IMAP4_SSL = gg.imaplib.IMAP4_SSL, FakeMailbox([headers])
            os.environ.update(IMAP_USERNAME="member", IMAP_PASSWORD="secret")
            err, sys.stderr = sys.stderr, __import__("io").StringIO()
            try:
                os.makedirs("oscr-package")
                with open("oscr-package/config.json", "w") as f:
                    json.dump({"list": "oscr-package", "google_group": "oscr_package"}, f)
                self.assertEqual(gg.google_name("oscr-package"), "oscr_package")
                self.assertEqual(gg.google_name("gsoc-r"), "gsoc-r", "no config.json: the same name")
                msgs = [{"id": "M1", "from": "Al", "author": "a", "date": "2020-09-13T12:00:00Z", "subject": "Hi",
                         "parts": [[1, "<div>x</div>"]]}]
                _, entry = gg.mbox_entry("oscr-package", "T1", msgs, 0)
                self.assertIn("Archived-At: <https://groups.google.com/g/oscr_package/c/T1/m/M1>", entry)
                gg.cmd_mail("oscr-package")
                self.assertEqual(os.listdir("oscr-package/mail"), [gg.short_hash("<reply-1@mail.example.org>") + ".json"])
            finally:
                sys.stderr, gg.imaplib.IMAP4_SSL = err, real
                for k, v in saved.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v
                os.chdir(cwd)


class Addresses(unittest.TestCase):
    def test_whole_addresses_are_cut_in_text_links_and_encoded_links(self):
        text = ('write to al.smith@example.org, <a href="mailto:al.smith@example.org">Al</a> or '
                '<a href="https://poll.example.net/?uid=al.smith%40example.org">vote</a>')
        cut = gg.mask_addresses(text)
        self.assertEqual(gg.whole_addresses(cut), 0)
        self.assertIn("al....@example.org", cut)
        self.assertIn("al....%40example.org", cut)

    def test_what_names_no_one_stays(self):
        text = ("grp@googlegroups.com, al...@example.org and "
                "https://groups.google.com/d/msgid/grp/CAF%2Bx%3Dyz%40mail.gmail.com, "
                "split as https://groups.google.com/d/\r\nmsgid/grp/abc.def%40mail.example.org")
        self.assertEqual(gg.mask_addresses(text), text)
        self.assertEqual(gg.whole_addresses(text), 0)

    def test_a_stored_page_has_its_addresses_cut(self):
        record = {"thread": "T1", "subject": "Proposal (al.smith@example.org)", "messages_listed": 1,
                  "last_activity": "2020-09-13T12:00:00Z",
                  "messages": [{"id": "M1", "from": "Al Smith <al.smith@example.org>", "author": "a",
                                "date": "2020-09-13T12:00:00Z", "subject": "Proposal (al.smith@example.org)",
                                "parts": [[1, '<a href="mailto:al.smith@example.org">mail me</a>']]}]}
        with tempfile.TemporaryDirectory() as d:
            cwd = os.getcwd()
            os.chdir(d)
            try:
                gg.save_thread("grp", record)
                with open("grp/source/T1.json", encoding="utf-8") as f:
                    stored = f.read()
                self.assertEqual(gg.whole_addresses(stored), 0)
                self.assertIn("Al Smith <al....@example.org>", stored)
                self.assertEqual(gg.scrub(json.loads(stored)), json.loads(stored))
            finally:
                os.chdir(cwd)

    def test_the_check_stops_a_whole_address_without_printing_it(self):
        with tempfile.TemporaryDirectory() as d:
            cwd = os.getcwd()
            os.chdir(d)
            err, sys.stderr = sys.stderr, __import__("io").StringIO()
            try:
                os.makedirs("grp/raw")
                with open("grp/config.json", "w") as f:
                    json.dump({"list": "grp"}, f)
                with open("grp/raw/2020-September.mbox", "w") as f:
                    f.write("Subject: hi\n\nmail grp@googlegroups.com or al...@example.org\n")
                gg.cmd_check("all")
                with open("grp/raw/2020-September.mbox", "a") as f:
                    f.write("or al.smith@example.org\n")
                with self.assertRaises(SystemExit) as stop:
                    gg.cmd_check("all")
                self.assertTrue(stop.exception.code)
                self.assertNotIn("al.smith", sys.stderr.getvalue() + str(stop.exception.code))
            finally:
                sys.stderr = err
                os.chdir(cwd)

    def test_the_check_names_the_field_of_a_whole_address_but_not_the_address(self):
        with tempfile.TemporaryDirectory() as d:
            cwd = os.getcwd()
            os.chdir(d)
            err, sys.stderr = sys.stderr, __import__("io").StringIO()
            try:
                os.makedirs("grp/processed")
                with open("grp/config.json", "w") as f:
                    json.dump({"list": "grp"}, f)
                with open("grp/processed/2020-09.json", "w") as f:
                    json.dump([{"from_name": "Al", "body_plain": "mail al.smith@example.org\n@app.route('/')"}], f)
                with self.assertRaises(SystemExit):
                    gg.cmd_check("grp")
                said = sys.stderr.getvalue()
                self.assertIn("grp/processed/2020-09.json: 1 whole addresses (body_plain 1, 1 only in the file's escaped text)", said)
                self.assertNotIn("al.smith", said)
            finally:
                sys.stderr = err
                os.chdir(cwd)

    def test_a_line_of_code_after_a_line_break_is_not_an_address_in_json(self):
        # stan-dev's processed months: "\n@app.route" in the file's text.
        with tempfile.TemporaryDirectory() as d:
            cwd = os.getcwd()
            os.chdir(d)
            err, sys.stderr = sys.stderr, __import__("io").StringIO()
            try:
                os.makedirs("grp/processed")
                with open("grp/config.json", "w") as f:
                    json.dump({"list": "grp"}, f)
                with open("grp/processed/2020-09.json", "w") as f:
                    json.dump([{"body_plain": "import flask\n@app.route('/')\ndef index(): pass"}], f)
                gg.cmd_check("grp")
                self.assertIn("check: no whole address", sys.stderr.getvalue())
                with open("grp/processed/2020-10.json", "w") as f:
                    json.dump({"al.smith@example.org": 1}, f)
                with self.assertRaises(SystemExit):
                    gg.cmd_check("grp")
            finally:
                sys.stderr = err
                os.chdir(cwd)

    def test_a_groups_source_can_be_checked_by_itself(self):
        with tempfile.TemporaryDirectory() as d:
            cwd = os.getcwd()
            os.chdir(d)
            err, sys.stderr = sys.stderr, __import__("io").StringIO()
            try:
                os.makedirs("grp/source")
                os.makedirs("grp/processed")
                with open("grp/config.json", "w") as f:
                    json.dump({"list": "grp"}, f)
                with open("grp/source/T1.json", "w") as f:
                    json.dump({"subject": "hi al...@example.org"}, f)
                with open("grp/processed/2020-09.json", "w") as f:
                    json.dump([{"body_plain": "al.smith@example.org"}], f)
                gg.main(["check", "grp/source"])
                with self.assertRaises(SystemExit):
                    gg.main(["check", "grp"])
                for bad in ("grp/processed", "grp/../x/source", "../grp/source"):
                    with self.assertRaises(SystemExit) as stop:
                        gg.main(["check", bad])
                    self.assertIn("not a group name", str(stop.exception.code))
            finally:
                sys.stderr = err
                os.chdir(cwd)

    def test_a_page_without_its_conversation_is_logged_without_an_address(self):
        # The title of such a page is the conversation's subject, and a log
        # on GitHub's runners is public.
        blank = "<html><head><title>Help (al.smith@example.org) - Google Groups</title></head></html>"
        get, sleep = gg.get, gg.time.sleep
        gg.get, gg.time.sleep = (lambda url, tries=4: blank), (lambda s: None)
        err, sys.stderr = sys.stderr, __import__("io").StringIO()
        try:
            self.assertIsNone(gg.fetch_thread("grp", "T1"))
            self.assertIn("no conversation on the page", sys.stderr.getvalue())
            self.assertEqual(gg.whole_addresses(sys.stderr.getvalue()), 0)
        finally:
            sys.stderr = err
            gg.get, gg.time.sleep = get, sleep


class Remove(unittest.TestCase):
    def listing(self, *threads):
        rows = [[[GROUP_ID, t, "Subj", "snip", [1600000000, 5], [1600000000], 1, 0, 0], [GROUP_ID, t, 0, 0, 0]]
                for t in threads]
        return page([[GROUP_ID, "group"], len(threads), rows])

    def test_a_removed_conversation_is_deleted_and_never_fetched_again(self):
        with tempfile.TemporaryDirectory() as d:
            cwd = os.getcwd()
            os.chdir(d)
            get, fetch = gg.get, gg.fetch_thread
            fetched = []
            out, sys.stdout = sys.stdout, __import__("io").StringIO()
            err, sys.stderr = sys.stderr, __import__("io").StringIO()
            try:
                os.makedirs("grp/source")
                msg = {"id": "M1", "from": "Al", "author": "a", "date": "2020-09-13T12:00:00Z", "subject": "Hi",
                       "parts": [[1, "<div>x</div>"]]}
                for t in ("T1", "T2"):
                    with open(f"grp/source/{t}.json", "w") as f:
                        json.dump({"thread": t, "messages": [msg], "messages_listed": 1,
                                   "last_activity": "2020-09-13T12:26:40Z"}, f)
                with open("grp/threads.txt", "w") as f:
                    f.write("T1\nT2\nT3\n")
                gg.cmd_remove("grp", ["T2", "T3"])
                gg.cmd_remove("grp", ["T3"])
                self.assertEqual(os.listdir("grp/source"), ["T1.json"])
                self.assertEqual(gg.read_threads("grp"), ["T1"])
                with open("grp/removed.txt") as f:
                    self.assertEqual([l for l in f.read().splitlines() if not l.startswith("#")], ["T2", "T3"])

                # The group's first page still shows them: neither is fetched
                # or listed again.
                gg.get = lambda url, tries=4: self.listing("T4", "T2", "T3")
                gg.fetch_thread = lambda group, thread, tries=2: fetched.append(thread)
                gg.cmd_recent("grp")
                self.assertEqual(fetched, ["T4"])
                self.assertEqual(gg.read_threads("grp"), ["T1", "T4"])
                with open("grp/threads.txt") as f:
                    self.assertEqual(f.read(), "T1\nT4\n")

                # A page stored again by hand is still left out of the mbox.
                with open("grp/source/T2.json", "w") as f:
                    json.dump({"thread": "T2", "messages": [dict(msg, id="M9")]}, f)
                gg.cmd_mbox("grp")
                with open("grp/raw/2020-September.mbox") as f:
                    text = f.read()
                self.assertIn("T1.M1@", text)
                self.assertNotIn("T2.", text)
            finally:
                sys.stdout, sys.stderr = out, err
                gg.get, gg.fetch_thread = get, fetch
                os.chdir(cwd)

    def test_remove_takes_only_conversation_ids(self):
        with tempfile.TemporaryDirectory() as d:
            cwd = os.getcwd()
            os.chdir(d)
            try:
                os.makedirs("grp/source")
                with self.assertRaises(SystemExit):
                    gg.cmd_remove("grp", ["../T1"])
                self.assertFalse(os.path.exists("grp/removed.txt"))
                err, sys.stderr = sys.stderr, __import__("io").StringIO()
                try:
                    with self.assertRaises(SystemExit):
                        gg.main(["remove", "grp"])
                    with self.assertRaises(SystemExit):
                        gg.main(["mbox", "grp", "T1"])
                finally:
                    sys.stderr = err
                out, sys.stdout = sys.stdout, __import__("io").StringIO()
                try:
                    gg.main(["remove", "grp", "--", "-T5", "T6"])
                finally:
                    sys.stdout = out
                self.assertEqual(gg.removed_threads("grp"), {"-T5", "T6"})
            finally:
                os.chdir(cwd)


HEADERS = (
    "Delivered-To: member@example.com\r\n"
    "From: 'Al Smith' via grp <grp@googlegroups.com>\r\n"
    "X-Original-From: Al Smith <al.smith@example.org>\r\n"
    "Reply-To: al.smith@example.org\r\n"
    "To: grp@googlegroups.com\r\n"
    "List-ID: <grp.googlegroups.com>\r\n"
    "Date: Sun, 13 Sep 2020 07:30:05 -0500\r\n"
    "Subject: Re: [grp] Hello\r\n"
    "Message-ID: <reply-1@mail.example.org>\r\n"
    "In-Reply-To: <start-1@mail.example.org>\r\n"
    "References: <start-1@mail.example.org>\r\n\r\n"
)


class FakeMailbox:
    """Stands in for imaplib.IMAP4_SSL: every search finds the given headers."""
    def __init__(self, emails):
        self.emails = emails

    def __call__(self, host):
        return self

    def login(self, user, password):
        pass

    def select(self, box, readonly=False):
        pass

    def uid(self, command, *args):
        if command == "SEARCH":
            return "OK", [b" ".join(str(i + 1).encode() for i in range(len(self.emails)))]
        return "OK", [(b"1 (BODY[HEADER] {0}", self.emails[int(args[0]) - 1]), b")"]

    def logout(self):
        pass


class Mail(unittest.TestCase):
    def test_a_group_email_keeps_its_time_and_hashes(self):
        r = gg.mail_record(HEADERS.encode(), "grp")
        self.assertEqual(r, {"id": gg.short_hash("<reply-1@mail.example.org>"), "date": "2020-09-13T12:30:05Z",
                             "from_hash": gg.short_hash("al smith"), "subject_hash": gg.short_hash("hello"),
                             "in_reply_to": gg.short_hash("<start-1@mail.example.org>")})

    def test_no_address_name_or_subject_is_kept(self):
        kept = json.dumps(gg.mail_record(HEADERS.encode(), "grp"))
        for text in ("@", "Al", "Smith", "Hello"):
            self.assertNotIn(text, kept)

    def test_a_sender_without_a_name_keeps_no_address(self):
        # Google puts the address where the name would be.
        nameless = HEADERS.replace("From: 'Al Smith' via grp", "From: 'al.smith@example.org' via grp")
        nameless = nameless.replace("X-Original-From: Al Smith <al.smith@example.org>\r\n", "")
        self.assertNotIn("@", json.dumps(gg.mail_record(nameless.encode(), "grp")))

    def test_an_unreadable_email_is_skipped_without_printing_it(self):
        broken = HEADERS.replace("Subject: Re: [grp] Hello", "Subject: =?x-no-such-charset?q?al.smith@example.org?=")
        broken = broken.replace("<reply-1@", "<reply-2@")
        with tempfile.TemporaryDirectory() as d:
            cwd, saved = os.getcwd(), {k: os.environ.get(k) for k in ("IMAP_USERNAME", "IMAP_PASSWORD")}
            os.chdir(d)
            os.environ.update(IMAP_USERNAME="member", IMAP_PASSWORD="secret")
            real, gg.imaplib.IMAP4_SSL = gg.imaplib.IMAP4_SSL, FakeMailbox([HEADERS.encode(), broken.encode()])
            err, sys.stderr = sys.stderr, __import__("io").StringIO()
            try:
                gg.cmd_mail("grp")
                printed = sys.stderr.getvalue()
                self.assertEqual(os.listdir("grp/mail"), [gg.short_hash("<reply-1@mail.example.org>") + ".json"])
                self.assertIn("1 could not be read", printed)
                self.assertNotIn("@", printed)
            finally:
                sys.stderr, gg.imaplib.IMAP4_SSL = err, real
                for k, v in saved.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v
                os.chdir(cwd)

    def test_mail_from_another_group_or_list_is_not_taken(self):
        self.assertIsNone(gg.mail_record(HEADERS.encode(), "other"))
        self.assertIsNone(gg.mail_record(HEADERS.replace("List-ID: <grp.googlegroups.com>\r\n", "").encode(), "grp"))

    def test_without_an_account_nothing_is_read(self):
        env = {k: os.environ.pop(k) for k in ("IMAP_USERNAME", "IMAP_PASSWORD") if k in os.environ}
        try:
            gg.cmd_mail("all")
        finally:
            os.environ.update(env)

    def test_subjects_compare_without_reply_markers_or_tags(self):
        self.assertEqual(gg.plain_subject("Re: RE: [grp] Fwd:  Hello  there"), "hello there")

    def test_the_mail_decides_which_message_a_reply_answers(self):
        with tempfile.TemporaryDirectory() as d:
            cwd = os.getcwd()
            os.chdir(d)
            try:
                msgs = [
                    {"id": "M1", "from": "Al", "author": "a", "date": "2020-09-13T12:00:00Z", "subject": "Hello",
                     "parts": [[1, "<div>start</div>"]]},
                    {"id": "M2", "from": "Bea", "author": "b", "date": "2020-09-13T12:10:00Z", "subject": "Re: Hello",
                     "parts": [[1, "<div>one</div>"]]},
                    {"id": "M3", "from": "Cy", "author": "c", "date": "2020-09-13T12:20:00Z", "subject": "Re: Hello",
                     "parts": [[1, "<div>two</div>"]]},
                ]
                os.makedirs("grp/source")
                with open("grp/source/T1.json", "w") as f:
                    json.dump({"thread": "T1", "messages": msgs}, f)
                os.makedirs("grp/mail")
                hello = gg.short_hash("hello")
                mails = [
                    {"id": "h1", "date": "2020-09-13T11:59:58Z", "from_hash": gg.short_hash("al"),
                     "subject_hash": hello, "in_reply_to": None},
                    {"id": "h2", "date": "2020-09-13T12:09:59Z", "from_hash": gg.short_hash("bea"),
                     "subject_hash": hello, "in_reply_to": "h1"},
                    {"id": "h3", "date": "2020-09-13T12:20:03Z", "from_hash": gg.short_hash("cy"),
                     "subject_hash": hello, "in_reply_to": "h2"},
                    {"id": "h4", "date": "2020-09-14T12:00:00Z", "from_hash": gg.short_hash("di"),
                     "subject_hash": hello, "in_reply_to": "h3"},
                ]
                for m in mails:
                    with open(f"grp/mail/{m['id']}.json", "w") as f:
                        json.dump(m, f)
                self.assertEqual(gg.mail_links("grp"), {("T1", "M2"): "M1", ("T1", "M3"): "M2"})
                gg.cmd_mbox("grp")
                with open("grp/raw/2020-September.mbox") as f:
                    text = f.read()
                third = text[text.index("Message-ID: <T1.M3@"):]
                self.assertIn("In-Reply-To: <T1.M2@grp.groups.google.com>", third)
            finally:
                os.chdir(cwd)


if __name__ == "__main__":
    unittest.main()
