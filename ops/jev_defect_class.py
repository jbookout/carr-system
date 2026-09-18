"""jev_defect_class.py — which existing defect class does a new defect belong to?

THE DAMAGE THIS EXISTS TO STOP, read live from the store on 2026-09-18:

    320 classes over 371 defects — 1.16 defects per class, 304 of them singletons.

A defect class earns its keep by counting. Three sessions making the same
mistake under three different class names is not three lessons, it is one
lesson recorded three times in a way that can never be noticed. The
record-defect verb already says so in its own schema text — "reuse an existing
class where one fits ... because the count per class is the entire point" —
and the numbers above are what that instruction achieves on its own. It asks a
session to compare a new defect against three hundred existing names, which is
work no session does, so every session invents a name and the ledger fragments.

WHAT THIS MODULE DOES. It scores the proposed defect against every existing
class, ONE REQUEST PER CLASS, and hands back the few worth looking at. It does
not file anything and it does not pick.

WHAT IT IS WORTH, measured on 2026-09-18. Sixteen defects were held out, one
from each class that already has more than one member, so the true answer is
known. Each was scored against all 320 classes — 320 requests, about 12.5
seconds, roughly a cent:

    true class ranked 1st ............  6 of 16   38%
    true class inside the top 3 .....   8 of 16   50%
    true class inside the top 5 .....  11 of 16   69%
    true class inside the top 8 .....  13 of 16   81%
    true class inside the top 10 ....  13 of 16   81%

WHY A SHORTLIST AND NEVER A PICK is that table, not caution. Thirty-eight
percent is a bad autopilot and eighty-one percent is an excellent reading list,
and the same numbers say both things. The top candidates cluster inside about
five hundredths of each other, so the ordering among them is nearly arbitrary:
the same held-out defect, scored twice with an identical question against an
identical corpus, came back 1st and then 4th. That is not the model being
unstable — reproducibility on a pinned version is tight — it is a genuinely
flat landscape, because a defect often does plausibly resemble several classes.
So the human or the larger model chooses, and this narrows what they read from
three hundred candidates to eight. Note also that top-10 buys nothing over
top-8, which is where SHORTLIST comes from rather than a round number.

THE INSTRUMENT MATTERED MORE THAN THE MODEL, and the shape below was measured
rather than assumed. Three ways of describing a class to the judgment, same
defect, same 320 candidates:

    class name alone .................... true class ranked  99th
    class name + ONE earlier defect ..... true class ranked   1st
    class name + THREE earlier defects .. true class ranked  11th

The bare name is too thin to judge against; three examples are worse than one
because the illustrations' own specifics pull the category toward them and the
name stops doing the work. One example anchors the name without burying it.
Rewording the question alone — telling it that the NAME is the category and any
example is an illustration, and that a new occurrence normally involves
different files and tools — moved the same state from 8th to 1st.

IT IS A LIBRARY AND MUST STAY ONE. No shebang and no main guard: either turns a
.py file into a registered script entrypoint in the sealed source inventory,
moves the frontier and owes a forward-only registry successor. The detector is
a regex over the whole file with no notion of docstrings, so the construct is
described here and never spelled. ops/typesafe_client.py carries the long form.
"""

import importlib.util
import json
import os
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# How many candidates a caller is shown. Taken from the recall table above:
# eight captures 81% of true classes and ten captures no more, so this is where
# the curve flattens rather than a round number. Small on purpose — a shortlist
# a session will not read is the same as no shortlist.
SHORTLIST = 8

# Below this, a class is not worth a reader's attention. It is NOT a threshold
# for acting: nothing in this module acts. It only trims a tail. Chosen from the
# same run: fourteen of the sixteen true classes scored 0.65 or higher, and the
# two that fell below it (0.52 and 0.27) are the same two that ranked outside
# the top twenty — so the floor discards nothing the limit was going to keep.
CONSIDER_AT = 0.60

# Serial scoring of three hundred classes takes minutes and would not be used.
WORKERS = 16

# The SQL that gets the corpus. One row per class: the name, how often it has
# been used, and ONE earlier defect as its anchor — one, for the reason the
# module docstring measures. The earliest is taken because it is the occurrence
# that named the class.
CORPUS_SQL = """
select json_build_object(
         'name', c.defect_class,
         'occurrences', c.occurrences,
         'claimed', left(d.claimed, 700),
         'actual', left(d.actual, 700))::text
from v_defect_class c
join lateral (
  select claimed, actual from v_defect
  where defect_class = c.defect_class
  order by occurred_on, id
  limit 1
) d on true
order by c.occurrences desc, c.defect_class
"""


