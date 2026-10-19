#!/usr/bin/env python3
"""Archive public Google Groups as Mailman-style mbox files.

Google Groups offers its readers no export, so this reads the pages a
signed-out visitor sees, which robots.txt allows. A conversation's page
carries its messages as structured data: an ID, the sender's name and
account number, the time to the second, the subject and the body as HTML.
That is kept as it is, with every whole address cut, one file per
conversation, in <group>/source/, and
written out as one mbox file per month in <group>/raw/, shaped like
Mailman's, for rmail-parser.

What a signed-out visitor cannot see is each message as it was sent, so
there are no Message-ID or In-Reply-To headers and no addresses. Each
message gets a Message-ID made from its Google ID, and a reply is attached
to the earlier message whose sender it quotes, or else to the message that
started the conversation.

Commands:
  describe GROUP print a group's description, size and newest activity
  list GROUP     page through the group's conversations in a headless
                 browser (needs Playwright) and write GROUP/threads.txt
  fetch GROUP    fetch the conversations in threads.txt that have no file
                 in GROUP/source/ yet (--all fetches every one again)
  recent GROUP   read the group's first page and fetch the conversations
                 on it that are new or have changed
  mail GROUP     read the headers of the group's email in the member
                 account (GROUP "all": every active group)
  scrub GROUP    cut the addresses in GROUP/source/ stored before they were
  mbox GROUP     write GROUP/raw/ from GROUP/source/
  urls GROUP     give each message in GROUP/processed/ its page at Google
  check GROUP    stop if any of GROUP's files holds a whole address
                 (GROUP "all": every group, and lists.tsv)
"""

import argparse
import datetime as dt
import email.header
import hashlib
import html
import html.parser
import imaplib
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request

BASE = "https://groups.google.com/g/"
UA = "googlegroups-archive (+https://github.com/r-mailing-lists/googlegroups-archive)"
DELAY = float(os.environ.get("GG_DELAY", "2"))
MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]


# -- fetching ---------------------------------------------------------------

def get(url, tries=4):
    """One page, with a pause before it. Waits longer after each refusal."""
    wait = 60
    for attempt in range(tries):
        time.sleep(DELAY)
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-US"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code not in (429, 500, 502, 503, 504) or attempt == tries - 1:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == tries - 1:
                raise
        print(f"  waiting {wait}s before trying {url} again", file=sys.stderr)
        time.sleep(wait)
        wait *= 2


def data_blocks(page):
    """The JSON the page hands its scripts, one value per AF_initDataCallback."""
    pat = re.compile(r"AF_initDataCallback\(\{key: '[^']+'.*?data:(.*?), sideChannel: \{\}\}\);</script>", re.S)
    for m in pat.finditer(page):
        try:
            yield json.loads(m.group(1))
        except json.JSONDecodeError:
            continue


def author_key(account, name):
    """A stand-in for the sender's address: the same for every message one
    Google account sent, and not the account number itself."""
    basis = f"account:{account}" if account else f"name:{name}"
    return hashlib.sha256(basis.encode()).hexdigest()[:16]


