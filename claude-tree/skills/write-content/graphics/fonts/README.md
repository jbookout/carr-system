# graphics/fonts — brand type (READ BEFORE REPLACING THESE FILES)

`Oswald.ttf` + `Montserrat.ttf` are GENUINE VARIABLE TTFs (wght axes 200–700 and 100–900) from
github.com/google/fonts (OFL license), installed July 7, 2026 after the static-subset incident
(decision-history: "Two-session font collision"). brand-card.html's @font-face declares weight
RANGES — variable files are required for the weight hierarchy (600/700 headlines vs 500 body,
700 footer name) to actually render. Do NOT replace these with single-weight subsets or
woff2 files renamed .ttf; if a replacement is ever needed, verify with a multi-weight specimen
render first (weights must look different). A copy of both files lives in
`CARR AI/DNA/Marketing/Brand Assets/fonts/` — keep the two locations identical (sha256).
SHA-256: Oswald 5b38c246…7c2817 · Montserrat 0f7b311b…cd82f7.