def _sibling(name):
    """Load a module from ops/ by path.

    ops/ holds no __init__.py, so it is not a package and a relative import
    from a sibling raises. That exact mistake cost a round trip on the day this
    was written and is why every module here loads its neighbours this way.
    """
    path = os.path.join(REPO, "ops", name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - import plumbing
        raise RuntimeError("cannot load ops/" + name + ".py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_classes(runner=None, repo=REPO):
    """Every existing class with its anchor defect, read from the live store.

    Goes through tools/db-tap.py, which is read-only by default and obtains its
    own connection string, because a shell command carrying a substitution is
    refused by the harness classifier. The SQL is written to a temporary file
    rather than added to the repository: db-tap takes a file, and a new tracked
    file would move the sealed inventory count for no benefit.

    `runner` is injectable so the suite runs offline. Returns [] rather than
    raising when the store cannot be reached — a caller that cannot get the
    corpus should carry on with what it was doing, not stop.
    """
    if runner is None:
        def runner(sql):
            handle = tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False)
            try:
                handle.write(sql)
                handle.close()
                result = subprocess.run(
                    [os.path.join(repo, ".venv", "bin", "python"),
                     os.path.join(repo, "tools", "db-tap.py"), "sql", handle.name],
                    capture_output=True, text=True, timeout=60, cwd=repo)
                return result.stdout if result.returncode == 0 else ""
            finally:
                os.unlink(handle.name)
    try:
        output = runner(CORPUS_SQL)
    except Exception:
        return []
    classes = []
    for line in (output or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            classes.append(json.loads(line))
        except ValueError:
            continue
    return classes


def belongs_question(client=None):
    """The one question, in the wording that was measured rather than assumed.

    The false criterion is the whole contract. Two defects can happen in the
    same part of the system, involve the same tool and arise during the same
    kind of work while being different mistakes — and a criterion that does not
    say so scores on topic instead of on mechanism, which is the degeneracy
    that made an earlier selector return the same rule for every moment.
    """
    tsc = client or _sibling("typesafe_client")
    return tsc.noul(
        "The state holds a defect a session is about to record, and one CLASS "
        "of defect that already exists in the ledger. The class name is the "
        "category; any earlier defect shown under it is an illustration of "
        "that category, not its definition. Does the proposed defect belong "
        "in this class?",
        true="The proposed defect is another occurrence of the category the "
             "class NAME describes. Filing it under this class would be "
             "correct and the class's count should go up by one. A new "
             "occurrence will normally involve different files, tools and "
             "subject matter from the earlier one — that is expected and does "
             "not make it a different class.",
        false="It is a different mistake from the one the class name "
              "describes. THIS IS THE CASE THAT IS EASY TO GET WRONG: two "
              "defects can happen in the same part of the system, involve the "
              "same tool, or arise during the same kind of work and still be "
              "different mistakes. SHARED SUBJECT MATTER IS NOT THE SAME "
              "CLASS — the test is the mechanism of the error named by the "
              "class, not where it happened. Also false when the proposed "
              "defect is markedly narrower or broader than the class name, so "
              "filing it here would blur the name's meaning.")


def _score(proposed, existing, question, judge, api_key, client):
    state = {
        "proposed_defect": {"claimed": proposed.get("claimed"),
                            "actual": proposed.get("actual")},
        "existing_class": {
            "name": (existing.get("name") or "").replace("-", " ").replace("_", " "),
            "earlier_defect_claimed": existing.get("claimed"),
            "earlier_defect_actual": existing.get("actual"),
        },
    }
    try:
        answer = judge.judge(state, {"belongs": question}, timeout=25.0,
                             client=client, api_key=api_key)
        return existing.get("name"), answer["answers"]["belongs"]["noul"]
    except Exception:
        return existing.get("name"), None


def shortlist(proposed, classes=None, *, floor=CONSIDER_AT, limit=SHORTLIST,
              client=None, api_key=None, judge=None, workers=WORKERS):
    """The few existing classes worth reading before naming a new one.

    ONE REQUEST PER CLASS, never one request carrying every class. A state
    holding many candidates lets each judgment see its competitors, and that
    does not reproduce the published method or its results.

    Returns [(class_name, probability, occurrences)], highest first. Returns []
    when the corpus is empty or every request failed — a caller then names a
    class the way it always did, which is the behaviour this replaces rather
    than something worse.
    """
    classes = load_classes() if classes is None else classes
    if not classes:
        return []
    judge = judge or _sibling("jev_judge")
    question = belongs_question(client)
    occurrences = {c.get("name"): c.get("occurrences") for c in classes}
    with ThreadPoolExecutor(max(1, workers)) as executor:
        scored = list(executor.map(
            lambda existing: _score(proposed, existing, question, judge, api_key, client),
            classes))
    ranked = [(name, probability) for name, probability in scored
              if probability is not None and probability >= floor]
    ranked.sort(key=lambda item: (-item[1], item[0]))
    return [(name, probability, occurrences.get(name))
            for name, probability in ranked[:limit]]


def advise(proposed, **kwargs):
    """The shortlist as a sentence a recorder can act on, or None.

    Deliberately says that nothing here is a decision. The ranking's top is
    flat enough that presenting it as an answer would be a misreading of the
    measurement it comes from.
    """
    candidates = shortlist(proposed, **kwargs)
    if not candidates:
        return None
    lines = ["EXISTING DEFECT CLASSES that may already cover this. Reuse one if "
             "it fits — the count per class is the point of the ledger. None of "
             "these is a decision; read them and choose."]
    for name, probability, count in candidates:
        seen = "seen once" if count == 1 else "seen %s times" % count
        lines.append("  %.2f  %s (%s)" % (probability, name, seen))
    return "\n".join(lines)
