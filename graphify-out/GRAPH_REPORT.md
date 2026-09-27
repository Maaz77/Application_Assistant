# Graph Report - Application_Assistant  (2026-09-27)

## Corpus Check
- 178 files · ~263,494 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 6 file(s) not represented in the graph (top: (none) 1, .toml 1, .command 1)

## Summary
- 1215 nodes · 3117 edges · 90 communities (62 shown, 28 thin omitted)
- Extraction: 91% EXTRACTED · 9% INFERRED · 0% AMBIGUOUS · INFERRED: 271 edges (avg confidence: 0.89)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- CLI Config & Models
- Question Coverage Judgment
- Jev Decision Engine
- Navigate-to-Form Loop
- Tab Bookkeeping
- Entry Click & New-Tab Handoff
- Answer Confirmation Rules
- Job Records & Tracker Rows
- Model API Error Handling
- Page Classification & Settle
- Fill Plan & Field Choice
- Page Element Snapshot
- Answer Engine & Verification
- CLI Preflight & Queue
- Entry State Judgment
- Model Rotation Fallback
- Never-Click Guard Checks
- Jev Package Wrapper Calls
- Job Outcomes & Report
- Tripwire & CLI Tests
- Jev Contract & Decision Errors
- Test Fixtures & Chrome Setup
- FakeMCP Browser Stub
- Contract Check Tool
- Guard & Probes Core
- Never-Submit Pipeline
- Never-Submit Rule Tests
- Live Model Answer Tests
- Answer Engine Model Calls
- Tracker File Sync
- Jev.py Safety Invariants
- Tracker Row Operations
- Discovery Log Decisions
- Fixture HTTP Server
- Model Access Cost Tests
- Entry Classification Fixtures
- Job Processing Pipeline Test
- Needs-Attention & Google Sign-In
- Package Request Cleaning
- Submit-Label Fixture Family
- Discovery Capture Tool
- Click Recording & Retry Tests
- Text Helper Rotation
- ATS Form Pattern Fixtures
- Captured External-Redirect Patterns
- FakeMCP Tab Stand-In
- Ashby Resume Autofill Captures
- Throwaway Chrome Cleanup
- Page Goal Prompt Tests
- Never-Click Confirm Reason
- UTF-16 Surrogate Cleaning
- Custom Widget Wandering Tests
- Run-Stopping Errors
- Modal-Blocking Captures
- Easy Apply Modal Fixtures
- Toast Run Captures
- Fixture HTTP Handler
- Broken-Form Refill Attempt
- Golden Guard Click Cases
- Golden Tab Handle Cases
- LinkedIn Job View Captures
- Single Worker Thread
- Greenhouse ATS Findings
- Golden Assert/Observe Cases
- Canned Test Answer Engine
- New-Tab Field Wait Test
- Workday ATS Findings
- Ashby ATS Findings
- Greeting ATS Findings
- Custom Widget & Validation Fixtures
- Captcha & Cookie Banner Fixtures
- Upload Fixture & Golden Case
- Golden Eval Result Cases
- Fake Click Labeling
- clean_text Utility
- Project Root Marker
- Nota AI Screenshot A
- Nota AI Screenshot B
- GitLab Greenhouse Screenshot
- Greenhouse Application Screenshot
- ElevenLabs Posting Screenshot A
- ElevenLabs Application Screenshot A
- ElevenLabs Posting Screenshot B
- ElevenLabs Application Screenshot B
- ElevenLabs Application Screenshot C
- NVIDIA Workday Screenshot
- LinkedIn C++ Job Screenshot A
- LinkedIn C++ Job Screenshot B
- Golden Keys-Enter Case

## God Nodes (most connected - your core abstractions)
1. `Page` - 81 edges
2. `run_pages()` - 66 edges
3. `FakeMCP` - 66 edges
4. `Jev` - 51 edges
5. `NeedsAttention` - 48 edges
6. `ctx_for()` - 46 edges
7. `judge()` - 43 edges
8. `El` - 38 edges
9. `Table` - 35 edges
10. `Tracker` - 35 edges

## Surprising Connections (you probably didn't know these)
- `test_an_engine_answer_for_an_older_resume_card_is_dropped()` --uses--> `Question`  [INFERRED]
  tests/test_fill_loop.py → assistant/answers.py
- `test_read_only_fields_and_custom_widgets_still_go_to_the_goal()` --uses--> `Question`  [INFERRED]
  tests/test_fill_loop.py → assistant/answers.py
