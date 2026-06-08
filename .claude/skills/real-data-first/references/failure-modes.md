# Schema-detail bugs that only real data reveals

A catalogue of the failure classes the "Real Data First" discipline exists to
prevent. Each is a *general* class; the concrete examples are illustrations, most
drawn from a real data-augmentation tool that shipped the same bug six times
before adopting the discipline. The through-line: every one is a single upstream
detail that **passed every synthetic test and broke every real run**, because the
fixture and the parser were written from the same wrong assumption.

Read this when you want to feel *why* the rule earns its keep — or when you're
about to argue yourself out of a real fetch ("the docs are clear, I'll just...").

---

## 1. Wrong entity grain / join key

**The class.** The dataset is keyed to a different geographic, organisational, or
temporal grain than its name, docs, or your intuition implies. Your join "works"
(no error) but matches the wrong rows or matches almost nothing.

**Examples.**
- A dataset assumed to be "LGA-native" (local government area) turned out to be
  keyed at a *finer statistical* grain — a completely different join key. Joining
  on the assumed key silently dropped most rows.
- A dataset assumed "SA3-keyed" (~340 codes) was actually "SA4-keyed" (89 codes).
  The code count is the tell: a `groupby` produces 89 groups, not 340, and the
  downscale logic is built on the wrong fan-out.

**What a real fetch shows instantly.** The actual key column's name, its distinct
count, and a few sample values. 89 ≠ 340 jumps out the moment you print
`df[key].nunique()`.

**The lesson.** "Native granularity" claims in docs are frequently wrong or
loose. Count the distinct keys against the real file before you build the join.

---

## 2. Near-identical names across siblings (the punctuation trap)

**The class.** Two artifacts that *look* interchangeable differ by one character
in a name your code matches on — a space vs an underscore, a hyphen vs an en-dash,
singular vs plural, casing. A parser copy-pasted from the sibling silently reads
the wrong sheet/file/key, or reads nothing.

**Example.** One Excel cube's data lived on a sheet named `Table 1` (with a
**space**). A sibling cube from the same publisher used `Table_1` (with an
**underscore**). A parser written for one and reused for the other finds no sheet
by that name — or, if it falls back to "first sheet", reads a metadata preamble as
data.

**What a real fetch shows instantly.** Sheet names printed *verbatim*. The
difference is invisible in prose ("the Table 1 sheet") and obvious in a literal
dump (`['Contents', 'Table 1', 'Notes']` vs `['Contents', 'Table_1', 'Notes']`).

**The lesson.** Never assume two artifacts from the same source share an internal
naming convention. Print the real names of *each* one. Match on what you see.

---

## 3. Encoding assumptions (the mojibake trap)

**The class.** A text artifact is in a legacy encoding (Windows-1252 / CP1252,
Latin-1, UTF-16) but your reader assumes UTF-8. ASCII-range characters decode
fine, so it *seems* to work — until a real row contains an en-dash, curly quote,
accented name, or degree sign, which either raises `UnicodeDecodeError` or
silently mojibakes (`–` → `â€"`).

**Example.** A CSV was Windows-1252, not UTF-8. En-dash characters in category
labels turned to garbage under the wrong decode. Tests used pure-ASCII synthetic
values, so they never hit a non-ASCII byte and never noticed.

**What a real fetch shows instantly.** A probe that tries `utf-8-sig`, `utf-8`,
`cp1252`, `latin-1` in order and reports which one cleanly decoded — and whether
the bytes contain anything outside ASCII at all.

**The lesson.** Don't hard-code `encoding="utf-8"`. Detect it against the real
bytes, and put a non-ASCII value (a real one) into your fixture so the test can
actually exercise the decode path.

---

## 4. Column / field name drifts across versions or releases

**The class.** The same logical field is named differently across vintages,
regions, or API versions (`SA2_MAIN16` vs `SA2_MAINCODE_2016`; `customerId` vs
`customer_id`; `total` nested under `summary` in v2 but top-level in v1). Code
that hard-codes one name breaks on the other, and a fixture built for one vintage
green-lights it.

**What a real fetch shows instantly.** The actual column/field name *for the
specific release you're targeting* — and, if you fetch two vintages, the drift
between them, which tells you to detect rather than hard-code.

**The lesson.** When supporting more than one version, fetch a sample of *each*
and either confirm they match or build name-detection. Don't assume the new
release renamed nothing.

---

## 5. File / path layout differs from the documented or guessed one

**The class.** The file isn't where the naming convention says it should be:
a different folder nesting inside the ZIP, a `data/` prefix you didn't expect, a
date-stamped filename whose format differs from the docs, a bucket key with an
extra path segment. "File not in bucket" / `FileNotFoundError` on the first real
run.

**What a real fetch shows instantly.** The archive's real internal tree or the
bucket's real key listing — folder names, nesting depth, and the actual filename
pattern, including any wrapper directory.

**The lesson.** List the real container before you construct paths into it.
Constructed paths are fine (deterministic is good) — but construct them from an
observed layout, then verify one resolves.

---

## 6. JSON / API response shape differs from the docs

**The class.** The response is wrapped, paginated, or typed differently than
documented: results inside a `data` envelope, numbers serialised as strings, a
field that's sometimes a scalar and sometimes a list, `null` where you expected
absent (or vice versa), an error returned with HTTP 200.

**Example.** A geocoding API returned `lat`/`lon` as JSON **strings**, not
numbers. Code doing arithmetic on them either concatenated or threw; a fixture
that put numbers there hid it.

**What a real fetch shows instantly.** The real top-level keys, the nesting, and
the *type* of each leaf value from an actual call.

**The lesson.** Probe the live response (or a saved capture of it) and assert on
its real types and nesting. Build your fixture from the captured response, not
from the API reference page.

---

## 7. Sentinel / pseudo rows and value quirks

**The class.** Real files carry rows or values that clean synthetic fixtures
never have: total/subtotal rows, "not applicable" / "offshore" / "migratory"
sentinel codes, thousands separators inside numeric strings, leading-zero codes
that a CSV reader silently turns into integers, mixed-type columns, footnote
markers appended to values.

**What a real fetch shows instantly.** The messy reality — the extra rows at the
bottom, the `9999` sentinels, the `"1,234"` strings, the codes that lose their
leading zeros.

**The lesson.** Look at the head *and the tail* of the real data, and at value
dtypes. Encode at least one quirk into the fixture so the parser's handling of it
is actually tested.

---

## The common shape

In every case above:

1. The documentation, naming convention, or intuition pointed one way.
2. The real artifact went another way — by one small, decisive detail.
3. The synthetic fixture encoded the *assumed* detail, so the test suite was
   green and confident.
4. The bug surfaced on first contact with real data, far from where it was
   introduced, often in a user's hands.

One real fetch, read carefully, collapses all of it. That's the whole argument.