def iso(seconds):
    return dt.datetime.fromtimestamp(seconds, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_thread(page, thread):
    """A conversation page as a record, or None if it holds no conversation."""
    for d in data_blocks(page):
        if not (isinstance(d, list) and len(d) >= 3 and isinstance(d[1], list)
                and len(d[1]) > 6 and d[1][1] == thread and isinstance(d[2], list)):
            continue
        summary = d[1]
        messages = []
        for entry in d[2]:
            head, body = entry[0][0], entry[0][1]
            sender = head[2][0] if head[2] and head[2][0] else [None, None, None, None]
            name = (sender[0] or "").strip()
            account = sender[3] if len(sender) > 3 else None
            parts = [[p[0], p[1][1] or ""] for p in (body[1] if body and len(body) > 1 and body[1] else [])
                     if isinstance(p, list) and len(p) > 1 and isinstance(p[1], list)]
            messages.append({
                "id": head[1],
                "from": name,
                "author": author_key(account, name),
                "date": iso(head[7][0]),
                "subject": head[5] or "",
                "parts": parts,
            })
        messages.sort(key=lambda m: (m["date"], m["id"]))
        return {
            "thread": thread,
            "subject": summary[2] or "",
            "messages_listed": summary[6],
            "last_activity": iso(summary[4][0]),
            "messages": messages,
        }
    return None


def parse_listing(page):
    """The conversations on a group's first page: (id, messages, last activity).
    A pinned conversation comes in a block of its own, sometimes ahead of the
    rest, so every block is read."""
    total, out = None, {}
    for d in data_blocks(page):
        if (isinstance(d, list) and len(d) >= 3 and isinstance(d[1], int)
                and isinstance(d[2], list) and d[2] and isinstance(d[2][0], list)):
            total = d[1]
            for entry in d[2]:
                t = entry[0]
                out.setdefault(t[1], (t[1], t[6], iso(t[4][0])))
    return total, list(out.values())


def thread_from_href(gname, href):
    """The conversation a link on the group's list opens, or None for a link
    anywhere else (a group's welcome text can link a forum's /c/ category)."""
    m = re.search(rf"/g/{re.escape(gname)}/c/([A-Za-z0-9_-]+)", href or "", re.I)
    return m.group(1) if m else None


# -- files ------------------------------------------------------------------

def write_atomic(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix=".tmp-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def read_threads(group):
    path = os.path.join(group, "threads.txt")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def write_threads(group, ids):
    write_atomic(os.path.join(group, "threads.txt"), "".join(i + "\n" for i in ids))


def google_name(group):
    """The group's name at Google: its directory's name, unless config.json
    gives another. Google allows "_" in a name, and the viewer's list names
    do not."""
    path = os.path.join(group, "config.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("google_group") or group
    return group


def source_path(group, thread):
    return os.path.join(group, "source", thread + ".json")


def save_thread(group, record):
    scrub(record)
    write_atomic(source_path(group, record["thread"]),
                 json.dumps(record, ensure_ascii=False, indent=1) + "\n")


class Refused(Exception):
    """Google kept serving pages without the conversations on them."""


def fetch_thread(group, thread, tries=2):
    """Fetch and store one conversation. A page without its data is asked
    for again after a pause; Google now and then serves one."""
    for attempt in range(tries):
        page = get(f"{BASE}{google_name(group)}/c/{thread}")
        if page is None:
            print(f"  {thread}: gone", file=sys.stderr)
            return None
        record = parse_thread(page, thread)
        if record is not None:
            break
        title = re.search(r"<title>(.*?)</title>", page, re.S)
        print(f"  {thread}: no conversation on the page ({len(page)} bytes, "
              f"title {mask_addresses(title.group(1).strip())[:60]!r})" if title
              else f"  {thread}: no conversation on the page",
              file=sys.stderr)
        if attempt < tries - 1:
            time.sleep(30)
    else:
        return None
    save_thread(group, record)
    if len(record["messages"]) < record["messages_listed"]:
        print(f"  {thread}: {len(record['messages'])} of {record['messages_listed']} messages on the page",
              file=sys.stderr)
    return record


# -- commands ---------------------------------------------------------------

def cmd_list(group):
    from playwright.sync_api import sync_playwright

    ids, total, gname = [], None, google_name(group)
    first_shown = last_shown = ""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(user_agent=UA, locale="en-US")
        page.goto(BASE + gname, wait_until="networkidle")
        while True:
            for href in page.eval_on_selector_all("a[href*='/c/']", "els => els.map(e => e.getAttribute('href'))"):
                thread = thread_from_href(gname, href)
                if thread and thread not in ids:
                    ids.append(thread)
            shown = page.inner_text("div.aEb7Ed") if page.query_selector("div.aEb7Ed") else ""
            m = re.search(r"of ([\d,]+)", shown)
            if m:
                total = int(m.group(1).replace(",", ""))
            first_shown, last_shown = first_shown or shown, shown or last_shown
            print(f"  {shown or '?'}: {len(ids)} conversations so far", file=sys.stderr)
            button = page.query_selector("div[aria-label='Next page']")
            if not button or button.get_attribute("aria-disabled") == "true":
                break
            first = page.eval_on_selector("a[href*='/c/']", "e => e.getAttribute('href')")
            button.click()
            page.wait_for_function(
                "f => { const a = document.querySelector(\"a[href*='/c/']\"); return a && a.getAttribute('href') !== f; }",
                arg=first, timeout=60000)
            page.wait_for_load_state("networkidle")
            time.sleep(DELAY)
        browser.close()
    known = read_threads(group)
    merged = known + [i for i in ids if i not in known]
    write_threads(group, merged)
    print(f"{group}: {len(ids)} conversations listed, the group says {total}; {len(merged)} in threads.txt")
    if total is not None and len(ids) < total:
        if not google_ran_out(len(ids), first_shown, last_shown):
            sys.exit(f"{group}: the list ended before the count the group gives")
        print(f"{group}: Google's own list ends there ({last_shown}); "
              "it counts conversations it shows no one signed out")


def shown_rows(shown):
    """The first and last row a page of the list says it shows, from its
    "1–30 of 4100", or None."""
    m = re.search(r"([\d,]+)\s*[–-]\s*([\d,]+) of", shown or "")
    return tuple(int(x.replace(",", "")) for x in m.groups()) if m else None


def google_ran_out(listed, first_shown, last_shown):
    """Whether a list that stopped short of the group's count stopped
    because Google had no more to show: its last page is only part full,
    and every row it numbered was read."""
    first, last = shown_rows(first_shown), shown_rows(last_shown)
    if not first or not last or last == first:
        return False
    return last[1] - last[0] < first[1] - first[0] and listed >= last[1]


def cmd_fetch(group, everything):
    ids = read_threads(group)
    todo = [t for t in ids if everything or not os.path.exists(source_path(group, t))]
    print(f"{group}: fetching {len(todo)} of {len(ids)} conversations", file=sys.stderr)
    failed = 0
    for n, thread in enumerate(todo, 1):
        if fetch_thread(group, thread) is None:
            failed += 1
            if failed >= 5:
                raise Refused(f"{group}: five conversations in a row came back without their data; stopping")
        else:
            failed = 0
        if n % 50 == 0:
            print(f"  {n} of {len(todo)}", file=sys.stderr)
    missing = [t for t in ids if not os.path.exists(source_path(group, t))]
    print(f"{group}: {len(ids) - len(missing)} of {len(ids)} conversations stored", file=sys.stderr)


def cmd_recent(group):
    page = get(BASE + google_name(group))
    if page is None:
        sys.exit(f"{group}: the group's page is gone")
    total, listed = parse_listing(page)
    if total is None:
        sys.exit(f"{group}: no conversations on the group's page")
    ids = read_threads(group)
    changed = []
    for thread, count, last in listed:
        path = source_path(group, thread)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                old = json.load(f)
            if old["messages_listed"] == count and old["last_activity"] == last:
                continue
        changed.append(thread)
    new = [t for t, _, _ in listed if t not in ids]
    if new:
        write_threads(group, ids + new)
    print(f"{group}: {len(changed)} of the {len(listed)} newest conversations are new or changed", file=sys.stderr)
    if listed and len(changed) == len(listed):
        print(f"  every conversation on the first page changed; run list to find any further back",
              file=sys.stderr)
    for thread in changed:
        fetch_thread(group, thread)


class TextOf(html.parser.HTMLParser):
    """Plain text from a message's HTML, with quoted text marked by '> '."""

    BLOCK = {"div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "table", "ul", "ol", "hr"}

    def __init__(self, keep_spaces=False):
        super().__init__(convert_charrefs=True)
        self.keep_spaces = keep_spaces
        self.lines = [[0, ""]]
        self.depth = 0
        self.pre = 0
        self.href = None
        self.link = ""
        self.skip = 0

    def newline(self):
        if self.lines[-1][1]:
            self.lines.append([self.depth, ""])
        else:
            self.lines[-1][0] = self.depth

    def blank(self):
        self.lines.append([self.depth, ""])

    def paragraph(self):
        self.newline()
        if len(self.lines) >= 2 and self.lines[-2][1]:
            self.blank()

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script"):
            self.skip += 1
        elif tag == "blockquote":
            self.newline()
            self.depth += 1
            self.lines[-1][0] = self.depth
        elif tag == "br":
            self.blank() if not self.lines[-1][1] else self.newline()
        elif tag == "p":
            self.paragraph()
        elif tag in self.BLOCK:
            self.newline()
            if tag == "pre":
                self.pre += 1
        elif tag == "a":
            self.href = dict(attrs).get("href")
            self.link = ""

    def handle_endtag(self, tag):
        if tag in ("style", "script"):
            self.skip = max(0, self.skip - 1)
        elif tag == "blockquote":
            self.depth = max(0, self.depth - 1)
            self.newline()
        elif tag in self.BLOCK or tag == "p":
            if tag == "pre":
                self.pre = max(0, self.pre - 1)
            self.newline()
        elif tag == "a":
            href, self.href = self.href, None
            if href and href.startswith("http") and href.rstrip("/") != self.link.strip().rstrip("/"):
                self.write(f" <{href}>")

    def write(self, text):
        parts = text.split("\n")
        for i, part in enumerate(parts):
            if i:
                self.lines.append([self.depth, ""])
            self.lines[-1][1] += part

    def handle_data(self, data):
        if self.skip:
            return
        if not self.pre and not self.keep_spaces:
            data = re.sub(r"[ \t\r\n]+", " ", data)
            if not self.lines[-1][1]:
                data = data.lstrip(" ")
        if self.href is not None:
            self.link += data
        self.write(data)

    def text(self):
        out = []
        for depth, line in self.lines:
            line = line.replace(" ", " ").rstrip()
            out.append(("> " * depth + line).rstrip() if depth else line)
        text = "\n".join(out)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip("\n")


def html_to_text(markup):
    """Plain text from a message part. A message sent as plain text comes as
    lines joined by <br> and paragraphs by <p>, with its spacing as sent;
    that spacing is kept, since it is often the indentation of R code."""
    plain = not re.search(r"<(div|table|span|font|ul|ol|h\d|blockquote)\b", markup, re.I)
    if plain:
        markup = markup.replace("\r\n", "\n").replace("\r", "\n")
        markup = re.sub(r"\n?(<br\s*/?>)\n?", r"\1", markup, flags=re.I)
        markup = markup.replace("\n", "<br>")
    p = TextOf(keep_spaces=plain)
    p.feed(markup)
    p.close()
    return p.text()


def message_id(group, thread, msg):
    return f"<{thread}.{msg}@{group}.groups.google.com>"


def find_parent(messages, i, text):
    """The earlier message a reply answers: the latest one from the sender
    its quote header names, or else the first in the conversation."""
    m = re.search(r"(?m)^(?:> )*(On .{6,200}?)wrote:\s*$", text)
    if m:
        header = m.group(1)
        for j in range(i - 1, -1, -1):
            name = messages[j]["from"]
            if name and name in header:
                return j
    return 0


def mbox_entry(group, thread, messages, i, parent=None):
    """One message as Mailman writes it. parent is the index of the message
    it answers, when the mail says so; otherwise the text decides."""
    msg = messages[i]
    text = "\n\n".join(t for t in (html_to_text(h) for _, h in msg["parts"]) if t)
    when = dt.datetime.strptime(msg["date"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    mailbox = f"u{msg['author']} at groups.google.com"
    name = mask_addresses(msg["from"]).replace("(", "[").replace(")", "]") or "unknown"
    subject = re.sub(r"\s+", " ", mask_addresses(msg["subject"])).strip()
    headers = [
        f"From {mailbox}  {when.strftime('%a %b %e %H:%M:%S %Y')}",
        f"From: {mailbox} ({encode_header(name)})",
        f"Date: {when.strftime('%a, %d %b %Y %H:%M:%S +0000')}",
        f"Subject: {encode_header(subject)}",
        f"Message-ID: {message_id(group, thread, msg['id'])}",
    ]
    if i > 0:
        if parent is None or not 0 <= parent < i:
            parent = find_parent(messages, i, text)
        root = message_id(group, thread, messages[0]["id"])
        mid = message_id(group, thread, messages[parent]["id"])
        headers.append(f"In-Reply-To: {mid}")
        headers.append(f"References: {root}" + ("" if parent == 0 else f" {mid}"))
    headers += [
        f"Archived-At: <{BASE}{google_name(group)}/c/{thread}/m/{msg['id']}>",
        "MIME-Version: 1.0",
        'Content-Type: text/plain; charset="utf-8"',
        "Content-Transfer-Encoding: 8bit",
    ]
    body = re.sub(r"(?m)^From ", ">From ", mask_addresses(text))
    return when, "\n".join(headers) + "\n\n" + body + "\n\n"


# Google cuts most addresses in a message's text to abc...@example.org, but
# leaves whole the ones in subjects, in senders' names and in links, plain
# or URL-encoded (%40). Every one is cut the same way before it is stored.
ADDRESS = re.compile(r"([A-Za-z0-9._%+-]+)(@|%40)([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")
# Google's footer links each message to its page by its Message-ID, which is
# shaped like an address but names no one: https://groups.google.com/d/msgid/
# <group>/<Message-ID>, the link sometimes broken across lines.
MSGID_LINK = re.compile(r"msgid/[^/\s]+/[^\s\"'<>/]*$")


def names_no_one(text, m):
    """Whether an address-shaped match is already cut, a group's own address,
    or a Message-ID in one of Google's links."""
    local, _, domain = m.groups()
    return (local.endswith("...") or domain.lower().endswith(("googlegroups.com", "groups.google.com"))
            or MSGID_LINK.search(text, max(0, m.start() - 300), m.start()) is not None)


def mask_addresses(text):
    """Cut every whole address to the first characters of its mailbox."""
    def cut(m):
        if names_no_one(text, m):
            return m.group(0)
        local, at, domain = m.groups()
        return f"{local[:max(1, min(3, len(local) - 1))]}...{at}{domain}"
    return ADDRESS.sub(cut, text)


def whole_addresses(text):
    return sum(1 for m in ADDRESS.finditer(text) if not names_no_one(text, m))


def scrub(record):
    """A stored conversation with its addresses cut, in place."""
    record["subject"] = mask_addresses(record.get("subject") or "")
    for m in record["messages"]:
        m["from"] = mask_addresses(m["from"])
        m["subject"] = mask_addresses(m["subject"])
        m["parts"] = [[t, mask_addresses(h) if isinstance(h, str) else h] for t, h in m["parts"]]
    return record


def cmd_scrub(group):
    """Cut the addresses in conversations stored before they were cut."""
    src = os.path.join(group, "source")
    changed = 0
    for name in sorted(os.listdir(src)) if os.path.isdir(src) else []:
        if not name.endswith(".json"):
            continue
        with open(os.path.join(src, name), encoding="utf-8") as f:
            record = json.load(f)
        before = json.dumps(record, ensure_ascii=False)
        if json.dumps(scrub(record), ensure_ascii=False) != before:
            save_thread(group, record)
            changed += 1
    if changed:
        print(f"{group}: addresses cut in {changed} conversations", file=sys.stderr)


def json_fields(value, key=""):
    """(field, text) for every string in a decoded JSON value."""
    if isinstance(value, str):
        yield key, value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from json_fields(v, k)
    elif isinstance(value, list):
        for v in value:
            yield from json_fields(v, key)


def fields_holding_addresses(text):
    """Which fields of a JSON file hold its whole addresses, by name and
    count, for the check to say where they are without saying what they
    are. Those found only in the file's text, and not in any field once it
    is decoded, are counted apart: an escape such as \\n can join a word to
    what follows it."""
    try:
        value = json.loads(text)
    except ValueError:
        return ""
    counts = {}
    for field, s in json_fields(value):
        n = whole_addresses(s)
        if n:
            counts[field or "(value)"] = counts.get(field or "(value)", 0) + n
    parts = [f"{field} {n}" for field, n in sorted(counts.items())]
    escaped = whole_addresses(text) - sum(counts.values())
    if escaped > 0:
        parts.append(f"{escaped} only in the file's escaped text")
    return f" ({', '.join(parts)})" if parts else ""


def cmd_check(group):
    """Stop if any file about to be published holds a whole address. It
    says where, never what: the run's log is public."""
    groups = [group] if group != "all" else sorted(
        os.path.dirname(c) for c in __import__("glob").glob("*/config.json"))
    paths = ["lists.tsv"] if group == "all" and os.path.exists("lists.tsv") else []
    for g in groups:
        for top, _, files in os.walk(g):
            paths += [os.path.join(top, f) for f in sorted(files) if not f.startswith(".")]
    found = 0
    for path in paths:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
        n = whole_addresses(text)
        if n:
            where = fields_holding_addresses(text) if path.endswith(".json") else ""
            print(f"{path}: {n} whole addresses{where}", file=sys.stderr)
            found += n
    if found:
        sys.exit(f"check: {found} whole addresses; nothing is published until they are cut")
    print(f"check: no whole address in {len(paths)} files", file=sys.stderr)


def encode_header(value):
    if value.isascii():
        return value
    return email.header.Header(value, "utf-8").encode()


def cmd_mbox(group):
    src = os.path.join(group, "source")
    links = mail_links(group)
    periods = {}
    for name in sorted(os.listdir(src)) if os.path.isdir(src) else []:
        if not name.endswith(".json"):
            continue
        with open(os.path.join(src, name), encoding="utf-8") as f:
            record = json.load(f)
        msgs = record["messages"]
        index = {m["id"]: i for i, m in enumerate(msgs)}
        for i in range(len(msgs)):
            answers = links.get((record["thread"], msgs[i]["id"]))
            when, entry = mbox_entry(group, record["thread"], msgs, i, index.get(answers))
            key = f"{when.year}-{MONTHS[when.month - 1]}"
            periods.setdefault(key, []).append((when, record["thread"], msgs[i]["id"], entry))
    raw = os.path.join(group, "raw")
    os.makedirs(raw, exist_ok=True)
    for old in os.listdir(raw):
        if old.endswith(".mbox") and old[:-5] not in periods:
            os.remove(os.path.join(raw, old))
    total = 0
    for key, entries in periods.items():
        entries.sort(key=lambda e: e[:3])
        total += len(entries)
        write_atomic(os.path.join(raw, key + ".mbox"), "".join(e[3] for e in entries))
    print(f"{group}: {total} messages in {len(periods)} monthly files")


# -- mail -------------------------------------------------------------------
#
# A member of a group gets each new message by email, headers and all. The
# headers say which message a reply answers, which the pages do not. Only
# that is kept: when the message was sent, and hashes of its Message-ID, of
# the one it answers, and of its sender's name and subject, which find it on
# its page. Nothing readable is written: no address (Google puts a sender's
# address where a missing name would be), and nothing of a message that is
# later deleted from the group.

def short_hash(value):
    return hashlib.sha256(value.strip().encode()).hexdigest()[:16]


def plain_subject(subject):
    s = re.sub(r"\s+", " ", subject or "").strip()
    while True:
        t = re.sub(r"(?i)^((re|fwd?|aw|sv|wg)\s*:\s*|\[[^\]]{1,40}\]\s*)", "", s)
        if t == s:
            return s.lower()
        s = t


def mail_record(raw_headers, group):
    """The record kept for one email from a group, or None if it is not one."""
    import email
    import email.utils
    msg = email.message_from_bytes(raw_headers)
    if (msg.get("List-ID") or "").strip().lower().find(f"<{group}.googlegroups.com>") < 0:
        return None
    mid = (msg.get("Message-ID") or "").strip()
    if not mid:
        return None
    try:
        when = email.utils.parsedate_to_datetime(msg.get("Date")).astimezone(dt.timezone.utc)
    except (TypeError, ValueError):
        return None
    name, _ = email.utils.parseaddr(str(email.header.make_header(email.header.decode_header(
        msg.get("X-Original-From") or msg.get("From") or ""))))
    name = re.sub(r"^'(.*)' via .+$|^(.*) via .+$", lambda m: m.group(1) or m.group(2), name).strip().strip("'\"")
    # Cut like the stored pages, so the hashes agree with theirs.
    name = mask_addresses(name)
    subject = mask_addresses(str(email.header.make_header(email.header.decode_header(msg.get("Subject") or ""))))
    ids = lambda h: re.findall(r"<[^<>\s]+>", msg.get(h) or "")
    replies = ids("In-Reply-To") or ids("References")[-1:]
    return {
        "id": short_hash(mid),
        "date": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "from_hash": short_hash(name.lower()) if name else None,
        "subject_hash": short_hash(plain_subject(subject)),
        "in_reply_to": short_hash(replies[-1]) if replies else None,
    }


def cmd_mail(group):
    """Read the member account's mail for each active group, headers only.
    The account comes from IMAP_USERNAME and IMAP_PASSWORD (an app password)."""
    user, password = os.environ.get("IMAP_USERNAME"), os.environ.get("IMAP_PASSWORD")
    if not user or not password:
        print("mail: no account configured, nothing read", file=sys.stderr)
        return
    groups = [group] if group != "all" else sorted(
        os.path.dirname(c) for c in __import__("glob").glob("*/config.json")
        if json.load(open(c, encoding="utf-8")).get("active"))
    days = int(os.environ.get("GG_MAIL_DAYS", "14"))
    since = (dt.date.today() - dt.timedelta(days=days)).strftime("%d-%b-%Y")
    box = imaplib.IMAP4_SSL(os.environ.get("IMAP_HOST", "imap.gmail.com"))
    try:
        box.login(user, password)
    except imaplib.IMAP4.error:
        sys.exit("mail: the account's login was refused")
    try:
        box.select('"[Gmail]/All Mail"', readonly=True)
        for g in groups:
            typ, data = box.uid("SEARCH", None, "SINCE", since, "HEADER", "List-ID", f"{google_name(g)}.googlegroups.com")
            uids = data[0].split() if typ == "OK" and data and data[0] else []
            new = unreadable = 0
            for uid in uids:
                typ, got = box.uid("FETCH", uid, "(BODY.PEEK[HEADER])")
                raw = next((part[1] for part in got if isinstance(part, tuple)), None) if typ == "OK" else None
                # The run's log is public, so a header that will not parse is
                # counted, never shown.
                try:
                    record = mail_record(raw, google_name(g)) if raw else None
                except Exception:
                    record, unreadable = None, unreadable + 1
                if record is None:
                    continue
                path = os.path.join(g, "mail", record["id"] + ".json")
                if not os.path.exists(path):
                    write_atomic(path, json.dumps(record, ensure_ascii=False, indent=1) + "\n")
                    new += 1
            print(f"{g}: {new} new of {len(uids)} emails in the last {days} days"
                  + (f", {unreadable} could not be read" if unreadable else ""), file=sys.stderr)
    finally:
        box.logout()


def mail_links(group):
    """Which page message answers which, as far as the mail says:
    {(conversation, message): message it answers}."""
    folder = os.path.join(group, "mail")
    if not os.path.isdir(folder):
        return {}
    mails = []
    for name in sorted(os.listdir(folder)):
        if name.endswith(".json"):
            with open(os.path.join(folder, name), encoding="utf-8") as f:
                mails.append(json.load(f))
    pages = {}
    src = os.path.join(group, "source")
    for name in sorted(os.listdir(src)) if os.path.isdir(src) else []:
        if name.endswith(".json"):
            with open(os.path.join(src, name), encoding="utf-8") as f:
                record = json.load(f)
            for m in record["messages"]:
                pages.setdefault(short_hash(plain_subject(m["subject"])), []).append((record["thread"], m))
    # An email is its page message when the subjects agree and the times are
    # within a quarter of an hour; the nearest wins, a sender's name breaking ties.
    found = {}
    for mail in mails:
        t = dt.datetime.strptime(mail["date"], "%Y-%m-%dT%H:%M:%SZ")
        best = None
        for thread, m in pages.get(mail["subject_hash"], []):
            gap = abs((dt.datetime.strptime(m["date"], "%Y-%m-%dT%H:%M:%SZ") - t).total_seconds())
            if gap <= 900:
                key = (gap, short_hash(m["from"].lower()) != mail["from_hash"])
                if best is None or key < best[0]:
                    best = (key, thread, m["id"])
        if best:
            found[mail["id"]] = (best[1], best[2])
    links = {}
    for mail in mails:
        here, there = found.get(mail["id"]), found.get(mail["in_reply_to"])
        if here and there and here[0] == there[0] and here != there:
            links[here] = there[1]
    return links


def cmd_urls(group):
    proc = os.path.join(group, "processed")
    pat = re.compile(r"^<([A-Za-z0-9_-]+)\.([A-Za-z0-9_-]+)@" + re.escape(group) + r"\.groups\.google\.com>$")
    for name in sorted(os.listdir(proc)):
        if not re.match(r"^\d{4}-\d{2}\.json$", name):
            continue
        path = os.path.join(proc, name)
        with open(path, encoding="utf-8") as f:
            month = json.load(f)
        for m in month.get("messages", []):
            hit = pat.match(m.get("message_id", ""))
            if hit:
                m["source_url"] = f"{BASE}{google_name(group)}/c/{hit.group(1)}/m/{hit.group(2)}"
        write_atomic(path, json.dumps(month, ensure_ascii=False, indent=2) + "\n")


def cmd_describe(group):
    """Print what a new group's config.json needs: its description, how many
    conversations it has, and when the newest one was active."""
    page = get(BASE + google_name(group))
    if page is None:
        sys.exit(f"{group}: no such group")
    title = description = ""
    for d in data_blocks(page):
        if isinstance(d, list) and len(d) > 2 and isinstance(d[1], str) and isinstance(d[2], str):
            title, description = d[1].strip(), re.sub(r"\s+", " ", d[2]).strip()
            break
    total, listed = parse_listing(page)
    newest = max((last for _, _, last in listed), default=None)
    print(json.dumps({"list": group, "title": title, "description": description,
                      "conversations": total, "last_activity": newest}, ensure_ascii=False))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["describe", "list", "fetch", "recent", "mail", "scrub", "mbox", "urls",
                                        "check"])
    ap.add_argument("group")
    ap.add_argument("--all", action="store_true", help="fetch: every conversation, not just new ones")
    a = ap.parse_args(argv)
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", a.group):
        sys.exit(f"not a group name: {a.group}")
    {"describe": lambda: cmd_describe(a.group),
     "list": lambda: cmd_list(a.group),
     "fetch": lambda: cmd_fetch(a.group, a.all),
     "recent": lambda: cmd_recent(a.group),
     "mail": lambda: cmd_mail(a.group),
     "scrub": lambda: cmd_scrub(a.group),
     "mbox": lambda: cmd_mbox(a.group),
     "urls": lambda: cmd_urls(a.group),
     "check": lambda: cmd_check(a.group)}[a.command]()


if __name__ == "__main__":
    main()
