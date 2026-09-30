"""Hash-only public-source identity check; plaintext never enters findings.

The public salt prevents accidental reuse of unsalted identity fingerprints.
It is not encryption and does not prevent guessing a known name. Refresh the
corpus with read-only record-layer calls on the private operator machine.
"""
import hashlib
import io
import json
import pathlib
import re
import unicodedata


def tokens(text):
    folded = unicodedata.normalize("NFKD", text.casefold())
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return re.findall(r"[a-z0-9]+", folded)


def fingerprint(value, salt):
    return hashlib.sha256((salt + "\0" + "".join(tokens(value))).encode()).hexdigest()


def load_corpus(path):
    data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    if (not isinstance(data, dict)
            or data.get("schema") != "public-source-identities/v1"
            or not isinstance(data.get("salt"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", data["salt"])
            or type(data.get("max_tokens")) is not int
            or not 1 <= data["max_tokens"] <= 64
            or not isinstance(data.get("hashes"), list)
            or not data["hashes"]
            or any(not isinstance(h, str) or not re.fullmatch(r"[0-9a-f]{64}", h)
                   for h in data["hashes"])):
        raise ValueError("invalid identity corpus")
    return data


def check(source, corpus_path, *, output):
    """Return 1 on exposure, 2 on invalid corpus; print only file:line.

    Tokens retain source lines so names split across lines are also checked.
    N-grams normalize punctuation, case, accents and whitespace identically
    to the locally generated corpus. No match text or hash is emitted.
    """
    try:
        corpus = load_corpus(corpus_path)
    except (OSError, ValueError):
        output.write("ops/config/public-source-identities.v1.json:1\n")
        return 2
    return _check_corpus(source, corpus, output=output)


def _check_corpus(source, corpus, *, output):
    banned = set(corpus["hashes"])
    found = set()
    for path, text in source:
        words = [(word, line) for line, value in enumerate(text.split("\n"), 1)
                 for word in tokens(value)]
        for start, (_, line) in enumerate(words):
            phrase = ""
            for word, _ in words[start:start + corpus["max_tokens"]]:
                phrase += word
                digest = hashlib.sha256((corpus["salt"] + "\0" + phrase).encode()).hexdigest()
                if digest in banned:
                    found.add((path, line))
    for path, line in sorted(found):
        output.write(f"{path}:{line}\n")
    return int(bool(found))


def synthetic_prose(text, corpus):
    """Replace matched identity spans with deterministic synthetic stand-ins."""
    words = [("".join(tokens(m[0])), m.start(), m.end())
             for m in re.finditer(r"[^\W_]+", text) if tokens(m[0])]
    hashes = set(corpus["hashes"])
    replacements = []
    consumed = 0
    for start, (_, begin, _) in enumerate(words):
        if begin < consumed:
            continue
        phrase = ""
        match = None
        for word, _, end in words[start:start + corpus["max_tokens"]]:
            phrase += word
            digest = hashlib.sha256((corpus["salt"] + "\0" + phrase).encode()).hexdigest()
            if digest in hashes:
                match = (end, digest)
        if match:
            end, digest = match
            original = text[begin:end]
            replacement = (original[0] + "-900" + str(int(digest[:8], 16))
                           if re.fullmatch(r"[CLVP]-\d+", original) else
                           "Example Organization " + digest[:8])
            replacements.append((begin, end, replacement))
            consumed = end
    for begin, end, replacement in reversed(replacements):
        text = text[:begin] + replacement + text[end:]
    return text


def sanitize_snapshot(text, corpus):
    """Project public prose only; never rewrite executable SQL or sealed rows.

    The only data-row projection is retrieval_proposal, a reference-vocabulary
    table already exported by schema-snapshot.sh. Primary keys, provenance
    pointers and registry seals are preserved. Any identity outside comments,
    COMMENT metadata and that table refuses the export.
    """
    projected = []
    copy_table = None
    comment_metadata = False
    for line in text.split("\n"):
        if line.startswith("COPY "):
            copy_table = line.split(" ", 2)[1]
        elif line == "\\.":
            copy_table = None
        if line.startswith("COMMENT ON "):
            comment_metadata = True
        if copy_table == "public.retrieval_proposal" and not line.startswith("COPY "):
            fields = line.split("\t")
            # id and UUID provenance/authority fields are not identity prose.
            fields = [field if re.fullmatch(r"[0-9a-f-]{36}", field) else
                      synthetic_prose(field, corpus) for field in fields]
            line = "\t".join(fields)
        elif line.lstrip().startswith("--") or comment_metadata:
            line = synthetic_prose(line, corpus)
        elif "--" in line:
            # Only an unquoted SQL line-comment is metadata. Quoted strings
            # stay executable; dollar bodies retain their SQL comment syntax.
            quoted = False
            comment = None
            i = 0
            while i < len(line):
                if line[i] == "'":
                    if quoted and i + 1 < len(line) and line[i + 1] == "'":
                        i += 2
                        continue
                    quoted = not quoted
                elif not quoted and line[i:i + 2] == "--":
                    comment = i
                    break
                i += 1
            if comment is not None:
                line = line[:comment] + synthetic_prose(line[comment:], corpus)
        projected.append(line)
        if comment_metadata and line.rstrip().endswith("';"):
            comment_metadata = False
    result = "\n".join(projected)
    # Reuse the same detector, rather than asserting that the projection worked.
    # The in-memory corpus has already been validated by the caller.
    sink = io.StringIO()
    if _check_corpus([("db/schema.sql", result)], corpus, output=sink):
        raise ValueError("snapshot identity outside permitted prose")
    return result
