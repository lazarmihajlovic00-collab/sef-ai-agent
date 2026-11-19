# Decision log

Short records of the design decisions behind this project: the question, the options, what was chosen and why.
Lazar Mihajlović owns the project (problem, scope, goals, final say). The implementation was built with Claude
(AI) as the builder. Where a decision was a builder recommendation that Lazar accepted, it says so.

## D0. Problem and scope (Lazar, 2026-11-19)
**Question:** Which project best demonstrates AI agents and process automation for real companies, without
being a generic chatbot demo?
**Chosen:** An accounts-payable agent for Serbian e-invoices (SEF). Since 2023 every B2B invoice in Serbia goes
through SEF as UBL XML, so every company has the same structured input and the same manual work: checking
invoices against orders and receipts, catching duplicates, booking, and answering "how much do we owe".
**Rejected:** an e-mail-to-spreadsheet agent (too generic, Lazar's call).

## D1. Backend language: Python (recommendation, accepted)
**Options:** (a) Python, (b) C#/.NET (Lazar's day-job stack).
**Chosen:** Python + FastAPI. Most AI/automation job ads ask for Python, the Anthropic SDK and data tooling are
strongest there, and the domain logic is small enough that the language is not the hard part.
**Trade-off:** less reuse of .NET experience. The SEF client and matching rules are plain functions and would port
to C# directly.

## D2. Autonomy: the agent only proposes (recommendation, accepted)
**Options:** (a) a person approves every booking and every accept/reject in SEF, (b) clean invoices go through
automatically, people see only exceptions.
**Chosen:** (a). Every state-changing step is an `ActionRequest` that a named person approves or denies, and the
API refuses decisions from `agent*` users. The chat agent can *request* a rejection but cannot execute it.
**Why:** accountants and owners will not trust an agent with money on day one, and in Serbia accepting an invoice
in SEF has tax consequences (input VAT). "One click to approve a prepared proposal" already removes most of the
work. Going to (b) later is a configuration change once trust is earned on real data.

## D3. Price tolerance: 1% (recommendation, accepted)
**Options:** (a) differences up to 1% pass with a warning, above that block, (b) zero tolerance.
**Chosen:** (a), configurable via `PRICE_TOLERANCE_PCT`. Rounding and small list-price updates are common; blocking
every 0.4% difference floods the exception queue and teaches people to click through warnings.

## D4. What blocks an invoice (recommendation, accepted)
**Chosen:** duplicates (exact and same-amount), unknown supplier, invalid PIB, changed bank account, missing PO for
goods, price difference above tolerance, quantity not received, item not on the PO, VAT or total errors all
**block** and go to a person. A price difference within tolerance is a **warning** only.
**Why:** each blocking finding is either a classic fraud pattern (fake supplier, changed bank account, re-sent
invoice) or a direct overpayment. Severity lives in one table (`matching/engine.py: SEVERITY`).

## D5. Models and cost (recommendation, accepted)
**Options:** (a) one strong model for everything, (b) a small model for booking classification and a strong model
for the conversational agent.
**Chosen:** (b). Booking a line is a narrow, high-volume classification task with structured output, so it runs on
a small, cheap model (`BOOKING_MODEL`, default Claude Haiku). Answering questions needs multi-step tool use, so it
runs on a stronger model (`CHAT_MODEL`, default Claude Opus). Both are environment variables, and the evaluation
measures accuracy and cost per line so the choice can be revisited with numbers.

## D6. Rules decide correctness, the LLM does not (builder design)
Whether an invoice matches the order is arithmetic, and money decisions must be repeatable and explainable to an
auditor. So duplicates, PIB checks, 3-way match and VAT checks are deterministic code. The LLM is used where rules
are weak: classifying free-text service lines to accounts, and turning questions in Serbian into tool calls.

## D7. The chat agent never does arithmetic (builder design)
All amounts come from SQL behind tools (`payables`, `spend_by_supplier`, ...). The system prompt forbids numbers
that did not come from a tool, and the evaluation checks answers against SQL ground truth. A guarded read-only
`run_sql` tool covers questions the fixed tools do not.

## D8. Tamper-evident audit log (builder design)
Each audit row stores the SHA-256 of the previous row. Editing or deleting history breaks the chain, and
`sef-agent verify-audit` / the dashboard show where. Cheap to build, and it answers the first question an auditor
or a finance director asks.

## D9. Mock SEF with the same interface (builder design)
The SEF demo environment requires a company account and an API key. The `SefClient` interface has two
implementations: `HttpSefClient` (public API, `ApiKey` header) and `MockSefClient` (folder of generated UBL files).
Everything, including tests and the evaluation, runs without SEF access; switching is `SEF_MODE=http`.

## D10. Honest evaluation (builder design)
The same team wrote the data generator and the rules, so detection scores only prove the controls work as specified.
To keep the LLM numbers honest the booking evaluation adds a hand-written "hard set" of ambiguous service
descriptions that the keyword baseline was not written for, and Q&A answers are compared with SQL results.

## D11. Two LLM providers: Claude and Gemini (Lazar, 2026-11-19)
**Question:** The evaluation needs a working API key. Pay for the Anthropic API, or use the Gemini key Lazar
already has?
**Chosen (Lazar):** support both. `LLM_PROVIDER=anthropic|gemini` switches the booking classifier and the chat agent;
the rules, tools, approvals and evaluation are shared. Results in the README say which provider produced them.
**Trade-offs:** Gemini's free tier may use submitted data to improve Google's models. That is fine for this project's
fictional invoices, but a real company's invoices need a paid tier (or Claude) with no-training terms. Keeping the
provider behind two call sites also shows the design is not locked to one vendor.
