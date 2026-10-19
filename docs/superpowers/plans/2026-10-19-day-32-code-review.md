# Day 32 Automatic PR Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Deliver a working GitHub Actions pipeline that reviews PR diffs with repository-code and documentation RAG, posts the review to the PR, and includes a video script.

**Architecture:** A Python entry point reads base/head Git objects without executing PR code, builds a temporary RAG index from allowlisted tracked text files in the base revision, and calls the repository's shared embedding and LLM clients. It can write a local Markdown report or post a GitHub review using the REST API; a `pull_request` workflow supplies the SHAs and token.

**Tech Stack:** Python 3.10+, standard library, existing `shared/index.py`, `shared/embeddings.py`, `shared/llm.py`, GitHub Actions, GitHub REST API.

**Spec:** `days/day-32-code-review/SPEC.md`

## Global Constraints

- Workflow trigger: `pull_request` for `opened`, `reopened`, and `synchronize`.
- Workflow permissions: `contents: read` and `pull-requests: write` only.
- Use `pull_request`, never `pull_request_target`; do not execute code from the PR.
- RAG corpus comes from the base revision; changed files and diff come from the PR head.
- Exclude `.env`, credentials, binary files, generated files, and unsupported file types.
- Treat diff and retrieved text as untrusted data; do not expose keys or full prompt contents in logs.
- Fork PRs lack repository secrets; document and report the limitation without publishing fabricated review.
- The reviewer must present potential bugs, architecture concerns, and actionable recommendations; line references must be exact or omitted.

## Review Focus

- Secret-like paths or content must never enter the index or model prompt; test exclusions for `.env`, `.pem`, and credential-like paths.
- Binary and generated files must be skipped and must not break Git object reads; test unsupported extensions and deleted files.
- Oversized diffs must fail explicitly instead of silently truncating review context; test the configured bound.
- Missing GitHub token must not call the API or claim a review was posted; test the local report path independently.
- Invalid/empty model output must not be published as a successful review; test validation before posting.

---

### Task 1: Build safe PR context, RAG review, and report generation

**Files:**
- Create: `days/day-32-code-review/corpus.py`
- Create: `days/day-32-code-review/review.py`
- Create: `days/day-32-code-review/test_review.py`
- Test: `days/day-32-code-review/test_review.py`

**Interfaces:**
- `corpus.collect_documents(repo: Path, base_ref: str) -> list[Document]` returns allowlisted tracked text documents from the base tree, with repo-relative `Document.path` values.
- `review.collect_changes(repo: Path, base_ref: str, head_ref: str) -> tuple[str, list[str], dict[str, str]]` returns the patch, changed paths, and readable head contents; it raises `ReviewError` on Git failure or a patch over 50,000 characters.
- `review.build_review(repo: Path, base_ref: str, head_ref: str) -> str` retrieves up to five relevant chunks using `Index.search(..., STRUCTURAL)`, calls `llm.ask`, and returns a non-empty Markdown review with the three required sections.
- `review.render_report(text: str) -> str` normalizes the required headings and rejects empty or malformed model output.
- `review.main(argv: list[str] | None = None) -> int` supports `--base`, `--head`, and `--output`; local mode writes the report without requiring a GitHub token. Task 2 adds posting options.

- [ ] **Step 1: Write focused unittest cases** for base-tree corpus allowlisting, secret/binary/generated exclusions, diff/path/content collection, oversized patch rejection, required report headings, empty model output rejection, and missing-token behavior.
- [ ] **Step 2: Run the focused tests and confirm they fail** on the missing module/functions or expected validation behavior.
- [ ] **Step 3: Implement `corpus.py`** using `git ls-tree` and `git show` for tracked base-revision content; allowlist Markdown and source-code extensions, skip credentials/environment files and generated directories, and keep source paths relative.
- [ ] **Step 4: Implement `review.py`** with bounded diff/file inputs, temporary `AI_ADVENT_MEMORY_DIR` and SQLite index, shared structural RAG and LLM clients, defensive prompt instructions, required review headings, and clear `ReviewError` messages.
- [ ] **Step 5: Run the focused tests** and confirm they pass; inspect the generated local report using a mocked LLM so no external model call or secret is needed.
- [ ] **Step 6: Commit** the spec, plan, implementation, and focused tests as `feat(day-32): implement RAG-based PR review`.

### Task 2: Wire GitHub Actions, publish reviews, and document the demo

**Files:**
- Create: `.github/workflows/day-32-review.yml`
- Modify: `days/day-32-code-review/review.py`
- Create: `days/day-32-code-review/README.md`
- Create: `days/day-32-code-review/video.md`
- Test: `days/day-32-code-review/test_review.py`

**Interfaces:**
- Workflow checks out the PR base revision, fetches the synthetic PR head ref without executing it, and invokes `review.py --base "$BASE_SHA" --head "$HEAD_REF" --output review.md`.
- `review.post_review(repository: str, pull_number: int, body: str, token: str) -> None` sends a GitHub REST API pull-request review with event `COMMENT`; it raises a clear `ReviewError` on missing token or non-success HTTP response.
- Workflow sets `GITHUB_TOKEN` from `secrets.GITHUB_TOKEN` and posts only after `review.py` exits successfully.

- [ ] **Step 1: Add tests** proving the API request uses the expected endpoint, `COMMENT` event, and bearer token, and that HTTP failures are surfaced without printing the token.
- [ ] **Step 2: Run the focused tests and confirm the API tests fail** before implementation.
- [ ] **Step 3: Implement `post_review`** with standard-library `urllib`, JSON payload, bounded timeout, and redacted errors.
- [ ] **Step 4: Add the workflow** with minimal permissions, PR event types, per-PR concurrency cancellation, Python setup, diff fetch, report generation, and review posting; use no fork secrets beyond GitHub's normal token availability.
- [ ] **Step 5: Write the README and video storyboard** covering local dry run, required model secret, workflow permissions, fork limitation, review format, and a concise end-to-end demo.
- [ ] **Step 6: Run focused tests and static checks** (`python3 -m unittest discover -s days/day-32-code-review -p 'test_*.py' -v`, Python compilation, YAML parse if a parser is already installed); inspect the workflow and docs against every spec criterion.
- [ ] **Step 7: Commit** as `feat(day-32): add PR workflow and delivery docs`.

### Final delivery

- [ ] Review the complete diff and current branch state.
- [ ] Push the feature branch to `origin`.
- [ ] Create a non-draft PR with a description of behavior, permissions, tests, and fork limitation; do not merge it.
- [ ] Read the created PR back through the GitHub API and report its URL and verification evidence.