- `test_the_fill_plan_is_jevs_pick_and_the_code_only_checks_it_can_be_done()` --uses--> `Question`  [INFERRED]
  tests/test_fill_loop.py → assistant/answers.py
- `test_the_read_back_is_one_jev_question_per_field_on_the_fresh_page()` --uses--> `Question`  [INFERRED]
  tests/test_fill_loop.py → assistant/answers.py
- `run()` --uses--> `PageAnswers`  [INFERRED]
  tests/test_answers.py → assistant/answers.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Never-submit safety mechanism across guard, jev wrapper, and prompts** — claude_guard_never_click, claude_jev_guard_clicks, claude_pages_alarm_re, prompts_page_goal, prompts_next_step_goal [INFERRED 0.85]
- **One form page fill loop: answer engine, checks, Jev decisions, fill plan, read-back** — claude_answers_py, claude_check_answers, assistant_decide, claude_fill_plan_fill, claude_fill_mismatches [EXTRACTED 1.00]
- **Model routing across OpenRouter and Vercel AI Gateway for Jev and chat models** — discovery_systemone_api, discovery_vercel_ai_gateway, discovery_rotation_rotation, claude_jev_env_values [INFERRED 0.85]
- **Ashby ElevenLabs job page progressing from overview to resume-autofill application form (captured across 4 snapshots)** — tests_captured_jobs_ashbyhq_com_20260923_144702, tests_captured_jobs_ashbyhq_com_20260923_150512, tests_captured_jobs_ashbyhq_com_20260923_150513, tests_captured_jobs_ashbyhq_com_20260923_150816 [INFERRED 0.85]
- **Modal/dialog obstacles the fill loop must navigate before reaching form fields (cookie consent, Easy Apply, safety reminder)** — tests_captured_run_20260923_toast, tests_captured_run_20260923_dmi, tests_captured_run_20260923_linda [INFERRED 0.75]
- **Captured page snapshots spanning distinct ATS platforms (Ashby, Workday, LinkedIn, Mastercard custom site) used as fixtures for pages.classify_entry / decide.py judgments** — concept_ashby_ats, concept_workday_ats, concept_linkedin_easy_apply, tests_captured_run_20260923_mastercard [INFERRED 0.70]
- **Submit-label variant fixtures used to prove the never-submit rule generalizes** — tests_fixtures_f03, tests_fixtures_f04, tests_fixtures_f05, tests_fixtures_f07 [INFERRED 0.85]
- **ATS fixtures exercising captcha/reCAPTCHA detection and the confirmation-text tripwire** — tests_fixtures_ats_captcha_visible, tests_fixtures_ats_ashby_like_app, pages_alarm_re [INFERRED 0.75]
- **Toast careers page captured pre- and post-fill via probes/observe to validate fill_loop behaviour** — tests_captured_run_20260923_toast_probes, tests_captured_run_20260924_toast_filled_observe, tests_captured_run_20260924_toast_filled_probes [INFERRED 0.85]
- **LinkedIn job entry pages exercising pages.classify_entry's state branches (easy apply, closed, applied, submitted, external, delayed render)** — tests_fixtures_jobs_view_4012345601_easy_html_easy_apply_entry, tests_fixtures_jobs_view_4012345602_closed_html_closed_posting_entry, tests_fixtures_jobs_view_4012345603_applied_html_already_applied_entry, tests_fixtures_jobs_view_4012345605_submitted_html_already_submitted_entry, tests_fixtures_jobs_view_4012345606_late_html_delayed_render_entry [INFERRED 0.75]
- **Interstitial obstacles the navigate stage of fill.run_pages must get past before reaching a form (cookie banner, captcha, sign-in/signup gates)** — tests_fixtures_f17_captcha_html_cloudflare_turnstile_captcha, tests_fixtures_f18_cookie_html_cookie_consent_banner, tests_fixtures_f15_google_html_google_signin_gate, tests_fixtures_f16_signup_html_account_signup_gate [INFERRED 0.75]
- **Three variants of a LinkedIn Apply click leading to the external ATS form: immediate link, delayed new tab, and confirmation dialog before opening it** — tests_fixtures_jobs_view_4012345604_external_html_external_apply_link_entry, tests_fixtures_jobs_view_4012345607_late_tab_html_delayed_new_tab_entry, tests_fixtures_jobs_view_4012345608_dialog_html_leaving_linkedin_dialog [INFERRED 0.85]
- **browser_act op-type golden coverage (click/eval/keys/upload)** — tests_golden_act_blocked_click, tests_golden_act_confirm_click, tests_golden_act_eval, tests_golden_act_eval_undefined, tests_golden_act_keys_enter, tests_golden_act_upload [INFERRED 0.85]
- **Easy Apply modal/dialog job-view fixtures used by fill.run_pages navigation tests** — tests_fixtures_jobs_view_4012345609_easy_li, tests_fixtures_jobs_view_4012345610_easy_dialog, tests_fixtures_probe_lab_display_contents [INFERRED 0.75]
- **Discovery form observe/open/tabs golden lifecycle (open -> observe -> new tab -> tabs list)** — tests_golden_open, tests_golden_observe_full_json, tests_golden_tabs_after_new_observe, tests_golden_tabs_new [INFERRED 0.75]

