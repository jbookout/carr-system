#!/usr/bin/env python3
"""Pin tools/mail-extract.py's contract without touching Apple Mail.

The extractor's value is that the matcher can read what it writes, and its
RISK is that it quietly starts carrying message bodies. Both are pinned here.
"""
import importlib.util
import inspect
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
FAILS: list[str] = []


def check(label, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + label)
    if not ok:
        FAILS.append(f"{label}: {detail}")


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mx = load("mail_extract", "tools/mail-extract.py")

check("a display-name sender resolves to the bare address",
      mx.addr_of("Joe Bookout <Joe.Bookout@carr.us>") == "joe.bookout@carr.us",
      mx.addr_of("Joe Bookout <Joe.Bookout@carr.us>"))
check("a bare address survives unchanged and is lowercased",
      mx.addr_of("  RFrancis@criadv.com ") == "rfrancis@criadv.com")
check("an empty recipient field yields no addresses, never one empty string",
      mx.split_addrs("") == [] and mx.split_addrs(None) == [])
check("a multi-recipient field splits on the comma the script joins with",
      mx.split_addrs("a@x.com,B@Y.com") == ["a@x.com", "b@y.com"])

# Deleted Items alone is larger than the real corpus on Joe's account (2,434
# against ~1,600 filed), so including it would swamp every count downstream.
for box in ("Deleted Items", "Junk Email", "Drafts", "Outbox"):
    check(f"{box} is excluded from the walk", box.lower() in mx.SKIP_MAILBOXES)
check("Sent Items is NOT excluded — it is the outbound half",
      "sent items" not in mx.SKIP_MAILBOXES)

src = (ROOT / "tools/mail-extract.py").read_text()
# Decision 745ab4aa admits derived facts only. A body read here would copy the
# mailbox wholesale, so the absence of one is a contract, not an omission.
check("the extractor never reads a message body",
      "content of messages" not in src and "source of messages" not in src and
      '"body"' not in src, "a body read appeared in the extractor")
check("the walk never uses a `whose` filter or a per-message Mail loop",
      "whose sender" not in src and "whose" not in src.split("EXTRACT_SCRIPT")[1],
      "a whose filter would never return on a 1,300-message mailbox")
check("Mail is probed before it is addressed, and never launched",
      "pgrep" in src and "mail_is_running" in src and
      "EX_CONFIG" in inspect.getsource(mx.main))

# THE JOIN THAT MATTERS: every key the matcher reads off a message must be a key
# the extractor writes. This is the seam loop #169 found dead — the matcher was
# pointed at a scratchpad file nothing produced.
matcher_src = (ROOT / "tools/mail-touch-matcher.py").read_text()
emitted = {"from", "to", "cc", "date", "subject", "mailbox", "direction"}
consumed = {k for k in ("from", "to", "cc", "date", "subject", "direction")
            if f'msg.get("{k}")' in matcher_src or f'"{k}"' in matcher_src}
check("the matcher reads only keys the extractor emits",
      consumed <= emitted, f"matcher wants {sorted(consumed - emitted)}")
check("the extractor's default output is the matcher's default input",
      pathlib.Path(mx.DEFAULT_OUT).name == "mail-extract.json" and
      "out/mail-extract.json" in matcher_src)

if FAILS:
    print("\nFAIL: mail extract contract")
    for f in FAILS:
        print("  - " + f)
    sys.exit(1)
print("\nPASS: mail extract contract")
