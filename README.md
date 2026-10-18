# googlegroups-archive

Archives of Google Groups about R, pooled into one repository and laid out like the other archives in [r-mailing-lists](https://github.com/r-mailing-lists).

Some of R's busiest help forums of the 2010s were Google Groups rather than R's Mailman lists: Shiny's and ggplot2's, plyr's manipulatr, knitr's, and the R Project's own Google Summer of Code list. Most went quiet after RStudio opened its Discourse forum, RStudio Community, in 2017, and Google Groups gives readers no way to download what they hold. This repository keeps a copy.

| Group | What it was | State |
|---|---|---|
| `shiny-discuss` | Shiny, the web application framework for R | sent to RStudio Community in September 2017 |
| `ggplot2` | Help with ggplot2 | superseded by RStudio Community in 2017 |
| `shinyapps-users` | Support for the shinyapps.io service | locked for new posts in September 2018 |
| `manipulatr` | Data manipulation with plyr and reshape | sent to RStudio Community |
| `knitr` | The knitr package | no longer active |
| `gsoc-r` | The R Project in Google Summer of Code, since 2010 | active, updated daily |

See [`lists.tsv`](lists.tsv) for message counts and date ranges. A group with only a `config.json` and `mail/` is still being collected and joins the table once all its conversations are in.

## How it is collected

Google Groups has no export for readers, so `scripts/gg.py` reads the pages a signed-out visitor sees, which the site's robots.txt allows, one page every two seconds. A conversation's page carries its messages as structured data: an ID, the sender's name, the time to the second, the subject, and the body as HTML. That is kept as served.

A signed-out visitor never sees a message as it was sent, so three things that the Mailman archives have are missing here:

- **Addresses.** Google cuts most addresses in message text to their first characters (`abc...@example.org`) but leaves whole the ones in subjects, in senders' names and in links. Those are cut the same way before a page is stored, and the daily run publishes nothing if a whole address turns up in any file. Google's links to a message carry its Message-ID, which is shaped like an address but names no one; those are kept. Each sender is identified by a hash of their Google account number, so one person's messages stay together within this repository, but they are not matched to the same person on the Mailman lists.
- **Message-ID headers.** Each message gets one built from its Google IDs, `<conversation.message@group.groups.google.com>`.
- **Reply headers.** Google shows a conversation as a flat list in time order. A reply is attached to the message its email says it answers (see below), or else to the earlier message whose sender it quotes ("On ..., Name wrote:"), or else to the message that started the conversation.

## New messages by email

A member account receives each active group's new messages by email. The daily run reads only their headers and keeps when each was sent, plus hashes of its Message-ID, of the message it answers, and of its sender's name and subject, which find it among the pages. Which message a reply answers is what the pages lack: from the day the account joined a group, a reply hangs off the message it actually answers rather than the one its quote suggests. The text still comes from the pages. Nothing readable is kept from the email: no address, neither the account's nor any sender's, and nothing of a message that is later deleted from the group. The account's credentials are repository secrets.

## Structure

Each group has its own directory:

- `<group>/config.json` - group name, description, source URL, and whether it is still active
- `<group>/threads.txt` - every conversation's ID, as the group's own list gives them
- `<group>/source/` - one JSON file per conversation, as Google serves it to a signed-out reader, with addresses cut
- `<group>/mail/` - one JSON file per message that reached the member account by email
- `<group>/raw/` - one Mailman-style .mbox file per month, written from `source/` and `mail/`
- `<group>/processed/` - structured JSON from [rmail-parser](https://github.com/r-mailing-lists/rmail-parser), one file per month, each message linking to its page at Google
- `<group>/meta.json` - group statistics

`lists.tsv` at the top level indexes all groups.

## Updating

The **Update Archive** workflow runs daily. For a group marked `"active": true` it reads the group's first page and fetches the conversations that are new or have changed; closed groups are only rebuilt from what is stored. Locally:

``` bash
RMAIL_PARSER=/path/to/rmail-parser scripts/update.sh
```

To add a group, create `<group>/config.json` and list and fetch its conversations once. Until its `threads.txt` is in, the daily run only reads the group's mail. A group whose name at Google holds a character the viewer's list names cannot (Google allows `_`) keeps a name of its own here and gives Google's as `"google_group"` in its config.json. Listing pages through the group in a headless browser and needs [Playwright](https://playwright.dev/python/):

``` bash
python3 scripts/gg.py list <group>
python3 scripts/gg.py fetch <group>
scripts/update.sh --no-fetch <group>
```

`python3 scripts/gg_test.py` runs the tests, and `python3 scripts/gg.py check all` looks for whole addresses in every file, saying where but not what.