## Communities (90 total, 28 thin omitted)

### Community 0 - "CLI Config & Models"
Cohesion: 0.05
Nodes (49): ArgumentParser, build_parser(), api_key(), Browser, chat_key(), chat_url(), ChatModels, _env() (+41 more)

### Community 1 - "Question Coverage Judgment"
Cohesion: 0.08
Nodes (57): judge_questions(), PageAnswers, BaseModel, Question, One Jev call: flag the questions the resume upload answers, and the uploads…, uncovered_optional(), uncovered_required(), Attempts (+49 more)

### Community 2 - "Jev Decision Engine"
Cohesion: 0.06
Nodes (47): _cost(), Decider, _error(), fit_state(), for_config(), _httpx_post(), narrow(), noul() (+39 more)

### Community 3 - "Navigate-to-Form Loop"
Cohesion: 0.12
Nodes (44): From wherever the job's tab is (the LinkedIn posting) to the parked final step.…, run_pages(), El, FakePage, ctx_for(), _goals(), Q(), Loop unit tests on FakeMCP: fill, upload, typing, goal, read-back, advance,… (+36 more)

### Community 4 - "Tab Bookkeeping"
Cohesion: 0.07
Nodes (28): handle(), RuntimeError, Tab bookkeeping (§4.5, §5): release a session without closing its tab, hand off…, Detach `session` from its tab and leave that tab open (B3: the exit hook and…, If the entry click opened exactly one new tab, move `session` to it and close…, Close tabs opened during this job that are neither in the baseline nor kept,…, Close the helper's own about:blank tab., Handles (8 chars) for sets and diffs; full IDs, learned from the helper's… (+20 more)

### Community 5 - "Entry Click & New-Tab Handoff"
Cohesion: 0.08
Nodes (40): _attempt2(), follow_new_tab(), guest_refused(), The current page, after it has drawn its content (pages.settle)., The control that uploads the resume (a file input, or LinkedIn's "Upload…, After a click: if a new tab appears within `seconds`, hand the job off to it…, Why the guest link of a sign-up wall is not clicked (user decision 2026-09-23),…, resume_input() (+32 more)

### Community 6 - "Answer Confirmation Rules"
Cohesion: 0.10
Nodes (29): Answer, held(), is_advance(), is_entry(), is_guest(), is_transmit(), pick(), Any (+21 more)

### Community 7 - "Job Records & Tracker Rows"
Cohesion: 0.11
Nodes (28): append_note(), build_queue(), Job, Journal, needs_attention_note(), overview_field(), parked_note(), Path (+20 more)

### Community 8 - "Model API Error Handling"
Cohesion: 0.12
Nodes (34): Policy, canned(), post(), q(), 429 (rate-limited upstream), 5xx, a 200 that carries an error body (an…, Vercel AI Gateway takes the same chat/completions request; its 401 names the…, Vercel, live 2026-09-24: 429 "this team's limit of 5 requests per minute ……, One Rotation for the run: the next page starts at the model that answered, not… (+26 more)

### Community 9 - "Page Classification & Settle"
Cohesion: 0.10
Nodes (34): capture(), Read-only snapshot of a real page for tests/captured/: no clicks, no typing., classify(), google_button(), iframe_form_src(), Re-read until the page is no longer `unsettled`, at most `seconds` (one read…, The iframe that holds the application form, when the page itself shows none of…, The page text section of an observe view ('text:' line onwards). (+26 more)

### Community 10 - "Fill Plan & Field Choice"
Cohesion: 0.07
Nodes (31): choice(), _carry_out(), _field_choices(), _option_choices(), plan_fill(), How and where each answer goes in, as Jev decides, in one round (jev-…, The browser op for Jev's pick, when the page allows it; else None (a page goal)., Option (+23 more)

### Community 11 - "Page Element Snapshot"
Cohesion: 0.12
Nodes (31): Element, One row of the element table (observe include_json=True). Shape recorded in…, gate(), is_final(), linkedin_feed_ok(), page_from_capture(), Not drawn yet: nothing on the page at all, or nothing to act on so far…, A Page built from a saved browser_observe(mode="full", include_json=True)… (+23 more)

### Community 12 - "Answer Engine & Verification"
Cohesion: 0.13
Nodes (26): answer_page(), check_answers(), _drop(), _held(), judge_answers(), _limit(), _loose(), _month_index() (+18 more)

### Community 13 - "CLI Preflight & Queue"
Cohesion: 0.15
Nodes (23): argparse, dry_run(), _load_package(), preflight(), PreflightError, RuntimeError, _queue(), Command line: run · preflight · tripwire · capture · requeue (§2, §1, §8.5). (+15 more)

### Community 14 - "Entry State Judgment"
Cohesion: 0.18
Nodes (24): classify_entry(), judge(), Jev's judgment of the page: one call per snapshot, cached on it. Raises…, signed_out | closed | applied | open | none., captured(), names(), _page(), browser (+16 more)

### Community 15 - "Model Rotation Fallback"
Cohesion: 0.12
Nodes (20): NoModelAvailable, Exception, RuntimeError, Model rotation: a list of free OpenRouter models, tried in turn until one…, Every model in the rotation failed on this call., This call's order: the model that answered last, then the others as configured., attempt(model) for each model in order; an `unavailable` error moves on to the…, Rotation (+12 more)

### Community 16 - "Never-Click Guard Checks"
Cohesion: 0.11
Nodes (22): check(), GuardError, RuntimeError, Raise GuardError if `op` leaves our intended paths. `table` is the latest…, ast, test_upload_on_a_transmit_label_is_refused(), _constants(), form_started() (+14 more)

### Community 17 - "Jev Package Wrapper Calls"
Cohesion: 0.15
Nodes (8): Jev, Any, Path, Typed calls to server.browser_*; every browser_act goes through the guard…, getattr(server, name)(**kwargs) on the worker thread. A hung call cannot be…, The only path to browser_act: every op passes guard.check against `table` first…, Run eval probes by name (probes.EVAL_PROBES) in one round trip., browser_goal. A goal the decision model could not serve (429/5xx, unreachable)…

### Community 18 - "Job Outcomes & Report"
Cohesion: 0.13
Nodes (12): OpenQuestion, Parked, Outcomes of a job and the two-attempt rule (§6.3)., JobResult, Path, runs/<ts>/report.md, terminal lines and exit codes (§8.5)., Deduplicated Scratch Pad suggestions, in the Profile.md Scratch Pad style., Report (+4 more)

### Community 19 - "Tripwire & CLI Tests"
Cohesion: 0.13
Nodes (19): main(), Guard/probe unit tests + the browser tripwire on local fixtures, in a throwaway…, tripwire(), fixture, Path, T9: --dry-run on a temp workspace prints the right queue and writes nothing;…, snapshot(), test_a_key_both_routes_use_is_reported_once() (+11 more)

### Community 20 - "Jev Contract & Decision Errors"
Cohesion: 0.13
Nodes (15): DecisionError, RuntimeError, The decision model could not answer (network, HTTP error, malformed reply, no…, CompletedProcess, child(), jev.py (spec v2 §3, §9): env before import, stray keys removed, sole importer,…, Mastercard, live 2026-09-24: Vercel's Jev answered 503 through the package's…, Run `code` in a fresh interpreter: the package config is fixed per process. (+7 more)

### Community 21 - "Test Fixtures & Chrome Setup"
Cohesion: 0.14
Nodes (16): contextlib, str, api_key(), chat_key(), chrome(), decider(), _fixture_server(), new_browser() (+8 more)

### Community 22 - "FakeMCP Browser Stub"
Cohesion: 0.19
Nodes (3): FakeMCP, A stand-in for the agent: decline a cookie banner, continue past a pop-up, then…, test_answer_engine_failure_is_a_needs_attention_blocker()

### Community 23 - "Contract Check Tool"
Cohesion: 0.15
Nodes (16): click_rule_differences(), differences(), main(), Compare the package's public browser_* signatures with spec v2 §3. python -m…, Parameter names and defaults per function, order-insensitive (every call uses…, jev.rotate_text_helper wraps policy.text_for(cfg, …); the wrap applies only…, jev.clean_requests wraps policy._post(url, key, body); it covers every request…, jev.guard_clicks replaces the package's confirm_reason(cfg, name, role), the… (+8 more)

### Community 24 - "Guard & Probes Core"
Cohesion: 0.16
Nodes (14): _FormStage, Checks on every browser_act op our code sends (§4.1), before it reaches the…, jev-ultrafast-mcp 0.1.5, called directly in Python (spec v2 §3). The only…, Split browser_observe(include_json=True) output into (view text, table)., split_json(), parse_eval_results(), ProbeError, RuntimeError (+6 more)

### Community 25 - "Never-Submit Pipeline"
Cohesion: 0.12
Nodes (15): Browser Agent (goal agent + text helper), cli.process, cli.run (one run pipeline), fill.run_pages (two-stage loop), guard.FORM.started, guard.never_click, jev.env_values / agent_route, jev.guard_clicks (+7 more)

### Community 26 - "Never-Submit Rule Tests"
Cohesion: 0.20
Nodes (16): single_page(), test_cookie_banner_then_form(), _confirms(), google_site(), T8 rule tests: the never-submit rule on the page loop, Google one-click (§6.2),…, Before the form is being filled, "Apply" / "Easy Apply" is clicked by the agent…, Toast's Greenhouse form ends in "Apply now!": once filling started it is…, signup_site() (+8 more)

### Community 27 - "Live Model Answer Tests"
Cohesion: 0.15
Nodes (10): Sources, pytest, live_model: the configured answer engine on f08 with small, fixed sources., test_engine_answers_f08(), fixture, parametrize, T6 live_model: f02 and f08 filled end-to-end with the real answer engine and…, resume() (+2 more)

### Community 28 - "Answer Engine Model Calls"
Cohesion: 0.14
Nodes (16): AnswerEngineError, _ask_model(), call_engine(), _code(), _message(), ModelUnavailable, RuntimeError, Ask the models in turn (rotation.py) until one gives a valid answer;… (+8 more)

### Community 29 - "Tracker File Sync"
Cohesion: 0.15
Nodes (12): fingerprint(), job_id(), Job_Tracker.numbers adapter (§8.1). Tracker I/O is imported from…, Save only if nobody else changed the file since load (else StopRun, exit 3)., The key that joins a tracker row to a job folder. Order (§8.1): the numeric ID…, _reconcile(), hashlib, os (+4 more)

### Community 30 - "Jev.py Safety Invariants"
Cohesion: 0.13
Nodes (14): answers.py (answer engine), jev.apply_env, check_answers, assistant.contract_check, tests/fake_mcp.py (FakeMCP, FakeBook), guard.check, jev.load, jev.py (only door to browser package) (+6 more)

### Community 31 - "Tracker Row Operations"
Cohesion: 0.22
Nodes (6): Path, load() · find(job_id) · set(job_id, status, notes) · add(fields) · save().…, Replace the whole Notes cell (requeue removes the line a run added)., Tracker, test_real_tracker_copy_loads_through_reconcile(), test_tracker_changed_on_disk_stops_the_run()

### Community 32 - "Discovery Log Decisions"
Cohesion: 0.18
Nodes (13): Application Assistant v2, fill.plan_fill, Never-Submit Rule, pages.ALARM_RE (confirmation-text tripwire), ../Reconcile/reconcile.py (sibling tracker tool), Direct typing for short text answers (decision), display: contents visibility bug, DISCOVERY.md (decision log) (+5 more)

### Community 33 - "Fixture HTTP Server"
Cohesion: 0.17
Nodes (10): functools, http_server, socket, subprocess, tempfile, _free_port(), Throwaway Chrome and the local fixture server (§9). Never touches the user's…, threading (+2 more)

### Community 34 - "Model Access Cost Tests"
Cohesion: 0.26
Nodes (11): httpx, Response, _error(), _get(), _json(), _money(), _per_million(), parametrize (+3 more)

### Community 35 - "Entry Classification Fixtures"
Cohesion: 0.17
Nodes (12): pages.ALARM_RE confirmation-text tripwire, pages.classify_entry (job entry state classifier), Ashby-like job overview fixture, Ashby-like application form fixture, Visible-captcha form fixture, Greenhouse-like application form fixture, f14: post-submission confirmation page ("application was submitted") — ALARM_RE tripwire case, 4012345601-easy: LinkedIn job page with Easy Apply button — baseline entry case (+4 more)

### Community 36 - "Job Processing Pipeline Test"
Cohesion: 0.20
Nodes (10): Path, resume_text(), process(), date, Path, Open the posting → the page loop (the browser agent from there) → parked.…, canned(), parametrize (+2 more)

### Community 37 - "Needs-Attention & Google Sign-In"
Cohesion: 0.25
Nodes (9): NeedsAttention, The job goes to Needs-Attention; its tab stays open (C8)., Record a failure. Raises NeedsAttention when no attempt 2 is left., _back_on_site(), Google one-click sign-in (§6.2). No model acts on accounts.google.com; every…, From a page offering Google (or already on accounts.google.com), pick `email`…, sign_in(), parametrize (+1 more)

### Community 38 - "Package Request Cleaning"
Cohesion: 0.18
Nodes (9): clean_requests(), _post(), load(), package_version(), Every request the package sends (Jev's decisions, option picks, the text…, Import jev_ultrafast_mcp.server (once per process), after apply_env()., Genesys and Mastercard, live 2026-09-24: browser_goal sent page text with a…, test_the_package_s_requests_are_cleaned_before_httpx_encodes_them() (+1 more)

### Community 39 - "Submit-Label Fixture Family"
Cohesion: 0.22
Nodes (11): guard.never_click (never-submit rule), Greeting/job-landing page fixture, f01 single-page apply form fixture, f02 modal multi-step form fixture, f03 final-step Apply-labeled submit fixture, f04 Enter-submits form fixture (no submit button), f05 Send-labeled submit fixture, f06 cookie-banner-over-form fixture (+3 more)

### Community 40 - "Discovery Capture Tool"
Cohesion: 0.24
Nodes (5): main(), T1 discovery: drive the throwaway Chrome against local fixtures and save raw…, save(), FixtureServer, http.server on 127.0.0.1 serving tests/fixtures; every POST or /submit request…

### Community 41 - "Click Recording & Retry Tests"
Cohesion: 0.22
Nodes (8): Toast: the pop-up's "I do not accept / I accept" matched none of the old cookie…, The package applies jev.never_click to every click (FakeMCP does the same):…, Genesys: the posting was still re-rendering — `detached`, then `page_changed` —…, _record_clicks(), click(), test_a_cookie_pop_up_over_the_form_is_declined_and_the_form_submit_never_confirmed(), test_the_agents_entry_click_is_tried_again_while_the_posting_re_renders(), test_the_never_submit_rule_on_the_server_refuses_submit_always_and_apply_while_filling()

### Community 42 - "Text Helper Rotation"
Cohesion: 0.22
Nodes (8): engine(), The package types a custom widget's value with ONE text model (TEXT_MODEL, read…, rotate_text_helper(), text_for(), attempt(), cfg(), jev.rotate_text_helper wraps the package's policy.text_for(cfg, …) with the…, test_the_text_helper_rotates_inside_the_package()

### Community 43 - "ATS Form Pattern Fixtures"
Cohesion: 0.28
Nodes (9): fill.run_pages (navigate + form-fill loop), f09: multi-step resume/cover-letter upload with hidden file input triggered by styled label, f10_form: generic ATS application form (relocate radio, resume upload, submit), f10: careers job posting page with external Apply link opening new tab, f15_google: sign-in gate with "Sign in with Google" and email/password form blocking application, f16_signup: candidate account creation gate with a guest-apply escape link, 4012345604-external: LinkedIn job page whose Apply link opens the external ATS form in a new tab, 4012345607-late-tab: Apply click opens the external form tab only after a 2.5s delay (delayed new-tab case) (+1 more)

### Community 44 - "Captured External-Redirect Patterns"
Cohesion: 0.32
Nodes (8): "Apply on company website" external redirect pattern, Demographic form fields (gender, age) on application forms, LinkedIn Easy Apply modal flow, probes.py read-only eval probe output format (n/more/items), Captured run: Digital Manufacturing Ireland LinkedIn Easy Apply modal, Captured run: Genesys LinkedIn job (Apply on company website), Captured run: Mastercard careers site job page, Captured run: The Flex Ashby application form (demographics fields)

### Community 45 - "FakeMCP Tab Stand-In"
Cohesion: 0.25
Nodes (3): FakeBook, FakeMCP: an in-memory stand-in for jev-ultrafast-browser at the text level…, TabBook stand-in for FakeMCP tests: one tab, no pop-ups, nothing to hand off.

### Community 46 - "Ashby Resume Autofill Captures"
Cohesion: 0.52
Nodes (7): Ashby (jobs.ashbyhq.com) ATS, Invisible reCAPTCHA cross-origin frame (unreadable), Resume upload autofill of application fields, Captured Ashby job posting (ElevenLabs, Overview tab), Captured Ashby job posting overview (ElevenLabs, observe+text), Captured Ashby application page (ElevenLabs, resume autofill), Captured Ashby application page (ElevenLabs, later state)

### Community 48 - "Page Goal Prompt Tests"
Cohesion: 0.40
Nodes (5): page_goal(), (goal text, max_steps) for one goal that sets every (question, answer) pair., test_page_goal_prompt(), §9: text helper, live_model — ANSWERS typed exactly on f08 through one page…, test_answers_set_exactly_on_f08()

### Community 49 - "Never-Click Confirm Reason"
Cohesion: 0.40
Nodes (6): never_click(), Why a click on this control is refused, or None., guard_clicks(), confirm_reason(), Every click, the agent's and ours, goes through the package's…, test_the_package_asks_never_click_before_every_click()

### Community 50 - "UTF-16 Surrogate Cleaning"
Cohesion: 0.33
Nodes (6): clean_json(), clean_text(), Replace lone UTF-16 surrogates with "?". The package cuts page text in…, clean_text on every string in a JSON-shaped value., Genesys, live 2026-09-24: page text cut in JavaScript left "\\ud835" alone;…, test_a_lone_surrogate_from_the_page_never_reaches_a_log()

### Community 51 - "Custom Widget Wandering Tests"
Cohesion: 0.40
Nodes (5): steps_site with a custom dropdown on step 1, so step 1 needs a page goal., test_fill_goal_that_always_leaves_the_page_is_a_broken_form(), test_fill_goal_that_leaves_the_page_once_is_recovered(), _wanderer(), widget_steps()

### Community 52 - "Run-Stopping Errors"
Cohesion: 0.40
Nodes (5): Exception, Stop the whole run now, exit 3 (§4.6 alarms, C19 signed out, tracker changed,…, Load failure, attempt 2: reopen the job from its LinkedIn URL., RestartFromEntry, StopRun

### Community 53 - "Modal-Blocking Captures"
Cohesion: 0.50
Nodes (5): Apply button covered/occluded once dialog is open (⊘ flag), Cookie consent modal blocking page interaction, LinkedIn "Job search safety reminder" modal, Captured run: Linda AI LinkedIn job (safety reminder dialog), Captured run: Toast careers page (cookie consent dialog)

### Community 54 - "Easy Apply Modal Fixtures"
Cohesion: 0.50
Nodes (5): Easy Apply navigation stage (goal agent reaching the application form), Easy Apply modal fixture (LinkedIn job view), Easy Apply <dialog> fixture (delayed modal, LinkedIn job view), display:contents probe lab fixture, LinkedIn login page fixture

### Community 55 - "Toast Run Captures"
Cohesion: 0.50
Nodes (5): Toast run-20260923 probes capture, probes.py (read-only page probes), Toast run-20260924-filled observe capture, Toast run-20260924-filled probes capture, Discovery form fixture (field-type coverage)

### Community 57 - "Broken-Form Refill Attempt"
Cohesion: 0.50
Nodes (3): Exception, broken_form attempt 2: reopen the page where filling started and fill it again., _Refill

### Community 58 - "Golden Guard Click Cases"
Cohesion: 0.50
Nodes (4): never-submit rule (guard.never_click), golden: browser_act click on detached element is blocked, golden: browser_act Submit click needs_confirmation via confirmation rule, golden: browser_goal turbo_unavailable when no decision-model key set

### Community 59 - "Golden Tab Handle Cases"
Cohesion: 0.67
Nodes (4): tabs.TabBook handle/target_id scheme, golden: observation delta showing NEW TAB notice with switch target_id, golden: browser_tabs list output with short handles, golden: browser_tabs new tab creation output

### Community 60 - "LinkedIn Job View Captures"
Cohesion: 0.50
Nodes (4): LinkedIn job view 123939 observe capture, LinkedIn job view 123939 text capture, LinkedIn job view 141137 observe capture (signed in), LinkedIn job view 141137 text capture

### Community 61 - "Single Worker Thread"
Cohesion: 0.67
Nodes (3): _executor(), One dedicated worker thread for the whole process: one call at a time (B7)., ThreadPoolExecutor

### Community 62 - "Greenhouse ATS Findings"
Cohesion: 0.67
Nodes (3): Greenhouse ATS findings, First real run: seven jobs to Needs-Attention (runs/20260923-224945), captured job-boards.greenhouse.io pages

### Community 63 - "Golden Assert/Observe Cases"
Cohesion: 0.67
Nodes (3): golden: browser_assert text_contains/text_absent/js checks, overall FAIL, golden: browser_observe full JSON element dump (Discovery form), golden: browser_open initial observation of Discovery form

## Ambiguous Edges - Review These
- `Captured run: Digital Manufacturing Ireland LinkedIn Easy Apply modal` → `Invisible reCAPTCHA cross-origin frame (unreadable)`  [AMBIGUOUS]
  tests/captured/run-20260923-dmi/observe.txt · relation: references
- `Captured run: Linda AI LinkedIn job (safety reminder dialog)` → `Invisible reCAPTCHA cross-origin frame (unreadable)`  [AMBIGUOUS]
  tests/captured/run-20260923-linda/observe.txt · relation: references
- `f10_form: generic ATS application form (relocate radio, resume upload, submit)` → `f16_signup: candidate account creation gate with a guest-apply escape link`  [AMBIGUOUS]
  tests/fixtures/f16_signup.html · relation: references
- `golden: browser_goal turbo_unavailable when no decision-model key set` → `never-submit rule (guard.never_click)`  [AMBIGUOUS]
  tests/golden/goal_no_key.txt · relation: conceptually_related_to

## Knowledge Gaps
- **74 isolated node(s):** `_FormStage`, `application-assistant`, `records.build_queue`, `pages.classify_entry`, `pages.gate` (+69 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 445 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **28 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **What is the exact relationship between `Captured run: Digital Manufacturing Ireland LinkedIn Easy Apply modal` and `Invisible reCAPTCHA cross-origin frame (unreadable)`?**
  _Edge tagged AMBIGUOUS (relation: references) - confidence is low._
- **What is the exact relationship between `Captured run: Linda AI LinkedIn job (safety reminder dialog)` and `Invisible reCAPTCHA cross-origin frame (unreadable)`?**
  _Edge tagged AMBIGUOUS (relation: references) - confidence is low._
- **What is the exact relationship between `f10_form: generic ATS application form (relocate radio, resume upload, submit)` and `f16_signup: candidate account creation gate with a guest-apply escape link`?**
  _Edge tagged AMBIGUOUS (relation: references) - confidence is low._
- **What is the exact relationship between `golden: browser_goal turbo_unavailable when no decision-model key set` and `never-submit rule (guard.never_click)`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._
- **Why does `Jev` connect `Jev Package Wrapper Calls` to `CLI Config & Models`, `Question Coverage Judgment`, `Job Processing Pipeline Test`, `Needs-Attention & Google Sign-In`, `Package Request Cleaning`, `Entry Click & New-Tab Handoff`, `Tab Bookkeeping`, `Page Classification & Settle`, `Discovery Capture Tool`, `Fill Plan & Field Choice`, `Answer Confirmation Rules`, `CLI Preflight & Queue`, `FakeMCP Tab Stand-In`, `Jev Contract & Decision Errors`, `Test Fixtures & Chrome Setup`, `FakeMCP Browser Stub`, `Guard & Probes Core`?**
  _High betweenness centrality (0.072) - this node is a cross-community bridge._
- **Why does `FakeMCP` connect `FakeMCP Browser Stub` to `New-Tab Field Wait Test`, `Navigate-to-Form Loop`, `Needs-Attention & Google Sign-In`, `Click Recording & Retry Tests`, `FakeMCP Tab Stand-In`, `Jev Package Wrapper Calls`, `Custom Widget Wandering Tests`, `Never-Submit Rule Tests`?**
  _High betweenness centrality (0.069) - this node is a cross-community bridge._
- **Why does `TabBook` connect `Tab Bookkeeping` to `CLI Config & Models`, `Job Processing Pipeline Test`, `Needs-Attention & Google Sign-In`, `Page Classification & Settle`, `CLI Preflight & Queue`, `Jev Package Wrapper Calls`, `Live Model Answer Tests`?**
  _High betweenness centrality (0.041) - this node is a cross-community bridge._